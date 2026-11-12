"""VPR round trips keep the original arrangement and embedded audio."""

from bisect import bisect_right
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import agent_style
from tuning_timeline import ProjectNotes, freeze_plan
import tuning_workflow as workflow
from vpr_tuning import DYN, PBS, PIT, SEQUENCE, Layout, process_project, read


AUDIO = 'Project/Audio/demo.wav'
EXTRA = 'Project/Additional/source.bin'


def note(pos, duration, pitch, lyric):
    return {'pos': pos, 'duration': duration, 'number': pitch, 'lyric': lyric, 'phoneme': 'a',
            'isProtected': True, 'exp': {'accent': 70}, 'vibrato': {'events': [{'pos': 48, 'value': 12}]}}


def project():
    return {
        'version': {'major': 6, 'minor': 5, 'revision': 1}, 'vender': 'Fixture',
        'masterTrack': {'tempo': {'global': {'isEnabled': False, 'value': 19000},
                                 'events': [{'pos': 0, 'value': 12000}, {'pos': 1200, 'value': 6000}]},
                        'timeSig': {'events': [{'bar': 0, 'numer': 4, 'denom': 4},
                                               {'bar': 1, 'numer': 3, 'denom': 4}]}},
        'voices': [{'compID': 'fixture-vocaloid', 'name': 'Fixture voice'}],
        'tracks': [
            {'type': 1, 'name': 'Audio', 'parts': [{'pos': 0, 'wav': {'name': 'demo.wav', 'originalName': 'inst.wav'}}]},
            {'type': 0, 'name': 'Lead', 'audioEffects': [{'id': 'existing'}], 'parts': [
                {'pos': 480, 'duration': 1920, 'voice': {'compID': 'fixture-vocaloid'},
                 'notes': [note(120, 480, 60, '青'), note(960, 480, 63, '空')],
                 'controllers': [
                     {'name': DYN, 'events': [{'pos': 0, 'value': 67}, {'pos': 240, 'value': 62},
                                              {'pos': 600, 'value': 74}], 'isFolded': True},
                     {'name': PIT, 'events': [{'pos': 80, 'value': 29}, {'pos': 430, 'value': -88},
                                              {'pos': 780, 'value': 36}]},
                     {'name': PBS, 'events': [{'pos': 0, 'value': 2}, {'pos': 360, 'value': 5}]},
                     {'name': 'brightness', 'events': [{'pos': 360, 'value': 74}]},
                 ]},
                {'pos': 3200, 'duration': 480, 'voice': {'compID': 'fixture-vocaloid'},
                 'notes': [note(0, 360, 65, '春')], 'controllers': None},
            ]},
            {'type': 2, 'name': 'AI', 'parts': [
                {'pos': 480, 'duration': 1920, 'aiVoice': {'compID': 'fixture-ai', 'langIDs': [0]},
                 'notes': [{**note(120, 480, 67, '青'), 'aiExp': {'pitchFine': 13},
                            'isAiVibratoEnabled': True,
                            'directPitches': [{'pos': 0, 'value': 'ZeroPitch'}, {'pos': 190, 'value': 35}]}]},
            ]},
        ],
        'unknownFutureFields': {'comment': '保留'},
    }


