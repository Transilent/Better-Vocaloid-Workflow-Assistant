from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

try:
    from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
    from PySide6.QtGui import QColor, QGuiApplication, QImage
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtQuickControls2 import QQuickStyle
except ModuleNotFoundError as error:
    if error.name not in {'PySide6', 'shiboken6'}:
        raise
    if __name__ != '__main__':
        raise unittest.SkipTest('No Qt 6 test runtime configured') from error
    class MissingQt6(unittest.TestCase):
        @unittest.skip('No Qt 6 test runtime configured')
        def test_quick_publication_platform_handoffs(self):
            pass
    unittest.main(exit=True)

import publication
import quick_publication
from quick_publication import QuickPublication


QQuickStyle.setStyle('Basic')
APP_INSTANCE = QGuiApplication.instance() or QGuiApplication([])


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
    def __init__(self, draft, browser_root):
        super().__init__()
        self.quick_publication_draft_path = draft
        self.quick_publication_browser_root = browser_root
        self.quick_publication_python = Path(sys.executable)
        self.storage = None
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


class FakeProcess:
    def __init__(self, argv, base):
        self.argv = list(argv)
        self.pid = 3000 + len(base)
        self._returncode = None
        base.append(self)

    def poll(self):
        return self._returncode

    def finish(self, returncode=0):
        self._returncode = int(returncode)


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


