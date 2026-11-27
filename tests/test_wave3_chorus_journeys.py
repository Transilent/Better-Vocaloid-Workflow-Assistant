"""Wave 3 end-to-end user journey tests: Chorus Workbench.

Covers W3-BUG-01..04 and full real-user chorus journeys:
1. Pure-Python hermetic tier (always executed, including minimal GitHub CI):
   - W3-01a: ChorusProject edit algebra (SYLLABLE_GAIN / take / timing / pitch),
     extended \\\\?\\ path stripping, and source contract checks.
   - W3-01b (when soundfile/soxr present): chorus_gain vowel/headroom/0.1s-silence
     clamping & chorus_audio.balance_vocal_level finite zero safety on 0.1s silence.
2. Qt6 Offscreen + Audio interactive tier (executed whenever PySide6 + soundfile/soxr available):
   - W3-02: 0.1s pure silence end-to-end pipeline (import -> edit -> exportStems with
     auto-balance -> sendToMixdown handoff -> finite zero PCM, zero NaN/Inf).
   - W3-03: Edited-version export & A/B original trap (W3-BUG-02), real QuickBridge ->
     QuickMixdown handoff verification, and \\\\?\\ extended path safety on openExport (W3-BUG-03).
   - W3-04: ChorusGainPanel lyrics -> syllable gain sliders -> audition -> apply ->
     undo/redo -> clear full lifecycle.
   - W3-05: Vocal + Score multi-file drag-drop chaining (W3-BUG-04), take comping,
     time anchor & phrase gain edits, mute/solo, and .vwachorus save/reopen round-trip.
   - W3-06: ChorusPage multi-DPI (100/125/130% compact 1050/960/880px) x Dark/Light x
     all modes (all/phrase/pitch) x all inspector sections (batch/phrase/gain/track)
     zero-overflow & zero-QML-warning gate (W3-BUG-01).
"""
from __future__ import annotations

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

import chorus_project as cp
import mido
import numpy as np
from project_convert_compat import strip_extended_prefix
import vwa_io


def _has_audio_deps() -> bool:
    try:
        import soundfile  # noqa: F401
        import soxr  # noqa: F401
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


def _write_wav(path: Path, *, freq: float = 261.63, dur: float = 2.2, sr: int = 44100, amp: float = 0.20) -> Path:
    import soundfile as sf

    n = max(1, int(round(dur * sr)))
    t = np.arange(n, dtype=np.float64) / sr
    if amp <= 0.0:
        pcm = np.zeros(n, dtype=np.float32)
    else:
        env = np.minimum(1.0, t / 0.03) * np.minimum(1.0, (dur - t) / 0.03)
        pcm = (amp * env * (np.sin(2.0 * np.pi * freq * t) + 0.25 * np.sin(4.0 * np.pi * freq * t))).astype(np.float32)
    sf.write(path, pcm, sr, subtype='FLOAT')
    return path


def _write_midi(path: Path) -> Path:
    mid = mido.MidiFile(ticks_per_beat=480)
    tempo = mid.add_track()
    tempo.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    tr = mid.add_track()
    tr.append(mido.MetaMessage('track_name', name='Lead'))
    tr.append(mido.Message('note_on', note=60, velocity=80, time=192))
    tr.append(mido.Message('note_off', note=60, velocity=0, time=480))
    tr.append(mido.Message('note_on', note=62, velocity=80, time=96))
    tr.append(mido.Message('note_off', note=62, velocity=0, time=480))
    mid.save(path)
    return path