def write_source(path, data):
    with io.BytesIO() as sound:
        with wave.open(sound, 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0\0' * 800)
        audio = sound.getvalue()
    with ZipFile(path, 'w', compression=ZIP_DEFLATED) as z:
        z.comment = b'fixture archive metadata'
        z.writestr(SEQUENCE, json.dumps(data, ensure_ascii=False).encode('utf-8-sig'))
        info = ZipInfo(AUDIO, date_time=(2024, 6, 12, 10, 0, 0))
        info.compress_type = ZIP_STORED
        info.comment = b'embedded audio unchanged'
        z.writestr(info, audio)
        z.writestr(EXTRA, bytes(range(256)) * 12)
    return path


def curve(part, channel):
    return next((c for c in part.get('controllers') or [] if c['name'] == channel), {'events': []})


def sampled(part, channel, tick):
    events = curve(part, channel)['events']
    index = bisect_right([e['pos'] for e in events], tick)
    return events[index - 1]['value'] if index else {DYN: 64, PIT: 0, PBS: 2}[channel]


class VprTuningTests(unittest.TestCase):
    def test_session_keeps_audio_and_non_selected_samples(self):
        data = project()
        with tempfile.TemporaryDirectory(prefix='vpr-qa-', dir=APP) as root:
            root = Path(root)
            source = write_source(root / 'original.vpr', data)
            source_sha = sha256(source.read_bytes()).hexdigest()
            # Tuning reads the score member and hashes/copies the archive by
            # stream; _raw is only for the text formats.
            with patch.object(workflow, '_raw', side_effect=AssertionError('VPR archive loaded into memory')):
                timeline = ProjectNotes.load(source)
                lead, ai = timeline.editable
                self.assertEqual([r['index'] for r in timeline.tracks.values()], [1, 2])
                first, second, _ = timeline.visible([lead], 0, 50)
                self.assertEqual((first['start'], first['end'], first['pitch']), (.625, 1.125, 60))
                self.assertEqual(second['start'], 1.75)
                self.assertEqual({m['trackId'] for m in timeline.matches('青')}, {lead, ai})
                region = timeline.region(lead, .68, .98, {'preset': 'gentle', 'pitch': True})
                opts = {'track_indices': [1], 'style_regions': [region['recipe']]}
                _, plan = freeze_plan(timeline, [region], opts, None)
                session = workflow.create_session(source, root / 'session')
                self.assertEqual(workflow.read_session(session)['input_file'], 'inputs/source.vpr')
                result = workflow.run_session(session, json.loads(plan)['options'])
                self.assertTrue(result['receipt_verified'])
                self.assertEqual(workflow.verified_result(session)['output_sha256'], result['output_sha256'])
                self.assertTrue(workflow.run_session(session, opts)['cached'])
                with self.assertRaisesRegex(ValueError, '格式相同'):
                    workflow.export_verified_copy(result, root / 'wrong.ustx')
                saved = Path(workflow.export_verified_copy(result, root / 'candidate.vpr'))
                self.assertEqual(len(ProjectNotes.load(saved).notes), len(timeline.notes))
            self.assertEqual(sha256(source.read_bytes()).hexdigest(), source_sha)
            self.assertEqual(sha256(saved.read_bytes()).hexdigest(), result['output_sha256'])
            from task_contract import bind, selected
            job = root / 'task'
            job.mkdir()
            (job / 'request.json').write_text('{}', encoding='utf-8')
            bind(job, 'project', saved, expected_sha256=result['output_sha256'])
            self.assertEqual(selected(job, 'project'), saved)
            self.assertEqual(workflow.job_projects(job)[0]['path'], str(saved))
            self.assertEqual(result['report']['archive']['embedded_audio_members'], 1)
            self.assertTrue(result['report']['archive']['non_sequence_members_unchanged'])
            with ZipFile(source) as a, ZipFile(saved) as b:
                self.assertEqual(a.namelist(), b.namelist())
                self.assertEqual(a.comment, b.comment)
                for name in (AUDIO, EXTRA):
                    self.assertEqual(sha256(a.read(name)).digest(), sha256(b.read(name)).digest())
                    self.assertEqual(a.getinfo(name).comment, b.getinfo(name).comment)
                self.assertTrue(b.read(SEQUENCE).startswith(b'\xef\xbb\xbf'))
            written = read(saved)
            old, new = data['tracks'][1]['parts'][0], written['tracks'][1]['parts'][0]
            self.assertEqual([c['parameter'] for c in result['report']['curve_changes']], [DYN, PIT])
            for channel in (DYN, PIT):
                self.assertTrue(any(sampled(old, channel, x) != sampled(new, channel, x) for x in range(120, 600)))
                outside = [*range(-20, 120), *range(600, 1441)]
                self.assertEqual([sampled(old, channel, x) for x in outside], [sampled(new, channel, x) for x in outside])
                curve(new, channel)['events'] = curve(old, channel)['events']
            self.assertEqual(written, data)

    def test_pitch_uses_existing_sensitivity_and_preserves_ai_direct_pitch(self):
        data = project()
        copy_data = deepcopy(data)
        p2 = copy_data['tracks'][1]['parts'][0]
        curve(p2, PBS)['events'] = [{'pos': 0, 'value': 11}]
        opts = {'track_indices': [1], 'performance_style': 'gentle', 'style_pitch': True}
        a, _ = process_project(data, opts)
        b, _ = process_project(copy_data, opts)
        p1 = data['tracks'][1]['parts'][0]
        # Different PBS values encode the same added cents to within a pitch
        # bend unit, without changing either project’s sensitivity events.
        for x in range(120, 600):
            ac = (sampled(a['tracks'][1]['parts'][0], PIT, x) - sampled(p1, PIT, x)) * sampled(p1, PBS, x)
            bc = (sampled(b['tracks'][1]['parts'][0], PIT, x) - sampled(p2, PIT, x)) * sampled(p2, PBS, x)
            self.assertLessEqual(abs(ac-bc), 8)
        for before, after in ((data, a), (copy_data, b)):
            self.assertEqual(curve(before['tracks'][1]['parts'][0], PBS), curve(after['tracks'][1]['parts'][0], PBS))
        ai, receipt = process_project(data, {**opts, 'track_indices': [2]})
        self.assertEqual([r['parameter'] for r in receipt['curve_changes']], [DYN])
        self.assertIn('vpr_direct_pitch_preserved', json.dumps(receipt))
        self.assertEqual(ai['tracks'][2]['parts'][0]['notes'], data['tracks'][2]['parts'][0]['notes'])

    def test_overlap_protects_notes_locally_and_agent_targets_one_part(self):
        data = project()
        old = data['tracks'][1]['parts'][0]
        old['notes'].append(note(1100, 480, 59, '重'))
        layout = Layout(data)
        self.assertTrue(layout.tracks[1].writable)
        self.assertEqual(len(layout.tracks[1].placements[0].overlap_spans), 2)
        output, report = process_project(data, {'track_indices': [1], 'performance_style': 'gentle', 'style_pitch': True})
        self.assertTrue(report['curve_changes'])
        for name in (DYN, PIT):
            new = output['tracks'][1]['parts'][0]
            self.assertEqual([sampled(new, name, x) for x in range(960, 1580)],
                             [sampled(old, name, x) for x in range(960, 1580)])
        with tempfile.TemporaryDirectory(prefix='vpr-agent-', dir=APP) as root:
            source = write_source(Path(root) / 'original.vpr', data)
            context = agent_style.read_context(source)
            chosen = next(n for n in context['notes'] if n['lyric'] == '春')
            plan = agent_style.plan_style(source, {'track_indices': [1], 'brief': 'single part only',
                'regions': [{'start_note_id': chosen['note_id'], 'end_note_id': chosen['note_id'],
                             'preset': 'gentle', 'strength': .6, 'style_pitch': False,
                             'rationale': 'fixture', 'evidence_refs': []}]})
            result = agent_style.render_style(source, plan, Path(root) / 'output')
            self.assertTrue(result['receipt_verified'])
            self.assertEqual(result['report']['curve_changes'],
                             [{'track_index': 1, 'group_placement': 'part:1', 'parameter': DYN}])
            self.assertTrue(result['media_relocation']['archive']['non_sequence_members_unchanged'])
            after = read(Path(result['candidate_path']))
            self.assertEqual(after['tracks'][1]['parts'][0], old)

    def test_archive_and_format_validation(self):
        data = project()
        data['masterTrack']['tempo']['global']['isEnabled'] = True
        self.assertEqual(Layout(data).score['time']['tempo'], [{'position': 0, 'bpm': 190.0}])
        data['version']['major'] = 7
        self.assertFalse(Layout(data).tracks[1].writable)
        with self.assertRaisesRegex(ValueError, '尚未验证'):
            process_project(data, {'track_indices': [1], 'performance_style': 'gentle'})
        with self.assertRaisesRegex(ValueError, 'VPR'):
            workflow.project_format('project.vprx')
        for name in ('../outside.txt', 'project/SEQUENCE.json'):
            with io.BytesIO() as buffer:
                with ZipFile(buffer, 'w') as z:
                    z.writestr(SEQUENCE, json.dumps(project()))
                    z.writestr(name, b'fixture')
                with self.assertRaisesRegex(ValueError, '归档'):
                    read(buffer.getvalue())


if __name__ == '__main__':
    unittest.main()
