from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

try:
    from PySide6.QtCore import QCoreApplication, QObject, QUrl, Signal
except ModuleNotFoundError as error:
    if error.name != 'PySide6':
        raise
    if __name__ != '__main__':
        raise unittest.SkipTest('No Qt 6 test runtime configured') from error

    class MissingQt6(unittest.TestCase):
        @unittest.skip('No Qt 6 test runtime configured')
        def test_quick_publication(self):
            pass

    unittest.main(exit=True)

import publication
import quick_publication
import task_contract
from quick_publication import QuickPublication
from quick_media_handoff import verify_task_artifact


APP = QCoreApplication.instance() or QCoreApplication([])


class FakeWorker(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._kind = ''
        self._stop = Event()

    @property
    def kind(self):
        return self._kind

    def start(self, kind, work, finish, progress=None):
        self._kind = kind
        self._stop = Event()
        value, error = None, None
        try:
            value = work(self._stop, progress or (lambda value: None))
        except Exception as exc:
            error = exc
        finally:
            self._kind = ''
        self.changed.emit()
        finish(value, error)
        self.changed.emit()
        return True

    def cancel(self):
        self._stop.set()


class Owner(QObject):
    def __init__(self, draft):
        super().__init__()
        self.quick_publication_draft_path = draft
        self.copied = []
        self.verified_roles = []

    def verify_task_artifact(self, job, row, role, check=None, *, platform=None):
        if check is not None and check():
            raise InterruptedError('cancelled')
        self.verified_roles.append(role)
        return verify_task_artifact(job, row, role, check=check, platform=platform)

    def verify_publication_bundle(self, job, bundle, check=None):
        if check is not None and check():
            raise InterruptedError('cancelled')
        self.verified_roles.append('publication')
        return artifact_row(Path(bundle) / 'manifest.json', 'publication',
                           identifier='5' * 32, revision='6' * 64)


    def copy_text(self, value):
        self.copied.append(value)
        return True


def artifact_row(path: Path, role: str, *, identifier='1' * 32, revision='2' * 64):
    return {
        'id': identifier,
        'revision': revision,
        'sha256': publication.file_hash(path),
        'path': str(path.resolve()),
        'bytes': path.stat().st_size,
        'role': role,
        'state': 'verified',
        'available': True,
    }


class QuickPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('VWA_QUICK_PUBLICATION_TEST_ROOT'))
        self.root = Path(self.temp.name)
        self.job = self.root / 'job'
        self.job.mkdir()
        (self.job / 'request.json').write_text(json.dumps({
            'source_kind': 'local', 'bv': None, 'url': None,
        }), encoding='utf-8')
        self.video = self.job / 'final.mp4'
        self.video.write_bytes(b'fake-mp4-registered-video')
        known = patch('task_store.job_directories', return_value=[self.job])
        known.start()
        self.addCleanup(known.stop)
        video_id = task_contract.bind(self.job, 'video', self.video, validate=False, dependencies=[])
        self.video_row = next(row for row in task_contract.inspect(self.job) if row['id'] == video_id)
        self.owner = Owner(self.root / 'quick-draft.json')
        self.worker = FakeWorker()
        self.controller = QuickPublication(self.owner, self.worker)

    def tearDown(self):
        self.controller.close()
        self.worker.close() if hasattr(self.worker, 'close') else None
        self.temp.cleanup()

    def configure_content(self, platform='bilibili'):
        self.assertTrue(self.controller.setField('platform', platform))
        self.assertTrue(self.controller.setField('title', '测试翻唱'))
        self.assertTrue(self.controller.setField('description', '本家\nSTAFF\n原创声明说明'))
        self.assertTrue(self.controller.setField('tags', '#翻唱 #测试'))
        self.assertTrue(self.controller.setField('ai', False))
        self.assertTrue(self.controller.setField('rights', '原创且已署名授权'))

    def test_view_exposes_the_agreed_field_map(self):
        expected = {'step', 'job', 'video', 'platform', 'title', 'description', 'tags', 'ai',
                    'rights', 'coverMode', 'coverSeconds', 'coverFile', 'coverPreview',
                    'busy', 'message', 'state', 'bundle', 'bundleStale', 'canPrepare',
                    'canCheck', 'verified', 'actions', 'taskChoices', 'taskMessage',
                    'taskScanBusy', 'sourceReady', 'sourceProblem', 'bundleOnly'}
        self.assertEqual(set(self.controller.view), expected)
        self.assertEqual(self.controller.view['actions'], {})


    def test_local_prepare_is_frozen_and_uses_verified_video_dependency(self):
        self.assertTrue(self.controller.accept_task(self.job, self.video_row))
        self.configure_content()
        self.assertTrue(self.controller.nextStep())
        self.assertTrue(self.controller.nextStep())
        view = self.controller.view
        self.assertEqual((view['step'], view['coverMode'], view['canPrepare']), (2, 'frame', True))

        bundle = self.root / 'bundle'
        bundle.mkdir()
        (bundle / 'cover.jpg').write_bytes(b'fake-jpeg')
        manifest = {
            'version': 2,
            'state': 'prepared',
            'source_job': str(self.job.resolve()),
            'source_bv': None,
            'video': {'path': str(self.video.resolve()), 'sha256': self.video_row['sha256'],
                      'bytes': self.video.stat().st_size, 'duration': 12.5, 'width': 1920, 'height': 1080},
            'cover': {'kind': 'frame', 'source': str(self.video.resolve()), 'seconds': 2.0,
                      'sha256': publication.file_hash(bundle / 'cover.jpg')},
            'platform_covers': {},
            'content': {'bilibili': {'title': '测试翻唱', 'description': '本家\nSTAFF\n原创声明说明',
                                      'tags': ['翻唱', '测试'], 'rights': '原创且已署名授权',
                                      'ai_generated': False}},
            'final_publish': 'human_only',
        }
        (bundle / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

        def make(*args, **kwargs):
            self.assertEqual(args[2], manifest['content'])
            self.assertEqual(kwargs['expected_video']['id'], self.video_row['id'])
            self.assertEqual(kwargs['cover_mode'], 'frame')
            self.assertTrue(callable(kwargs['check']))
            self.assertFalse(kwargs['register'])
            return bundle

        with patch.object(quick_publication.publication, 'prepare', side_effect=make) as prepare, \
                patch.object(quick_publication.publication, 'load_bundle', return_value=manifest):
            self.assertTrue(self.controller.prepareBundle())
        prepare.assert_called_once()
        self.assertIn('video', self.owner.verified_roles)
        self.assertEqual(task_contract.inspect(self.job, artifact_ids=(
            self.controller._state.bundle_ref.id,))[-1]['state'], 'verified')
        self.assertEqual(self.controller.view['bundle'], str(bundle.resolve()))
        self.assertTrue(self.controller.view['verified'])
        self.assertFalse(self.controller.view['bundleStale'])
        self.assertEqual(set(self.controller.view['actions']), {'video', 'cover', 'bundle', 'checklist'})

        self.assertTrue(self.controller.setField('title', '编辑后标题'))
        self.assertTrue(self.controller.view['bundleStale'])
        self.assertFalse(self.controller.view['verified'])
        self.assertEqual(self.controller.view['state'], 'outdated')

    def test_existing_xiaohongshu_bundle_restores_without_registration_and_hands_off(self):
        bundle = self.root / 'existing-bundle'
        bundle.mkdir()
        cover = bundle / 'cover.jpg'
        cover.write_bytes(b'fake-jpeg-cover')
        manifest = {
            'version': 2,
            'state': 'prepared',
            'source_job': str(self.job.resolve()),
            'source_bv': None,
            'video': {'path': str(self.video.resolve()), 'sha256': self.video_row['sha256'],
                      'bytes': self.video.stat().st_size, 'duration': 10.0},
            'cover': {'kind': 'frame', 'seconds': 1.0, 'sha256': publication.file_hash(cover)},
            'platform_covers': {},
            'content': {'xiaohongshu': {
                'title': '小红书标题', 'description': '正文与署名', 'tags': ['翻唱'],
                'rights': 'original', 'ai_generated': False}},
            'final_publish': 'human_only',
        }
        (bundle / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        bundle_row = task_contract.register_publication(self.job, bundle,
                                                        expected_video=self.video_row)['bundle']
        handoff = {
            'fields': manifest['content']['xiaohongshu'],
            'video': self.video.resolve(), 'cover': cover.resolve(), 'stamps': {},
        }
        with patch.object(quick_publication.publication, 'load_bundle', return_value=manifest), \
                patch.object(quick_publication.publication, 'prepare') as prepare:
            self.assertTrue(self.controller.accept_task(self.job, self.video_row, bundle_row))
            self.assertEqual(self.controller.view['platform'], 'xiaohongshu')
            self.assertEqual(self.controller.view['title'], '小红书标题')
            self.assertFalse(self.controller.view['bundleStale'])
            with patch.object(quick_publication.publication, 'xiaohongshu_handoff', return_value=handoff):
                self.assertTrue(self.controller.checkBundle())
        prepare.assert_not_called()
        self.assertTrue(self.controller.view['verified'])
        self.assertTrue(self.controller.copyField('cover'))
        self.assertEqual(self.owner.copied[-1], str(cover.resolve()))
        self.video.write_bytes(b'changed-after-check')
        self.controller._poll_drift()
        self.assertTrue(self.controller.view['bundleStale'])
        self.assertFalse(self.controller.view['verified'])
        self.assertIn('publication', self.owner.verified_roles)

    def test_platform_limits_and_explicit_declarations_gate_navigation(self):
        self.assertTrue(self.controller.accept_task(self.job, self.video_row))
        self.assertTrue(self.controller.setField('platform', 'xiaohongshu'))
        self.assertTrue(self.controller.setField('title', '长' * 21))
        self.assertTrue(self.controller.setField('description', '带署名的正文'))
        self.assertTrue(self.controller.setField('tags', '翻唱'))
        self.assertFalse(self.controller.nextStep())
        self.assertIn('20', self.controller.view['message'])
        self.assertTrue(self.controller.setField('title', '有效标题'))
        self.assertFalse(self.controller.nextStep())
        self.assertIn('AI', self.controller.view['message'])
        self.assertTrue(self.controller.setField('ai', False))
        self.assertFalse(self.controller.nextStep())
        self.assertIn('原创', self.controller.view['message'])
        self.assertTrue(self.controller.setField('rights', '原创并已核对署名授权'))
        self.assertTrue(self.controller.nextStep())
        self.assertEqual(self.controller.view['step'], 1)


    def test_task_fields_and_local_video_are_independent_choices(self):
        (self.job / 'source-info.json').write_text(json.dumps({
            'kind': 'local', 'title': '任务来源歌', 'owner': {},
        }, ensure_ascii=False), encoding='utf-8')
        (self.job / '发布简介草稿.txt').write_text('本家与 STAFF 草稿', encoding='utf-8')
        scan_row = {'jobId': str(self.job), 'title': '任务来源歌', 'searchText': '任务来源歌'}
        inspected = []
        with patch('quick_results.scan', return_value=[scan_row]), \
                patch('task_store.known_job', side_effect=lambda value: Path(value).resolve()), \
                patch.object(quick_publication.publication, 'inspect_video',
                             side_effect=lambda path: inspected.append(Path(path)) or {'duration': 10.0}):
            self.assertTrue(self.controller.refreshTaskChoices(''))
            self.assertEqual(self.controller.view['taskChoices'][0]['value'], str(self.job.resolve()))
            self.assertTrue(self.controller.chooseTask(str(self.job)))
            self.assertEqual(self.controller.view['job'], str(self.job.resolve()))
            self.assertEqual(self.controller.view['video'], '')
            self.assertEqual(self.controller.view['description'], '本家与 STAFF 草稿')
            self.assertTrue(self.controller.chooseVideo(QUrl.fromLocalFile(str(self.video))))
        self.assertEqual(inspected, [self.video.resolve()])
        self.assertEqual(self.controller.view['video'], str(self.video.resolve()))
        self.assertEqual(self.controller.view['bundle'], '')
        self.assertTrue(self.controller.view['sourceReady'])
        self.assertEqual(self.controller.view['taskChoices'][0]['title'], '任务来源歌')

    def test_separate_local_video_binds_only_during_explicit_prepare(self):
        (self.job / 'source-info.json').write_text(json.dumps({
            'kind': 'local', 'title': '显式绑定歌', 'owner': {},
        }, ensure_ascii=False), encoding='utf-8')
        (self.job / '发布简介草稿.txt').write_text('本家与 STAFF 草稿', encoding='utf-8')
        new_video = self.job / 'new.mp4'
        new_video.write_bytes(b'new chosen video')
        old_meta = (self.job / 'task.json').read_bytes()
        registered = {'sha256': publication.file_hash(new_video), 'bytes': new_video.stat().st_size}
        with patch.object(quick_publication.publication, 'inspect_video', return_value={'duration': 10.0}):
            self.assertTrue(self.controller.chooseTask(str(self.job)))
            self.assertTrue(self.controller.chooseVideo(QUrl.fromLocalFile(str(new_video))))
            self.assertEqual((self.job / 'task.json').read_bytes(), old_meta)
            self.configure_content()
            self.assertTrue(self.controller.nextStep())
            self.assertTrue(self.controller.nextStep())

            captured = {}
            def prepare(job, video, content, seconds, cover_file, **kwargs):
                captured.update(kwargs)
                bundle = self.root / 'generated-local-bundle'
                bundle.mkdir(exist_ok=True)
                cover = bundle / 'cover.jpg'
                cover.write_bytes(b'generated-cover')
                manifest = {
                    'version': 2, 'state': 'prepared', 'source_job': str(self.job.resolve()),
                    'source_bv': None,
                    'video': {'path': str(new_video.resolve()), 'sha256': registered['sha256'],
                              'bytes': registered['bytes'], 'duration': 10.0},
                    'cover': {'kind': 'frame', 'seconds': float(seconds),
                              'sha256': publication.file_hash(cover)},
                    'platform_covers': {}, 'content': content,
                    'final_publish': 'human_only',
                }
                (bundle / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
                captured['manifest'] = manifest
                return bundle

            with patch.object(quick_publication.publication, 'prepare', side_effect=prepare), \
                    patch.object(quick_publication.publication, 'load_bundle',
                                 side_effect=lambda *args, **kwargs: captured['manifest']):
                original = self.controller._handoffs_for_snapshot
                def cancel_before(*args, **kwargs):
                    result = original(*args, **kwargs)
                    self.worker.cancel()
                    return result
                with patch.object(self.controller, '_handoffs_for_snapshot', side_effect=cancel_before):
                    self.assertTrue(self.controller.prepareBundle())
                    self.assertEqual(self.controller.view['state'], 'cancelled')
                    self.assertEqual((self.job / 'task.json').read_bytes(), old_meta)
                original_commit = task_contract.register_publication
                def stop_after(*args, **kwargs):
                    value = original_commit(*args, **kwargs)
                    self.worker.cancel()
                    return value
                with patch.object(task_contract, 'register_publication', side_effect=stop_after):
                    self.assertTrue(self.controller.prepareBundle())
        self.assertEqual(task_contract.selected(self.job, 'video'), new_video.resolve())
        self.assertFalse(captured['register'])
        self.assertEqual(captured['expected_video']['sha256'], registered['sha256'])
        self.assertEqual(self.controller.view['video'], str(new_video.resolve()))
        self.assertTrue(self.controller.view['verified'])

    def test_platform_variant_failure_keeps_other_handoff_ready(self):
        bundle = self.root / 'two-platforms'
        bundle.mkdir()
        cover = bundle / 'cover.jpg'
        cover.write_bytes(b'common-cover')
        variant = bundle / 'cover-bilibili-4-3.jpg'
        variant.write_bytes(b'bilibili-cover')
        content = {platform: {'title': '离线测试', 'description': '本家和 STAFF',
                   'tags': [], 'ai_generated': False, 'rights': '已核对'}
                   for platform in ('bilibili', 'xiaohongshu')}
        manifest = bundle / 'manifest.json'
        manifest.write_text(json.dumps({
            'version': 2, 'state': 'prepared', 'source_job': str(self.job),
            'final_publish': 'human_only', 'video': dict(self.video_row),
            'cover': {'sha256': publication.file_hash(cover), 'kind': 'frame'},
            'content': content, 'platform_covers': {'bilibili': {'4:3': {
                'file': variant.name, 'sha256': publication.file_hash(variant)}}},
        }, ensure_ascii=False), encoding='utf-8')
        (bundle / 'cleanup-inventory.json').write_text(json.dumps({'entries': [
            {'file': p.name, 'sha256': publication.file_hash(p)} for p in (cover, variant)
        ]}), encoding='utf-8')
        receipt = task_contract.register_publication(self.job, bundle, expected_video=self.video_row)
        self.assertTrue(self.controller.accept_task(self.job, self.video_row, receipt['bundle']))
        self.assertTrue(self.controller.checkBundle())
        self.assertTrue(self.controller.view['handoffs']['bilibili']['ready'])
        self.assertTrue(self.controller.view['handoffs']['xiaohongshu']['ready'])
        variant.write_bytes(b'changed variant')
        self.assertTrue(self.controller.checkBundle())
        self.assertFalse(self.controller.view['handoffs']['bilibili']['ready'])
        self.assertTrue(self.controller.view['handoffs']['xiaohongshu']['ready'])
        self.assertTrue(self.controller.copyFieldFor('xiaohongshu', 'title'))
        self.assertFalse(self.controller.copyFieldFor('bilibili', 'title'))
        cover.write_bytes(b'changed shared cover')
        self.assertFalse(self.controller.copyFieldFor('xiaohongshu', 'title'))

    def test_imported_bundle_checks_without_source_task_and_keeps_human_only_gate(self):
        bundle = self.root / 'standalone-bundle'
        bundle.mkdir()
        cover = bundle / 'cover.jpg'
        cover.write_bytes(b'standalone-cover')
        manifest = {
            'version': 2, 'state': 'prepared', 'source_job': str(self.root / 'missing-job'),
            'source_bv': None,
            'video': {'path': str(self.video.resolve()), 'sha256': publication.file_hash(self.video),
                      'bytes': self.video.stat().st_size, 'duration': 10.0},
            'cover': {'kind': 'frame', 'source': str(self.video.resolve()), 'seconds': 2.0,
                      'sha256': publication.file_hash(cover)},
            'platform_covers': {},
            'content': {'xiaohongshu': {
                'title': '独立资料标题', 'description': '本家与 STAFF', 'tags': ['翻唱'],
                'rights': '原创并已核对署名授权', 'ai_generated': False}},
            'final_publish': 'human_only',
        }
        (bundle / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        with patch.object(quick_publication.publication, 'prepare') as prepare:
            self.assertTrue(self.controller.importBundle(QUrl.fromLocalFile(str(bundle))))
            self.assertEqual(self.controller.view['job'], '')
            self.assertTrue(self.controller.view['bundleOnly'])
            self.assertEqual(self.controller.view['title'], '独立资料标题')
            self.assertTrue(self.controller.view['canCheck'])
            self.assertFalse(self.controller.prepareBundle())
            prepare.assert_not_called()
            self.assertTrue(self.controller.checkBundle())
        self.assertTrue(self.controller.view['verified'])
        self.assertEqual(self.controller.view['browserSession']['finalPublish'], 'human_only')
        self.assertTrue(self.controller.copyFieldFor('xiaohongshu', 'title'))

    def test_saved_bundle_draft_restores_with_local_edits(self):
        bundle = self.root / 'draft-bundle'
        bundle.mkdir()
        cover = bundle / 'cover.jpg'
        cover.write_bytes(b'draft-cover')
        manifest = {
            'version': 2, 'state': 'prepared', 'source_job': '', 'source_bv': None,
            'video': {'path': str(self.video.resolve()), 'sha256': publication.file_hash(self.video),
                      'bytes': self.video.stat().st_size, 'duration': 10.0},
            'cover': {'kind': 'frame', 'seconds': 2.0, 'sha256': publication.file_hash(cover)},
            'platform_covers': {},
            'content': {'xiaohongshu': {
                'title': '原题', 'description': '原正文', 'tags': [], 'rights': '授权',
                'ai_generated': False}}, 'final_publish': 'human_only'}
        (bundle / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        saved = {
            'version': 1, 'job': '', 'video': str(self.video.resolve()),
            'videoSha256': publication.file_hash(self.video), 'sourceStamp': [],
            'bundle': str(bundle.resolve()), 'bundleOnly': True,
            'platform': 'xiaohongshu', 'selectedPlatforms': ['xiaohongshu'],
            'coverRatio': '4:3', 'step': 2,
            'drafts': {'xiaohongshu': {
                'title': '恢复后的标题', 'description': '恢复后的正文', 'tags': '翻唱 恢复',
                'ai': False, 'rights': '恢复后授权', 'coverMode': 'frame',
                'coverSeconds': 2.0, 'coverFile': ''}},
        }
        (self.root / 'quick-draft.json').write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
        self.controller.close()
        self.owner = Owner(self.root / 'quick-draft.json')
        self.worker = FakeWorker()
        self.controller = QuickPublication(self.owner, self.worker)
        self.assertTrue(self.controller.restoreSavedDraft())
        self.assertEqual(self.controller.view['title'], '恢复后的标题')
        self.assertEqual(self.controller.view['tags'], '翻唱 恢复')
        self.assertEqual(self.controller.view['coverRatio'], '4:3')
        self.assertTrue(self.controller.view['bundleOnly'])


if __name__ == '__main__':
    unittest.main()
