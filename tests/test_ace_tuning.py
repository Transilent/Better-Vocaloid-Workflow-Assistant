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
