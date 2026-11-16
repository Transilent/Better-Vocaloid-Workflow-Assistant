"""Native ACE and exchange-score tests using an independent codec and clock."""
from base64 import b64decode, b64encode
from bisect import bisect_right
from copy import deepcopy
import json
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from ace_tuning import ACEP, UFDATA
from common import atomic_json, file_hash
from tuning_timeline import ProjectNotes
import tuning_workflow as workflow


def codecs():
    path = str(APP / 'dependencies/export')
    sys.path.insert(0, path)
    try:
        from backports import zstd
        import cbor2
        return zstd, cbor2
    finally:
        sys.path.remove(path)


def ace_score():
    def note(pos, duration, key, lyric, **attrs):
        return dict(pos=pos, dur=duration, pitch=key, lyric=lyric, pronunciation='mei', **attrs)

    def clip(i, pos, notes, **attrs):
        return dict(uuid=i, pos=pos, dur=1200, clipPos=0, clipDur=1200, timeUnit='tick',
                    notes=notes, parameters={}, **attrs)

    lead = clip('lead-1', 480, [note(0, 480, 60, '窗'), note(480, 480, 65, '边'),
        note(990, 210, 63, '光', vibrato={'amplitude':.18,'frequency':5.5})])
    lead['parameters'] = {
        'pitchDelta':[{'type':'data','offset':-20,'values':[0.14]*32+[None]*6+[0.23]*180}],
        'breathiness':[{'type':'data','offset':40,'values':[.98]*40}],
        'realEnergy':[{'type':'data','offset':60,'values':[1.2]*18}],
        'futureLane':{'anchors':[1,2,3]},
    }
    anchor = clip('lead-2', 1920, [note(12,900,60,'留')])
    anchor['parameters']['pitchDelta'] = [{'type':'anchor','points':[12,60,900,61]}]
    trimmed = clip('lead-3',3840,[note(0,480,60,'裁')])
    trimmed['clipPos'] = 240
    hidden = clip('lead-4',5400,[note(0,720,60,'藏')],enabled=False)
    hidden['uuid'] = 'lead-shared'
    cloned = clip('lead-shared',6600,[note(0,480,62,'共')])
    overlap = clip('overlap',6720,[note(0,720,64,'叠')])
    bass = clip('harmony-1',720,[note(0,480,55,'和'),note(528,480,57,'声')])
    return {
        'version':9,'versionRevision':0,
        'tempos':[{'position':0,'bpm':120}, {'position':720,'bpm':90},{'position':2400,'bpm':150}],
        'timeSignatures':[{'barPos':0,'numerator':4,'denominator':4},{'barPos':2,'numerator':3,'denominator':4}],
        'tracks':[
            {'type':'sing','name':'主唱','patterns':[lead,anchor,trimmed,hidden,cloned,overlap],
             'singers':[{'singer':{'singerId':123},'randomSeed':77}],'future-track':{'blend':.48}},
            {'type':'audio','name':'伴奏','patterns':[{'path':'Samples/原伴奏.wav', 'timeUnit':'sec',
                                                   'pos':0.123, 'gain':-4,'unknownExtra':'keep'}]},
            {'type':'sing','name':'和声','patterns':[bass]},
            {'type':'instrument','name':'弦乐','patterns':[{'notes':[{'futureNote':True}]}]},
        ],
        'videoTracks':[{'path':'Samples/画面.mp4'}], 'vendor-extra':{'keep':[True,1,2.1,'值']},
    }


