"""Receipt identity, dependency rechecks and handoff choices for the studio."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
from common import atomic_json, file_hash
from quick_media_handoff import studio_targets, verify_publication_bundle, verify_task_artifact
import task_contract
import task_store


class MediaHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name).resolve()
        atomic_json(self.job / 'request.json', {'source_kind': 'local'})
        known = patch.object(task_store, 'job_directories', return_value=(self.job,))
        known.start()
        self.addCleanup(known.stop)
        self.source = self.asset('video/source.mp4', b'original clip')
        self.stem = self.asset('audio/vocals.wav', b'separated stem')
        self.mix = self.asset('mix.wav', b'registered mix')
        self.video = self.asset('candidate.mp4', b'registered video')
        task_contract.register(self.job, [{'path': self.source, 'role': 'source_video'}], 'download', parents=())
        task_contract.register(self.job, [{'path': self.stem, 'role': 'audio'}], 'separation', parents=())
        self.source_row = self.row('source_video')
        self.stem_row = self.row('audio')
        task_contract.bind(self.job, 'mixdown', self.mix, validate=False,
                           dependencies=[self.dependency(self.stem_row)])
        self.mix_row = self.row('mixdown')
        task_contract.bind(self.job, 'video', self.video, validate=False,
                           dependencies=[self.dependency(self.mix_row), self.dependency(self.source_row)])
        self.video_row = self.row('video')

    def asset(self, name, payload):
        path = self.job / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def row(self, role):
        return next(row for row in task_contract.inspect(self.job) if row['role'] == role)

    @staticmethod
    def dependency(row):
        return {key: row[key] for key in ('id', 'revision')}

    def bundle(self):
        cover = self.asset('bundle/cover.jpg', b'local cover')
        manifest = cover.parent / 'manifest.json'
        atomic_json(manifest, {'source_job': str(self.job),
                               'video': {'path': str(self.video), 'sha256': file_hash(self.video)}})
        atomic_json(cover.parent / 'cleanup-inventory.json', {
            'entries': [{'file': cover.name, 'sha256': file_hash(cover)}]})
        return cover.parent

    def test_only_target_dependencies_are_hashed_and_stale_inputs_block_handoff(self):
        unrelated = self.asset('notes.mid', b'not involved')
        task_contract.register(self.job, [{'path': unrelated, 'role': 'midi'}], 'midi', parents=())
        with patch.object(task_contract, 'file_hash', wraps=file_hash) as hashed:
            self.assertEqual(verify_task_artifact(self.job, self.video_row, 'video'), self.video_row)
        self.assertEqual({Path(call.args[0]) for call in hashed.call_args_list},
                         {self.source, self.stem, self.mix, self.video})
        self.stem.write_bytes(b'new stem with different bytes')
        with self.assertRaisesRegex(ValueError, '上游修订'):
            verify_task_artifact(self.job, self.video_row, 'video')
        self.assertEqual(studio_targets(self.job, task_contract.inspect(self.job)), {})

    def test_publication_preserves_pinned_video_dependencies(self):
        bundle = self.bundle()
        self.assertEqual(studio_targets(self.job, task_contract.inspect(self.job)), {
            'video_mixdown': [self.mix_row['id'], self.source_row['id']],
            'publishing': [self.video_row['id']],
        })
        task_contract.register_publication(self.job, bundle, expected_video=self.video_row)
        self.assertEqual(self.row('video'), self.video_row)
        publication = self.row('publication')
        self.assertIn(self.dependency(self.video_row), publication['dependencies'])
        self.assertEqual(verify_publication_bundle(self.job, bundle), publication)
        self.assertEqual(studio_targets(self.job, task_contract.inspect(self.job))['publishing'],
                         [self.video_row['id'], publication['id']])

    def test_superseded_video_rejects_bundle_before_task_mutation(self):
        bundle = self.bundle()
        replacement = self.asset('replaced.mp4', b'another registered clip')
        task_contract.bind(self.job, 'video', replacement, validate=False)
        before = [(self.job / name).read_bytes() for name in ('task.json', 'artifacts.json')]
        with self.assertRaisesRegex(ValueError, '已变化'):
            task_contract.register_publication(self.job, bundle, expected_video=self.video_row)
        self.assertEqual([(self.job / name).read_bytes() for name in ('task.json', 'artifacts.json')], before)

    def test_manual_source_is_verified_and_retained_with_the_video_binding(self):
        source = self.asset('locally-selected.mov', b'local clip')
        result = self.asset('local-composition.mp4', b'local composition')
        source_receipt = {'path': str(source), 'sha256': file_hash(source)}
        before = [(self.job / name).read_bytes() for name in ('task.json', 'artifacts.json')]
        with self.assertRaisesRegex(ValueError, '合成回执'):
            task_contract.bind(self.job, 'video', result, validate=False,
                               video_source={**source_receipt, 'sha256': 'a' * 64})
        self.assertEqual([(self.job / name).read_bytes() for name in ('task.json', 'artifacts.json')], before)
        identifier = task_contract.bind(self.job, 'video', result, validate=False,
                                        dependencies=[self.dependency(self.mix_row)], video_source=source_receipt)
        rows = task_contract.inspect(self.job, artifact_ids=(identifier,))
        selected = next(row for row in rows if row['id'] == identifier)
        self.assertIn(self.dependency(next(row for row in rows if Path(row['path']) == source)),
                      selected['dependencies'])
        source.write_bytes(b'new bytes!')
        with self.assertRaisesRegex(ValueError, '上游修订'):
            verify_task_artifact(self.job, selected, 'video')


if __name__ == '__main__':
    unittest.main(verbosity=2)