class PublicationFixture(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get('VWA_QUICK_PUBLICATION_TEST_ROOT', APP / 'work'))
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        self.job = self.root / 'job'
        self.job.mkdir()
        (self.job / 'request.json').write_text(json.dumps({
            'source_kind': 'local', 'bv': None, 'url': None,
        }), encoding='utf-8')
        self.video = self.job / 'final.mp4'
        self.video.write_bytes(b'quick-publication-platform-fixture-video')
        self.video_row = artifact_row(self.video, 'video')
        self.owner = Owner(self.root / 'draft.json', self.root / 'cache/publish-browser')
        self.worker = FakeWorker()
        self.controller = QuickPublication(self.owner, self.worker)

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    def fill(self, platform, title):
        self.assertTrue(self.controller.editPlatform(platform))
        self.assertTrue(self.controller.setField('title', title))
        self.assertTrue(self.controller.setField('description', '本家\nSTAFF\n已核对授权与署名'))
        self.assertTrue(self.controller.setField('tags', '#翻唱 #测试'))
        self.assertTrue(self.controller.setField('ai', False))
        self.assertTrue(self.controller.setField('rights', '原创并已核对署名授权'))

    def make_bundle(self, both):
        bundle = self.root / ('bundle-both' if both else 'bundle-bili')
        bundle.mkdir()
        (bundle / 'cover.jpg').write_bytes(b'cover-base')
        (bundle / 'cover-bilibili-4-3.jpg').write_bytes(b'cover-43')
        (bundle / 'cover-bilibili-16-9.jpg').write_bytes(b'cover-169')
        (bundle / '发布前核对.txt').write_text('human review', encoding='utf-8')
        content = {
            'bilibili': {
                'title': 'B站独立标题', 'description': '本家\nSTAFF\n已核对授权与署名',
                'tags': ['翻唱', '测试'], 'rights': '原创并已核对署名授权',
                'ai_generated': False,
            }
        }
        if both:
            content['xiaohongshu'] = {
                'title': '小红书独立标题', 'description': '小红书正文与署名',
                'tags': ['翻唱', '测试'], 'rights': '原创并已核对署名授权',
                'ai_generated': False,
            }
        manifest = {
            'version': 2,
            'state': 'prepared',
            'source_job': str(self.job.resolve()),
            'source_bv': None,
            'video': {
                'path': str(self.video.resolve()), 'sha256': self.video_row['sha256'],
                'bytes': self.video.stat().st_size, 'duration': 12.0,
                'width': 1920, 'height': 1080,
            },
            'cover': {'kind': 'frame', 'seconds': 1.0,
                      'sha256': publication.file_hash(bundle / 'cover.jpg')},
            'platform_covers': {'bilibili': {
                'policy': 'contain',
                '4:3': {'file': 'cover-bilibili-4-3.jpg', 'width': 1440, 'height': 1080,
                        'sha256': publication.file_hash(bundle / 'cover-bilibili-4-3.jpg')},
                '16:9': {'file': 'cover-bilibili-16-9.jpg', 'width': 1920, 'height': 1080,
                         'sha256': publication.file_hash(bundle / 'cover-bilibili-16-9.jpg')},
            }},
            'content': content,
            'final_publish': 'human_only',
        }
        (bundle / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        return bundle, manifest

    def prepare(self, both=False):
        self.assertTrue(self.controller.accept_task(self.job, self.video_row))
        if both:
            self.assertTrue(self.controller.togglePlatform('xiaohongshu', True))
        self.fill('bilibili', 'B站独立标题')
        if both:
            self.fill('xiaohongshu', '小红书独立标题')
            self.assertTrue(self.controller.setField('description', '小红书正文与署名'))
        self.assertTrue(self.controller.nextStep())
        self.assertTrue(self.controller.nextStep())
        bundle, manifest = self.make_bundle(both)
        captured = {}

        def prepare_bundle(job, video, content, *args, **kwargs):
            captured['content'] = content
            return bundle

        with patch.object(quick_publication.publication, 'prepare', side_effect=prepare_bundle), \
                patch.object(quick_publication.publication, 'load_bundle', return_value=manifest):
            self.assertTrue(self.controller.prepareBundle())
            self.assertIn('content', captured)
        return bundle, manifest, captured['content']


class QuickPublicationPlatformTests(PublicationFixture):
    def test_one_frozen_bundle_has_independent_platform_handoffs(self):
        bundle, manifest, content = self.prepare(both=True)
        self.assertEqual(set(content), {'bilibili', 'xiaohongshu'})
        view = self.controller.view
        self.assertEqual(view['selectedPlatforms'], ['bilibili', 'xiaohongshu'])
        self.assertEqual(set(view['handoffs']), {'bilibili', 'xiaohongshu'})
        self.assertTrue(view['handoffs']['bilibili']['ready'])
        self.assertTrue(view['handoffs']['xiaohongshu']['ready'])
        self.assertEqual([item['ratio'] for item in view['coverVariants']], ['4:3', '16:9'])
        self.assertTrue(self.controller.setCoverRatio('4:3'))
        self.assertTrue(self.controller.copyFieldFor('bilibili', 'cover'))
        self.assertEqual(self.owner.copied[-1], str((bundle / 'cover-bilibili-4-3.jpg').resolve()))
        self.assertTrue(self.controller.copyFieldFor('xiaohongshu', 'all'))
        self.assertIn('小红书独立标题', self.owner.copied[-1])
        self.assertNotIn('B站独立标题', self.owner.copied[-1])

    def test_bilibili_manual_session_lifecycle_is_explicit_and_status_bound(self):
        bundle, manifest, _content = self.prepare(both=False)
        processes = []

        def start(argv, **kwargs):
            return FakeProcess(argv, processes)

        with patch.object(quick_publication.subprocess, 'Popen', side_effect=start), \
                patch.object(quick_publication.QDesktopServices, 'openUrl') as open_url, \
                patch.object(quick_publication.publication, 'load_bundle', return_value=manifest):
            self.assertTrue(self.controller.openCreatorFor('bilibili'))
            open_url.assert_not_called()
            self.assertEqual(len(processes), 1, self.controller.view['message'])
            self.assertIn('--bundle', processes[0].argv)
            self.assertEqual(Path(processes[0].argv[-1]), bundle.resolve())
            self.assertTrue(self.controller.view['browserSession']['active'])

            status = bundle / 'bilibili_上传状态.json'
            status.write_text(json.dumps({
                'state': 'uploading',
                'message': '视频已选择，等待编辑表单。',
                'updated_at': '2026-11-11T12:00:00+08:00',
                'checks': {'video_selected': True, 'upload_complete': False},
                'final_publish': 'human_only',
            }, ensure_ascii=False), encoding='utf-8')
            self.controller._poll_browser()
            session = self.controller.view['browserSession']
            self.assertEqual(session['state'], 'uploading')
            self.assertTrue(session['checks']['video_selected'])
            self.assertTrue(session['canContinue'])
            self.assertTrue(session['canStop'])

            profile = self.owner.quick_publication_browser_root / 'bilibili'
            profile.mkdir(parents=True, exist_ok=True)
            (profile / 'active.json').write_text(json.dumps({
                'pid': processes[0].pid, 'bundle': str(bundle), 'control_bundle': str(bundle),
            }), encoding='utf-8')
            self.assertTrue(self.controller.continueFill())
            self.assertEqual(json.loads((bundle / 'bilibili.replacement.json').read_text(encoding='utf-8'))['bundle'],
                             str(bundle.resolve()))
            self.assertTrue((bundle / 'bilibili.repair').is_file())

            self.assertTrue(self.controller.stopFill())
            self.assertTrue((bundle / 'bilibili.stop').is_file())
            processes[0].finish(0)
            status.write_text(json.dumps({
                'state': 'cancelled', 'message': '已停止浏览器任务。',
                'updated_at': '2026-11-11T12:00:01+08:00', 'checks': {'video_selected': True},
                'final_publish': 'human_only',
            }, ensure_ascii=False), encoding='utf-8')
            self.controller._poll_browser()
            final = self.controller.view['browserSession']
            self.assertFalse(final['active'])
            self.assertEqual(final['state'], 'cancelled')
            self.assertEqual(final['finalPublish'], 'human_only')

            self.assertTrue(self.controller.loginBilibili())
            self.assertEqual(len(processes), 2)
            self.assertIn('--login', processes[1].argv)
            open_url.assert_not_called()
            processes[1].finish(0)
            self.controller._poll_browser()

    def test_xiaohongshu_remains_copy_and_official_manual_handoff(self):
        _bundle, manifest, _content = self.prepare(both=True)
        opened = []
        with patch.object(quick_publication.publication, 'load_bundle', return_value=manifest), \
                patch.object(quick_publication.QDesktopServices, 'openUrl',
                             side_effect=lambda url: opened.append(url) or True), \
                patch.object(quick_publication.subprocess, 'Popen') as popen:
            self.assertTrue(self.controller.openCreatorFor('xiaohongshu'))
            popen.assert_not_called()
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0].toString(), publication.XIAOHONGSHU_CREATOR_URL)
            self.assertTrue(self.controller.copyFieldFor('xiaohongshu', 'title'))
            self.assertEqual(self.owner.copied[-1], '小红书独立标题')