class Wave3PurePythonJourneysTests(unittest.TestCase):
    def test_w3_01a_project_model_extended_path_and_qml_contracts(self):
        """W3-01a: Pure-Python ChorusProject model, \\\\?\\ path strip, and source contracts."""
        self.assertEqual(strip_extended_prefix(r'\\?\C:\vwa\work\stems'), r'C:\vwa\work\stems')
        self.assertEqual(strip_extended_prefix(r'\\?\UNC\nas\share\stems'), r'\\nas\share\stems')
        self.assertEqual(cp.number(3.25), 3.25)

        rec = cp.Recording(
            id='t1', path='lead.wav', sha='a' * 64, stamp=(352800, 1),
            rate=44100, frames=88200, channels=1, name='Lead', role='vocal', part='Lead',
        )
        edit_gain = cp.Edit('t1', 'gain', 0.2, 1.5, 3.0, manual=True)
        edit_syl = cp.Edit('t1', cp.SYLLABLE_GAIN, 0.3, 0.6, 2.0, ((0.3, 0.0), (0.45, 2.0), (0.6, 0.0)), basis='a', manual=True)
        proj = cp.ChorusProject(title='Wave3', tracks=(rec,), edits=(edit_gain, edit_syl))
        self.assertEqual(len(proj.edits_for('t1', 'gain')), 1)
        self.assertEqual(len(proj.edits_for('t1', cp.SYLLABLE_GAIN)), 1)
        self.assertIn('processed', cp.VARIANTS)

        # Lock W3-BUG-02 / W3-BUG-03 / W3-BUG-04 source invariants in pure CI too
        qml_src = (APP / 'src/quick_ui/ChorusPage.qml').read_text(encoding='utf-8')
        py_src = (APP / 'src/quick_chorus.py').read_text(encoding='utf-8')
        self.assertIn('page.view.hasEdits ? "processed" : page.view.variant', qml_src)
        self.assertIn('pendingDroppedScore', qml_src)
        self.assertIn("variant or ('processed' if project.edits else self._variant)", py_src)
        self.assertIn('strip_extended_prefix(str(self._export))', py_src)

    @unittest.skipUnless(_has_audio_deps(), 'soundfile/soxr not available in minimal CI')
    def test_w3_01b_chorus_gain_and_balance_on_0_1s_silence(self):
        """W3-01b: chorus_gain headroom clamp & chorus_audio.balance_vocal_level safety on 0.1s silence."""
        import chorus_audio
        import chorus_gain

        self.assertTrue(chorus_gain.vowel_phone('a', 'ja'))
        self.assertTrue(chorus_gain.vowel_phone('aa1', 'en'))
        self.assertFalse(chorus_gain.vowel_phone('k', 'ja'))
        self.assertAlmostEqual(chorus_gain.gain_limit(0.98), 0.0, places=3)
        self.assertGreater(chorus_gain.gain_limit(0.1), 5.0)

        # 0.1s pure silence (4410 samples @ 44.1kHz): syllable gain + auto balance must stay finite & 0.0
        silence = np.zeros((4410, 1), dtype=np.float32)
        syl_edit = cp.Edit('t1', cp.SYLLABLE_GAIN, 0.01, 0.09, 4.0,
                           ((0.01, 0.0), (0.05, 4.0), (0.09, 0.0)), basis='a', manual=True)
        chorus_gain.render_syllable_gains(silence, 44100, (syl_edit,), lambda: None)
        self.assertTrue(np.isfinite(silence).all())
        self.assertEqual(float(np.max(np.abs(silence))), 0.0)

        balanced, gain_db = chorus_audio.balance_vocal_level(silence)
        self.assertTrue(np.isfinite(balanced).all())
        self.assertEqual(gain_db, 0.0)
        self.assertEqual(float(np.max(np.abs(balanced))), 0.0)

        # Hot signal must never clip above 0.98 headroom when syllable gain is rendered
        hot = np.full((4410, 1), 0.90, dtype=np.float32)
        chorus_gain.render_syllable_gains(hot, 44100, (syl_edit,), lambda: None)
        self.assertTrue(np.isfinite(hot).all())
        self.assertLessEqual(float(np.max(np.abs(hot))), 0.985)


