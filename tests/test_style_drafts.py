"""Contract tests for the cross-process shared style draft store."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src'
sys.path.insert(0, str(SRC))

import style_drafts
from common import native_path
from style_drafts import DraftConflict, DraftError, DraftStore, normalize_draft


SOURCE_SHA = '1' * 64
OTHER_SHA = '2' * 64


def base_draft(sha=SOURCE_SHA):
    return {
        'schema_version': 1,
        'input_sha256': sha,
        'style_regions': [{
            'start': .05, 'end': .55, 'preset': 'sad', 'strength': .625,
            'detail': {'onset': .2, 'sustain': .35, 'connection': .45, 'release': .55},
            'pitch': False,
        }],
        'mode': 'regions',
        'selection': [.05, .55],
        'selected_index': 0,
        'track_indices': [0, 2],
        'tool': {
            'performance_style': 'sad', 'style_strength': .625, 'style_pitch': False,
            'style_detail': {'onset': .2, 'sustain': .35, 'connection': .45, 'release': .55},
            'style_technique': 'chinese_opera', 'technique_strength': .7,
            'technique_pitch': True,
        },
        'editor_intent': 'edit',
        'next_tool': {
            'performance_style': 'gentle', 'style_strength': .4, 'style_pitch': True,
            'style_detail': {'onset': 1, 'sustain': 1, 'connection': 1, 'release': 1},
            'style_technique': 'none', 'technique_strength': .6,
            'technique_pitch': True,
        },
    }


def write_raw(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def remove_long_tree(path):
    path = Path(path)
    try:
        shutil.rmtree(native_path(path))
    except FileNotFoundError:
        pass


def draft_worker(module_root, store_root, sha, revision, marker, ready, gate, results):
    sys.path.insert(0, str(module_root))
    from style_drafts import DraftStore
    data = {
        'schema_version': 1,
        'input_sha256': sha,
        'style_regions': [],
        'mode': 'whole',
        'selection': None,
        'selected_index': -1,
        'track_indices': [marker],
        'tool': {'performance_style': 'none', 'style_strength': .6,
                 'style_pitch': False,
                 'style_detail': {'onset': 1, 'sustain': 1,
                                  'connection': 1, 'release': 1}},
        'editor_intent': 'new',
    }
    ready.put(marker)
    gate.get(timeout=10)
    try:
        result = DraftStore(store_root, lock_timeout=2).write(
            sha, revision, data, writer='process-' + str(marker))
        outcome = {'ok': True, 'writer': result['data']['_sync']['writer'],
                   'track_indices': result['data']['track_indices']}
    except Exception as error:
        outcome = {'ok': False, 'type': type(error).__name__,
                   'code': getattr(error, 'code', None),
                   'details': getattr(error, 'details', {})}
    results.put(outcome)


class DraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP / 'work', prefix='style-drafts-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'shared drafts'
        self.store = DraftStore(self.root)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(DraftError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_error_constructor_and_read_are_stable(self):
        error = DraftError('custom', 'message')
        self.assertEqual((error.code, str(error), error.details), ('custom', 'message', {}))
        self.assertEqual(error.message, 'message')
        details = {'key': 'value'}
        self.assertEqual(DraftError('x', 'y', details).details, details)
        with self.assertRaises(TypeError):
            DraftError('x', 'y', ['not', 'a', 'dict'])

        self.assertFalse(self.root.exists())
        snapshot = self.store.read(SOURCE_SHA)
        self.assertEqual(snapshot, {
            'exists': False, 'input_sha256': SOURCE_SHA, 'revision': None,
            'data': None, 'path': str(self.root / (SOURCE_SHA + '.json')),
            'legacy': False,
        })
        self.assertFalse(self.root.exists())

        self.root.mkdir()
        empty = self.root / (SOURCE_SHA + '.json')
        empty.write_bytes(b'')
        self.assertFalse(self.store.read(SOURCE_SHA)['exists'])
        self.assertEqual(empty.read_bytes(), b'')

    def test_first_write_and_cas_revision_bind_actual_bytes(self):
        incoming = base_draft()
        incoming['_sync'] = {'seq': 999999999, 'writer': 'forged'}
        first = self.store.write(SOURCE_SHA, None, incoming, writer='gui')
        self.assertTrue(first['exists'] and not first['legacy'])
        self.assertEqual(first['revision'], hashlib.sha256(Path(first['path']).read_bytes()).hexdigest())
        self.assertEqual(first['data']['_sync'], {'seq': 1, 'writer': 'gui'})
        self.assertEqual(first['data']['style_regions'], base_draft()['style_regions'])

        reused = self.store.write(SOURCE_SHA, first['revision'], first['data'], writer='ignored')
        self.assertEqual(reused['revision'], first['revision'])
        self.assertEqual(reused['data']['_sync'], {'seq': 1, 'writer': 'gui'})

        changed = copy.deepcopy(first['data'])
        changed['track_indices'] = [1]
        second = self.store.write(SOURCE_SHA, first['revision'], changed, writer='agent')
        self.assertNotEqual(second['revision'], first['revision'])
        self.assertEqual(second['data']['_sync'], {'seq': 2, 'writer': 'agent'})
        self.assertEqual(second['data']['next_tool'], first['data']['next_tool'])
        self.assertEqual(second['revision'], hashlib.sha256(Path(second['path']).read_bytes()).hexdigest())

    def test_legacy_read_and_manual_edits_change_revision(self):
        legacy = base_draft()
        legacy.pop('editor_intent')
        legacy.pop('next_tool')
        write_raw(self.root / (SOURCE_SHA + '.json'), legacy)
        snapshot = self.store.read(SOURCE_SHA)
        self.assertTrue(snapshot['legacy'])
        self.assertEqual(snapshot['data'], legacy)
        self.assertNotIn('_sync', snapshot['data'])

        path = Path(snapshot['path'])
        edited = json.loads(path.read_text(encoding='utf-8'))
        edited['track_indices'] = [3]
        write_raw(path, edited)
        manual = self.store.read(SOURCE_SHA)
        self.assertNotEqual(manual['revision'], snapshot['revision'])
        updated = self.store.write(SOURCE_SHA, manual['revision'], manual['data'], writer='gui')
        self.assertEqual(updated['data']['_sync'], {'seq': 1, 'writer': 'gui'})
        self.assertNotEqual(updated['revision'], manual['revision'])
        self.assertFalse(updated['legacy'])

        supplied = copy.deepcopy(updated['data'])
        supplied['_sync'] = {'seq': 1, 'writer': 'hijack'}
        supplied['selection'] = [.2, .4]
        next_snapshot = self.store.write(SOURCE_SHA, updated['revision'], supplied, writer='agent')
        self.assertEqual(next_snapshot['data']['_sync'], {'seq': 2, 'writer': 'agent'})

    def test_conflict_keeps_primary_and_writes_both_sides_recovery(self):
        first = self.store.write(SOURCE_SHA, None, base_draft(), writer='gui')
        external = copy.deepcopy(first['data'])
        external['_sync']['seq'] = 987654
        external['_sync']['writer'] = 'forged'
        external['style_regions'] = []
        external['selected_index'] = -1
        external['track_indices'] = [4]
        external_clean = {key: value for key, value in external.items() if key != '_sync'}
        external_snapshot = self.store.write(SOURCE_SHA, first['revision'], external_clean, writer='agent')
        primary_before = Path(external_snapshot['path']).read_bytes()

        local = copy.deepcopy(first['data'])
        local['track_indices'] = [1, 3]
        with self.assertRaises(DraftConflict) as caught:
            self.store.write(SOURCE_SHA, first['revision'], local, writer='gui')
        error = caught.exception
        self.assertEqual(error.code, 'draft_conflict')
        self.assertEqual(error.details['expected_revision'], first['revision'])
        self.assertEqual(error.details['current_revision'], external_snapshot['revision'])
        self.assertEqual(Path(external_snapshot['path']).read_bytes(), primary_before)
        recovery_path = Path(error.details['recovery_path'])
        self.assertTrue(recovery_path.is_file())
        self.assertEqual(recovery_path.parent, self.root)
        recovery = json.loads(recovery_path.read_text(encoding='utf-8'))
        self.assertEqual(recovery['kind'], 'style_draft_conflict')
        self.assertEqual(recovery['expected_revision'], first['revision'])
        self.assertEqual(recovery['current_revision'], external_snapshot['revision'])
        self.assertEqual(recovery['current_data'], external_snapshot['data'])
        self.assertEqual(recovery['incoming_data']['track_indices'], [1, 3])

    def test_invalid_values_regions_and_booleans_are_rejected(self):
        invalid_values = (
            {'style_regions': [{'start': 0, 'end': float('nan'), 'preset': 'sad'}]},
            {'style_regions': [{'start': 0, 'end': float('inf'), 'preset': 'sad'}]},
            {'track_indices': [True]},
            {'track_indices': [-1]},
            {'track_indices': [0, 0]},
            {'style_regions': [{'start': 0, 'end': .1, 'preset': 'sad'}] * 129},
            {'unexpected': True},
            {'mode': 'unknown'},
            {'selected_index': True},
            {'selection': [0]},
            {'tool': {'performance_style': 'sad', 'style_strength': True}},
            {'style_regions': [{'start': 0, 'end': .1, 'preset': 'sad',
                                'style_pitch': True}]},
        )
        for update in invalid_values:
            value = base_draft()
            value.update(update)
            with self.subTest(update=update):
                self.assert_error('invalid_draft', normalize_draft, SOURCE_SHA, value)

        valid_strength = base_draft()
        valid_strength['style_regions'] = [{'start': 0, 'end': .1,
                                            'preset': 'sad', 'strength': .3}]
        normalized = normalize_draft(SOURCE_SHA, valid_strength)
        self.assertEqual(normalized['style_regions'][0]['strength'], .3)

    def test_json_rejects_duplicates_constants_bom_only_array_and_oversize(self):
        path = self.root / (SOURCE_SHA + '.json')
        self.root.mkdir()
        invalid = (
            (b'{"schema_version":1,"schema_version":1}', 'duplicate'),
            (b'{"schema_version":1,"x":NaN}', 'non-finite'),
            (b'\xef\xbb\xbf', 'expecting value'),
            (b'[]', 'json object'),
        )
        for raw, reason in invalid:
            path.write_bytes(raw)
            with self.subTest(raw=raw):
                error = self.assert_error('draft_corrupt', self.store.read, SOURCE_SHA)
                self.assertIn(reason.casefold(), (error.details.get('reason') or '').casefold())
        path.write_bytes(b' ' * (style_drafts.MAX_DRAFT_BYTES + 1))
        self.assert_error('draft_too_large', self.store.read, SOURCE_SHA)
        self.store.read(OTHER_SHA)
        self.assertFalse((self.root / (OTHER_SHA + '.json')).exists())

    def test_sync_shape_and_writer_are_validated(self):
        path = self.root / (SOURCE_SHA + '.json')
        self.root.mkdir()
        for sync in ({'seq': 0, 'writer': 'x'}, {'seq': True, 'writer': 'x'},
                     {'seq': 1, 'writer': ''}, {'seq': 1, 'writer': 'x', 'forged': 1},
                     {'seq': 1}, {}):
            value = base_draft()
            value['_sync'] = sync
            write_raw(path, value)
            with self.subTest(sync=sync):
                self.assert_error('draft_corrupt', self.store.read, SOURCE_SHA)
        self.assert_error('invalid_writer', self.store.write,
                          SOURCE_SHA, None, base_draft(), writer='')

    def test_root_must_be_absolute_plain_and_not_a_reparse_ancestor(self):
        for invalid in ('', 'relative', Path('relative')):
            with self.subTest(invalid=invalid):
                self.assert_error('invalid_root', DraftStore, invalid)

        self.root.mkdir()
        original_lstat = style_drafts._lstat
        attributes = getattr(original_lstat(self.root), 'st_file_attributes', 0) | 0x400

        def fake_lstat(path):
            if Path(path).resolve() == self.root.resolve():
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=0o40777, st_file_attributes=attributes)
            return original_lstat(path)

        with patch.object(style_drafts, '_lstat', fake_lstat):
            error = self.assert_error('unsafe_path', self.store.read, SOURCE_SHA)
            self.assertEqual(Path(error.details['path']).resolve(), self.root.resolve())

        child = self.root / 'child'
        child.mkdir()
        link = self.root / 'link'
        try:
            os.symlink(child, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaises(DraftError) as caught:
                DraftStore(link)
            self.assertEqual(caught.exception.code, 'unsafe_path')


    def test_post_check_reparse_is_rejected_before_primary_io(self):
        self.root.mkdir()
        original_lstat = style_drafts._lstat
        root_checks = []

        def replaced_lstat(path):
            if Path(path) == self.root:
                root_checks.append(path)
                if len(root_checks) >= 3:
                    from types import SimpleNamespace
                    real = original_lstat(path)
                    return SimpleNamespace(st_mode=real.st_mode,
                                           st_file_attributes=getattr(real, 'st_file_attributes', 0) | 0x400)
            return original_lstat(path)

        with patch.object(style_drafts, '_lstat', replaced_lstat):
            self.assert_error('unsafe_path', self.store.write,
                              SOURCE_SHA, None, base_draft(), writer='post-check')
        self.assertGreaterEqual(len(root_checks), 3)
        self.assertFalse((self.root / (SOURCE_SHA + '.json')).exists())

    def test_target_swap_after_lstat_is_not_followed(self):
        first = self.store.write(SOURCE_SHA, None, base_draft(), writer='gui')
        target = Path(first['path'])
        original_open = style_drafts._open_read_stream
        marker = target.parent / 'outside-marker'

        def swap_before_open(path):
            target.unlink()
            target.mkdir()
            (target / 'sentinel').write_text('inside replaced target', encoding='utf-8')
            marker.write_text('outside untouched', encoding='utf-8')
            return original_open(path)

        with patch.object(style_drafts, '_open_read_stream', swap_before_open):
            self.assert_error('unsafe_path', self.store.read, SOURCE_SHA)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'outside untouched')
        self.assertTrue((target / 'sentinel').is_file())

    def test_failed_replace_preserves_primary_and_cleans_temporary_file(self):
        first = self.store.write(SOURCE_SHA, None, base_draft(), writer='gui')
        primary = Path(first['path'])
        before = primary.read_bytes()
        changed = copy.deepcopy(first['data'])
        changed['selection'] = None
        changed['selected_index'] = -1
        with patch.object(style_drafts, '_replace_file', side_effect=OSError(13, 'denied')):
            error = self.assert_error('draft_io', self.store.write,
                                      SOURCE_SHA, first['revision'], changed, writer='agent')
        self.assertIn('reason', error.details)
        self.assertEqual(primary.read_bytes(), before)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.endswith('.tmp')], [])
        after = self.store.read(SOURCE_SHA)
        self.assertEqual(after['revision'], first['revision'])
        retry = self.store.write(SOURCE_SHA, first['revision'], changed, writer='agent')
        self.assertEqual(retry['data']['_sync'], {'seq': 2, 'writer': 'agent'})

    def test_cross_process_writers_with_same_revision_yield_one_winner(self):
        first = self.store.write(SOURCE_SHA, None, base_draft(), writer='seed')
        ctx = __import__('multiprocessing').get_context('spawn')
        ready, gate, results = ctx.Queue(), ctx.Queue(), ctx.Queue()
        processes = []
        try:
            for marker in (1, 2):
                process = ctx.Process(target=draft_worker, args=(
                    str(SRC), str(self.root), SOURCE_SHA, first['revision'],
                    marker, ready, gate, results))
                process.start()
                processes.append(process)
            self.assertEqual({ready.get(timeout=15), ready.get(timeout=15)}, {1, 2})
            gate.put(True)
            gate.put(True)
            outcomes = [results.get(timeout=15), results.get(timeout=15)]
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                process.join(5)

        winners = [value for value in outcomes if value['ok']]
        losers = [value for value in outcomes if not value['ok']]
        self.assertEqual(len(winners), 1, outcomes)
        self.assertEqual(len(losers), 1, outcomes)
        self.assertEqual(losers[0]['code'], 'draft_conflict')
        self.assertTrue(Path(losers[0]['details']['recovery_path']).is_file())
        current = self.store.read(SOURCE_SHA)
        self.assertEqual(current['data']['track_indices'], winners[0]['track_indices'])
        self.assertEqual(current['data']['_sync']['writer'], winners[0]['writer'])
        self.assertEqual(current['data']['_sync']['seq'], 2)

    def test_bounded_lock_timeout_releases_after_error(self):
        first = self.store.write(SOURCE_SHA, None, base_draft(), writer='gui')
        lock_path = self.store._lock_path(SOURCE_SHA)
        held = lock_path.open('a+b')
        held.seek(0, os.SEEK_END)
        if held.tell() == 0:
            held.write(b'\0')
            held.flush()
        try:
            import msvcrt
            held.seek(0)
            msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
        except (ImportError, OSError):
            held.close()
            self.skipTest('The platform does not expose msvcrt byte locks')
        try:
            busy = DraftStore(self.root, lock_timeout=.05)
            changed = copy.deepcopy(first['data'])
            changed['track_indices'] = [9]
            self.assert_error('draft_busy', busy.write,
                              SOURCE_SHA, first['revision'], changed, writer='agent')
        finally:
            held.seek(0)
            msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
            held.close()
        changed = copy.deepcopy(first['data'])
        changed['track_indices'] = [9]
        saved = self.store.write(SOURCE_SHA, first['revision'], changed, writer='agent')
        self.assertEqual(saved['data']['track_indices'], [9])

    def test_windows_long_path_round_trip(self):
        if os.name != 'nt':
            self.skipTest('Windows extended paths only')
        long_base = Path(self.temp.name) / ('draft-' + 'x' * 24)
        long_root = long_base
        for _ in range(7):
            long_root = long_root / ('draft-' + 'x' * 24)
        store = DraftStore(long_root)
        try:
            saved = store.write(SOURCE_SHA, None, base_draft(), writer='long-path')
            loaded = store.read(SOURCE_SHA)
            self.assertEqual(loaded['revision'], saved['revision'])
            self.assertEqual(loaded['data']['track_indices'], [0, 2])
            self.assertGreater(len(str(loaded['path'])), 260)
        finally:
            remove_long_tree(long_base)

    def test_normalization_defaults_old_payload_and_reselects_sorted_region(self):
        data = {
            'schema_version': 1,
            'input_sha256': SOURCE_SHA,
            'style_regions': [
                {'start': .8, 'end': 1, 'preset': 'gentle'},
                {'start': .1, 'end': .5, 'preset': 'none', 'technique': 'chinese_opera'},
            ],
            'mode': 'regions',
            'selected_index': 0,
        }
        normalized = normalize_draft(SOURCE_SHA, data)
        self.assertEqual([row['start'] for row in normalized['style_regions']], [.1, .8])
        self.assertEqual(normalized['selected_index'], 1)
        self.assertEqual(normalized['editor_intent'], 'new')
        self.assertEqual(normalized['track_indices'], [])
        self.assertIsNone(normalized['selection'])
        self.assertEqual(normalized['tool']['style_technique'], 'none')
        self.assertEqual(normalized['next_tool'], normalized['tool'])
        self.assertNotIn('_sync', normalized)

    def test_input_sha_cannot_escape_or_mismatch(self):
        for invalid in ('', '../' + 'a' * 61, 'A' * 64, 'a' * 63, 'a' * 65, True):
            with self.subTest(invalid=invalid):
                self.assert_error('invalid_input_sha256', self.store.read, invalid)
        mismatch = base_draft(OTHER_SHA)
        self.assert_error('invalid_draft', self.store.write,
                          SOURCE_SHA, None, mismatch)


if __name__ == '__main__':
    unittest.main(verbosity=2)