class UiController(QObject):
    viewChanged = Signal()

    def __init__(self, view):
        super().__init__()
        self._view = view

    @Property('QVariantMap', notify=viewChanged)
    def view(self):
        return self._view

    @Slot(str, result=str)
    def text(self, source):
        return str(source)

    @Slot(str, str)
    def setField(self, name, value):
        return None

    @Slot(str)
    def editPlatform(self, platform):
        return None

    @Slot(str, bool)
    def togglePlatform(self, platform, selected):
        return None

    @Slot(str)
    def setCoverRatio(self, ratio):
        return None

    @Slot(QUrl)
    def chooseCover(self, url):
        return None

    @Slot()
    def previewCover(self):
        return None

    @Slot()
    def nextStep(self):
        return None

    @Slot()
    def previousStep(self):
        return None

    @Slot()
    def prepareBundle(self):
        return None

    @Slot()
    def checkBundle(self):
        return None

    @Slot(str)
    def copyField(self, field):
        return None

    @Slot(str, str)
    def copyFieldFor(self, platform, field):
        return None

    @Slot(str)
    def openMaterial(self, name):
        return None

    @Slot(str)
    def openCreatorFor(self, platform):
        return None

    @Slot()
    def loginBilibili(self):
        return None

    @Slot()
    def continueFill(self):
        return None

    @Slot()
    def stopFill(self):
        return None

    @Slot()
    def requestExit(self):
        return None