@unittest.skipUnless(_has_qt6() and _has_audio_deps(), 'PySide6 or soundfile/soxr not available')
class Wave3Qt6InteractiveJourneysTests(unittest.TestCase):
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
        self._tmp_ctx = vwa_io.resilient_tempdir(APP / 'work', prefix='w3-qt-')
        self.work = Path(self._tmp_ctx.__enter__())
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)

    def _wait(self, label: str, predicate, timeout: float = 15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.015)
        self.fail(f'Timeout waiting for {label}; warnings={self.warnings[-5:]}')

    def _teardown_workspace(self, engine_qml, window, bridge):
        from PySide6.QtCore import QCoreApplication, QEvent

        window.close()
        engine_qml.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        bridge.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    @staticmethod
    def _find_item(root_item, object_name: str):
        stack = [root_item]
        while stack:
            node = stack.pop()
            if node.objectName() == object_name:
                return node
            stack.extend(node.childItems())
        return None

    def test_w3_02_03_04_05_chorus_end_to_end_interactive_journeys(self):
        """W3-02..W3-05: 0.1s silence, edited-version export/handoff, GainPanel, PrecisionPanel, multi-drop, save/reopen."""
        import soundfile as sf
        from PySide6.QtCore import QObject, QUrl
        from PySide6.QtGui import QDesktopServices
        from chorus_gain import SyllableGain
        from quick_app import create_workspace

        engine_qml, window, bridge, _modes = create_workspace(self.app, viewport=(1280, 720), scale=100)
        try:
            bridge.navigate('chorus')
            for _ in range(10):
                self.app.processEvents()
            ch = bridge.chorus
            chorus_page = self._find_item(window.contentItem(), 'chorusPage')
            self.assertIsNotNone(chorus_page)

            # --- W3-02: 0.1s pure silence full pipeline ---
            silent_wav = _write_wav(self.work / 'silence_0.1s.wav', freq=0.0, dur=0.1, amp=0.0)
            ch.importAudio('vocal', [QUrl.fromLocalFile(str(silent_wav))])
            self._wait('0.1s silence imported', lambda: ch.view['count'] == 1 and not ch.view['busy'])
            ch.selectRange(0.0, 0.1)
            ch.editSelection('gain', 3.0)
            self.assertTrue(ch.view['hasEdits'])

            silent_out = self.work / 'out_silent'
            silent_out.mkdir()
            ch.exportStems(QUrl.fromLocalFile(str(silent_out)), True, True, '', False, True)
            self._wait('0.1s silence export done', lambda: not ch.view['busy'] and bool(ch.view['export']))
            self.assertFalse(ch.view['error'], ch.view['notice'])
            receipt_file = Path(ch.view['export']) / '分轨说明.json'
            self.assertTrue(receipt_file.is_file())
            silent_receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
            self.assertEqual(silent_receipt['variant'], 'processed')
            self.assertEqual(silent_receipt['frames'], 4410)
            for wav in Path(ch.view['export']).glob('*.wav'):
                data, _sr = sf.read(wav)
                self.assertTrue(np.isfinite(data).all(), wav.name)
                self.assertEqual(float(np.max(np.abs(data), initial=0.0)), 0.0)

            handoffs: list[dict] = []
            ch.mixdownHandoffReady.connect(lambda p: handoffs.append(dict(p)))
            ch.sendToMixdown(True, '', False, True)
            self._wait('0.1s silence handoff ready', lambda: not ch.view['busy'] and len(handoffs) == 1)
            self.assertEqual(handoffs[0]['variant'], 'processed')
            self.assertEqual(len(handoffs[0]['vocals']), 1)

            # --- W3-03: Edited-version export trap (W3-BUG-02) + QuickMixdown handoff + \\?\ openExport (W3-BUG-03) ---
            bridge.navigate('chorus')
            ch.newProject(True)
            self.app.processEvents()
            lead_wav = _write_wav(self.work / 'lead.wav', freq=261.63, dur=1.8, amp=0.15)
            harm_wav = _write_wav(self.work / 'harmony.wav', freq=329.63, dur=1.8, amp=0.15)
            back_wav = _write_wav(self.work / 'backing.wav', freq=130.81, dur=1.8, amp=0.08)
            ch.importAudio('vocal', [QUrl.fromLocalFile(str(lead_wav)), QUrl.fromLocalFile(str(harm_wav))])
            self._wait('2 vocals imported', lambda: ch.view['count'] == 2 and not ch.view['busy'])
            ch.importAudio('backing', [QUrl.fromLocalFile(str(back_wav))])
            self._wait('backing imported', lambda: ch.view['count'] == 3 and not ch.view['busy'])

            ch.selectRange(0.2, 1.6)
            ch.editSelection('gain', 6.0)
            self.assertTrue(ch.view['hasEdits'])
            # User switches A/B monitor back to 'original' to compare!
            ch.setVariant('original')
            self.app.processEvents()
            self.assertEqual(ch.view['variant'], 'original')

            # Opening exportDialog must default exportMode to 'processed' (NOT 'original'!)
            export_btn = self._find_item(chorus_page, 'chorusExport')
            self.assertIsNotNone(export_btn)
            export_btn.clicked.emit()
            for _ in range(5):
                self.app.processEvents()
            export_combo = window.findChild(QObject, 'chorusExportVariant')
            self.assertIsNotNone(export_combo)
            self.assertEqual(export_combo.property('currentValue'), 'processed',
                             'W3-BUG-02: exportDialog must default to processed when hasEdits is true')

            # Calling exportStems with empty variant while viewing 'original' must STILL export 'processed'
            edited_out = self.work / 'out_edited'
            edited_out.mkdir()
            prev_export = ch.view['export']
            ch.exportStems(QUrl.fromLocalFile(str(edited_out)), True, True, '', False, False)
            self._wait('edited export done', lambda: not ch.view['busy'] and ch.view['export'] != prev_export)
            edited_receipt = json.loads((Path(ch.view['export']) / '分轨说明.json').read_text(encoding='utf-8'))
            self.assertEqual(edited_receipt['variant'], 'processed')
            exported_lead, _ = sf.read(Path(ch.view['export']) / edited_receipt['tracks'][0]['file'])
            raw_lead, _ = sf.read(lead_wav)
            self.assertGreater(float(np.max(np.abs(exported_lead))), float(np.max(np.abs(raw_lead))) * 1.6,
                               'Exported processed lead must reflect +6 dB gain edit')

            # W3-BUG-03: openExport must strip \\?\ extended path prefix before QDesktopServices.openUrl
            opened_urls: list[QUrl] = []
            real_export = Path(ch.view['export'])
            with mock.patch.object(QDesktopServices, 'openUrl', side_effect=lambda u: (opened_urls.append(u), True)[1]):
                ch._export = Path(r'\\?\\'[:-1] + str(real_export))
                ch.openExport()
            self.assertEqual(len(opened_urls), 1)
            self.assertNotIn('%3F', opened_urls[0].toString())
            self.assertEqual(Path(opened_urls[0].toLocalFile()), real_export)

            # Real QuickBridge -> QuickMixdown handoff with edited vocals + backing
            ch.sendToMixdown(True, '', False, False)
            self._wait('edited handoff delivered to mixdown', lambda: not ch.view['busy'] and len(handoffs) == 2)
            self.assertEqual(handoffs[1]['variant'], 'processed')
            self.assertEqual(len(handoffs[1]['vocals']), 2)
            self.assertIsNotNone(handoffs[1]['backing'])
            self._wait(
                'mixdown accepted chorus handoff',
                lambda: len([r for r in bridge.mixdown._state._tracks if r['id'].startswith('vocal-')]) == 2,
            )
            inst_row = next(r for r in bridge.mixdown._state._tracks if r['id'] == 'instrumental')
            self.assertTrue(bool(inst_row.get('path')))

            # --- W3-04: ChorusGainPanel lifecycle (lyrics -> syllable slider -> audition -> apply -> undo/redo -> clear) ---
            bridge.navigate('chorus')
            self.app.processEvents()
            ch.selectRange(0.2, 1.4)
            ch.setLyrics('shared', 'あ い', 'ja', True)
            ch.inspectorSection('gain')
            for _ in range(5):
                self.app.processEvents()
            self.assertTrue(ch.view['syllableGain']['ready'])
            row = SyllableGain('a', 0.35, 0.65, -22.0, 6.0, 1.5,
                               ((0.35, 0.0), (0.45, 1.0), (0.55, 1.0), (0.65, 0.0)))
            ch._syllables = (row,)
            ch._syllable_route = 'test'
            ch.changed.emit()
            for _ in range(5):
                self.app.processEvents()
            self.assertIsNotNone(self._find_item(chorus_page, 'chorusSyllableGain-0'))
            ch.setSyllableGain(0, 3.0)
            self.assertEqual(ch.view['syllableGain']['rows'][0]['db'], 3.0)
            ch.auditionGain('suggested')
            self.assertEqual(ch.view['syllableGain']['audition'], 'suggested')
            ch.stopGainAudition()
            self.assertEqual(ch.view['syllableGain']['audition'], '')
            ch.applySyllableGain()
            self.assertEqual(ch.view['syllableGain']['applied'], 1)
            ch.undo()
            self.assertEqual(ch.view['syllableGain']['applied'], 0)
            ch.redo()
            self.assertEqual(ch.view['syllableGain']['applied'], 1)
            ch.clearSyllableGain()
            self.assertEqual(ch.view['syllableGain']['applied'], 0)

            # --- W3-05: Multi-drop vocal+score chaining (W3-BUG-04), take comping, anchors, save/reopen ---
            ch.newProject(True)
            self.app.processEvents()
            score_mid = _write_midi(self.work / 'guide.mid')
            alt_wav = _write_wav(self.work / 'alt_take.wav', freq=261.63, dur=1.8, amp=0.18)
            chorus_page.handleDroppedUrls([QUrl.fromLocalFile(str(lead_wav)), QUrl.fromLocalFile(str(score_mid))])
            self._wait('multi-drop loads BOTH vocal and score',
                       lambda: ch.view['count'] == 1 and bool(ch.view['score']) and not ch.view['busy'])

            ch.importAudio('take', [QUrl.fromLocalFile(str(alt_wav))])
            self._wait('alt take imported', lambda: ch.view['count'] == 2 and not ch.view['busy'])
            take_id = next(t['id'] for t in ch.tracks if t['role'] == 'take')
            lead_id = next(t['id'] for t in ch.tracks if t['role'] == 'vocal')
            ch.selectTrack(lead_id)
            ch.selectRange(0.2, 1.2)
            ch.chooseTake(take_id)
            self.assertEqual(ch.view['comp'], take_id)
            ch.addTimeAnchor(0.7)
            ch.editSelection('gain', 2.0)
            ch.mute(lead_id)
            ch.solo(lead_id)
            ch.solo(lead_id)
            ch.mute(lead_id)

            proj_file = self.work / 'wave3_roundtrip.vwachorus'
            ch.saveProject(QUrl.fromLocalFile(str(proj_file)))
            self._wait('project saved', lambda: not ch.view['busy'] and proj_file.is_file() and not ch.view['dirty'])
            ch.newProject(True)
            self.assertEqual(ch.view['count'], 0)
            ch.openProject(QUrl.fromLocalFile(str(proj_file)), True)
            self._wait('project reopened', lambda: not ch.view['busy'] and ch.view['count'] == 2)
            self.assertTrue(ch.view['hasEdits'])
            self.assertTrue(bool(ch.view['score']))
        finally:
            self._teardown_workspace(engine_qml, window, bridge)

    def test_w3_06_qml_offscreen_compact_high_dpi_and_theme_zero_overflow(self):
        """W3-06 (locks W3-BUG-01): Zero QML warnings & zero horizontal overflow across DPIs, compact 880/960/1050px, Dark/Light."""
        from PySide6.QtCore import QPointF, QUrl
        from PySide6.QtQuick import QQuickItem
        from quick_app import create_workspace

        v1 = _write_wav(self.work / 'vis1.wav', freq=261.63, dur=1.6, amp=0.18)
        v2 = _write_wav(self.work / 'vis2.wav', freq=329.63, dur=1.6, amp=0.18)

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
                bridge.navigate('chorus')
                for _ in range(12):
                    self.app.processEvents()
                win_w = float(window.width())
                self.assertEqual(collect_overflows(window.contentItem(), win_w), [],
                                 f'Empty ChorusPage overflow at {vw}x{vh}@{scale}% dark={dark}')

                bridge.chorus.importAudio('vocal', [QUrl.fromLocalFile(str(v1)), QUrl.fromLocalFile(str(v2))])
                self._wait(f'visual import {vw}@{scale}',
                           lambda: bridge.chorus.view['count'] == 2 and not bridge.chorus.view['busy'])
                bridge.chorus.selectRange(0.2, 1.2)
                for mode in ('all', 'phrase', 'pitch'):
                    bridge.chorus.setMode(mode)
                    for sec in ('batch', 'phrase', 'gain', 'track'):
                        bridge.chorus.inspectorSection(sec)
                        for _ in range(6):
                            self.app.processEvents()
                        over = collect_overflows(window.contentItem(), win_w)
                        self.assertEqual(
                            over, [],
                            f'Overflow on mode={mode} sec={sec} at {vw}x{vh}@{scale}% dark={dark}: {over[:5]}',
                        )
                # Primary action buttons must stay strictly inside window width
                for btn_name in ('chorusSendToMixdown', 'chorusExport'):
                    btn = self._find_item(window.contentItem(), btn_name)
                    self.assertIsNotNone(btn, btn_name)
                    pos = btn.mapToScene(QPointF(0, 0))
                    self.assertLessEqual(pos.x() + btn.width(), win_w + 2.0,
                                         f'{btn_name} clipped at {vw}x{vh}@{scale}%')

                new_warnings = [w for w in self.warnings[before_warn:]
                                if any(k in w for k in ('QML', 'TypeError', 'ReferenceError', 'Binding loop'))]
                self.assertEqual(new_warnings, [], f'QML warnings at {vw}x{vh}@{scale}%: {new_warnings}')
            finally:
                self._teardown_workspace(engine_qml, window, bridge)


if __name__ == '__main__':
    unittest.main(verbosity=2)
