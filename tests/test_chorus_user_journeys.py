"""Comprehensive real-user-path regression suite for the Chorus workbench.

Covers:
1. Windows [WinError 32] worker.log cleanup lock resilience & Win32 Job Object process tree kill.
2. Full end-to-end QuickChorus user workflows:
   - Analyze & apply suggestions -> jump to review issue -> audition processed audio (with real subprocess render).
   - Solo alignment when jumping to another track's review issue + Loop pre-roll preservation.
   - Single-render commit+variant transition (no double-render cancellation race).
   - Variant A/B switching, MIDI guide audition, stale error clearing, monitor cancellation.
   - Piano Roll edits (shift, snap semitone/score, split, merge, draw, reset) + undo/redo while playing.
   - Phrase timing edits (anchors, move, slide, gain, clear) + accept/keep review issues (incl. transpose).
   - WASAPI device selection & 1-shot hot-swap auto-reconnect.
   - Project save/reopen, stem export, and mixdown handoff.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src' if (APP / 'src').is_dir() else APP
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import audio_device_recovery as adr
import chorus_model_worker as cmw


def _write_vocal_wav(path: Path, rate: int = 44100, *, freq_a: float = 264.6, freq_b: float = 297.0,
                     delay: float = 0.0, duration: float = 2.6) -> None:
    """Synthesize a clean two-note voiced vocal clip with gentle envelopes."""
    import numpy as np
    import soundfile as sf

    n = round(duration * rate)
    t = np.arange(n, dtype=np.float64) / rate - delay
    pcm = np.zeros(n, dtype=np.float64)
    for start, end, freq in ((0.35, 1.10, freq_a), (1.30, 2.05, freq_b)):
        mask = (t >= start) & (t < end)
        if not np.any(mask):
            continue
        local = t[mask] - start
        span = end - start
        env = np.minimum(1.0, local / 0.04) * np.minimum(1.0, (span - local) / 0.05)
        wave = (
            np.sin(2.0 * np.pi * freq * local)
            + 0.30 * np.sin(2.0 * np.pi * 2.0 * freq * local)
            + 0.10 * np.sin(2.0 * np.pi * 3.0 * freq * local)
        )
        pcm[mask] = 0.30 * env * wave
    sf.write(path, pcm.astype(np.float32), rate, subtype='FLOAT')


def _write_backing_wav(path: Path, rate: int = 44100, duration: float = 2.6) -> None:
    import numpy as np
    import soundfile as sf

    n = round(duration * rate)
    t = np.arange(n, dtype=np.float64) / rate
    stereo = np.column_stack([
        0.08 * np.sin(2.0 * np.pi * 130.81 * t),
        0.08 * np.sin(2.0 * np.pi * 196.00 * t),
    ]).astype(np.float32)
    sf.write(path, stereo, rate, subtype='FLOAT')


def _write_score_midi(path: Path) -> None:
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    tempo = mid.add_track()
    tempo.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))  # 120 BPM => 960 ticks/sec
    for name, notes in (
        ('Lead', ((60, 0.35, 1.10), (62, 1.30, 2.05))),
        ('Harmony', ((64, 0.35, 1.10), (65, 1.30, 2.05))),
    ):
        tr = mid.add_track()
        tr.append(mido.MetaMessage('track_name', name=name))
        cursor = 0
        for pitch, start, end in notes:
            a = round(start * 960)
            b = round(end * 960)
            tr.append(mido.Message('note_on', note=pitch, velocity=80, time=max(0, a - cursor)))
            tr.append(mido.Message('note_off', note=pitch, velocity=0, time=max(1, b - a)))
            cursor = b
    mid.save(path)


def _synthetic_curve(duration: float, midi_a: float, midi_b: float, *, delay: float = 0.0, step: float = 0.01):
    import numpy as np

    count = max(1, round(duration / step))
    curve = np.full(count, np.nan, dtype=np.float32)
    for start, end, pitch in ((0.35 + delay, 1.10 + delay, midi_a), (1.30 + delay, 2.05 + delay, midi_b)):
        a = max(0, round(start / step))
        b = min(count, round(end / step))
        curve[a:b] = pitch
    return curve, step


class TestWorkerTempCleanupAndLocks(unittest.TestCase):
    """Exact Bug 1 regression tests: [WinError 32] on worker.log must never crash playback."""

    def test_safe_rmtree_retries_transient_winerror32(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'model-retry'
            target.mkdir()
            (target / 'worker.log').write_text('ok', encoding='utf-8')
            real_rmtree = shutil.rmtree
            attempts = {'n': 0}

            def flaky_rmtree(path, *args, **kwargs):
                attempts['n'] += 1
                if attempts['n'] <= 2:
                    raise PermissionError(13, '另一个程序正在使用此文件，进程无法访问。', str(target / 'worker.log'))
                return real_rmtree(path, *args, **kwargs)

            with patch.object(cmw.shutil, 'rmtree', side_effect=flaky_rmtree):
                removed = cmw.safe_rmtree(target, retries=4, base_delay=0.005)
            self.assertTrue(removed)
            self.assertEqual(attempts['n'], 3)
            self.assertFalse(target.exists())

    def test_resilient_tempdir_never_raises_on_persistent_lock(self):
        with tempfile.TemporaryDirectory() as root:
            def always_locked(_path, *_args, **_kwargs):
                raise PermissionError(13, 'WinError 32 locked worker.log')

            with patch.object(cmw.shutil, 'rmtree', side_effect=always_locked):
                with cmw.resilient_tempdir(Path(root), 'model-', stale_seconds=0) as folder:
                    (folder / 'worker.log').write_text('done', encoding='utf-8')
                    saved_path = folder
            # Context manager exits cleanly without crashing caller
            self.assertTrue(saved_path.exists())

    def test_sweep_stale_tempdirs_only_cleans_old_matching_prefixes(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            stale = base / 'model-stale01'
            fresh = base / 'model-fresh02'
            unrelated = base / 'keep-old03'
            for folder in (stale, fresh, unrelated):
                folder.mkdir()
                (folder / 'worker.log').write_text('x', encoding='utf-8')
            old_ts = time.time() - 3600.0
            os.utime(stale, (old_ts, old_ts))
            os.utime(unrelated, (old_ts, old_ts))

            removed = cmw.sweep_stale_tempdirs(base, ('model-',), max_age_seconds=600.0)
            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(unrelated.exists())

    def test_model_batch_survives_locked_worker_log_and_cancels_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            worker_script = base / 'dummy_worker.py'
            worker_script.write_text(
                'import json, pathlib, sys, time\n'
                'req = pathlib.Path(sys.argv[1])\n'
                'spec = json.loads(req.read_text(encoding="utf-8"))\n'
                'print("starting dummy worker", flush=True)\n'
                'if spec.get("sleep"):\n'
                '    time.sleep(float(spec["sleep"]))\n'
                '(req.parent / "progress.txt").write_text("0", encoding="utf-8")\n'
                '(req.parent / "result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")\n',
                encoding='utf-8',
            )
            cache = base / 'work'
            real_rmtree = shutil.rmtree
            locked_calls = {'n': 0}

            def simulate_winerror32(path, *args, **kwargs):
                locked_calls['n'] += 1
                if locked_calls['n'] <= 2:
                    raise PermissionError(13, 'WinError 32', str(Path(path) / 'worker.log'))
                return real_rmtree(path, *args, **kwargs)

            with patch.object(cmw.shutil, 'rmtree', side_effect=simulate_winerror32):
                with cmw.model_batch(
                    worker_script, {'sleep': 0}, cache, lambda: None, lambda _i, _l: None, ['step'], 'failed'
                ) as folder:
                    data = (folder / 'result.json').read_text(encoding='utf-8')
            self.assertIn('"ok": true', data)
            self.assertGreaterEqual(locked_calls['n'], 3)

            # Cancellation test: check() raises InterruptedError mid-flight -> kills child cleanly
            started = time.monotonic()

            def cancel_soon():
                if time.monotonic() - started > 0.25:
                    raise InterruptedError('操作已取消。')

            with self.assertRaises(InterruptedError):
                with cmw.model_batch(
                    worker_script, {'sleep': 15}, cache, cancel_soon, lambda _i, _l: None, ['step'], 'failed'
                ):
                    pass
            self.assertLess(time.monotonic() - started, 4.0)

    def test_make_wasapi_output_forwards_resolved_device_index(self):
        captured = {}

        class _FakeOutput:
            def __init__(self, dll, rate, ch, buf, period, backend, device_index=-1):
                captured.update(dll=dll, rate=rate, ch=ch, buf=buf, period=period,
                                backend=backend, device_index=device_index)

        with patch.object(adr, 'resolve_device_index', return_value=3), \
             patch('mixdown_native_output.NativeOutput', _FakeOutput):
            adr.make_wasapi_output('fake.dll', 44100)
        self.assertEqual(captured['device_index'], 3)
        self.assertEqual(captured['backend'], 'wasapi')
        self.assertEqual(captured['rate'], 44100)


class TestQuickChorusUserJourneys(unittest.TestCase):
    """End-to-end QuickChorus controller & Qt6 offscreen user-path verification."""

    @classmethod
    def setUpClass(cls):
        cls._qt_available = False
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ['VWA_REALTIME_SILENT_PROBE'] = '1'
        try:
            from PySide6.QtCore import QCoreApplication
            from PySide6.QtGui import QGuiApplication
            cls._app = QGuiApplication.instance() or QGuiApplication([])
            cls._QCoreApplication = QCoreApplication
            cls._qt_available = True
        except Exception:
            cls._qt_available = False

    def _pump(self, predicate, *, timeout: float = 20.0, label: str = 'condition'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._QCoreApplication.processEvents()
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError(f'Timed out waiting for {label} after {timeout}s')

    def _make_controller(self):
        from PySide6.QtCore import QObject
        from quick_chorus import QuickChorus

        self._parent = QObject()
        ctrl = QuickChorus(self._parent)
        self.addCleanup(ctrl.close)
        return ctrl

    def test_full_chorus_user_journeys_end_to_end(self):
        if not self._qt_available:
            self.skipTest('PySide6 not available in this Python environment')

        from PySide6.QtCore import QUrl
        from chorus_notes import Suggestion
        import chorus_pitch
        import quick_chorus

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lead_wav = root / 'lead.wav'
            harm_wav = root / 'harmony.wav'
            backing_wav = root / 'backing.wav'
            score_mid = root / 'score.mid'
            # Deliberately ~20 cents sharp on Lead, ~35 cents flat & 30ms late on Harmony
            _write_vocal_wav(lead_wav, freq_a=264.6, freq_b=297.0, delay=0.0)
            _write_vocal_wav(harm_wav, freq_a=323.0, freq_b=342.0, delay=0.03)
            _write_backing_wav(backing_wav)
            _write_score_midi(score_mid)

            def fake_estimate_many(tracks, _cache, check, progress):
                out = {}
                for idx, tr in enumerate(tracks):
                    check()
                    progress(idx, f'提取音高 · {tr.name}')
                    if 'lead' in tr.name.lower():
                        out[tr.id] = _synthetic_curve(tr.duration, 60.22, 62.20, delay=0.0)
                    else:
                        out[tr.id] = _synthetic_curve(tr.duration, 63.65, 64.65, delay=0.03)
                return out

            ctrl = self._make_controller()
            with patch.object(quick_chorus, 'ROOT', root), \
                 patch.object(chorus_pitch, 'estimate_many', side_effect=fake_estimate_many):
                # 1. Import vocal tracks + backing + MIDI score
                ctrl.importAudio('vocal', [QUrl.fromLocalFile(str(lead_wav)), QUrl.fromLocalFile(str(harm_wav))])
                self._pump(lambda: not ctrl.worker.kind and len(ctrl.project.tracks) == 2, label='import vocals')
                ctrl.importAudio('backing', [QUrl.fromLocalFile(str(backing_wav))])
                self._pump(lambda: not ctrl.worker.kind and len(ctrl.project.tracks) == 3, label='import backing')
                ctrl.importScore(QUrl.fromLocalFile(str(score_mid)))
                self._pump(lambda: not ctrl.worker.kind and ctrl.project.score is not None, label='import score')
                ctrl.confirmScore(True)

                vocals = [t for t in ctrl.project.tracks if t.role == 'vocal']
                lead_id, harm_id = vocals[0].id, vocals[1].id
                # Bind distinct MIDI lanes explicitly
                lanes = ctrl.project.score.lanes
                ctrl.bindScore(lead_id, lanes[0].id)
                ctrl.bindScore(harm_id, lanes[1].id)

                # 2. Start playback on original so _session is active, verifying single-render on analyze()!
                ctrl.togglePlay()
                self._pump(lambda: not ctrl.monitor_worker.kind and ctrl.view['activeVariant'] == 'original',
                           label='initial original audition')
                self.assertFalse(ctrl.view['error'], f'Unexpected initial error: {ctrl.view["notice"]}')

                # 3. Exact User Bug Journey: "生成并应用建议" -> jump to issue -> audition processed
                # Count _request_audio calls triggered by analyze() completion (must be <= 1, never 2!)
                req_calls = {'n': 0}
                orig_req = ctrl._request_audio

                def counted_req():
                    req_calls['n'] += 1
                    return orig_req()

                with patch.object(ctrl, '_request_audio', side_effect=counted_req):
                    ctrl.analyze('both', 'all', True, True, False, 100.0, False, 88.0, 'midi')
                    self._pump(lambda: not ctrl.worker.kind, label='analyze & apply')
                self.assertEqual(ctrl.view['variant'], 'processed')
                self.assertGreater(len(ctrl.project.edits), 0)
                self.assertLessEqual(req_calls['n'], 1, 'analyze() done must not double-trigger _request_audio()')

                # Wait for real subprocess monitor render (model_batch -> chorus_audio.py -> monitor.npy)
                self._pump(lambda: not ctrl.monitor_worker.kind and ctrl.view['activeVariant'] == 'processed',
                           label='processed monitor render')
                self.assertFalse(ctrl.view['error'], f'Processed playback failed: {ctrl.view["notice"]}')

                # Pause before jumping to a review issue, and also inject a review issue on track B + transpose issue
                ctrl.togglePlay()
                self.assertFalse(ctrl.view['playing'])
                extra_issues = (
                    *ctrl._suggestions,
                    Suggestion(harm_id, 'pitch', 1.30, 2.05, 35.0, '待核对高音片段', True, True,
                               category='large_shift', severity='high'),
                    Suggestion(harm_id, 'pitch', 0.35, 2.05, -1200.0, '疑似整体偏高八度', True, True,
                               category='transpose', severity='high', target_octave=-12),
                )
                ctrl._suggestions = extra_issues
                harm_issue_idx = len(extra_issues) - 2
                transpose_issue_idx = len(extra_issues) - 1

                # 4. Journey B: Solo Track A -> focusIssue on Track B -> verify Solo aligns & Loop keeps pre-roll!
                ctrl.solo(lead_id)
                self.assertEqual(ctrl.view['solo'], lead_id)
                ctrl.focusIssue(harm_issue_idx)
                self.assertEqual(ctrl.view['selected'], harm_id)
                self.assertEqual(ctrl.view['solo'], harm_id, 'focusIssue must align active solo to target track')
                self.assertEqual(ctrl.view['mode'], 'pitch')
                preroll_pos = ctrl.position
                self.assertLess(preroll_pos, ctrl.view['rangeFirst'])

                # Enable Loop and press Play while simulating transient WinError 32 on worker.log cleanup!
                ctrl.setLoop(True)
                real_rmtree = shutil.rmtree
                lock_hits = {'n': 0}

                def flaky_worker_log_rmtree(path, *args, **kwargs):
                    if 'model-' in str(path) and lock_hits['n'] < 2:
                        lock_hits['n'] += 1
                        raise PermissionError(13, 'WinError 32 locked worker.log')
                    return real_rmtree(path, *args, **kwargs)

                with patch.object(cmw.shutil, 'rmtree', side_effect=flaky_worker_log_rmtree):
                    ctrl.togglePlay()
                    self.assertAlmostEqual(ctrl.position, preroll_pos, delta=0.05,
                                           msg='Loop togglePlay must preserve 120ms pre-roll after focusIssue')
                    self._pump(lambda: not ctrl.monitor_worker.kind and ctrl.view['playing']
                               and ctrl.view['activeVariant'] == 'processed',
                               label='audition after focusIssue with locked worker.log')
                self.assertFalse(ctrl.view['error'], f'Audition after jump failed: {ctrl.view["notice"]}')
                ctrl.setLoop(False)
                ctrl.solo(harm_id)  # un-solo

                # 5. Journey C: A/B variant toggles + MIDI audition + stale error banner self-clear
                for target_var in ('original', 'timing', 'pitch', 'processed'):
                    ctrl.setVariant(target_var)
                    self._pump(lambda tv=target_var: not ctrl.monitor_worker.kind and ctrl.view['activeVariant'] == tv,
                               label=f'variant {target_var}')
                    self.assertFalse(ctrl.view['error'], f'Variant {target_var} error: {ctrl.view["notice"]}')

                ctrl.setMidiAudition(True)
                self._pump(lambda: not ctrl.monitor_worker.kind and ctrl.view['midiAudition'], label='midi audition on')
                self.assertFalse(ctrl.view['error'])
                ctrl.setMidiAudition(False)
                self._pump(lambda: not ctrl.monitor_worker.kind and not ctrl.view['midiAudition'], label='midi audition off')

                # Stale error banner must clear automatically when subsequent playback succeeds
                ctrl._say('旧的播放错误', error=True)
                self.assertTrue(ctrl.view['error'])
                ctrl.setVariant('original')
                self._pump(lambda: not ctrl.monitor_worker.kind and ctrl.view['activeVariant'] == 'original',
                           label='clear stale error on success')
                self.assertFalse(ctrl.view['error'], 'Stale red error banner should clear when playback succeeds')

                # 6. Journey D: Piano Roll manual edits + undo/redo while playing!
                ctrl.selectTrack(lead_id)
                notes = ctrl._cached_vocal_notes(ctrl.project.track(lead_id))
                self.assertGreaterEqual(len(notes), 2)
                n0, n1 = notes[0], notes[1]
                ctrl.selectPitchNotes([[n0.start, n0.end]])
                ctrl.shiftPitchNotes([], 50.0)
                self.assertEqual(ctrl.view['variant'], 'processed')
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after shiftPitchNotes')
                self.assertFalse(ctrl.view['error'])

                ctrl.snapPitchNotesToSemitone([])
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after snapSemitone')
                ctrl.snapPitchNotesToScore([])
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after snapScore')

                # Split first note in half, then merge the two halves back
                mid_disp = (n0.start + n0.end) / 2.0 + ctrl.project.track(lead_id).offset
                ctrl.splitPitchNote(n0.start, n0.end, mid_disp)
                split_notes = ctrl._cached_vocal_notes(ctrl.project.track(lead_id))
                self.assertEqual(len(split_notes), len(notes) + 1)
                ctrl.mergePitchNotes([[split_notes[0].start, split_notes[0].end],
                                      [split_notes[1].start, split_notes[1].end]])
                merged_notes = ctrl._cached_vocal_notes(ctrl.project.track(lead_id))
                self.assertEqual(len(merged_notes), len(notes))

                # Hand-draw pitch curve & reset notes
                ctrl.drawPitch([{'time': 0.50, 'note': 60.5}, {'time': 0.85, 'note': 60.5}])
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after drawPitch')
                ctrl.resetPitchNotes([[n0.start, n0.end]], False)
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after resetPitchNotes')

                # Undo / Redo while playing
                edits_before_undo = ctrl.project.edits
                ctrl.undo()
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after undo')
                self.assertNotEqual(ctrl.project.edits, edits_before_undo)
                ctrl.redo()
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after redo')
                self.assertEqual(ctrl.project.edits, edits_before_undo)

                # Phrase timing edits & gain edit
                ctrl.setMode('phrase')
                ctrl.selectRange(0.20, 2.20)
                ctrl.addTimeAnchor(1.00)
                ctrl.moveTimeAnchor(1.00, 1.04)
                ctrl.slideSelection(15.0)
                ctrl.editSelection('gain', 1.5)
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after phrase edits')
                self.assertFalse(ctrl.view['error'])
                ctrl.clearSelectionEdits('timing')
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after clearSelectionEdits')

                # Re-attach test issues and exercise acceptIssueAt (normal + transpose) & keepIssueAt
                ctrl._suggestions = extra_issues
                ctrl._resolved = set()
                ctrl.keepIssueAt(harm_issue_idx)
                self.assertIn(harm_issue_idx, ctrl._resolved)
                ctrl.acceptIssueAt(transpose_issue_idx)
                self.assertIn(transpose_issue_idx, ctrl._resolved)
                self.assertEqual(ctrl.project.track(harm_id).score_octave, -12)
                self._pump(lambda: not ctrl.monitor_worker.kind, label='after transpose issue accept')
                self.assertFalse(ctrl.view['error'])

                # 6b. autoTuneToScore + loopSelectedNotes (with cross-track solo alignment) + focusNextReviewNote
                ctrl.selectTrack(harm_id)
                ctrl.autoTuneToScore('track', 'both')
                self._pump(lambda: not ctrl.worker.kind, label='autoTuneToScore track both')
                self.assertFalse(ctrl.view['error'], f'autoTuneToScore failed: {ctrl.view["notice"]}')
                self._pump(lambda: not ctrl.monitor_worker.kind, label='monitor after autoTuneToScore')
                ctrl.solo(lead_id)
                h_notes = ctrl._cached_vocal_notes(ctrl.project.track(harm_id))
                self.assertGreaterEqual(len(h_notes), 1)
                ctrl.selectPitchNotes([[h_notes[0].start, h_notes[0].end]])
                ctrl.loopSelectedNotes()
                self.assertEqual(ctrl.view['solo'], harm_id, 'loopSelectedNotes must align active solo to selected track')
                self._pump(lambda: not ctrl.monitor_worker.kind and ctrl.view['playing'], label='loopSelectedNotes play')
                self.assertFalse(ctrl.view['error'])
                ctrl.setLoop(False)
                ctrl.solo(harm_id)  # un-solo
                ctrl.focusNextReviewNote()
                self.assertFalse(ctrl.view['error'])

                # 7. Journey E: Rapid A->B->A switch mid-render (must recover, NOT abort!) + user cancel
                if ctrl.view['playing']:
                    ctrl.togglePlay()
                self._pump(lambda: not ctrl.monitor_worker.kind, label='idle before rapid switch')
                ctrl._cache.clear()
                calls = {'n': 0}

                def slow_then_fast_render(proj, variant, solo, cache, check, progress):
                    import numpy as np
                    calls['n'] += 1
                    if calls['n'] == 1:
                        for _ in range(40):
                            check()
                            time.sleep(0.02)
                    return np.zeros((proj.frames, 2), dtype=np.float32)

                with patch('chorus_audio.render_monitor', side_effect=slow_then_fast_render):
                    ctrl.setVariant('processed')
                    ctrl.togglePlay()
                    self._pump(lambda: bool(ctrl.monitor_worker.kind), label='render #1 started')
                    # Switch away to timing (cancels #1), then immediately back to processed!
                    ctrl.setVariant('timing')
                    ctrl.setVariant('processed')
                    self._pump(lambda: not ctrl.monitor_worker.kind and ctrl.view['activeVariant'] == 'processed',
                               label='rapid A->B->A recovery')
                    self.assertTrue(ctrl.view['playing'], 'Rapid A->B->A switch must not abort playback')
                    self.assertFalse(ctrl.view['error'])

                if ctrl.view['playing']:
                    ctrl.togglePlay()
                self._pump(lambda: not ctrl.monitor_worker.kind, label='idle before slow render')
                ctrl._cache.clear()

                def slow_render(*_args, **kwargs):
                    check = _args[4] if len(_args) > 4 else kwargs.get('check')
                    for _ in range(80):
                        if check:
                            check()
                        time.sleep(0.02)
                    raise AssertionError('Should have been cancelled')

                with patch('chorus_audio.render_monitor', side_effect=slow_render):
                    ctrl.togglePlay()
                    self._pump(lambda: bool(ctrl.monitor_worker.kind), label='slow monitor start')
                    ctrl.cancel()
                    self._pump(lambda: not ctrl.monitor_worker.kind, label='slow monitor cancelled')
                self.assertFalse(ctrl.view['playing'])
                self.assertFalse(ctrl.view['error'], f'Cancel must not show red error: {ctrl.view["notice"]}')

                # 8. Journey F: WASAPI hot-swap 1-shot auto-reconnect
                class _DisconnectedSession:
                    def snapshot(self):
                        return {'error': 'WASAPI playback device disconnected unexpectedly', 'state': 'error'}
                    def close(self):
                        pass

                ctrl._session = _DisconnectedSession()
                ctrl._intent = True
                ctrl._reconnect_guard = adr.DeviceReconnectGuard(max_attempts=1, cooldown_seconds=2.0, backoff_ms=10)
                with patch.object(ctrl, '_request_audio'):
                    ctrl._clock()
                    self.assertTrue(ctrl._intent, 'First transient WASAPI disconnect must trigger auto-reconnect')
                    self.assertFalse(ctrl.view['error'])
                    # Second immediate disconnect inside cooldown stops cleanly with red error (no infinite loop)
                    ctrl._session = _DisconnectedSession()
                    ctrl._clock()
                    self.assertFalse(ctrl._intent)
                    self.assertTrue(ctrl.view['error'])

                # 9. Journey G: Save .vwachorus -> New -> Reopen -> Export Stems -> Send to Mixdown
                proj_file = root / 'chorus_test.vwachorus'
                ctrl.saveProject(QUrl.fromLocalFile(str(proj_file)))
                self.assertTrue(proj_file.is_file())
                self.assertFalse(ctrl.dirty)
                saved_tracks = len(ctrl.project.tracks)
                saved_edits = len(ctrl.project.edits)

                ctrl.newProject(True)
                self.assertEqual(len(ctrl.project.tracks), 0)
                ctrl.openProject(QUrl.fromLocalFile(str(proj_file)), True)
                self._pump(lambda: not ctrl.worker.kind and len(ctrl.project.tracks) == saved_tracks,
                           label='reopen project')
                self.assertEqual(len(ctrl.project.edits), saved_edits)

                export_dir = root / 'exports'
                export_dir.mkdir()
                ctrl.exportStems(QUrl.fromLocalFile(str(export_dir)), True, True, 'processed', False, True)
                self._pump(lambda: not ctrl.worker.kind and ctrl._export is not None, label='export stems')
                self.assertFalse(ctrl.view['error'], f'Export stems failed: {ctrl.view["notice"]}')
                self.assertTrue((ctrl._export / '合唱干声.wav').is_file())

                handoffs = []
                ctrl.mixdownHandoffReady.connect(handoffs.append)
                ctrl.sendToMixdown(True, 'processed', False, True)
                self._pump(lambda: not ctrl.worker.kind and len(handoffs) == 1, label='send to mixdown')
                self.assertFalse(ctrl.view['error'], f'Mixdown handoff failed: {ctrl.view["notice"]}')
                self.assertEqual(len(handoffs[0]['vocals']), 2)
                self.assertTrue(bool(handoffs[0].get('backing')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
