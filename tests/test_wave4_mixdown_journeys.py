"""Wave 4 end-to-end user journey tests: Audio Mixdown.

Covers W4-BUG-01..03 and full Audio Mixdown user journeys:
1. Pure-Python hermetic tier (always executed, including minimal GitHub CI):
   - W4-01: MixdownWorkspaceState 16-vocal cap & EQ/option state machine,
     clean_local_path \\\\?\\ stripping, DeviceReconnectGuard 1-shot helper,
     and MixdownRealtimeService._default_output_factory_factory WASAPI device_index
     forwarding (locks W4-BUG-01).
   - W4-02a: mixdown_limiter True-Peak ceiling safety on 0.1s pure silence and
     extreme +12 dB hot stereo signal (zero NaN/Inf, peak <= -0.9 dBFS).
2. Full Audio DSP tier (when soundfile + audio_mixdown backend available):
   - W4-02b: Full FX chain (EQ + Compression + De-esser + Reverb + Limiter)
     audio_mixdown.render() on 0.1s silence and +12 dB hot vocal + backing.
3. Qt6 Offscreen interactive tier (when PySide6 + soundfile available):
   - W4-03: QuickMixdown A/B loudness match, de-esser reduction listen, and
     preset preview -> confirm -> undo -> save -> export -> import lifecycle.
   - W4-04: QuickMixdown WASAPI hot-swap 1-shot auto-reconnect + budget
     exhaustion / reset on _player_prepared (locks W4-BUG-02).
   - W4-05: QuickMixdown openResult / openOutputFolder \\\\?\\ extended path
     safety (locks W4-BUG-03) and video handoff via composeVideo().
   - W4-06: MixdownPage multi-DPI (100/125/130% compact 1050/960/880px) x
     Dark/Light x empty/loaded x collapsed/expanded analysis zero-overflow gate.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import types
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

import numpy as np
from mixdown_contract import MAX_VOCALS
import mixdown_handoff_service as handoff_svc
import mixdown_limiter as limiter
import mixdown_realtime_service as rt_svc
from mixdown_workspace_state import MixdownWorkspaceState
import vwa_io


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


def _write_wav(path: Path, *, freq: float = 261.63, dur: float = 1.6, sr: int = 44100,
               amp: float = 0.22, stereo: bool = False) -> Path:
    import soundfile as sf

    n = max(1, int(round(dur * sr)))
    t = np.arange(n, dtype=np.float64) / sr
    if amp <= 0.0:
        mono = np.zeros(n, dtype=np.float32)
    else:
        env = np.minimum(1.0, t / 0.02) * np.minimum(1.0, (dur - t) / 0.02)
        mono = (amp * env * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    data = np.column_stack([mono, mono]) if stereo else mono
    sf.write(path, data, sr, subtype='FLOAT')
    return path


class Wave4PurePythonJourneysTests(unittest.TestCase):
    def setUp(self):
        (APP / 'work').mkdir(parents=True, exist_ok=True)
        self._tmp_ctx = vwa_io.resilient_tempdir(APP / 'work', prefix='w4-')
        self.work = Path(self._tmp_ctx.__enter__())
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)

    def test_w4_01_state_cap_wasapi_device_routing_and_helpers(self):
        """W4-01 (locks W4-BUG-01): 16-vocal cap, EQ edits, clean_local_path, reconnect guard, WASAPI device_index."""
        # 1) MixdownWorkspaceState 16-vocal cap + EQ/option mutations
        st = MixdownWorkspaceState(output=self.work / 'mix.wav')
        for _ in range(MAX_VOCALS - 1):
            st.add_vocal()
        self.assertEqual(len([t for t in st.tracks if t['id'].startswith('vocal-')]), MAX_VOCALS)
        with self.assertRaises(ValueError):
            st.add_vocal()
        bell_idx = next(i for i, b in enumerate(st.bands) if b.get('type') not in ('highpass', 'lowpass'))
        st.move_band(bell_idx, 180.0, -3.5)
        self.assertAlmostEqual(st.bands[bell_idx]['gain_db'], -3.5, places=2)
        st.reset_band(bell_idx)
        self.assertAlmostEqual(st.bands[bell_idx]['gain_db'], 0.0, places=2)

        # 2) clean_local_path strips Windows \\?\ and \\?\UNC\ prefixes
        self.assertEqual(handoff_svc.clean_local_path(r'\\?\C:\vwa\out\mix.wav'), r'C:\vwa\out\mix.wav')
        self.assertEqual(handoff_svc.clean_local_path(r'\\?\UNC\nas\share\mix.wav'), r'\\nas\share\mix.wav')

        # 3) try_schedule_device_reconnect 1-shot budget & cooldown semantics
        now = [100.0]
        from audio_device_recovery import DeviceReconnectGuard
        guard = DeviceReconnectGuard(max_attempts=1, cooldown_seconds=2.0, backoff_ms=10, clock=lambda: now[0])
        self.assertIsNone(handoff_svc.try_schedule_device_reconnect(
            guard, was_playing=False, message='AUDCLNT_E_DEVICE_INVALIDATED'))
        self.assertIsNone(handoff_svc.try_schedule_device_reconnect(
            guard, was_playing=True, message='FileNotFoundError: missing wav'))
        backoff = handoff_svc.try_schedule_device_reconnect(
            guard, was_playing=True, message='AUDCLNT_E_DEVICE_INVALIDATED wasapi playback device')
        self.assertEqual(backoff, 10)
        # 2nd consecutive device error without reset must NOT retry again
        self.assertIsNone(handoff_svc.try_schedule_device_reconnect(
            guard, was_playing=True, message='AUDCLNT_E_DEVICE_INVALIDATED wasapi playback device'))
        guard.reset()
        # Still inside 2.0s cooldown -> suppressed; after cooldown expires -> allowed again
        self.assertIsNone(handoff_svc.try_schedule_device_reconnect(
            guard, was_playing=True, message='wasapi playback device disconnected'))
        now[0] += 3.0
        self.assertEqual(handoff_svc.try_schedule_device_reconnect(
            guard, was_playing=True, message='wasapi playback device disconnected'), 10)

        # 4) W4-BUG-01: MixdownRealtimeService._default_output_factory_factory forwards user WASAPI device_index
        captured: list[dict] = []

        def fake_native_output(dll_path, **kwargs):
            captured.append({'dll_path': dll_path, **kwargs})
            return object()

        fake_mod = types.SimpleNamespace(NativeOutput=fake_native_output)
        service = rt_svc._RealtimeWorkerServer.__new__(rt_svc._RealtimeWorkerServer)
        graph = types.SimpleNamespace(sample_rate=44100, channels=2)
        spec = types.SimpleNamespace(dll_path='C:/fake/vwa_output.dll')

        import audio_device_recovery as adr
        with mock.patch.object(rt_svc, '_RUNTIME_NATIVE_OUTPUT', fake_mod):
            with mock.patch.object(adr, 'resolve_device_index', return_value=2):
                factory = service._default_output_factory_factory(graph, spec)
                factory()
            with mock.patch.object(adr, 'resolve_device_index', return_value=-1):
                factory2 = service._default_output_factory_factory(graph, spec)
                factory2()

        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0]['mode'], 'wasapi')
        self.assertEqual(captured[0].get('device_index'), 2)
        self.assertEqual(captured[1]['mode'], 'wasapi')
        self.assertNotIn('device_index', captured[1])

    def test_w4_02a_limiter_contract_and_native_streaming_ceiling(self):
        """W4-02a: Limiter ceiling contract + native StreamingLimiter on 0.1s silence & +12 dB hot stereo signal."""
        self.assertLessEqual(limiter.PEAK_CEILING_DBFS, -0.95)
        self.assertIn('alimiter', limiter.ffmpeg_filter())
        dll = None
        for cand in (
            APP / 'runtime/native/vwa_limiter.dll',
            Path('C:/vwa/workspace/preview/vwa/runtime/native/vwa_limiter.dll'),
        ):
            if cand.is_file():
                dll = cand
                break
        if dll is None:
            return
        sr = 44100
        # 0.1s pure silence
        with limiter.StreamingLimiter(dll, sample_rate=sr, channels=2) as lim_sil:
            sil = np.zeros((4410, 2), dtype=np.float32)
            out_sil = lim_sil.process(sil)
            self.assertTrue(np.isfinite(out_sil).all())
            self.assertEqual(float(np.max(np.abs(out_sil))), 0.0)
        # Extreme +12 dB hot stereo signal
        with limiter.StreamingLimiter(dll, sample_rate=sr, channels=2) as lim_hot:
            t = np.arange(sr, dtype=np.float64) / sr
            hot = np.column_stack([
                (4.0 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32),
                (4.0 * np.sin(2.0 * np.pi * 660.0 * t)).astype(np.float32),
            ])
            out_hot = lim_hot.process(hot)
            self.assertTrue(np.isfinite(out_hot).all())
            peak = float(np.max(np.abs(out_hot)))
            self.assertGreater(peak, 0.5)
            self.assertLessEqual(peak, limiter.PEAK_CEILING + 0.015, f'Limiter failed ceiling: peak={peak}')

    @unittest.skipUnless(_has_soundfile(), 'soundfile not available in minimal CI')
    def test_w4_02b_full_fx_chain_render_silence_and_hot_signal(self):
        """W4-02b: Full audio_mixdown.render() FX chain on 0.1s silence and +12 dB hot vocal+backing."""
        import soundfile as sf
        import audio_mixdown as am

        ffmpeg_path = None
        for cand in (
            APP / 'dependencies/ffmpeg/ffmpeg.exe',
            Path('C:/vwa/workspace/preview/vwa/dependencies/ffmpeg/ffmpeg.exe'),
        ):
            if cand.is_file():
                ffmpeg_path = cand
                break
        if ffmpeg_path is None:
            self.skipTest('FFmpeg binary not available')
        for pb_dir in (
            APP / 'dependencies/optional/pedalboard/current/packages',
            Path('C:/vwa/workspace/preview/vwa/dependencies/optional/pedalboard/current/packages'),
        ):
            if pb_dir.is_dir() and str(pb_dir) not in sys.path:
                sys.path.insert(0, str(pb_dir))
        try:
            import pedalboard  # noqa: F401
        except ImportError:
            self.skipTest('Pedalboard optional package not available')

        sil_v = _write_wav(self.work / 'sil_v.wav', freq=0.0, dur=0.1, amp=0.0)
        sil_b = _write_wav(self.work / 'sil_b.wav', freq=0.0, dur=0.1, amp=0.0, stereo=True)
        hot_v = _write_wav(self.work / 'hot_v.wav', freq=440.0, dur=1.2, amp=0.85)
        hot_b = _write_wav(self.work / 'hot_b.wav', freq=220.0, dur=1.2, amp=0.85, stereo=True)

        with mock.patch.object(am, '_ffmpeg_executable', return_value=ffmpeg_path):
            # 1) 0.1s pure silence through full FX chain
            out_sil = self.work / 'out_sil.wav'
            st_sil = MixdownWorkspaceState(output=out_sil)
            st_sil.set_source('vocal-1', sil_v)
            st_sil.set_source('instrumental', sil_b)
            st_sil.set_track('vocal-1', 'deess_enabled', True)
            st_sil.set_option('eq_enabled', True)
            st_sil.set_option('compression_enabled', True)
            st_sil.set_option('reverb_enabled', True)
            rep_sil = am.render(st_sil.request(out_sil))
            self.assertIsInstance(rep_sil, dict)
            self.assertTrue(out_sil.is_file())
            data_sil, _ = sf.read(out_sil)
            self.assertTrue(np.isfinite(data_sil).all())
            self.assertLessEqual(float(np.max(np.abs(data_sil), initial=0.0)), 1e-4)

            # 2) +12 dB hot vocal + loud backing through full FX chain + limiter
            out_hot = self.work / 'out_hot.wav'
            st_hot = MixdownWorkspaceState(output=out_hot)
            st_hot.set_source('vocal-1', hot_v)
            st_hot.set_source('instrumental', hot_b)
            st_hot.set_track('vocal-1', 'gain', 12.0)
            st_hot.set_track('vocal-1', 'deess_enabled', True)
            st_hot.set_option('eq_enabled', True)
            st_hot.set_option('compression_enabled', True)
            st_hot.set_option('reverb_enabled', True)
            rep_hot = am.render(st_hot.request(out_hot))
            self.assertIsInstance(rep_hot, dict)
            self.assertTrue(out_hot.is_file())
            data_hot, _ = sf.read(out_hot)
            self.assertTrue(np.isfinite(data_hot).all())
            peak = float(np.max(np.abs(data_hot), initial=0.0))
            self.assertGreater(peak, 0.3)
            self.assertLessEqual(peak, 0.92, f'Full mixdown export clipped above ceiling: peak={peak}')


@unittest.skipUnless(_has_qt6() and _has_soundfile(), 'PySide6 or soundfile not available')
class Wave4Qt6InteractiveJourneysTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication

        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.warnings: list[str] = []
        cls._prev_handler = qInstallMessageHandler(
            lambda kind, _ctx, text: cls.warnings.append(text)
            if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg)
            else None
        )

    def setUp(self):
        (APP / 'work').mkdir(parents=True, exist_ok=True)
        self._tmp_ctx = vwa_io.resilient_tempdir(APP / 'work', prefix='w4-qt-')
        self.work = Path(self._tmp_ctx.__enter__())
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)

    def _teardown_workspace(self, engine_qml, window, bridge):
        from PySide6.QtCore import QCoreApplication, QEvent

        window.close()
        engine_qml.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        bridge.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_w4_03_04_05_ab_presets_wasapi_reconnect_extended_path_and_video_handoff(self):
        """W4-03..W4-05: A/B & de-esser listen, preset lifecycle, WASAPI hot-swap reconnect, \\\\?\\ open, video handoff."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from quick_app import create_workspace

        engine_qml, window, bridge, _modes = create_workspace(self.app, viewport=(1280, 720), scale=100)
        try:
            bridge.navigate('audio_mixdown')
            for _ in range(10):
                self.app.processEvents()
            mx = bridge.mixdown

            v1 = _write_wav(self.work / 'v1.wav', freq=261.63, dur=1.4, amp=0.20)
            v2 = _write_wav(self.work / 'v2.wav', freq=329.63, dur=1.4, amp=0.18)
            bg = _write_wav(self.work / 'bg.wav', freq=130.81, dur=1.4, amp=0.14, stereo=True)
            mx.chooseSource('vocal-1', QUrl.fromLocalFile(str(v1)))
            mx.addTrack()
            mx.chooseSource('vocal-2', QUrl.fromLocalFile(str(v2)))
            mx.chooseSource('instrumental', QUrl.fromLocalFile(str(bg)))
            self.app.processEvents()

            # --- W4-03: A/B switching, de-esser listen, and preset lifecycle ---
            mx.selectMix('A')
            self.assertEqual(mx.view['selection'], 'A')
            mx.selectMix('B')
            self.assertEqual(mx.view['selection'], 'B')
            mx.setTrack('vocal-1', 'deess_enabled', True)
            self.assertEqual(mx._reduction_index('vocal-1'), 0)
            if mx.view.get('canPlay'):
                mx.setReductionListen('vocal-1', True)
                self.assertEqual(mx._listen_reduction, 'vocal-1')
                mx.setReductionListen('vocal-1', False)
                self.assertEqual(mx._listen_reduction, '')
            else:
                mx.setReductionListen('vocal-1', True)
                self.assertIn('柔化齿音', mx.view['message'])
            mx.toggleLoudnessMatch()
            self.app.processEvents()

            # Wait for any background support/match probe to settle before preset mutations
            import time
            t0 = time.monotonic()
            while time.monotonic() - t0 < 8.0:
                self.app.processEvents()
                if not mx._active_kind:
                    break
                time.sleep(0.02)

            # Preset preview -> confirm -> undo -> save -> export -> import
            presets = list(mx.presets)
            first_key = next((p['key'] for p in presets if p.get('key') and p['key'] != 'custom'), '')
            self.assertTrue(first_key, f'Expected non-custom mixdown preset in {presets}')
            self.assertTrue(mx.previewPreset(first_key, False, False), (first_key, mx._active_kind, mx.view['message']))
            self.assertTrue(mx.confirmPreset(), mx.view['message'])
            self.assertTrue(mx.undoPreset(), mx.view['message'])

            saved_name = f'Wave4 Preset {self.work.name}'
            self.assertTrue(mx.savePreset(saved_name, False), mx.view['message'])
            saved_row = next((p for p in mx.presets if p.get('label') == saved_name or p.get('name') == saved_name), None)
            self.assertIsNotNone(saved_row, list(mx.presets))
            exported_preset = self.work / 'wave4.vwa-preset.json'
            mx.exportPreset(QUrl.fromLocalFile(str(exported_preset)), saved_row['key'])
            self.assertTrue(exported_preset.is_file())
            mx.importPresets(QUrl.fromLocalFile(str(exported_preset)))

            # --- W4-04 (locks W4-BUG-02): WASAPI hot-swap 1-shot auto-reconnect + budget exhaustion ---
            problems: list[tuple] = []
            reconnect_calls: list[bool] = []
            fake_player = types.SimpleNamespace(
                play_intent=True,
                failed=False,
                available=True,
                ready=True,
                loading=False,
                generation='gen-1',
                duration=lambda: 1400,
                position=lambda: 0,
                poll=lambda: None,
                release=lambda: None,
                close=lambda *a: None,
                set_play_intent=lambda v: reconnect_calls.append(bool(v)),
            )
            mx._player = fake_player
            mx._reconnect_guard.reset()
            mx._reconnect_guard.backoff_ms = 5
            with mock.patch.object(bridge, 'report_problem', side_effect=lambda *a: problems.append(a)), \
                 mock.patch.object(mx, '_refresh_preview', side_effect=lambda: reconnect_calls.append(True)):
                # 1st transient WASAPI error during playback -> auto-reconnect, NO diagnostic problem
                mx._player_error('AUDCLNT_E_DEVICE_INVALIDATED: wasapi playback device disconnected')
                self.assertIn('自动重连', mx.view['message'])
                self.assertEqual(len(problems), 0)
                # Let QTimer.singleShot(5ms) fire
                for _ in range(15):
                    self.app.processEvents()
                    if reconnect_calls:
                        break
                    import time
                    time.sleep(0.01)
                self.assertTrue(reconnect_calls, 'Expected auto-reconnect timer to trigger _refresh_preview')

                # 2nd consecutive WASAPI error without _player_prepared -> budget exhausted, reports error!
                mx._player_error('AUDCLNT_E_DEVICE_INVALIDATED: wasapi playback device disconnected')
                self.assertEqual(len(problems), 1)
                self.assertIn('AUDCLNT_E_DEVICE_INVALIDATED', mx.view['message'])

                # After _player_prepared succeeds, reconnect budget resets cleanly
                mx._player_prepared()
                self.assertEqual(mx._reconnect_guard._attempts, 0)

            mx._player = None

            # --- W4-05 (locks W4-BUG-03): \\?\ extended path safety on openResult & openOutputFolder + video handoff ---
            out_wav = _write_wav(self.work / 'mix_result.wav', freq=440.0, dur=0.5, amp=0.25, stereo=True)
            mx._state.result = r'\\?\\'[:-1] + str(out_wav)
            opened_urls: list[QUrl] = []
            with mock.patch.object(QDesktopServices, 'openUrl', side_effect=lambda u: (opened_urls.append(u), True)[1]):
                mx.openResult()
                mx.openOutputFolder()
            self.assertEqual(len(opened_urls), 2)
            for u in opened_urls:
                self.assertNotIn('%3F', u.toString(), u.toString())
            self.assertEqual(Path(opened_urls[0].toLocalFile()), out_wav)
            self.assertEqual(Path(opened_urls[1].toLocalFile()), out_wav.parent)

            # Video handoff via composeVideo() with verified export receipt SHA-256
            from common import file_hash
            sha = file_hash(out_wav)
            mx._state.result = str(out_wav)
            mx._export_receipt = {'output': {'path': str(out_wav), 'sha256': sha}}
            mx._view['canComposeVideo'] = True
            with mock.patch.object(bridge, 'open_independent_mix', wraps=bridge.open_independent_mix) as spy_video:
                mx.composeVideo()
                self.app.processEvents()
                spy_video.assert_called_once_with(str(out_wav), sha)
        finally:
            self._teardown_workspace(engine_qml, window, bridge)

    def test_w4_06_qml_offscreen_multi_dpi_compact_and_theme_zero_overflow(self):
        """W4-06: Zero QML warnings & zero horizontal overflow across DPIs, compact 880/960/1050px, Dark/Light."""
        from PySide6.QtCore import QPointF, QUrl
        from PySide6.QtQuick import QQuickItem
        from quick_app import create_workspace

        v1 = _write_wav(self.work / 'vis1.wav', freq=261.63, dur=1.2, amp=0.20)
        v2 = _write_wav(self.work / 'vis2.wav', freq=329.63, dur=1.2, amp=0.18)
        bg = _write_wav(self.work / 'vis_bg.wav', freq=130.81, dur=1.2, amp=0.12, stereo=True)

        def collect_overflows(root_item: QQuickItem, max_w: float) -> list[tuple]:
            over = []
            def walk(item: QQuickItem):
                if isinstance(item, QQuickItem) and item.isVisible() and item.width() > 0 and item.height() > 0:
                    pos = item.mapToScene(QPointF(0, 0))
                    if pos.x() + item.width() > max_w + 2.0 and not item.clip():
                        over.append((item.objectName() or item.metaObject().className(),
                                     round(pos.x(), 1), round(pos.x() + item.width(), 1), max_w))
                    if not item.clip():
                        for child in item.childItems():
                            walk(child)
                elif isinstance(item, QQuickItem) and item.isVisible():
                    for child in item.childItems():
                        walk(child)
            walk(root_item)
            return over

        for vw, vh, scale, dark in (
            (1280, 720, 100, True),
            (1366, 768, 125, False),
            (1050, 620, 130, True),
            (960, 640, 130, False),
            (880, 640, 130, True),
        ):
            for _ in range(5):
                self.app.processEvents()
            before_warn = len(self.warnings)
            engine_qml, window, bridge, _modes = create_workspace(self.app, viewport=(vw, vh), scale=scale)
            try:
                if hasattr(bridge, 'setThemeMode'):
                    bridge.setThemeMode('dark' if dark else 'light')
                bridge.navigate('audio_mixdown')
                for _ in range(10):
                    self.app.processEvents()
                win_w = float(window.width())
                self.assertEqual(collect_overflows(window.contentItem(), win_w), [],
                                 f'Empty MixdownPage overflow at {vw}x{vh}@{scale}% dark={dark}')

                mx = bridge.mixdown
                mx.chooseSource('vocal-1', QUrl.fromLocalFile(str(v1)))
                mx.addTrack()
                mx.chooseSource('vocal-2', QUrl.fromLocalFile(str(v2)))
                mx.chooseSource('instrumental', QUrl.fromLocalFile(str(bg)))
                for _ in range(10):
                    self.app.processEvents()

                for expanded in (False, True):
                    mx.setExpanded(expanded)
                    for analysis in ('', 'eq', 'alignment', 'loudness'):
                        mx.setAnalysis(analysis)
                        for _ in range(6):
                            self.app.processEvents()
                        over = collect_overflows(window.contentItem(), win_w)
                        self.assertEqual(
                            over, [],
                            f'Mixdown overflow exp={expanded} an={analysis} at {vw}x{vh}@{scale}% dark={dark}: {over[:4]}',
                        )

                new_warnings = [w for w in self.warnings[before_warn:]
                                if any(k in w for k in ('QML', 'TypeError', 'ReferenceError', 'Binding loop'))]
                self.assertEqual(new_warnings, [], f'QML warnings at {vw}x{vh}@{scale}%: {new_warnings}')
            finally:
                self._teardown_workspace(engine_qml, window, bridge)


if __name__ == '__main__':
    unittest.main(verbosity=2)