def wrap_ace(score, *, binary=False):
    zstd, cbor = codecs()
    if binary:
        contents = cbor.dumps(score)
        zipped = zstd.compress(contents)
        # This header models the bundled reader's unencrypted payload layout.
        prefix = struct.pack('<5sBHQQQH',b'ACEP2',1,0,192,len(zipped),len(contents),138)
        return prefix + bytes(138) + b'\x01' + bytes(19) + zipped
    payload = json.dumps(score, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return json.dumps({'version':1000,'compressMethod':'zstd','content':b64encode(zstd.compress(payload)).decode(),
                       'salt':'','debugInfo':{'build':'fixture'},'unknownEnvelopeField':[42,None]}).encode('utf-8-sig')


def uf_score(*, relative=False, empty=False):
    pitch = ({'isAbsolute':False,'ticks':[0,70,310,480,600,1000], 'values':[0,.22,0,.3,0,0]}
             if relative else {'isAbsolute':True,'ticks':[0,0,70,310,479,480,480,720,960,1250,1340],
             'values':[0,60,60.25,59.93,0,0,64,63.9,None,67.25,None]})
    if empty:
        pitch = None
    return {'formatVersion':1,'project':{
        'measurePrefix':2,'name':'exchange','tempos':[{'tickPosition':0,'bpm':120},{'tickPosition':500,'bpm':90}],
        'timeSignatures':[{'measurePosition':0,'numerator':4,'denominator':4},
                          {'measurePosition':1,'numerator':7,'denominator':8}],
        'tracks':[
            {'name':'主唱','notes':[dict(tickOn=0,tickOff=480,key=60,lyric='春',phoneme='chun'),
                                  dict(tickOn=480,tickOff=1000,key=64,lyric='江',phoneme=None),
                                  dict(tickOn=1200,tickOff=1680,key=67,lyric='水')],
             'pitch':pitch,'unknown':{'mustStay':True}},
            {'name':'和声','notes':[dict(tickOn=120,tickOff=1050,key=50,lyric='伴')],'pitch':None},
        ],'unknown':{'mustStay':True}}, 'extension':{'schema':'keep'}}


def editable_ace_score():
    data = ace_score()
    clip = data['tracks'][0]['patterns'][0]
    clip.update(clipPos=40, clipDur=1160, parameters={})
    clip['notes'][0].update(pos=40, dur=300, pronunciation=None)
    return data


def editable_uf_score():
    data = uf_score(empty=True)
    notes = data['project']['tracks'][0]['notes']
    notes[0]['phoneme'] = None
    notes[1]['tickOn'] = 720
    return data


def parameters(track, begin, end):
    return {'track_indices':[track], 'style_regions':[{
        'start':begin,'end':end,'preset':'gentle','strength':.85,'pitch':True,'track_index':track}]}


def replace_data(before, after, track, part):
    expected = deepcopy(before)
    expected['tracks'][track]['patterns'][part]['parameters']['pitchDelta'] = (
        after['tracks'][track]['patterns'][part]['parameters']['pitchDelta'])
    return expected


def native_curve(score, track=0, part=0):
    curves = score['tracks'][track]['patterns'][part]['parameters'].get('pitchDelta', [])
    return {c['offset']+i:v for c in curves for i,v in enumerate(c['values'])}


def uf_value(curve, tick):
    if curve is None:
        return None
    i = bisect_right(curve['ticks'],tick)-1
    return curve['values'][i] if i>=0 else None if curve['isAbsolute'] else 0.


class AceFormats(unittest.TestCase):
    def setUp(self):
        (APP/'work').mkdir(exist_ok=True)
        temp = TemporaryDirectory(prefix='ace-workflow-',dir=APP/'work')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.zstd, _ = codecs()

    def test_ace_score_edits_preserve_pronunciation_clip_bounds_and_resources(self):
        original = editable_ace_score()
        source = self.root / 'original.acep'
        source.write_bytes(wrap_ace(original))
        before = file_hash(source)
        timeline = ProjectNotes.load(source)
        first, = timeline.matches('窗')
        peer, = timeline.matches('边')
        anchor, = timeline.matches('留')
        self.assertFalse(any(first[k] for k in ('lyricReason', 'pitchReason', 'timingReason')))
        self.assertTrue(peer['lyricReason'] and not peer['pitchReason'])
        self.assertTrue(anchor['lyricReason'] and anchor['timingReason'] and anchor['pitchReason'])
        with self.assertRaisesRegex(ValueError, '可编辑范围'):
            timeline.with_note_edits([{'note_id': first['id'], 'onset': 39}])
        with self.assertRaisesRegex(ValueError, '手工发音'):
            timeline.with_note_edits([{'note_id': peer['id'], 'lyric': '河'}])
        edits = [{'note_id': first['id'], 'lyric': '星', 'pitch': 62, 'onset': 95, 'duration': 330}]
        session = workflow.create_session(source, self.root / 'native-session')
        workflow.run_session(session, {'track_indices': [0], 'note_edits': edits})
        receipt = workflow.verified_result(session)
        saved = self.root / 'edited.acep'
        workflow.export_verified_copy(receipt, saved)
        expected = deepcopy(original)
        expected['tracks'][0]['patterns'][0]['notes'][0].update(pos=95, dur=330, pitch=62, lyric='星')
        self.assertEqual(workflow.read_project(saved)['project'], expected)
        row, = receipt['report']['note_changes']
        self.assertEqual(set(row['fields']), {'lyric', 'pitch', 'onset', 'duration'})
        self.assertEqual(file_hash(source), before)
        self.assertEqual(file_hash(saved), receipt['output_sha256'])
        protected = self.root / 'readonly.acep'
        protected.write_bytes(wrap_ace(original, binary=True))
        model = ProjectNotes.load(protected)
        self.assertFalse(model.score_editor.editable_tracks)
        first, = model.matches('窗')
        with self.assertRaisesRegex(ValueError, '原生编辑能力'):
            model.with_note_edits([{'note_id': first['id'], 'pitch': 68}])

    def test_ufdata_score_edits_write_end_ticks_and_leave_authored_pitch_alone(self):
        original = editable_uf_score()
        track = original['project']['tracks'][0]
        track['pitch'] = {'isAbsolute': True, 'ticks': [680, 990], 'values': [63.97, None]}
        source = self.root / 'original.ufdata'
        atomic_json(source, original)
        before = file_hash(source)
        timeline = ProjectNotes.load(source)
        first, = timeline.matches('春')
        anchored, = timeline.matches('江')
        self.assertFalse(first['timingReason'] or first['pitchReason'])
        self.assertTrue(anchored['timingReason'] and anchored['pitchReason'] and not anchored['lyricReason'])
        for edits, reason in [([{'note_id': first['id'], 'duration': 690}], '原生曲线'),
                              ([{'note_id': anchored['id'], 'pitch': 65}], '原生音高'),
                              ([{'note_id': anchored['id'], 'onset': 690}], '原位置')]:
            with self.subTest(edits=edits), self.assertRaisesRegex(ValueError, reason):
                timeline.with_note_edits(edits)
        moved = {'note_id': first['id'], 'onset': 55, 'pitch': 62, 'lyric': '青'}
        materialized, detail = timeline.score_editor.materialize(original, [moved])
        first_native = materialized['project']['tracks'][0]['notes'][0]
        self.assertEqual((first_native['tickOn'], first_native['tickOff']), (55, 535))
        self.assertEqual(detail[0]['native_fields']['tickOff']['after'], 535)
        session = workflow.create_session(source, self.root / 'exchange-session')
        # Resize the head note and edit the protected note's lyric independently.
        workflow.run_session(session, {'track_indices': [0], 'note_edits': [
            {**moved, 'duration': 545}, {'note_id': anchored['id'], 'lyric': '河'}]})
        receipt = workflow.verified_result(session)
        saved = self.root / 'edited.ufdata'
        workflow.export_verified_copy(receipt, saved)
        expected = deepcopy(original)
        notes = expected['project']['tracks'][0]['notes']
        notes[0].update(tickOn=55, tickOff=600, key=62, lyric='青')
        notes[1]['lyric'] = '河'
        self.assertEqual(workflow.read_project(saved), expected)
        self.assertEqual(file_hash(source), before)

        relative = self.root / 'relative.ufdata'
        atomic_json(relative, uf_score(relative=True))
        timeline = ProjectNotes.load(relative)
        first, = timeline.matches('春')
        self.assertTrue(first['lyricReason'] and first['timingReason'])
        self.assertFalse(first['pitchReason'])
        edited, _ = timeline.score_editor.materialize(workflow.read_project(relative), [
            {'note_id': first['id'], 'pitch': 65}])
        self.assertEqual(edited['project']['tracks'][0]['pitch'], uf_score(relative=True)['project']['tracks'][0]['pitch'])
        self.assertEqual(edited['project']['tracks'][0]['notes'][0]['key'], 65)

    def test_manual_pitch_preserves_ace_resources_and_both_ufdata_axes(self):
        for format, raw in [('acep', wrap_ace(ace_score())), ('ufdata', json.dumps(uf_score()).encode()),
                            ('ufdata-relative', json.dumps(uf_score(relative=True)).encode())]:
            with self.subTest(format=format):
                suffix = format.split('-')[0]
                native = suffix == 'acep'
                source = self.root/f'{format}.{suffix}'
                source.write_bytes(raw)
                timeline = ProjectNotes.load(source)
                note, *_ = timeline.visible(timeline.editable[:1], 0, 30)
                assert note['lyric'] in ('窗', '春')
                a = note['start']
                strokes = timeline.pitch_layer.gesture([], {}, note['id'], 'draw',
                    [[a+.052, 0], [a+.079, 85], [a+.145, -33]])
                if native:
                    for text in ('留','裁','光'):
                        protected, = timeline.matches(text)
                        self.assertTrue(timeline.notes[protected['id']]['drawReason'])
                else:
                    self.assertEqual(timeline.edit_track_indices(), set())
                timeline = timeline.with_edits([], strokes)
                self.assertEqual(timeline.edit_track_indices(), {0})
                span, = timeline.pitch_layer.resolve(strokes, {})
                old = workflow.read_project(source)['project']

                def value(project, tick):
                    if native:
                        return native_curve(project).get(tick, 0.)
                    pitch = project['tracks'][0]['pitch']
                    heard = uf_value(pitch, tick)
                    if heard is None:
                        return 0.
                    return heard if not pitch['isAbsolute'] else heard - note['pitch']

                session = workflow.create_session(source, self.root/f's-{format}')
                both, plain = [], []
                for preset in ('gentle','bold'):
                    opts = parameters(0, a, note['end'])
                    opts['style_regions'][0]['preset'] = preset
                    styled_receipt = workflow.run_session(session, opts)
                    styled = workflow.read_project(styled_receipt['output'])['project']
                    receipt = workflow.run_session(session, {**opts,'pitch_strokes':strokes})
                    saved = self.root/f'{format}-{preset}.{suffix}'
                    workflow.export_verified_copy(workflow.verified_result(session), saved)
                    read = workflow.read_project(saved)['project']
                    self.assertTrue(receipt['report']['hand_pitch']['curve_changes'])
                    self.assertTrue(receipt['report']['notes_and_timing_unchanged'])
                    if native:
                        self.assertEqual(read, replace_data(styled, read, 0, 0))
                        envelope = json.loads(saved.read_bytes())
                        self.assertEqual(envelope['unknownEnvelopeField'], [42,None])
                        self.assertEqual(read, json.loads(self.zstd.decompress(b64decode(envelope['content']))))
                    else:
                        normalized = deepcopy(read)
                        normalized['tracks'][0]['pitch'] = styled['tracks'][0]['pitch']
                        self.assertEqual(normalized, styled)
                        self.assertEqual(read['tracks'][0]['pitch']['isAbsolute'],
                                         old['tracks'][0]['pitch']['isAbsolute'])
                        if format == 'ufdata':
                            pitch = read['tracks'][0]['pitch']
                            self.assertEqual(list(zip(pitch['ticks'],pitch['values']))[:2], [(0,0),(0,60)])
                    xs, ys = zip(*span.points)
                    actual, without_ink = [], []
                    for tick in range(0, 480):
                        if tick <= span.left or tick >= span.right:
                            self.assertEqual(value(read,tick), value(styled,tick), tick)
                        if xs[0] <= tick <= xs[-1]:
                            got = value(read,tick)
                            self.assertAlmostEqual(got, value(old,tick) + np.interp(tick,xs,ys)/100., delta=.0000011)
                            actual.append(got)
                            without_ink.append(value(styled,tick))
                    both.append(actual)
                    plain.append(without_ink)
                np.testing.assert_allclose(both[0], both[1], rtol=0., atol=.0000011)
                self.assertNotEqual(plain[0], plain[1])
                self.assertEqual(source.read_bytes(),raw)

    def test_manual_pitch_rejects_ace_null_and_ufdata_break_without_writing(self):
        uf = uf_score()
        pitch = uf['project']['tracks'][0]['pitch']
        pitch['ticks'][2:2] = [55, 65]
        pitch['values'][2:2] = [0., 60.]
        for suffix, raw in [('ufdata',json.dumps(uf).encode()), ('acep',wrap_ace(ace_score()))]:
            with self.subTest(suffix=suffix):
                source = self.root/f'break.{suffix}'
                source.write_bytes(raw)
                timeline = ProjectNotes.load(source)
                note, *_ = timeline.visible(timeline.editable[:1],0,30)
                a = note['start']
                strokes = timeline.pitch_layer.gesture([], {}, note['id'], 'draw',
                                                       [[a+.022,40],[a+.12,54]])
                session = workflow.create_session(source,self.root/f'break-{suffix}')
                with self.assertRaisesRegex(ValueError,'音高断点'):
                    workflow.run_session(session, {'pitch_strokes':strokes})
                self.assertFalse(list((session.parent/'outputs').glob('*')))

    def test_native_round_trip_selected_clip_and_samples(self):
        score = ace_score()
        raw = wrap_ace(score)
        folder = self.root/'工程'
        folder.mkdir()
        resource = folder/'Samples'
        resource.mkdir()
        audio = resource/'原伴奏.wav'
        audio.write_bytes(b'reference-fixture-not-decoded')
        audio_hash = file_hash(audio)
        source = folder/'春江.acep'
        source.write_bytes(raw)
        view = ProjectNotes.load(source)
        lead, harmony = list(view.tracks)
        self.assertEqual([view.tracks[k]['noteCount'] for k in (lead,harmony)], [8,2])
        self.assertEqual(set(view.editable), {lead,harmony})
        word = view.matches('窗')[0]
        self.assertAlmostEqual(word['start'], .5, places=10)
        self.assertAlmostEqual(word['end'], .75 + .3333333333333333, places=10)
        self.assertTrue(any(n.get('readOnlyReason') for n in view.notes.values() if n['lyric']=='留'))
        self.assertTrue(any(n.get('readOnlyReason') for n in view.notes.values() if n['lyric']=='裁'))

        session = workflow.create_session(source,self.root/'会话')
        expected_left = .5
        expected_right = .75 + (1200-720)/720
        options = parameters(0,expected_left,expected_right)
        result = workflow.run_session(session,options)
        self.assertTrue(result['receipt_verified'])
        self.assertTrue(result['report']['pitch_changed'])
        self.assertEqual([r['group_placement'] for r in result['report']['curve_changes']], ['clip:0'])
        target = Path(result['output'])
        original_envelope = json.loads(raw)
        envelope = json.loads(target.read_bytes())
        project = json.loads(self.zstd.decompress(b64decode(envelope.pop('content'))))
        del original_envelope['content']
        self.assertEqual(envelope, original_envelope)
        self.assertEqual(project, replace_data(score,project,0,0))
        before, after = native_curve(score), native_curve(project)
        changed = {k for k in before.keys() | after.keys() if before.get(k,'missing') != after.get(k,'missing')}
        self.assertTrue(changed and min(changed) >= 0 and max(changed) < 720)
        self.assertTrue(all(after[i] is None for i,value in before.items() if value is None))
        self.assertEqual(source.read_bytes(),raw)
        self.assertEqual(file_hash(audio),audio_hash)
        self.assertEqual(workflow.verified_result(session)['output_sha256'],file_hash(target))
        reused = workflow.run_session(session,options)
        self.assertTrue(reused['cached'])
        self.assertEqual(result['output'], reused['output'])
        self.assertEqual(reused['project_commit_count'],0)

        with self.assertRaisesRegex(ValueError,'原工程所在'):
            workflow.export_verified_copy(result,self.root/'too-far.acep')
        with self.assertRaises(FileExistsError):
            workflow.export_verified_copy(result, source)
        saved = folder/'春江-VWA.acep'
        workflow.export_verified_copy(result,saved)
        self.assertEqual(file_hash(saved),file_hash(target))
        self.assertTrue(workflow.inspect_project(saved)['candidate_origin']['verified'])
        with self.assertRaises(FileExistsError):
            workflow.export_verified_copy(result,saved)
        import task_contract
        job = self.root/'job'
        job.mkdir()
        atomic_json(job/'request.json',{'source_kind':'local'})
        with self.assertRaisesRegex(ValueError,'同目录'):
            workflow.register_result(session,job=job)
        task_contract.bind(job,'project',saved)
        self.assertEqual(task_contract.selected(job,'project'),saved)
        self.assertEqual(file_hash(audio),audio_hash)

    def test_native_no_effect_is_byte_identical_and_protected(self):
        raw = wrap_ace(ace_score())
        source = self.root/'source.acep'
        source.write_bytes(raw)
        session = workflow.create_session(source,self.root/'session')
        result = workflow.run_session(session,{'track_indices':[0],'performance_style':'gentle','style_pitch':False})
        self.assertEqual(Path(result['output']).read_bytes(),raw)
        self.assertEqual(result['status'],'unchanged')
        all_regions = workflow.run_session(session,{'track_indices':[0],'performance_style':'gentle','style_pitch':True})
        codes = {s['reason'] for s in all_regions['report']['native_curve_skips']}
        self.assertTrue({'ace_pitch_controls','ace_clipped_note','ace_disabled_or_shared','ace_overlap'} <= codes)
        self.assertEqual(native_curve(workflow.read_project(all_regions['output'])['project']).get(1075),
                         native_curve(ace_score()).get(1075))

    def test_binary_is_read_only_and_corruption_never_falls_back(self):
        score = ace_score()
        raw = wrap_ace(score,binary=True)
        path = self.root/'new.acep'
        path.write_bytes(raw)
        timeline = ProjectNotes.load(path)
        self.assertEqual(timeline.project['format_version'],'2 / 9')
        self.assertEqual(len(timeline.notes),10)
        self.assertFalse(timeline.editable)
        session = workflow.create_session(path,self.root/'session')
        with self.assertRaisesRegex(ValueError,'UFData'):
            workflow.run_session(session,{'track_indices':[0],'style_pitch':True,'performance_style':'gentle'})
        self.assertFalse(list((session.parent/'outputs').glob('*')))
        bad = bytearray(raw)
        struct.pack_into('<Q',bad,8,10**8)
        for payload in (bytes(bad),raw[:-1],raw+b'junk',b'{"version":2}',b'{"version":1000,"version":1}'):
            with self.subTest(size=len(payload)),self.assertRaisesRegex(ValueError,'UFData'):
                ACEP.read(payload)

        ramp = deepcopy(score)
        ramp['tempos'][0]['isLerp'] = True
        path.write_bytes(wrap_ace(ramp))
        with self.assertRaisesRegex(ValueError,'连续速度曲线'):
            ProjectNotes.load(path)

    def test_exchange_preserves_breaks_prefix_and_untouched_ticks(self):
        for settings in ({},{'relative':True},{'empty':True}):
            with self.subTest(settings=settings):
                data = uf_score(**settings)
                tag = next(iter(settings),'absolute')
                path = self.root/f'{tag}.ufdata'
                atomic_json(path,data)
                raw = path.read_bytes()
                timeline = ProjectNotes.load(path)
                start = timeline.matches('春')[0]['start']
                self.assertEqual(start,0.)
                self.assertAlmostEqual(timeline.matches('江')[0]['end'],500/960+500/720,places=10)
                session = workflow.create_session(path, self.root/tag)
                result = workflow.run_session(session,parameters(0,.53,.75))
                self.assertTrue(result['receipt_verified'])
                self.assertTrue(result['report']['pitch_changed'],result['report'])
                target = Path(result['output'])
                after = json.loads(target.read_text(encoding='utf-8'))
                old = data['project']['tracks'][0]['pitch']
                new = after['project']['tracks'][0]['pitch']
                data['project']['tracks'][0]['pitch'] = new
                self.assertEqual(after,data)
                first = 500+(.53-500/960)*720
                last = 500+(.75-500/960)*720
                changed = [tick for tick in range(-40,1700) if uf_value(new,tick)!=uf_value(old,tick)]
                self.assertTrue(changed and min(changed)>=first and max(changed)<last,(tag,changed[:2],changed[-2:]))
                if not settings:
                    self.assertEqual([v for t,v in zip(new['ticks'],new['values']) if t==480],[0,64])
                    self.assertEqual(uf_value(new,479),0)
                if settings.get('relative'):
                    self.assertFalse(new['isAbsolute'])
                self.assertEqual(path.read_bytes(),raw)
                saved = self.root/f'export-{tag}.ufdata'
                self.assertEqual(workflow.export_verified_copy(result,saved),str(saved))
                self.assertEqual(file_hash(target),file_hash(saved))

    def test_cancel_and_original_drift_do_not_publish(self):
        raw=wrap_ace(ace_score())
        path=self.root/'source.acep'
        path.write_bytes(raw)
        session=workflow.create_session(path,self.root/'interrupted')
        def cancel():
            raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            workflow.run_session(session,parameters(0,.5,1),cancel=cancel)
        self.assertFalse((session.parent/'outputs').exists())
        path.write_bytes(raw+b' ')
        with self.assertRaisesRegex(ValueError,'原始工程已在外部改变'):
            workflow.run_session(session,parameters(0,.5,1))
        self.assertFalse((session.parent/'outputs').exists())


if __name__ == '__main__':
    unittest.main()
