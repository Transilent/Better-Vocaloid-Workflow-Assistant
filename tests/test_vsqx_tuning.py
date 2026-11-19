"""Check native VSQX3 and VSQX4 writes, timing and held controller samples."""

from pathlib import Path
import sys
import tempfile
import unittest
import wave
from xml.dom import minidom

import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP / 'src')]

from common import atomic_json, file_hash
from tuning_timeline import ProjectNotes
import tuning_workflow as workflow
from vocaloid_pitch import step_values
from vpr_tuning import CONTROLLERS, DYN, PBS, PIT, _curve
from vsqx_tuning import Layout, read


def write_fixture(path, version, *, with_audio=False):
    tick, duration, key, lyric, phoneme, name, part, bank, controller, value = (
        ('posTick', 'durTick', 'noteNum', 'lyric', 'phnms', 'trackName', 'musicalPart', 'vBS', 'mCtrl', 'attr')
        if version == 3 else ('t', 'dur', 'n', 'y', 'p', 'name', 'vsPart', 'bs', 'cc', 'v'))
    measure, numer, denom, bpm = ('posMes', 'nume', 'denomi', 'bpm') if version == 3 else ('m', 'nu', 'de', 'v')
    dyn, pit, pbs, other = ('DYN', 'PIT', 'PBS', 'BRE') if version == 3 else ('D', 'P', 'S', 'B')
    style = 'noteStyle' if version == 3 else 'nStyle'

    def note(start, length, pitch, words, phones, lock=0):
        return (f'<note><{tick}>{start}</{tick}><{duration}>{length}</{duration}>'
                f'<{key}>{pitch}</{key}><{lyric}><![CDATA[{words}]]></{lyric}>'
                f'<{phoneme} lock="{lock}"><![CDATA[{phones}]]></{phoneme}>'
                f'<{style}><{value} id="vibLen">0</{value}></{style}></note>')

    def control(at, identity, amount):
        return (f'<{controller}><{tick}>{at}</{tick}>'
                f'<{value} id="{identity}">{amount}</{value}></{controller}>')

    def track(index, title, notes, controls=''):
        return (f'<vsTrack><{name}>{title}</{name}><{part}><{tick}>3120</{tick}><playTime>2840</playTime>'
                f'<singer><{tick}>0</{tick}><{bank}>0</{bank}></singer>'
                f'{controls}{notes}<future:part index="{index}"/></{part}></vsTrack>')

    editable = track(0, 'Lead', note(120, 780, 60, 'ま', 'm a') + note(1700, 480, 63, 'そ', 's o', lock=1))
    controls = ''.join(control(at, identity, amount) for at, identity, amount in
                       ((0, pbs, 2), (0, dyn, 64), (47, pit, 22), (190, other, 31),
                        (248, pit, -157), (460, pbs, 5), (772, pit, 48)))
    controlled = track(1, 'Harmony', note(120, 780, 67, 'ら', '4 a') + note(1320, 600, 65, 'る', '4 M'), controls)
    audio = (f'<stTrack><wavPart><{tick}>2880</{tick}><playTime>1800</playTime>'
             '<filePath><![CDATA[伴奏.wav]]></filePath></wavPart></stTrack>') if with_audio else ''
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
           f'<vsq{version} xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq{version}/" xmlns:future="urn:fixture">'
           '<!--fixture annotation--><vVoiceTable><future:bank>keep</future:bank></vVoiceTable>'
           '<mixer><future:effect k="x"/></mixer>'
           f'<masterTrack><resolution>960</resolution><preMeasure>1</preMeasure>'
           f'<timeSig><{measure}>0</{measure}><{numer}>3</{numer}><{denom}>4</{denom}></timeSig>'
           f'<timeSig><{measure}>2</{measure}><{numer}>7</{numer}><{denom}>8</{denom}></timeSig>'
           f'<tempo><{tick}>0</{tick}><{bpm}>12000</{bpm}></tempo>'
           f'<tempo><{tick}>3840</{tick}><{bpm}>9600</{bpm}></tempo></masterTrack>'
           f'{editable}{controlled}{audio}</vsq{version}>\n')
    path.write_bytes(xml.encode('utf-8-sig'))
    return path


