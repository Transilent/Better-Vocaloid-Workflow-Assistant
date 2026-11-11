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
    from PySide6.QtCore import QCoreApplication, QObject, Signal
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
from quick_publication import QuickPublication


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

    def verify_task_artifact(self, job, row, role, check=None):
        if check is not None and check():
            raise InterruptedError('cancelled')
        self.verified_roles.append(role)
        return dict(row)

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
        self.video_row = artifact_row(self.video, 'video')
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
                    'canCheck', 'verified', 'actions'}
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
            return bundle

        with patch.object(quick_publication.publication, 'prepare', side_effect=make) as prepare, \
                patch.object(quick_publication.publication, 'load_bundle', return_value=manifest):
            self.assertTrue(self.controller.prepareBundle())
        prepare.assert_called_once()
        self.assertIn('video', self.owner.verified_roles)
        self.assertIn('publication', self.owner.verified_roles)
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
        bundle_row = artifact_row(bundle / 'manifest.json', 'publication',
                                  identifier='3' * 32, revision='4' * 64)
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


if __name__ == '__main__':
    unittest.main()
