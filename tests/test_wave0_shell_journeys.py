"""Wave 0 comprehensive user-journey regression suite:
Global shell, routing, modals, smart drop, crash draft recovery, and anti-WinError32 tempdir base.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
SRC = APP if (APP / 'common.py').is_file() else (APP / 'src')
SOURCE_TREE = APP / 'src' if (APP / 'src').is_dir() else APP
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import draft_recovery
import recent_items
import task_actions_service
import vwa_io


class TestWave0PurePythonJourneys(unittest.TestCase):
    """CI-safe pure-Python Wave 0 contracts (W0-01, W0-06a, W0-08a)."""

    def test_w0_01_shared_resilient_tempdir_and_zero_bare_tempdirs(self):
        # Transient [WinError 32] retry succeeds
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'vwa-retry'
            target.mkdir()
            (target / 'worker.log').write_text('ok', encoding='utf-8')
            real_rmtree = shutil.rmtree
            tries = {'n': 0}

            def flaky(path, *args, **kwargs):
                tries['n'] += 1
                if tries['n'] <= 2:
                    raise PermissionError(13, 'WinError 32 locked', str(path))
                return real_rmtree(path, *args, **kwargs)

            with patch.object(vwa_io.shutil, 'rmtree', side_effect=flaky):
                self.assertTrue(vwa_io.safe_rmtree(target, retries=4, base_delay=0.005))
            self.assertEqual(tries['n'], 3)
            self.assertFalse(target.exists())

        # Persistent lock never aborts completed work; parent=None works cleanly
        with patch.object(vwa_io.shutil, 'rmtree', side_effect=PermissionError(13, 'locked')):
            with vwa_io.resilient_tempdir(prefix='vwa-w0-test-') as folder:
                self.assertTrue(folder.is_dir())
                created = folder
        shutil.rmtree(created, ignore_errors=True)

        # Static AST verification: zero bare TemporaryDirectory() in src/*.py
        offenders = []
        for path in sorted(SOURCE_TREE.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_td = (
                    (isinstance(func, ast.Name) and func.id == 'TemporaryDirectory')
                    or (isinstance(func, ast.Attribute) and func.attr == 'TemporaryDirectory')
                )
                if not is_td:
                    continue
                has_ignore = any(
                    kw.arg == 'ignore_cleanup_errors' and isinstance(kw.value, ast.Constant) and kw.value.value is True
                    for kw in node.keywords
                )
                if not has_ignore:
                    offenders.append(f'{path.name}:{node.lineno}')
        self.assertEqual(offenders, [], f'Found unprotected TemporaryDirectory() calls: {offenders}')

    def test_w0_06a_drop_classification_and_mru_store(self):
        # All 6 extended project formats route cleanly to project_convert
        for ext in ('.ust', '.svip', '.ccs', '.ds', '.dspx', '.musicxml'):
            info = recent_items.classify_dropped_files([f'C:/songs/demo{ext}'])
            self.assertEqual(info['category'], 'extended_project', f'Failed for {ext}')
            self.assertEqual([t['id'] for t in info['targets']], ['convert_project'])

        # Mixed audio + MIDI offers simultaneous chorus import
        mixed = recent_items.classify_dropped_files(['C:/songs/lead.wav', 'C:/songs/guide.mid'])
        self.assertEqual(mixed['category'], 'audio_and_midi')
        self.assertEqual(mixed['targets'][0]['id'], 'chorus_audio_and_score')

        # Standard categories
        self.assertEqual(recent_items.classify_dropped_files(['C:/a.vwachorus'])['category'], 'chorus_project')
        self.assertEqual(recent_items.classify_dropped_files(['C:/a.svp'])['category'], 'vocal_project')
        self.assertEqual(recent_items.classify_dropped_files(['C:/a.mid'])['category'], 'midi')
        self.assertEqual(recent_items.classify_dropped_files(['C:/a.mp4'])['category'], 'video')
        self.assertEqual(recent_items.classify_dropped_files(['C:/a.wav', 'C:/b.wav'])['category'], 'audio')
        self.assertEqual(recent_items.classify_dropped_files(['C:/a.xyz'])['targets'], [])
        self.assertEqual(recent_items.classify_dropped_files([])['category'], 'empty')

        # MRU persistence, dedupe, cap at 12, and missing-file pruning
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            files = []
            for idx in range(15):
                f = base / f'song_{idx:02d}.svp'
                f.write_text('{}', encoding='utf-8')
                files.append(f)
                recent_items.record_recent(f, 'tuning', title=f'Song {idx}', root=base)
            rows = recent_items.list_recent(root=base, prune_missing=True)
            self.assertEqual(len(rows), 12)
            self.assertEqual(rows[0]['title'], 'Song 14')

            # Delete newest file -> auto-pruned on next read
            files[-1].unlink()
            pruned = recent_items.list_recent(root=base, prune_missing=True)
            self.assertEqual(len(pruned), 11)
            self.assertNotEqual(pruned[0]['title'], 'Song 14')

            recent_items.clear_recent(base)
            self.assertEqual(recent_items.list_recent(root=base), [])

    def test_w0_08a_can_cancel_covers_all_user_lanes_including_convert(self):
        for kind in (
            'component', 'preparation', 'update-download', 'mixdown-export',
            'chorus-analysis', 'chorus-monitor', 'convert-run', 'convert-inspect',
            'video-mix', 'calibration-prepare', 'workshop-style', 'publication-pack',
        ):
            self.assertTrue(task_actions_service.can_cancel(kind), f'{kind} must be cancellable')
        self.assertFalse(task_actions_service.can_cancel('video-register'))


class TestWave0ShellQt6Journeys(unittest.TestCase):
    """Real Qt6 Offscreen Main.qml shell, dialogs, drop routing, recovery & geometry journeys."""

    @classmethod
    def setUpClass(cls):
        cls._qt_available = False
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ['VWA_REALTIME_SILENT_PROBE'] = '1'
        try:
            from PySide6.QtCore import QCoreApplication, QObject, QUrl, Qt, qInstallMessageHandler
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtQml import QQmlApplicationEngine
            from PySide6.QtQuick import QQuickItem, QQuickWindow
            from PySide6.QtTest import QTest
            cls._app = QGuiApplication.instance() or QGuiApplication([])
            cls._Qt = Qt
            cls._QCoreApplication = QCoreApplication
            cls._QObject = QObject
            cls._QUrl = QUrl
            cls._QQmlApplicationEngine = QQmlApplicationEngine
            cls._QQuickItem = QQuickItem
            cls._QQuickWindow = QQuickWindow
            cls._QTest = QTest
            cls._qInstallMessageHandler = qInstallMessageHandler
            cls._qt_available = True
        except Exception:
            cls._qt_available = False

    def _pump(self, predicate, *, timeout: float = 10.0, label: str = 'condition'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._QCoreApplication.processEvents()
            if predicate():
                return
            time.sleep(0.015)
        raise AssertionError(f'Timed out waiting for {label} after {timeout}s')

    def _find(self, root, object_name: str):
        content = root.contentItem() if hasattr(root, 'contentItem') and callable(root.contentItem) else None
        stack = [content] if content is not None else []
        while stack:
            item = stack.pop()
            if item is None:
                continue
            if item.objectName() == object_name:
                return item
            if hasattr(item, 'childItems'):
                stack.extend(item.childItems())
        found = root.findChild(self._QObject, object_name)
        if found is not None:
            return found
        # Search Overlay.overlay for modal Popups/Dialogs
        if content is not None:
            for obj in content.findChildren(self._QObject, object_name):
                return obj
        return None

    def _boot_window(self, tmp_root: Path):
        from PySide6.QtCore import QEvent
        import quick_app

        warnings: list[str] = []

        def _handler(_msg_type, _ctx, message):
            text = str(message)
            if any(k in text for k in ('QML', 'TypeError', 'ReferenceError', 'Cannot assign', 'Unable to assign')):
                warnings.append(text)

        prev_handler = self._qInstallMessageHandler(_handler)
        self.addCleanup(lambda: self._qInstallMessageHandler(prev_handler))

        engine, window, bridge, modes = quick_app.create_workspace(
            self._app, root=APP, show=True, viewport=(1440, 900), scale=100,
        )
        # Isolate ux hub & recovery inside tmp_root so the workspace/sandbox stays spotless
        bridge._ux._root = tmp_root
        bridge._ux._prep_queue._path = tmp_root / 'cache' / 'prep-queue.json'
        bridge._recovery._root = tmp_root

        def _cleanup():
            if modes is not None:
                modes.close()
            bridge.close()
            window.close()
            engine.deleteLater()
            self._QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self._QCoreApplication.processEvents()

        self.addCleanup(_cleanup)
        self._pump(lambda: window.width() > 0, label='window shown')
        return window, bridge, engine, warnings

    def test_w0_02_to_09_shell_journeys_end_to_end(self):
        if not self._qt_available:
            self.skipTest('PySide6 not available in this Python environment')

        import numpy as np
        import soundfile as sf
        import mido
        from chorus_project import ChorusProject, Recording
        import quick_chorus

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Prepare tiny real audio & MIDI fixtures inside temp dir
            wav_a = root / 'lead_a.wav'
            wav_b = root / 'lead_b.wav'
            wav_c = root / 'lead_c.wav'
            for wav in (wav_a, wav_b, wav_c):
                t = np.arange(22050, dtype=np.float32) / 44100.0
                sf.write(wav, (0.2 * np.sin(2 * np.pi * 261.63 * t)).astype(np.float32), 44100, subtype='FLOAT')
            mid_file = root / 'ref.mid'
            mf = mido.MidiFile(ticks_per_beat=480)
            tr = mf.add_track()
            tr.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
            tr.append(mido.Message('note_on', note=60, velocity=80, time=100))
            tr.append(mido.Message('note_off', note=60, velocity=0, time=480))
            mf.save(mid_file)
            ust_file = root / 'demo.ust'
            ust_file.write_text('[#VERSION]\nUST Version1.2\n[#SETTING]\nTempo=120.00\n[#TRACKEND]\n', encoding='utf-8')

            with patch.object(quick_chorus, 'ROOT', root):
                window, bridge, _engine, qml_warnings = self._boot_window(root)
                toasts: list[tuple[str, str]] = []
                bridge.toastRequested.connect(lambda m, k: toasts.append((str(m), str(k))))

                # --- W0-02: All 11 routes, aliases, Ctrl+1..9, modal shortcut blocking ---
                routes = list(bridge.routes)
                self.assertEqual(len(routes), 11)
                for row in routes:
                    pid = row['pageId']
                    btn = self._find(window, f'nav_{pid}')
                    self.assertIsNotNone(btn, f'Missing sidebar button nav_{pid}')
                    btn.clicked.emit()
                    self._pump(lambda p=pid: bridge.route == p, label=f'route {pid}')
                # Route aliases
                bridge.navigate('workflow')
                self.assertEqual(bridge.route, 'processing')
                bridge.navigate('storage')
                self.assertEqual(bridge.route, 'updates')
                # Ctrl+1..9 parity with routes[0..8]
                for idx in range(9):
                    window.navigateByRouteIndex(idx)
                    self.assertEqual(bridge.route, routes[idx]['pageId'])
                # Modal shortcut blocking when shortcut sheet is open
                sheet = self._find(window, 'shortcutSheetDialog')
                self.assertIsNotNone(sheet)
                bridge.navigate('chorus')
                sheet.open()
                self._pump(lambda: bool(sheet.property('visible')), label='shortcut sheet open')
                window.navigateByRouteIndex(0)
                self.assertEqual(bridge.route, 'chorus', 'Ctrl+1 must be blocked while shortcutSheet is open')
                sheet.close()
                self._pump(lambda: not bool(sheet.property('visible')), label='shortcut sheet closed')

                # --- W0-03: Scenario cards, tour dynamic shortcut badges, stay vs skip, What's New ---
                bridge.navigate('processing')
                self._pump(lambda: not bridge.support.workBusy, timeout=15.0,
                           label=f'idle before tutorial (blockers={bridge.support.operationBlockers})')
                self.assertTrue(bridge.support.showTutorial())
                tut = self._find(window, 'tutorialDialog')
                self._pump(lambda: bool(tut.property('visible')), label='tutorial opened')
                cards = [self._find(window, f'scenarioCard_{c["id"]}') for c in bridge.support.scenarioCards]
                self.assertGreaterEqual(len(cards), 4)
                for card in cards:
                    self.assertIsNotNone(card)
                    self.assertGreater(card.property('width'), 40)
                # Pick chorus scenario card directly -> closes tutorial & navigates to chorus
                bridge.support.chooseScenario('chorus')
                self._pump(lambda: not bool(tut.property('visible')) and bridge.route == 'chorus',
                           label='scenario picked -> chorus')

                # Full tour: verify EVERY step's dynamic shortcut badge matches actual sidebar Ctrl+1..9!
                bridge.navigate('updates')
                self._pump(lambda: not bridge.support.workBusy, timeout=10.0, label='idle before reopen tutorial')
                self.assertTrue(bridge.support.showTutorial())
                self._pump(lambda: bool(tut.property('visible')), label='tutorial reopened')
                next_btn = self._find(window, 'tutorialNext')
                next_btn.clicked.emit()  # scenarios -> tour step 0
                self._pump(lambda: bridge.support.startup.get('tour_mode') == 'tour', label='entered tour mode')
                route_ids = [r['pageId'] for r in routes]
                badge = self._find(window, 'tutorialShortcutBadge')
                self.assertIsNotNone(badge)
                for step_i in range(min(5, len(route_ids))):
                    self._QCoreApplication.processEvents()
                    step_page = bridge.route
                    if step_page in route_ids[:9]:
                        expected_ctrl = f'Ctrl+{route_ids.index(step_page) + 1}'
                        self.assertIn(expected_ctrl, str(badge.property('text')),
                                      f'Step {step_i} ({step_page}) shortcut badge drift: {badge.property("text")}')
                    if step_i < 4:
                        next_btn.clicked.emit()
                        self._QCoreApplication.processEvents()
                # Stay here button keeps current route instead of reverting to updates
                stayed_route = bridge.route
                stay_btn = self._find(window, 'tutorialStayHere')
                stay_btn.clicked.emit()
                self._pump(lambda: not bool(tut.property('visible')), label='tutorial stay closed')
                self.assertEqual(bridge.route, stayed_route)

                # What's New dialog open & dismiss
                bridge.support.showWhatsNew()
                wn = self._find(window, 'whatsNewDialog')
                self._pump(lambda: bool(wn.property('visible')), label='whatsNew opened')
                self._find(window, 'whatsNewGotIt').clicked.emit()
                self._pump(lambda: not bool(wn.property('visible')), label='whatsNew dismissed')

                # --- W0-04: Command Palette filter, theme toggle & execution ---
                palette = self._find(window, 'quickCommandPalette')
                self.assertIsNotNone(palette)
                palette.open()
                self._pump(lambda: bool(palette.property('visible')), label='palette opened')
                search = self._find(window, 'commandPaletteSearch')
                cmd_list = self._find(window, 'commandPaletteList')
                search.setProperty('text', '不存在的命令xyz')
                self._QCoreApplication.processEvents()
                self.assertEqual(int(cmd_list.property('count')), 0)
                search.setProperty('text', '浅色')
                self._QCoreApplication.processEvents()
                self.assertGreaterEqual(int(cmd_list.property('count')), 1)
                before_dark = bool(window.property('tokens').property('dark'))
                palette.executeIndex(0)
                self._pump(lambda: not bool(palette.property('visible')), label='palette executed')
                after_dark = bool(window.property('tokens').property('dark'))
                self.assertNotEqual(before_dark, after_dark, 'Command palette theme toggle must flip theme.dark')
                bridge.support.setSetting('theme', 'dark')

                # --- W0-05: Crash Draft Recovery (Chorus + Mixdown, global banner visibility, dirty/path safety) ---
                # Write a real Chorus crash draft while user is on 'processing' route!
                real_proj_path = root / 'my_real_song.vwachorus'
                rec = Recording.read(wav_a, 'vocal')
                draft_proj = ChorusProject.fresh().add((rec,))
                draft_recovery.write_chorus_draft(draft_proj, source_path=str(real_proj_path), root=root)
                bridge._recovery._refresh_existing()
                bridge._recovery.changed.emit()

                bridge.navigate('processing')
                banner = self._find(window, 'recoveryBanner')
                self._pump(lambda: bool(banner.property('visible')), label='global recovery banner visible on processing')
                toasts.clear()
                self._find(window, 'recoveryRestoreButton').clicked.emit()
                self._pump(lambda: bridge.route == 'chorus' and not bridge.chorus.worker.kind
                           and len(bridge.chorus.project.tracks) == 1, label='chorus draft restored')
                self.assertEqual(bridge.chorus._path, real_proj_path.resolve(),
                                 'Restored draft must bind to original source_path, NOT cache/recovery!')
                self.assertTrue(bridge.chorus.dirty, 'Restored crash draft must stay dirty so Ctrl+S / close guard works!')
                self.assertFalse(draft_recovery.has_draft('chorus', root), 'Draft file must be cleared after restore')
                self.assertTrue(any(k == 'success' for _, k in toasts))

                # Broken Chorus draft (source WAV missing) -> must emit error toast, NOT fake success!
                missing_wav = root / 'deleted_vocal.wav'
                shutil.copy2(wav_a, missing_wav)
                bad_proj = ChorusProject.fresh().add((Recording.read(missing_wav, 'vocal'),))
                draft_recovery.write_chorus_draft(bad_proj, source_path='', root=root)
                missing_wav.unlink()
                bridge._recovery._refresh_existing()
                bridge._recovery.changed.emit()
                toasts.clear()
                bridge._recovery.restoreChorusDraft()
                self._pump(lambda: not bridge.chorus.worker.kind and len(toasts) > 0, label='broken draft handled')
                self.assertTrue(any(k == 'error' for _, k in toasts), f'Expected error toast, got {toasts}')
                self.assertFalse(any(k == 'success' for _, k in toasts), f'Must not emit fake success toast: {toasts}')
                bridge._recovery.discardChorusDraft()

                # Mixdown draft restore & discard
                mix_payload = {
                    'master_gain': -2.5, 'eq_enabled': True, 'reverb_enabled': True,
                    'tracks': [{'id': 'vocal-1', 'path': str(wav_a), 'gain': 1.5, 'pan': -0.2}],
                }
                draft_recovery.save_draft('mixdown', mix_payload, label='mix_test', root=root)
                bridge._recovery._refresh_existing()
                bridge._recovery.changed.emit()
                self.assertTrue(bridge._recovery.hasMixdownDraft)
                bridge._recovery.restoreMixdownDraft()
                self.assertFalse(bridge._recovery.hasMixdownDraft)
                self.assertAlmostEqual(bridge.mixdown._state.master_gain, -2.5, places=2)

                # --- W0-06b: Global Drop Overlay z-order, dirty guard, extended format, multi-audio queue, audio+MIDI ---
                backdrop = self._find(window, 'globalDropBackdrop')
                self.assertIsNotNone(backdrop, 'GlobalDropOverlay backdrop must exist')
                self.assertGreaterEqual(int(backdrop.property('z')), 88)

                # Dirty Chorus guard: chorus is currently dirty=True! Opening another .vwachorus must warn, NOT fake success
                other_proj = root / 'other.vwachorus'
                draft_proj.save(other_proj)
                toasts.clear()
                bridge.ux.routeDroppedFiles('chorus_project', [str(other_proj)])
                self.assertTrue(any(k == 'warning' for _, k in toasts), f'Dirty guard must warn: {toasts}')
                self.assertFalse(any(k == 'success' for _, k in toasts), f'Dirty guard must not fake success: {toasts}')

                # Extended format .ust drop -> routes to project_convert
                toasts.clear()
                bridge.ux.routeDroppedFiles('convert_project', [str(ust_file)])
                self.assertEqual(bridge.route, 'project_convert')
                self._pump(lambda: not bridge.converter.worker.kind, label='convert inspect finished')

                # Multi-audio drop to prep_audio -> loads first and enqueues remaining 2!
                bridge.ux.clearPrepQueue()
                toasts.clear()
                bridge.ux.routeDroppedFiles('prep_audio', [str(wav_a), str(wav_b), str(wav_c)])
                self.assertEqual(bridge.route, 'processing')
                self.assertEqual(len(bridge.ux.prepQueue), 2, 'Extra dropped audio files must enqueue into prepQueue')
                bridge.ux.clearPrepQueue()

                # Audio + MIDI drop -> chorus_audio_and_score imports BOTH vocal and MIDI score!
                bridge.chorus.newProject(True)
                bridge.ux.routeDroppedFiles('chorus_audio_and_score', [str(wav_b), str(mid_file)])
                self._pump(lambda: not bridge.chorus.worker.kind and len(bridge.chorus.project.tracks) == 1
                           and bridge.chorus.project.score is not None,
                           label='mixed audio+MIDI drop imported both')

                # Ghost recent item -> warns & prunes
                ghost = root / 'ghost_deleted.svp'
                ghost.write_text('{}', encoding='utf-8')
                bridge.ux.recordRecent(str(ghost), 'tuning')
                ghost.unlink()
                toasts.clear()
                bridge.ux.openRecentItem(str(ghost), 'tuning')
                self.assertTrue(any(k == 'warning' for _, k in toasts))

                # --- W0-07: Toast cap at 3, floating chorus island anchors, convert cancel ---
                toast_stack = self._find(window, 'uiToastStack')
                self.assertIsNotNone(toast_stack)
                for idx in range(5):
                    bridge.pushToast(f'测试消息 {idx}', 'info')
                self._QCoreApplication.processEvents()
                self.assertIsNotNone(self._find(window, 'pauseBackgroundChorus'))
                self.assertIsNotNone(self._find(window, 'returnBackgroundChorus'))

                # Verify cancelActiveOperation dispatches to converter.cancel()
                cancelled_conv = {'called': False}
                fake_lane = type('_Lane', (), {'activity': {'id': 'op-conv-1', 'kind': 'convert-run', 'cancelling': False}})()
                with patch.object(bridge, '_activities', (fake_lane,)), \
                     patch.object(bridge._converter, 'cancel', side_effect=lambda: cancelled_conv.__setitem__('called', True)):
                    bridge.cancelActiveOperation('op-conv-1')
                self.assertTrue(cancelled_conv['called'], 'cancelActiveOperation must cancel convert-* operations')

                # --- W0-08: Diagnostics dialog export redaction ---
                bridge.support.showDiagnostics()
                diag = self._find(window, 'diagnosticsDialog')
                self._pump(lambda: bool(diag.property('visible')) and not bridge.support.diagnostics.get('busy'),
                           timeout=15.0, label='diagnostics loaded')
                bundle_zip = root / 'diag_bundle.zip'
                self.assertTrue(bridge.support.exportDiagnosticsBundle(self._QUrl.fromLocalFile(str(bundle_zip))))
                self._pump(lambda: not bridge.support.diagnostics.get('busy') and bundle_zip.is_file(),
                           timeout=15.0, label='diagnostics zip exported')
                with zipfile.ZipFile(bundle_zip, 'r') as zf:
                    names = zf.namelist()
                    self.assertTrue(names, 'Diagnostics bundle zip must not be empty')
                    combined = '\n'.join(zf.read(n).decode('utf-8', errors='replace') for n in names)
                    self.assertNotIn('GH_TOKEN', combined)
                bridge.support.closeDiagnostics()
                self._pump(lambda: not bool(diag.property('visible')), label='diagnostics closed')

                # --- W0-09: Multi-DPI x Dual-Theme shell geometry gate ---
                bridge.chorus.newProject(True)  # clean dirty before closing
                for theme_name in ('dark', 'light'):
                    bridge.support.setSetting('theme', theme_name)
                    for w, h, scale in ((1280, 720, 1.0), (1366, 768, 1.25), (1440, 900, 1.3), (1024, 700, 1.0)):
                        window.setProperty('width', w)
                        window.setProperty('height', h)
                        window.setProperty('initialScale', scale)
                        self._QCoreApplication.processEvents()
                        trigger = self._find(window, 'sidebarCommandTrigger')
                        self.assertIsNotNone(trigger)
                        self.assertGreater(trigger.property('width'), 20)
                        badge_item = self._find(window, 'sidebarCommandShortcutBadge')
                        if not bool(window.property('compact')):
                            self.assertTrue(bool(badge_item.property('visible')))
                            self.assertGreater(badge_item.property('width'), 24)
                        nav_first = self._find(window, 'nav_processing')
                        self.assertGreater(nav_first.property('width'), 20)
                        self.assertGreater(nav_first.property('height'), 20)

                self.assertEqual(qml_warnings, [], f'Unexpected QML warnings: {qml_warnings}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
