"""Black-box contracts for the resident mixdown audition service."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True

APP = Path(__file__).resolve().parents[1]
DEPENDENCY_ENV = "VWA_MIX_TEST_PEDALBOARD_PATH"

if not os.environ.get(DEPENDENCY_ENV):
    installed = APP / "dependencies/optional/pedalboard/current/packages"
    if installed.is_dir():
        os.environ[DEPENDENCY_ENV] = str(installed)
    else:
        print("SKIP: " + DEPENDENCY_ENV + " is not set and Pedalboard is not installed.")
        raise SystemExit(0)


def _pedalboard_import_root(value):
    root = Path(value).expanduser()
    if not root.exists():
        raise RuntimeError(DEPENDENCY_ENV + " does not exist: " + str(root))
    candidates = [root]
    if root.is_dir():
        candidates.extend((root / "unpacked", root / "site-packages"))
        candidates.extend(sorted(path for path in root.iterdir() if path.is_dir()))
    for candidate in candidates:
        if (candidate / "pedalboard").is_dir():
            return candidate.resolve()
    raise RuntimeError(DEPENDENCY_ENV + " must identify an importable Pedalboard package")


try:
    pedalboard_root = _pedalboard_import_root(os.environ[DEPENDENCY_ENV])
except RuntimeError as error:
    print("ERROR: " + str(error))
    raise SystemExit(1)

sys.path[:0] = [str(pedalboard_root), str(APP / "src")]
try:
    import numpy as np
    import soundfile as sf
    from mixdown_audition_service import (
        MixdownAuditionService,
        PROTOCOL_VERSION,
        SERVICE_SCHEMA,
        TICK_HZ,
    )
except Exception as error:
    print("ERROR: cannot import resident service: " + type(error).__name__ + ": " + str(error))
    raise SystemExit(1)


def _tone(seconds, rate, frequency, amplitude, channels=1):
    samples = int(round(seconds * rate))
    axis = np.arange(samples, dtype=np.float64) / rate
    signal = (amplitude * np.sin(2 * np.pi * frequency * axis)).astype(np.float32)
    return np.repeat(signal[:, None], channels, axis=1) if channels > 1 else signal


def _write(path, audio, rate):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(audio, dtype=np.float32), int(rate), subtype="FLOAT")


def _fixture(root, seconds, rate):
    vocals = [root / "v1.wav", root / "v2.wav"]
    _write(vocals[0], _tone(seconds, rate, 190, 0.10), rate)
    _write(vocals[1], _tone(seconds, rate, 310, 0.08), rate)
    backing = root / "backing.wav"
    _write(
        backing,
        np.stack((
            0.06 * np.sin(2 * np.pi * 95 * np.arange(int(seconds * rate)) / rate),
            0.05 * np.sin(2 * np.pi * 137 * np.arange(int(seconds * rate)) / rate),
        ), axis=1).astype(np.float32),
        rate,
    )
    request = {
        "instrumental_path": str(backing),
        "output_path": str(root / "planned-output.wav"),
        "instrumental_db": -4.0,
        "vocals": [
            {"path": str(vocals[0]), "gain_db": -1.0, "offset_seconds": 0.0,
             "mute": False, "solo": False, "pan": -0.12, "reverb_send": 0.35},
            {"path": str(vocals[1]), "gain_db": -2.0, "offset_seconds": 0.0,
             "mute": False, "solo": False, "pan": 0.18, "reverb_send": 0.65},
        ],
        "eq_low_cut_hz": 80.0,
        "eq_presence_db": 1.0,
        "compression_strength": 0.35,
        "reverb_mix": 0.18,
        "reverb_profile": "natural",
        "preview_start_seconds": 0.0,
        "preview_duration_seconds": min(12.0, seconds),
        "eq_bands": [
            {"type": "highpass", "freq_hz": 85, "gain_db": 0, "q": 0.707,
             "enabled": True},
            {"type": "bell", "freq_hz": 2300, "gain_db": 2.0, "q": 0.8,
             "enabled": True},
        ],
        "reverb_predelay_ms": 20.0,
        "width": 0.85,
    }
    return request


def _pid_running(pid):
    if pid is None:
        return False
    if os.name == 'nt':
        result = subprocess.run(
            ['tasklist', '/FI', 'PID eq %d' % int(pid), '/FO', 'CSV', '/NH'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5.0,
            check=False,
        )
        return any(
            line.strip().startswith('"') and ('"%d"' % int(pid)) in line
            for line in result.stdout.splitlines()
        )
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _percentile(values, ratio):
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def _audition_artifacts(root):
    return sorted(
        path.name for path in Path(root).rglob("*")
        if path.name.startswith(".audition")
        or path.name.startswith("audition-encode-")
    )


class ResidentAuditionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mix-audition-service-")
        self.root = Path(self.temp.name)
        self.rate = 16000
        self.request = _fixture(self.root / "base", 2.0, self.rate)
        self.cache = self.root / "cache"
        self.services = []

    def tearDown(self):
        for service in reversed(self.services):
            try:
                service.close(timeout=3.0)
            except Exception:
                pass
        self.temp.cleanup()

    def _start(self):
        service = MixdownAuditionService(
            self.cache,
            extra_paths=(str(pedalboard_root), str(APP / "src")),
            startup_timeout=20.0,
            shutdown_timeout=8.0,
        )
        ready = service.start()
        self.services.append(service)
        return service, ready

    def _wait(self, service, predicate, timeout=20.0):
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            batch = service.poll(timeout=0.02)
            for event in batch:
                events.append(event)
                if predicate(event):
                    return event, events
                if event.get("type") in ("crashed", "exited"):
                    self.fail("service exited unexpectedly: %r\n%s" % (
                        event, service.stderr_tail,
                    ))
            if not service.is_alive:
                time.sleep(0.05)
                events.extend(service.poll())
                self.fail("service stopped before expected event: %r\n%s" % (
                    events, service.stderr_tail,
                ))
        self.fail("timed out waiting for service event; tail=%r\n%s" % (
            events[-10:], service.stderr_tail,
        ))

    def _request_result(self, service, request, after, *, variant="after",
                        merge=True, timeout=30.0):
        request_id = service.request(
            request, after_output=after, variant=variant, merge=merge
        )
        event, events = self._wait(
            service,
            lambda value: (
                value.get("request_id") == request_id
                and value.get("type") in ("result", "error", "cancelled")
            ),
            timeout=timeout,
        )
        return request_id, event, events

    def test_startup_request_and_thirty_hz_progress(self):
        self.assertNotIn("PyQt5", sys.modules)
        service, ready = self._start()
        self.assertTrue(service.is_alive)
        self.assertEqual(ready["type"], "ready")
        self.assertEqual(ready["protocol"], PROTOCOL_VERSION)
        self.assertEqual(ready["service_schema"], SERVICE_SCHEMA)
        self.assertEqual(ready["tick_hz"], TICK_HZ)

        after = self.root / "render" / "after.wav"
        request_id, event, events = self._request_result(service, self.request, after)
        self.assertEqual(event["type"], "result", event)
        report = event["report"]
        self.assertIn("after", report)
        self.assertEqual(report["rendered_variants"], ["after"])
        self.assertEqual(report["after"]["output_encoding"]["subtype"], "FLOAT")
        self.assertTrue(after.is_file())
        accepted = [item for item in events
                    if item.get("type") == "accepted" and item.get("request_id") == request_id]
        self.assertEqual(len(accepted), 1)
        progress = [item for item in events
                    if item.get("type") == "progress" and item.get("request_id") == request_id]
        self.assertGreaterEqual(len(progress), 2)
        self.assertTrue(all(item["tick_hz"] == TICK_HZ for item in progress))
        intervals = [
            b["ts"] - a["ts"] for a, b in zip(progress, progress[1:])
            if b["tick_sequence"] > a["tick_sequence"]
        ]
        if intervals:
            self.assertGreaterEqual(statistics.median(intervals), 0.015)
            self.assertLessEqual(statistics.median(intervals), 0.080)

    def test_cached_twelve_second_after_targets(self):
        root = self.root / "latency"
        request = _fixture(root, 12.0, 44100)
        service, _ready = self._start()
        warm_id = service.warm(request)
        warm_event, _events = self._wait(
            service,
            lambda value: value.get("request_id") == warm_id and value.get("type") == "result",
        )
        self.assertEqual(warm_event["report"]["schema"], "vwa.audio_mixdown.audition-warm/v1")

        after = root / "after.wav"
        started = time.perf_counter()
        request_id = service.request(request, after_output=after, variant="after")
        result, events = self._wait(
            service,
            lambda value: value.get("request_id") == request_id and value.get("type") == "result",
            timeout=20.0,
        )
        elapsed = time.perf_counter() - started
        report = result["report"]
        self.assertEqual(report["decode_cache_source"], "memory")
        self.assertTrue(report["reverb_edc"]["cache_hit"])
        self.assertLess(report["timings_ms"]["after"], 300.0)
        self.assertLess(elapsed, 0.350, "out-of-process cached after roundtrip: %.3fs" % elapsed)
        self.assertTrue(any(
            item.get("type") == "progress" and item.get("request_id") == request_id
            for item in events
        ))

    def test_cancel_current_request_cleans_outputs_and_keeps_service_alive(self):
        root = self.root / "cancel"
        request = _fixture(root, 30.0, 44100)
        service, _ready = self._start()
        warm_id = service.warm(request)
        self._wait(
            service,
            lambda value: value.get("request_id") == warm_id and value.get("type") == "result",
            timeout=30.0,
        )
        after = root / "after.wav"
        request_id = service.request(request, after_output=after, variant="after")
        self._wait(
            service,
            lambda value: value.get("request_id") == request_id and value.get("type") == "progress",
        )
        service.cancel()
        cancelled, _events = self._wait(
            service,
            lambda value: (
                value.get("request_id") == request_id
                and value.get("type") == "cancelled"
            ),
        )
        self.assertTrue(cancelled["was_running"])
        self.assertIn(cancelled["reason"], ("cancel_requested", "superseded"))
        self.assertFalse(after.exists())
        self.assertEqual(_audition_artifacts(after.parent), [])
        self.assertEqual(list(self.cache.rglob("s-*")), [])
        self.assertEqual(list(self.cache.rglob("*.tmp")), [])
        self.assertTrue(service.is_alive)
        warm_again = service.warm(request)
        self._wait(
            service,
            lambda value: value.get("request_id") == warm_again and value.get("type") == "result",
        )

    def test_newer_requests_merge_and_only_latest_runs(self):
        root = self.root / "coalesce"
        request = _fixture(root, 30.0, 44100)
        service, _ready = self._start()
        warm_id = service.warm(request)
        self._wait(
            service,
            lambda value: value.get("request_id") == warm_id and value.get("type") == "result",
            timeout=30.0,
        )
        first_after = root / "first.wav"
        first_id = service.request(request, after_output=first_after, variant="after")
        self._wait(
            service,
            lambda value: value.get("request_id") == first_id and value.get("type") == "progress",
        )

        second_after = root / "second.wav"
        second_patch = {
            "vocals": copy.deepcopy(request["vocals"]),
            "instrumental_db": -6.0,
        }
        second_patch["vocals"][0]["gain_db"] = 2.0
        second_id = service.request(
            second_patch, after_output=second_after, variant="after", merge=True
        )
        third_after = root / "third.wav"
        third_id = service.request(
            {"instrumental_db": -7.5},
            after_output=third_after,
            variant="after",
            merge=True,
        )
        result, events = self._wait(
            service,
            lambda value: value.get("request_id") == third_id and value.get("type") == "result",
            timeout=30.0,
        )
        self.assertEqual(result["type"], "result")
        tracks = result["report"]["after"]["tracks"]
        self.assertAlmostEqual(tracks[0]["gain_db"], 2.0)
        self.assertAlmostEqual(tracks[-1]["gain_db"], -7.5)
        cancelled = {
            item.get("request_id") for item in events if item.get("type") == "cancelled"
        }
        self.assertIn(first_id, cancelled)
        self.assertIn(second_id, cancelled)
        results = {
            item.get("request_id") for item in events if item.get("type") == "result"
        }
        self.assertIn(third_id, results)
        self.assertNotIn(second_id, results)
        self.assertTrue(third_after.is_file())
        self.assertFalse(first_after.exists())
        self.assertFalse(second_after.exists())

    def test_continuous_v3_parameter_changes_are_cache_hit_and_near_realtime(self):
        root = self.root / "continuous-performance"
        request = _fixture(root, 12.0, 44100)
        # The compressor threshold is program dependent.  A louder fixture makes
        # every strength change observable instead of producing identical bytes
        # below threshold.
        request["vocals"][0]["gain_db"] = 7.0
        request["vocals"][1]["gain_db"] = 6.0
        service_started = time.perf_counter()
        service, _ready = self._start()
        service_start_ms = (time.perf_counter() - service_started) * 1000.0

        cold = copy.deepcopy(request)
        cold_started = time.perf_counter()
        cold_id, cold_event, _cold_events = self._request_result(
            service, cold, root / "cold.wav", merge=False, timeout=30.0
        )
        self.assertEqual(cold_event["type"], "result", cold_event)
        self.assertEqual(cold_event["request_id"], cold_id)
        cold_wall_ms = (time.perf_counter() - cold_started) * 1000.0
        cold_report = cold_event["report"]
        self.assertFalse(cold_report["cache_hit"])

        warm_started = time.perf_counter()
        warm_id, warm_event, _warm_events = self._request_result(
            service, cold, root / "warm.wav", merge=False, timeout=30.0
        )
        self.assertEqual(warm_event["type"], "result", warm_event)
        self.assertEqual(warm_event["request_id"], warm_id)
        warm_wall_ms = (time.perf_counter() - warm_started) * 1000.0
        warm_report = warm_event["report"]
        self.assertTrue(warm_report["cache_hit"])
        self.assertEqual(warm_report["decode_cache_source"], "memory")

        operations = [
            ("eq_node_frequency", lambda value: value["eq_bands"][1].update(
                freq_hz=2750.0, q=1.15)),
            ("vocal_send", lambda value: (
                value["vocals"][0].update(reverb_send=0.52),
                value["vocals"][1].update(reverb_send=0.44),
            )),
            ("compression", lambda value: value.update(
                compression_strength=0.68)),
            ("stereo_width", lambda value: value.update(width=1.18)),
            ("eq_node_gain", lambda value: value["eq_bands"][1].update(
                gain_db=-3.5)),
            ("reverb_predelay", lambda value: value.update(
                reverb_predelay_ms=37.0)),
            ("send_reverse", lambda value: (
                value["vocals"][0].update(reverb_send=0.18),
                value["vocals"][1].update(reverb_send=0.91),
            )),
            ("compression_light", lambda value: value.update(
                compression_strength=0.12)),
            ("stereo_narrow", lambda value: value.update(width=0.62)),
        ]
        samples = []
        hashes = set()
        render_keys = set()
        current = copy.deepcopy(cold)
        for index, (label, mutate) in enumerate(operations, 1):
            mutate(current)
            output = root / ("change-%02d.wav" % index)
            started = time.perf_counter()
            request_id, event, _events = self._request_result(
                service, current, output, timeout=30.0
            )
            wall_ms = (time.perf_counter() - started) * 1000.0
            self.assertEqual(event["type"], "result", event)
            self.assertEqual(event["request_id"], request_id)
            report = event["report"]
            self.assertTrue(report["cache_hit"])
            self.assertEqual(report["decode_cache_source"], "memory")
            self.assertEqual(report["rendered_variants"], ["after"])
            self.assertIn("after", report)
            self.assertNotIn("before", report)
            self.assertTrue(output.is_file())
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            self.assertNotIn(digest, hashes, label + ": " + digest)
            hashes.add(digest)
            render_key = report["cache_identity"]["render_cache_key"]
            self.assertNotIn(render_key, render_keys, label)
            render_keys.add(render_key)
            samples.append({
                "label": label,
                "wall_ms": round(wall_ms, 3),
                "total_ms": round(float(report["timings_ms"]["total"]), 3),
                "cache_hit": bool(report["cache_hit"]),
                "render_cache_key": render_key,
            })

        final = event["report"]
        self.assertEqual(final["request_features"]["width"], 0.62)
        self.assertEqual(final["request_features"]["reverb_predelay_ms"], 37.0)
        self.assertEqual(
            [row["reverb_send"] for row in final["after"]["tracks"]
             if row["role"] == "vocal"],
            [0.18, 0.91],
        )
        self.assertTrue(final["after"]["gr_envelope"]["enabled"])
        self.assertFalse(final["cache_identity"]["processed_audio_cached"])

        burst_a = copy.deepcopy(current)
        burst_a["width"] = 0.30
        burst_a_output = root / "burst-old.wav"
        burst_a_id = service.request(
            burst_a, after_output=burst_a_output, variant="after", merge=True
        )
        self._wait(
            service,
            lambda value: (
                value.get("request_id") == burst_a_id
                and value.get("type") == "progress"
            ),
            timeout=5.0,
        )
        burst_b_output = root / "burst-latest.wav"
        burst_b_id, burst_b_event, burst_events = self._request_result(
            service,
            {"width": 0.97, "compression_strength": 0.77,
             "reverb_predelay_ms": 41.0},
            burst_b_output,
            timeout=30.0,
        )
        self.assertEqual(burst_b_event["type"], "result", burst_b_event)
        self.assertEqual(burst_b_event["request_id"], burst_b_id)
        burst_report = burst_b_event["report"]
        stale_cancelled = any(
            item.get("type") == "cancelled"
            and item.get("request_id") == burst_a_id
            and item.get("reason") == "superseded"
            for item in burst_events
        )
        stale_result = any(
            item.get("type") == "result"
            and item.get("request_id") == burst_a_id
            for item in burst_events
        )
        latest_matches = (
            burst_report["request_features"]["width"] == 0.97
            and burst_report["request_features"]["reverb_predelay_ms"] == 41.0
        )
        last_request_wins = bool(
            stale_cancelled and not stale_result and latest_matches
            and burst_b_output.is_file() and not burst_a_output.exists()
        )
        self.assertTrue(stale_cancelled, burst_events)
        self.assertFalse(stale_result, burst_events)
        self.assertTrue(latest_matches, burst_report["request_features"])
        self.assertTrue(burst_b_output.is_file())
        self.assertFalse(burst_a_output.exists())
        artifacts = _audition_artifacts(root)
        self.assertEqual(artifacts, [])

        wall = [item["wall_ms"] for item in samples]
        total = [item["total_ms"] for item in samples]
        summary = {
            "schema": "vwa.audio_mixdown.audition-performance/v1",
            "service_start_ms": round(service_start_ms, 3),
            "cold_render": {
                "wall_ms": round(cold_wall_ms, 3),
                "total_ms": round(float(cold_report["timings_ms"]["total"]), 3),
                "cache_hit": bool(cold_report["cache_hit"]),
            },
            "warm_render": {
                "wall_ms": round(warm_wall_ms, 3),
                "total_ms": round(float(warm_report["timings_ms"]["total"]), 3),
                "cache_hit": bool(warm_report["cache_hit"]),
            },
            "changes": samples,
            "wall_ms": {
                "p50": round(_percentile(wall, 0.50), 3),
                "p95": round(_percentile(wall, 0.95), 3),
            },
            "total_ms": {
                "p50": round(_percentile(total, 0.50), 3),
                "p95": round(_percentile(total, 0.95), 3),
            },
            "last_request_wins": last_request_wins,
            "last_request_detail": {
                "old_request_id": burst_a_id,
                "latest_request_id": burst_b_id,
                "stale_cancelled": stale_cancelled,
                "stale_result": stale_result,
                "latest_params_match": latest_matches,
                "stale_output_removed": not burst_a_output.exists(),
            },
            "cache_hit": all(item["cache_hit"] for item in samples),
            "temp_files_clean": artifacts == [],
        }
        print("AUDITION_PERFORMANCE_JSON=" + json.dumps(
            summary, ensure_ascii=False, sort_keys=True
        ))
        # The real-time contract is median roundtrip below 350 ms.  Mainline
        # acceptance measured p95 at 234.1 ms.  Windows AppContainer replay has
        # shown isolated scheduler stalls above 600 ms, so p95 stays visible in
        # the JSON and has a pathological-regression ceiling rather than being
        # silently treated as the median target.
        self.assertLess(summary["wall_ms"]["p50"], 350.0, summary)
        self.assertLess(summary["wall_ms"]["p95"], 750.0, summary)
        self.assertLess(summary["total_ms"]["p50"], 350.0, summary)

    def test_crash_is_reported_once_without_automatic_restart(self):
        service, _ready = self._start()
        warm_id = service.warm(self.request)
        self._wait(
            service,
            lambda value: (
                value.get("request_id") == warm_id
                and value.get("type") == "result"
            ),
        )
        pid = service.pid
        process = service._process
        self.assertIsNotNone(process)
        process.kill()
        crashed, events = self._wait(
            service,
            lambda value: value.get("type") in ("crashed", "exited"),
            timeout=10.0,
        )
        self.assertEqual(crashed["type"], "crashed")
        self.assertFalse(crashed["auto_restart"])
        self.assertEqual(crashed["restart_count"], 0)
        self.assertFalse(service.is_alive)
        self.assertIsNone(service.pid)
        self.assertEqual(process.pid, pid)
        time.sleep(0.15)
        later = service.poll()
        self.assertFalse(any(item.get("type") == "ready" for item in later))
        self.assertFalse(service.is_alive)

        ready = service.start()
        self.assertEqual(ready["type"], "ready")
        self.assertNotEqual(service.pid, pid)
        self.assertEqual(service.restart_count, 1)
        restarted_output = self.root / "restart" / "after.wav"
        request_id, result, _events = self._request_result(
            service, self.request, restarted_output, merge=False
        )
        self.assertEqual(result["type"], "result", result)
        self.assertEqual(result["request_id"], request_id)
        self.assertTrue(result["report"]["cache_hit"])
        self.assertEqual(result["report"]["decode_cache_source"], "disk")
        self.assertTrue(restarted_output.is_file())

    def test_shutdown_is_bounded_and_leaves_no_process(self):
        service, _ready = self._start()
        pid = service.pid
        self.assertIsNotNone(pid)
        returncode = service.close(timeout=5.0)
        self.assertEqual(returncode, 0)
        self.assertFalse(service.is_alive)
        self.assertIsNone(service.pid)
        self.assertIsNotNone(service.returncode)
        self.assertFalse(_pid_running(pid))
        self.assertEqual(service.close(timeout=1.0), returncode)
        final_events = service.poll()
        self.assertTrue(any(item.get("type") == "shutdown" for item in final_events))


if __name__ == "__main__":
    unittest.main(verbosity=2)