def native_track(project, index):
    return minidom.parseString(project['xml']).getElementsByTagName('vsTrack')[index]


def samples(part, channel, grid):
    default, _, _ = CONTROLLERS[channel]
    _, curve = _curve(part, channel)
    return step_values(grid, curve['events'], default)


class VsqxTuningTests(unittest.TestCase):
    def setUp(self):
        parent = APP / 'work'
        parent.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(dir=parent, prefix='vsqx-')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def test_pickup_uses_actual_tempo_before_content_zero(self):
        source = write_fixture(self.root / 'pickup.vsqx', 4)
        xml = source.read_text(encoding='utf-8-sig')
        source.write_text(xml.replace('<t>3120</t>', '<t>2400</t>').replace(
            '<tempo><t>3840</t><v>9600</v></tempo>',
            '<tempo><t>2640</t><v>6000</v></tempo>'), encoding='utf-8')
        timeline = ProjectNotes.load(source)
        pickup, = timeline.matches('ま')
        clock = timeline.native.clock
        self.assertEqual(clock.native_origin, 2880)
        self.assertAlmostEqual(clock.to_seconds(clock.native_position(0)), -1.625)
        self.assertAlmostEqual(pickup['start'], -.3125)
        self.assertEqual(clock.to_position(pickup['start']), clock.native_position(2520))
        self.assertTrue(pickup['timingReason'])

    def test_score_uses_native_ticks_and_preserves_xml_and_wav(self):
        for version in (3, 4):
            with self.subTest(version=version):
                source = write_fixture(self.root / f'原谱-{version}.vsqx', version, with_audio=True)
                wav = source.with_suffix('.wavparts') / '伴奏.wav'
                wav.parent.mkdir()
                with wave.open(str(wav), 'wb') as stream:
                    stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    stream.writeframes(b'\0\0' * 800)
                before, audio_sha = file_hash(source), file_hash(wav)
                baseline = workflow.read_project(source)
                timeline = ProjectNotes.load(source)
                self.assertEqual(timeline.project['format_version'], str(version))
                chosen, = timeline.matches('ま')
                protected, = timeline.matches('そ')
                controlled, = timeline.matches('ら')
                self.assertEqual((chosen['start'], chosen['end']), (.1875, .6171875))
                self.assertTrue(protected['lyricReason'] and protected['timingReason'])
                self.assertTrue(controlled['timingReason'])
                with self.assertRaisesRegex(ValueError, '原位置和时长'):
                    timeline.with_note_edits([{'note_id': controlled['id'], 'onset': 200}])
                with self.assertRaisesRegex(ValueError, '确定原语言'):
                    timeline.with_note_edits([{'note_id': chosen['id'], 'lyric': 'unknown word'}])
                edits = [{'note_id': chosen['id'], 'lyric': 'め', 'pitch': 62, 'onset': 180, 'duration': 800}]
                session = workflow.create_session(source, self.root / f'session-{version}')
                result = workflow.run_session(session, {'note_edits': edits})
                self.assertTrue(result['receipt_verified'])
                saved = self.root / f'另存-{version}.vsqx'
                workflow.export_verified_copy(result, saved)
                candidate = ProjectNotes.load(saved)
                self.assertTrue(candidate.project['candidate_origin']['verified'])
                projected, = candidate.matches('め')
                self.assertEqual((projected['pitch'], projected['start']), (62, .21875))
                written = workflow.read_project(saved)
                score = Layout(written).sequence
                new = score['tracks'][0]['parts'][0]['notes'][0]
                self.assertEqual((new['pos'], new['duration'], new['number'], new['phoneme']), (180, 800, 62, 'm e'))
                self.assertEqual(native_track(written, 1).toxml(), native_track(baseline, 1).toxml())
                untouched = ('masterTrack', 'mixer', 'vVoiceTable')
                old, new = (minidom.parseString(doc['xml']) for doc in (baseline, written))
                for name in untouched:
                    self.assertEqual(old.getElementsByTagName(name)[0].toxml(), new.getElementsByTagName(name)[0].toxml())
                self.assertIn('<!--fixture annotation-->', written['xml'])
                self.assertIn('<![CDATA[め]]>', written['xml'])
                self.assertTrue(saved.read_bytes().startswith(b'\xef\xbb\xbf'))
                reference, = result['media_references']
                self.assertEqual(Path(reference['output_reference']), wav)
                self.assertEqual(Path(new.getElementsByTagName('filePath')[0].firstChild.data), wav)
                from task_contract import bind, selected
                job = self.root / f'job-{version}'
                job.mkdir()
                atomic_json(job / 'request.json', {})
                bind(job, 'project', saved, expected_sha256=result['output_sha256'])
                self.assertEqual(selected(job, 'project', verify=True), saved)
                self.assertEqual((file_hash(source), file_hash(wav)), (before, audio_sha))

    def test_style_and_manual_pitch_keep_pbs_and_restore_rest_values(self):
        for version in (3, 4):
            with self.subTest(version=version):
                source = write_fixture(self.root / f'style-{version}.vsqx', version)
                snap = workflow.read_source(source)
                timeline = ProjectNotes.load(source)
                chosen, = timeline.matches('ら')
                start = chosen['start']
                region = timeline.region(chosen['trackId'], start+.015, chosen['end']-.015,
                                         {'preset': 'gentle', 'strength': .85, 'pitch': True})
                stroke = timeline.pitch_layer.gesture([], {}, chosen['id'], 'draw',
                                                      [[start+.09, 0], [start+.15, 60], [start+.28, -25]])
                span, = timeline.pitch_layer.resolve(stroke, {})
                options = {'track_indices': [1], 'style_regions': [region['recipe']]}
                session = workflow.create_session(source, self.root / f'style-session-{version}')
                style_result = workflow.run_session(session, options)
                result = workflow.run_session(session, {**options, 'pitch_strokes': stroke})
                self.assertTrue(result['receipt_verified'] and result['report']['hand_pitch']['curve_changes'])
                styled = workflow.read_project(style_result['output'])
                output = workflow.read_project(result['output'])
                self.assertEqual(native_track(snap.data, 0).toxml(), native_track(output, 0).toxml())
                self.assertEqual([row['parameter'] for row in style_result['report']['curve_changes']], [DYN, PIT])
                native, style, manual = (Layout(doc).sequence['tracks'][1]['parts'][0]
                                         for doc in (snap.data, styled, output))
                self.assertEqual(_curve(native, PBS)[1], _curve(manual, PBS)[1])
                self.assertEqual(_curve(style, DYN)[1], _curve(manual, DYN)[1])
                for name in (PIT, DYN):
                    guards = np.r_[0:121, 899:1320, 1920:2840]
                    np.testing.assert_array_equal(samples(manual, name, guards), samples(native, name, guards))
                x, y = zip(*span.points)
                inside = np.arange(x[0], x[-1] + 1)
                outside = np.r_[0:span.left+1, span.right:2840]
                quantum = samples(native, PBS, inside) * 100 / 8192
                offset = (samples(manual, PIT, inside) - samples(native, PIT, inside)) * quantum
                self.assertTrue(np.all(np.abs(offset - np.interp(inside, x, y)) <= quantum / 2 + 1e-8))
                np.testing.assert_array_equal(samples(style, PIT, outside), samples(manual, PIT, outside))
                self.assertEqual(file_hash(source), snap.sha256)

    def test_no_edit_copies_bytes_and_tuning_requires_a_verified_task_export(self):
        job = self.root / 'task'
        (job / 'exports').mkdir(parents=True)
        atomic_json(job / 'request.json', {})
        source = write_fixture(job / 'exports/voices.vsqx', 4)
        self.assertEqual(workflow.job_projects(job), [])
        atomic_json(job / 'exports/receipts.json', {'exports/voices.vsqx': {'sha256': file_hash(source)}})
        self.assertTrue(workflow.job_projects(job)[0]['verified'])
        session = workflow.create_session(source, self.root / 'no-edits')
        result = workflow.run_session(session, {})
        self.assertEqual(source.read_bytes(), Path(result['output']).read_bytes())
        source.write_bytes(b'<vsq4/>')
        self.assertEqual(workflow.job_projects(job), [])
        with self.assertRaisesRegex(ValueError, 'VSQX3 或 VSQX4'):
            read(source.read_bytes())


if __name__ == '__main__':
    unittest.main()