def make_theme():
    colors = {
        'base': '#f5f4f9', 'surface': '#ffffff', 'raised': '#eeebf5',
        'sidebar': '#eae7f2', 'text': '#242233', 'muted': '#5c576d',
        'line': '#d8d3e3', 'accent': '#6544a5', 'onAccent': '#ffffff',
        'accentPressed': '#503289', 'selected': '#e1d8f5', 'selectedLine': '#b6a4d5',
        'hover': '#e8e3f2', 'pressed': '#dcd4e9', 'success': '#286a53',
        'error': '#9f3545', 'attention': '#825320',
    }
    attributes = {
        'scale': Property(float, lambda self: 1.0, constant=True),
        'reduceMotion': Property(bool, lambda self: False, constant=True),
    }
    for name, value in colors.items():
        attributes[name] = Property(QColor, lambda self, value=value: QColor(value), constant=True)
    theme_type = type('PublicationPageTheme', (QObject,), attributes)
    return theme_type()


class QuickPublicationQmlTests(unittest.TestCase):
    def test_page_compiles_with_two_handoffs_and_bilibili_lifecycle(self):
        evidence = Path(os.environ.get(
            'VWA_QUICK_PUBLICATION_EVIDENCE',
            APP / 'work/quick-capability-migration-20261111-publication-handoffs'))
        evidence.mkdir(parents=True, exist_ok=True)
        image = evidence / 'publication-page-1440x900.png'
        bottom_image = evidence / 'publication-page-lifecycle-1440x900.png'
        cover_image = evidence / 'publication-page-cover-1440x900.png'
        cover_43_image = evidence / 'publication-page-cover-4x3-1440x900.png'
        cover_source = evidence / 'fixture-cover.png'
        preview = QImage(960, 540, QImage.Format_RGB32)
        preview.fill(QColor('#d9c9f2'))
        self.assertTrue(preview.save(str(cover_source)))
        view = {
            'step': 2, 'job': 'C:/fixture/job', 'video': 'C:/fixture/final.mp4',
            'platform': 'bilibili', 'selectedPlatforms': ['bilibili', 'xiaohongshu'],
            'title': 'B站独立标题', 'description': '本机校对文稿', 'tags': '#翻唱 #测试',
            'ai': False, 'rights': '原创并已署名', 'coverMode': 'frame', 'coverSeconds': 1,
            'coverFile': '', 'coverPreview': QUrl.fromLocalFile(str(cover_source)).toString(),
            'busy': False, 'message': '两个平台已分别核验。', 'state': 'ready',
            'bundle': 'C:/fixture/bundle', 'bundleStale': False, 'canPrepare': True,
            'canCheck': True, 'verified': True,
            'actions': {'video': '打开成品视频', 'cover': '打开封面'},
            'coverRatio': '16:9',
            'coverVariants': [
                {'ratio': '4:3', 'file': 'cover-bilibili-4-3.jpg',
                 'path': 'C:/fixture/bundle/cover-bilibili-4-3.jpg',
                 'preview': QUrl.fromLocalFile(str(cover_source)).toString(), 'width': 1440, 'height': 1080},
                {'ratio': '16:9', 'file': 'cover-bilibili-16-9.jpg',
                 'path': 'C:/fixture/bundle/cover-bilibili-16-9.jpg',
                 'preview': QUrl.fromLocalFile(str(cover_source)).toString(), 'width': 1920, 'height': 1080},
            ],
            'handoffs': {
                'bilibili': {'platform': 'bilibili', 'ready': True,
                             'fields': {'title': 'B站独立标题'}, 'video': 'C:/fixture/final.mp4',
                             'cover': 'C:/fixture/bundle/cover-bilibili-16-9.jpg'},
                'xiaohongshu': {'platform': 'xiaohongshu', 'ready': True,
                                'fields': {'title': '小红书独立标题', 'ai_generated': False},
                                'video': 'C:/fixture/final.mp4', 'cover': 'C:/fixture/bundle/cover.jpg'},
            },
            'browserSession': {
                'state': 'uploading', 'message': '视频已选择，等待编辑表单。',
                'active': True, 'mode': 'fill', 'pid': 4242, 'updatedAt': '2026-11-11T12:00:00+08:00',
                'checks': {'video_selected': True, 'upload_complete': False,
                           'title_filled': False, 'description_filled': False, 'cover_filled': False},
                'output': 'C:/fixture/bundle', 'controlBundle': 'C:/fixture/bundle',
                'returncode': None, 'finalPublish': 'human_only', 'canLogin': False,
                'canOpen': False, 'canContinue': True, 'canStop': True,
            },
        }
        controller = UiController(view)
        theme = make_theme()
        engine = QQmlEngine()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(APP / 'src/quick_ui/PublicationPage.qml')))
        if component.status() != QQmlComponent.Ready:
            self.fail('; '.join(error.toString() for error in component.errors()))
        page = component.createWithInitialProperties({
            'theme': theme, 'controller': controller, 'width': 1440, 'height': 900,
        })
        self.assertIsNotNone(page)
        self.assertEqual(component.status(), QQmlComponent.Ready)
        self.assertIsNotNone(page.findChild(QObject, 'publicationPlatformBilibili'))
        self.assertIsNotNone(page.findChild(QObject, 'publicationPlatformXiaohongshu'))
        self.assertIsNotNone(page.findChild(QObject, 'publicationBilibiliLogin'))
        self.assertIsNotNone(page.findChild(QObject, 'publicationBilibiliContinue'))
        self.assertIsNotNone(page.findChild(QObject, 'publicationBilibiliStop'))
        self.assertIsNotNone(page.findChild(QObject, 'publicationXiaohongshuCreator'))
        continue_button = page.findChild(QObject, 'publicationBilibiliContinue')
        stop_button = page.findChild(QObject, 'publicationBilibiliStop')
        self.assertTrue(continue_button.property('enabled'))
        self.assertTrue(stop_button.property('enabled'))
        window = QQuickWindow()
        window.resize(1440, 900)
        page.setParentItem(window.contentItem())
        page.setSize(window.size())
        window.show()
        APP_INSTANCE.processEvents()
        self.assertTrue(window.grabWindow().save(str(image)))
        scroll = page.findChild(QObject, 'publicationContentScroll')
        self.assertIsNotNone(scroll)
        content_item = scroll.property('contentItem')
        self.assertIsNotNone(content_item)
        for _ in range(4):
            APP_INSTANCE.processEvents()
        content_height = float(content_item.property('contentHeight'))
        viewport_height = float(content_item.property('height'))
        content_width = float(content_item.property('contentWidth'))
        viewport_width = float(content_item.property('width'))
        self.assertLessEqual(content_width, viewport_width + 1.0, (content_width, viewport_width))
        maximum = max(0.0, content_height - viewport_height)
        self.assertGreater(maximum, 0.0, (content_height, viewport_height))
        content_item.setProperty('contentY', maximum)
        APP_INSTANCE.processEvents()
        self.assertAlmostEqual(float(content_item.property('contentY')), maximum, places=1)
        self.assertTrue(window.grabWindow().save(str(bottom_image)))
        content_item.setProperty('contentY', 0.0)
        APP_INSTANCE.processEvents()
        view['step'] = 1
        controller.viewChanged.emit()
        APP_INSTANCE.processEvents()
        ratio_169 = page.findChild(QObject, 'publicationBilibiliCover169')
        self.assertTrue(ratio_169.isVisible())
        canvas = page.findChild(QObject, 'publicationCoverCanvas')
        self.assertIsNotNone(canvas)
        width_169 = float(canvas.property('width'))
        height_169 = float(canvas.property('height'))
        self.assertAlmostEqual(width_169 / height_169, 16 / 9, places=2)
        self.assertTrue(window.grabWindow().save(str(cover_image)))
        view['coverRatio'] = '4:3'
        controller.viewChanged.emit()
        APP_INSTANCE.processEvents()
        width_43 = float(canvas.property('width'))
        height_43 = float(canvas.property('height'))
        self.assertAlmostEqual(width_43 / height_43, 4 / 3, places=2)
        self.assertTrue(window.grabWindow().save(str(cover_43_image)))
        window.close()
        page.deleteLater()
        engine.deleteLater()
        APP_INSTANCE.processEvents()


if __name__ == '__main__':
    unittest.main()
