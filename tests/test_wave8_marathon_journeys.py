"""Wave 8 (final wave) end-to-end user journey tests:
Full Cross-Page Marathon + ZH/EN/JA x Dark/Light x Multi-DPI Global Visual Gate +
Long-Session Stability Stress + Global Hygiene Locks.

Covers W8-BUG-01..03 and cross-cutting quality gates:
1. Pure-Python hermetic tier (always executed, including minimal GitHub CI):
   - W8-01a: vwa_paths.strip_extended_prefix & vwa_io re-export contract, plus
     full-repo AST lock guaranteeing 100% of QDesktopServices.openUrl(QUrl.fromLocalFile(...))
     sites in src/*.py strip Windows \\\\?\\ prefixes (locks W8-BUG-01) and 0 bare
     TemporaryDirectory() calls exist in src/*.py.
   - W8-01b: 4-way registration parity across Wave 0..8 (tests/, source-files.json,
     run_tests.py, .github/workflows/ci.yml) + core ZH/EN/JA i18n completeness.
2. Qt6 Offscreen + Real Audio interactive tier (when PySide6 + soundfile available):
   - W8-02: Single live QuickBridge cross-page marathon:
     Project Convert -> Tuning (.svp load) -> Chorus (2 WAV vocals -> sendToMixdown)
     -> Audio Mixdown (chorus handoff accepted) -> Video Mixdown (mix handoff)
     -> Publishing -> Results -> Components -> Settings with zero state bleed/errors.
   - W8-03: All 13 pages/panes x 3 real languages (zh-CN/en/ja) x 2 themes (dark/light)
     x 3 viewports (1280x720@100%, 1050x620@130%, 960x640@130%) = 234 live UI
     combinations with 0 horizontal overflows and 0 QML warnings (locks W8-BUG-02..03).
   - W8-04: Long-session stability stress (65 rapid cross-page navigations + 12 theme
     flips + 6 tutorial open/skip cycles + clean bridge/window teardown).
   - W8-05: Direct \\\\?\\ and \\\\?\\UNC\\ extended-path safety verification across all
     5 remaining openUrl sites (QuickBridge, QuickPublication, QuickSupport, QuickVideo x2).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1]
for candidate in (APP / 'src', APP):
    text = str(candidate)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

import i18n
import vwa_io
import vwa_paths


WAVE_TEST_FILES = (
    'test_wave0_shell_journeys.py',
    'test_wave1_preparation_journeys.py',
    'test_wave2_tuning_journeys.py',
    'test_wave3_chorus_journeys.py',
    'test_wave4_mixdown_journeys.py',
    'test_wave5_delivery_journeys.py',
    'test_wave6_convert_journeys.py',
    'test_wave7_management_journeys.py',
    'test_wave8_marathon_journeys.py',
)

ALL_13_ROUTES = (
    ('processing', None),
    ('project_convert', None),
    ('tuning', None),
    ('tuning_workshop', 'style'),
    ('tuning_workshop', 'calibration'),
    ('tuning_workshop', 'presets'),
    ('chorus', None),
    ('audio_mixdown', None),
    ('video_mixdown', None),
    ('publishing', None),
    ('results', None),
    ('components', None),
    ('updates', None),
)


def _has_soundfile() -> bool:
    try:
        import soundfile  # noqa: F401
        return True
    except ImportError:
        return False


def _has_qt6() -> bool:
    for qt_root in (
        os.environ.get('VWA_TEST_QT6_ROOT', ''),
        str(APP / 'runtime/qt6'),
        'C:/vwa/workspace/preview/vwa/runtime/qt6',
    ):
        if qt_root and Path(qt_root).is_dir() and qt_root not in sys.path:
            sys.path.insert(0, qt_root)
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _write_wav(path: Path, *, freq: float = 261.63, dur: float = 1.4, sr: int = 44100,
               amp: float = 0.22) -> Path:
    import numpy as np
    import soundfile as sf

    n = max(1, int(round(dur * sr)))
    t = np.arange(n, dtype=np.float64) / sr
    env = np.minimum(1.0, np.minimum(t / 0.03, (dur - t) / 0.03))
    env = np.clip(env, 0.0, 1.0)
    data = (amp * env * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    sf.write(str(path), data, sr, subtype='PCM_16')
    return path


def _write_minimal_svp(path: Path) -> Path:
    svp = {
        'version': 133,
        'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120.0}],
        },
        'library': [],
        'tracks': [{
            'name': 'Lead',
            'dispColor': 'ff7db235',
            'dispOrder': 0,
            'renderEnabled': True,
            'mixer': {'gainDecibel': 0.0, 'pan': 0.0, 'mute': False, 'solo': False, 'display': True},
            'mainGroup': {
                'name': 'main',
                'uuid': 'w8-main-group',
                'parameters': {
                    'pitchDelta': {'mode': 'cubic', 'points': []},
                    'vibratoEnv': {'mode': 'cubic', 'points': []},
                    'loudness': {'mode': 'cubic', 'points': []},
                    'tension': {'mode': 'cubic', 'points': []},
                    'breathiness': {'mode': 'cubic', 'points': []},
                    'voicing': {'mode': 'cubic', 'points': []},
                    'gender': {'mode': 'cubic', 'points': []},
                },
                'vocalModes': {},
                'notes': [
                    {'onset': 705600000, 'duration': 705600000, 'lyrics': 'la', 'phonemes': '', 'pitch': 60, 'attributes': {}},
                    {'onset': 1411200000, 'duration': 705600000, 'lyrics': 'mi', 'phonemes': '', 'pitch': 64, 'attributes': {}},
                ],
            },
            'mainRef': {
                'groupID': 'w8-main-group',
                'blickOffset': 0,
                'pitchOffset': 0,
                'isInstrumental': False,
                'systemPitchDelta': {'mode': 'cubic', 'points': []},
                'database': {'name': 'Demo', 'language': 'mandarin', 'phoneset': 'xsampa', 'version': '100'},
                'dictionary': '',
                'voice': {},
            },
            'groups': [],
        }],
    }
    path.write_text(json.dumps(svp, ensure_ascii=False), encoding='utf-8')
    return path


class Wave8HermeticGlobalGuardTests(unittest.TestCase):
    """Pure-Python global hygiene, 4-way manifest parity, and i18n checks (always run in CI)."""

    def test_w8_01a_global_static_open_url_and_tempdir_hygiene(self):
        # 1) Single source of truth for strip_extended_prefix
        self.assertIs(vwa_io.strip_extended_prefix, vwa_paths.strip_extended_prefix)
        self.assertEqual(vwa_paths.strip_extended_prefix(r'\\?\C:\Songs\demo.wav'), r'C:\Songs\demo.wav')
        self.assertEqual(vwa_paths.strip_extended_prefix(r'\\?\UNC\nas\share\a.mp4'), r'\\nas\share\a.mp4')
        self.assertEqual(vwa_paths.strip_extended_prefix(Path(r'C:\Songs\demo.wav')), r'C:\Songs\demo.wav')
        self.assertEqual(vwa_paths.strip_extended_prefix(''), '')

        # 2) Full-repo AST scan over src/*.py
        src_dir = APP / 'src' if (APP / 'src').is_dir() else APP
        allowed_cleaners = frozenset({'strip_extended_prefix', 'clean_local_path', 'local_file_path'})
        unguarded_open = []
        bare_tempdirs = []
        for py_file in sorted(src_dir.glob('*.py')):
            tree = ast.parse(py_file.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == 'openUrl' and node.args:
                    arg0 = node.args[0]
                    if (
                        isinstance(arg0, ast.Call)
                        and isinstance(arg0.func, ast.Attribute)
                        and arg0.func.attr == 'fromLocalFile'
                        and arg0.args
                    ):
                        inner = arg0.args[0]
                        cleaner = ''
                        if isinstance(inner, ast.Call):
                            if isinstance(inner.func, ast.Name):
                                cleaner = inner.func.id
                            elif isinstance(inner.func, ast.Attribute):
                                cleaner = inner.func.attr
                        if cleaner not in allowed_cleaners:
                            unguarded_open.append(f'{py_file.name}:{node.lineno}')
                is_td = (
                    (isinstance(func, ast.Name) and func.id == 'TemporaryDirectory')
                    or (isinstance(func, ast.Attribute) and func.attr == 'TemporaryDirectory')
                )
                if is_td:
                    has_ignore = any(
                        kw.arg == 'ignore_cleanup_errors'
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                        for kw in node.keywords
                    )
                    if not has_ignore:
                        bare_tempdirs.append(f'{py_file.name}:{node.lineno}')

        self.assertEqual(unguarded_open, [], f'Unguarded openUrl(QUrl.fromLocalFile): {unguarded_open}')
        self.assertEqual(bare_tempdirs, [], f'Bare TemporaryDirectory() in src: {bare_tempdirs}')

    def test_w8_01b_wave0_to_wave8_four_way_manifest_parity_and_i18n_core(self):
        # 1) All 9 Wave suites registered in tests/, packaging/source-files.json, run_tests.py, ci.yml
        manifest = json.loads((APP / 'packaging/source-files.json').read_text(encoding='utf-8'))
        manifest_entries = set(manifest.get('files', []))
        run_tests_text = (APP / 'tests/run_tests.py').read_text(encoding='utf-8')
        ci_text = (APP / '.github/workflows/ci.yml').read_text(encoding='utf-8')

        for fname in WAVE_TEST_FILES:
            self.assertTrue((APP / 'tests' / fname).is_file(), f'Missing test file: tests/{fname}')
            self.assertIn(f'tests/{fname}', manifest_entries, f'Missing in source-files.json: tests/{fname}')
            self.assertIn(f"'{fname}'", run_tests_text, f'Missing in tests/run_tests.py: {fname}')
            self.assertIn(f'tests/{fname}', ci_text, f'Missing in .github/workflows/ci.yml: tests/{fname}')

        # 2) Key cross-page labels have genuine non-empty EN & JA translations
        en_cat = i18n.catalog('en')
        ja_cat = i18n.catalog('ja')
        self.assertGreater(len(en_cat), 4000)
        self.assertGreater(len(ja_cat), 4000)
        for zh_key in ('调音工坊', '简易混音', '视频合成', '返回调音', '安装混音组件', '导出新 WAV', '本机日志目录暂不可打开。'):
            self.assertTrue(en_cat.get(zh_key), f'Missing EN translation for {zh_key!r}')
            self.assertNotEqual(en_cat.get(zh_key), zh_key, f'EN translation unchanged for {zh_key!r}')
            self.assertTrue(ja_cat.get(zh_key), f'Missing JA translation for {zh_key!r}')


@unittest.skipUnless(_has_qt6() and _has_soundfile(), 'Qt6 (PySide6) and soundfile required for Wave 8 UI/Marathon tests')
class Wave8Qt6MarathonAndVisualTests(unittest.TestCase):
    """Live Qt6 offscreen cross-page marathon, 234-combo global visual gate, stress, and openUrl tests."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication

        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.qt_warnings: list[str] = []

        def _handler(kind, _ctx, text):
            if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                cls.qt_warnings.append(str(text))

        cls._msg_handler = _handler
        qInstallMessageHandler(_handler)

    def _pump(self, steps: int = 8):
        for _ in range(steps):
            self.app.processEvents()

    def _wait_until(self, predicate, *, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.015)
        self.app.processEvents()
        return bool(predicate())

    def _teardown_workspace(self, engine, window, bridge, modes=None):
        import gc
        from PySide6.QtCore import QCoreApplication, QEvent

        window.close()
        if modes is not None:
            modes.close()
        bridge.close()
        engine.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        gc.collect()

    def _walk_overflows(self, root_item, max_w: float):
        from PySide6.QtCore import QPointF
        from PySide6.QtQuick import QQuickItem

        overflows = []

        def walk(item):
            if isinstance(item, QQuickItem) and item.isVisible() and item.width() > 0 and item.height() > 0:
                pos = item.mapToScene(QPointF(0, 0))
                if pos.x() + item.width() > max_w + 2.0 and not item.clip():
                    overflows.append((
                        item.objectName() or item.metaObject().className(),
                        round(pos.x(), 1),
                        round(pos.x() + item.width(), 1),
                        max_w,
                    ))
                if not item.clip():
                    for child in item.childItems():
                        walk(child)
            elif isinstance(item, QQuickItem) and item.isVisible():
                for child in item.childItems():
                    walk(child)

        walk(root_item)
        return overflows

    def test_w8_02_cross_page_marathon_end_to_end(self):
        """Single live QuickBridge marathon across Convert->Tuning->Chorus->Mixdown->Video->Publishing->Results."""
        from PySide6.QtCore import QUrl
        from quick_bridge import QuickBridge

        bridge = QuickBridge()
        try:
            with vwa_io.resilient_tempdir(prefix='vwa-w8-marathon-') as tmp:
                root = Path(tmp)
                svp_path = _write_minimal_svp(root / 'marathon_song.svp')
                lead_wav = _write_wav(root / 'lead.wav', freq=293.66, dur=1.4)
                harm_wav = _write_wav(root / 'harmony.wav', freq=369.99, dur=1.4)
                mix_wav = _write_wav(root / 'final_mix.wav', freq=220.0, dur=1.4)

                # Stage 1: Project Convert -> Tuning handoff
                bridge.navigate('project_convert')
                self.assertEqual(bridge.route, 'project_convert')
                bridge._open_converted_in_tuning(str(svp_path))
                loaded_tuning = self._wait_until(
                    lambda: bridge.route == 'tuning'
                    and not bridge.tuning.view.get('busy', False)
                    and len(bridge.tuning.notes) >= 2,
                    timeout=15.0,
                )
                self.assertTrue(
                    loaded_tuning,
                    f'Tuning did not load SVP: route={bridge.route} notes={len(bridge.tuning.notes)} '
                    f'msg={bridge.tuning.view.get("message")}',
                )

                # Stage 2: Chorus import 2 real vocal WAVs -> sendToMixdown handoff
                bridge.navigate('chorus')
                self.assertEqual(bridge.route, 'chorus')
                bridge.chorus.importAudio('vocal', [QUrl.fromLocalFile(str(lead_wav)), QUrl.fromLocalFile(str(harm_wav))])
                loaded_chorus = self._wait_until(
                    lambda: not bridge.chorus.view.get('busy', False)
                    and bridge.chorus.view.get('count', 0) == 2,
                    timeout=20.0,
                )
                self.assertTrue(
                    loaded_chorus,
                    f'Chorus did not import 2 vocals: count={bridge.chorus.view.get("count")} '
                    f'notice={bridge.chorus.view.get("notice")}',
                )

                bridge.chorus.sendToMixdown(True, 'original', False, True)
                handed_mixdown = self._wait_until(
                    lambda: bridge.route == 'audio_mixdown'
                    and not bridge.chorus.view.get('busy', False)
                    and len([r for r in bridge.mixdown._state._tracks if r['id'].startswith('vocal-')]) == 2,
                    timeout=25.0,
                )
                self.assertTrue(
                    handed_mixdown,
                    f'Chorus->Mixdown handoff failed: route={bridge.route} chorus={bridge.chorus.view.get("notice")} '
                    f'mix_tracks={len(bridge.mixdown._state._tracks)}',
                )

                # Stage 3: Audio Mixdown -> Video Mixdown handoff (independent WAV)
                mix_sha = hashlib.sha256(mix_wav.read_bytes()).hexdigest()
                bridge.open_independent_mix(str(mix_wav), mix_sha)
                handed_video = self._wait_until(
                    lambda: bridge.route == 'video_mixdown'
                    and str(mix_wav.name) in str(bridge.video.view.get('mixdown', '')),
                    timeout=10.0,
                )
                self.assertTrue(handed_video, f'Mixdown->Video handoff failed: route={bridge.route} video={bridge.video.view}')

                # Stage 4: Delivery & Management routes clean navigation (zero bleed/crash)
                for route in ('publishing', 'results', 'components', 'updates', 'processing'):
                    bridge.navigate(route)
                    self._pump(4)
                    self.assertEqual(bridge.route, route)
        finally:
            bridge.close()
            self._pump(4)

    def test_w8_03_global_13_pages_3_languages_2_themes_3_viewports_zero_overflow(self):
        """234 live UI combos: 13 pages x 3 real languages (zh-CN/en/ja) x 2 themes x 3 viewports."""
        import appearance_state
        from quick_app import create_workspace

        expected_workshop = {
            'zh-CN': '调音工坊',
            'en': 'Tuning Workshop',
            'ja': '調声ワークショップ',
        }
        overflow_failures = []
        qml_warning_failures = []

        prev_lang = i18n.current_language()
        try:
            for lang in ('zh-CN', 'en', 'ja'):
                fake_appearance = dict(appearance_state.DEFAULTS, ui_language=lang)
                with mock.patch.object(appearance_state, 'load_appearance', return_value=fake_appearance), \
                     mock.patch.object(appearance_state, 'save_appearance', lambda _v: None):
                    for vw, vh, sc in ((1280, 720, 100), (1050, 620, 130), (960, 640, 130)):
                        before_w = len(self.qt_warnings)
                        engine, window, bridge, modes = create_workspace(self.app, viewport=(vw, vh), scale=sc)
                        try:
                            self.assertEqual(i18n.current_language(), lang)
                            self.assertEqual(i18n.tr('调音工坊'), expected_workshop[lang])
                            win_w = float(window.width())
                            for theme in ('dark', 'light'):
                                bridge.support.state.values['theme'] = theme
                                bridge.support.settingsChanged.emit()
                                self._pump(3)
                                for route, pane in ALL_13_ROUTES:
                                    bridge.navigate(route)
                                    self.assertEqual(bridge.route, route, f'Failed to navigate to route {route!r}')
                                    if pane:
                                        bridge.workshop.setPane(pane)
                                    self._pump(6)
                                    ov = self._walk_overflows(window.contentItem(), win_w)
                                    if ov:
                                        overflow_failures.append(
                                            f'{lang} {theme} {vw}x{vh}@{sc}% {route}/{pane}: {len(ov)} {ov[:3]}'
                                        )
                        finally:
                            self._teardown_workspace(engine, window, bridge, modes)
                        new_w = [
                            w for w in self.qt_warnings[before_w:]
                            if any(k in w for k in ('QML', 'TypeError', 'ReferenceError', 'Binding loop'))
                        ]
                        if new_w:
                            qml_warning_failures.append(f'{lang} {vw}x{vh}@{sc}%: {new_w[:4]}')
        finally:
            i18n.set_language(prev_lang)

        self.assertEqual(overflow_failures, [], 'Horizontal UI overflows detected:\n' + '\n'.join(overflow_failures[:12]))
        self.assertEqual(qml_warning_failures, [], 'QML runtime warnings detected:\n' + '\n'.join(qml_warning_failures[:8]))

    def test_w8_04_long_session_stability_rapid_navigation_and_theme_stress(self):
        """Long-session stress: 65 rapid route flips + 12 theme toggles + 6 tutorial cycles -> 0 warnings."""
        import appearance_state
        from quick_app import create_workspace

        fake_appearance = dict(appearance_state.DEFAULTS, ui_language='zh-CN')
        before_w = len(self.qt_warnings)
        with mock.patch.object(appearance_state, 'load_appearance', return_value=fake_appearance), \
             mock.patch.object(appearance_state, 'save_appearance', lambda _v: None):
            engine, window, bridge, modes = create_workspace(self.app, viewport=(1280, 720), scale=100)
            try:
                # 5 full rounds across all 13 routes/panes = 65 navigations
                for round_idx in range(5):
                    for route, pane in ALL_13_ROUTES:
                        bridge.navigate(route)
                        if pane:
                            bridge.workshop.setPane(pane)
                        self._pump(2)
                    # Toggle dark/light twice per round + one more pair after loop = 12 flips
                    for theme in ('light', 'dark'):
                        bridge.support.state.values['theme'] = theme
                        bridge.support.settingsChanged.emit()
                        self._pump(2)
                    # Open & skip tutorial cleanly
                    bridge.support.showTutorial()
                    self._pump(2)
                    bridge.support.skipTutorial()
                    self._pump(2)
                for theme in ('light', 'dark'):
                    bridge.support.state.values['theme'] = theme
                    bridge.support.settingsChanged.emit()
                    self._pump(2)
                bridge.support.showTutorial()
                self._pump(2)
                bridge.support.skipTutorial()
                self._pump(3)
            finally:
                self._teardown_workspace(engine, window, bridge, modes)

        self.assertTrue(bridge._closed)
        new_w = [
            w for w in self.qt_warnings[before_w:]
            if any(k in w for k in ('QML', 'TypeError', 'ReferenceError', 'Binding loop'))
        ]
        self.assertEqual(new_w, [], f'Unexpected QML warnings during stress session: {new_w[:6]}')

    def test_w8_05_remaining_five_open_url_sites_extended_prefix_safety(self):
        """Verify all 5 Wave-8 guarded openUrl sites strip \\\\?\\ and \\\\?\\UNC\\ prefixes."""
        from PySide6.QtGui import QDesktopServices
        from quick_bridge import QuickBridge
        import quick_support

        opened_urls: list = []

        def fake_open(url):
            opened_urls.append(url)
            return True

        bridge = QuickBridge()
        try:
            with vwa_io.resilient_tempdir(prefix='vwa-w8-openurl-') as tmp:
                root = Path(tmp)
                sample_mp4 = root / 'demo.mp4'
                sample_mp4.write_bytes(b'fake-mp4')
                sample_wav = root / 'mix.wav'
                sample_wav.write_bytes(b'fake-wav')
                log_dir = root / 'logs'
                log_dir.mkdir(parents=True)

                ext_mp4 = '\\\\?\\' + str(sample_mp4.resolve())
                ext_unc = r'\\?\UNC\fileserver\vwa\demo.wav'

                with mock.patch.object(QDesktopServices, 'openUrl', side_effect=fake_open):
                    # 1) QuickBridge._open_local (Win32 + UNC)
                    bridge._open_local(ext_mp4)
                    bridge._open_local(ext_unc)

                    # 2) QuickPublication._open_local
                    self.assertTrue(bridge.publication._open_local(Path(ext_mp4)))

                    # 3) QuickSupport.openDiagnosticLogs (with \\?\ extended ROOT)
                    with mock.patch.object(quick_support, 'ROOT', Path('\\\\?\\' + str(root.resolve()))), \
                         mock.patch.object(quick_support, 'LOG_DIR', 'logs'):
                        bridge.support.openDiagnosticLogs()

                    # 4 & 5) QuickVideo._open_input + openResult
                    bridge.video._open_input(ext_mp4, '原视频')
                    bridge.video._result = ext_mp4
                    bridge.video.openResult()

                self.assertEqual(len(opened_urls), 6, f'Expected 6 openUrl calls, got {len(opened_urls)}')
                for idx, url in enumerate(opened_urls):
                    self.assertTrue(url.isLocalFile(), f'Call #{idx} not local file URL: {url.toString()}')
                    self.assertNotIn('%3F', url.toString(), f'Call #{idx} leaked \\\\?\\ into URL: {url.toString()}')
                    self.assertFalse(url.toLocalFile().startswith('\\\\?\\'), f'Call #{idx} leaked prefix: {url.toLocalFile()}')
        finally:
            bridge.close()
            self._pump(4)


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('PASS test_wave8_marathon_journeys')


if __name__ == '__main__':
    main()
