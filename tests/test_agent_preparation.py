"""Preparation contract tests. No model inference or audio synthesis runs here."""
import copy
import hashlib
import json
import shutil
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
sys.path.insert(0, str(SRC))
import agent_preparation
import common
import pipeline


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path):
    return json.loads(Path(common.native_path(path)).read_text(encoding="utf-8"))


class PreparationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP / "work", prefix="agent-preparation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def preflight(self, options, stages, source):
        return {
            "ready": True,
            "missing_required": [],
            "unverified": [{"kind": "test", "verified": False}],
            "tools": {"config_sha256": None, "required": []},
            "dependencies": {"features": [], "ready": True, "missing_count": 0,
                             "missing": [], "missing_truncated": False},
        }

    def plan(self, source, options=None):
        with patch.object(agent_preparation, "_preflight", side_effect=self.preflight):
            return agent_preparation.plan_preparation(source, options)

    def source_file(self, name="测试 music.mp3", data=b"not-decoded-audio"):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_bilibili_plan_is_explicit_digest_bound_json(self):
        plan = self.plan(
            "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333.1",
            {"language": "ja", "midi_steps": 32, "bpm": 128,
             "lyrics": "君の声\nまだ夜"},
        )
        self.assertEqual(plan["source"]["bv"], "BV1xx411c7mD")
        self.assertEqual(plan["source"]["url"], "https://www.bilibili.com/video/BV1xx411c7mD/")
        self.assertEqual(plan["required_stages"],
                         ["metadata", "download", "audio", "separation", "midi"])
        self.assertTrue(plan["prepare_only"])
        self.assertTrue(plan["network"]["required"])
        self.assertTrue(plan["inference"]["required"])
        self.assertFalse(plan["inference"]["semantic_model"])
        self.assertEqual(plan["inference"]["providers"], [])
        self.assertEqual(plan["options"]["lyrics"], "君の声\nまだ夜")
        self.assertEqual(plan["options"]["bpm"], 128.0)
        unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
        self.assertEqual(plan["plan_sha256"], agent_preparation._digest(unsigned))
        json.dumps(plan, ensure_ascii=False, allow_nan=False)

    def test_local_plan_hashes_source_and_uses_ordinary_identity_path(self):
        source = self.source_file()
        plan = self.plan(str(source), {"language": "zh", "voice_mode": "single"})
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(plan["source"]["sha256"], digest)
        self.assertEqual(plan["source"]["size"], source.stat().st_size)
        self.assertEqual(plan["source"]["path"], str(source.resolve()))
        self.assertNotIn("\\\\?\\", plan["source"]["path"])
        self.assertFalse(plan["network"]["required"])
        self.assertEqual(plan["required_stages"], ["metadata", "download", "audio", "separation", "midi"])

    def test_unsupported_sites_and_uncurated_options_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "其他站点|仅支持"):
            self.plan("https://example.com/video/BV1xx411c7mD")
        with self.assertRaisesRegex(ValueError, "未授权字段"):
            self.plan("BV1xx411c7mD", {"output_path": str(self.root / "escape")})
        with self.assertRaisesRegex(ValueError, "midi_steps"):
            self.plan("BV1xx411c7mD", {"midi_steps": 24})
        with self.assertRaisesRegex(ValueError, "TiFA"):
            self.plan("BV1xx411c7mD", {"midi_backend": "v1", "alignment_engine": "tifa"})
        with self.assertRaisesRegex(ValueError, "bpm"):
            self.plan("BV1xx411c7mD", {"bpm": float("nan")})

    def test_source_summary_is_current_video_bounded_and_omits_collection(self):
        job = self.root / "source-job"
        write_json(job / "request.json", {
            "source_kind": "bilibili", "bv": "BV1ZL411i7YQ", "voice_mode": "single",
        })
        info = {
            "bvid": "BV1ZL411i7YQ", "title": "牵丝戏测试", "duration": 242,
            "owner": {"name": "JLRS-LeoFM", "face": "https://example.invalid/face.jpg"},
            "desc": "说明文字 " * 200,
            "pic": "https://i1.hdslb.com/cover.jpg",
            "rights": {"download": 1, "no_reprint": 0, "nested": {"secret": "x"}},
            "videos": 2,
            "pages": [
                {"page": 1, "part": "第一部分", "duration": 120},
                {"page": 2, "part": "第二部分", "duration": 122},
            ],
            "ugc_season": {"title": "不应返回合集", "sections": [{"items": ["x"] * 50000}]},
        }
        write_json(job / "source-info.json", info)
        summary = agent_preparation.source_summary(job)
        encoded = json.dumps(summary, ensure_ascii=False)
        self.assertLess(len(encoded.encode("utf-8")), 4 * 1024)
        self.assertEqual(summary["bvid"], "BV1ZL411i7YQ")
        self.assertEqual(summary["owner"], "JLRS-LeoFM")
        self.assertEqual(summary["duration_seconds"], 242)
        self.assertEqual(summary["page_count"], 2)
        self.assertEqual(summary["current_page"]["title"], "第一部分")
        self.assertEqual(summary["rights_flags"], {"download": True, "no_reprint": False})
        self.assertFalse(summary["collection_payload_returned"])
        self.assertNotIn("不应返回合集", encoded)
        self.assertNotIn("sections", encoded)
        self.assertEqual(summary["voice_plan"]["roles"][0]["role"], "mixed_vocal")
        self.assertEqual(summary["raw_evidence"]["path"], str((job / "source-info.json").resolve()))

    def test_preflight_missing_tifa_has_executable_hfa_alternative(self):
        options = agent_preparation._normalise_options({
            "midi_backend": "v2", "alignment_engine": "tifa", "voice_mode": "dual"})
        with (patch.object(agent_preparation, "_tool_evidence", return_value={
                  "config_sha256": None, "required": []}),
              patch.object(agent_preparation, "_dependency_evidence", return_value={
                  "features": [], "ready": True, "missing_count": 0,
                  "missing": [], "missing_truncated": False}),
              patch.object(agent_preparation, "ROOT", self.root)):
            report = agent_preparation._preflight(
                options, list(agent_preparation.PREPARATION_STAGES),
                {"kind": "bilibili", "bv": "BV1xx411c7mD"})
        self.assertFalse(report["ready"])
        self.assertEqual(report["suggested_options"], {"alignment_engine": "hfa"})
        self.assertIn("alignment_engine", report["missing_required"][0]["remediation"])
        self.assertFalse(report["voice_plan"]["singer_diarization"])
        self.assertEqual([row["role"] for row in report["voice_plan"]["roles"][:-1]],
                         ["lead_vocal", "backing_or_harmony"])

    def test_tampered_plan_and_changed_local_source_are_rejected_before_creation(self):
        source = self.source_file("changed.wav", b"first-content")
        plan = self.plan(str(source))
        tampered = copy.deepcopy(plan)
        tampered["options"]["midi_steps"] = 32
        with self.assertRaises(agent_preparation.PreparationError) as raised:
            agent_preparation.create_preparation(tampered, self.root)
        self.assertEqual(raised.exception.code, "plan_tampered")

        source.write_bytes(b"second-content")
        with self.assertRaises(agent_preparation.PreparationError) as raised:
            agent_preparation.create_preparation(plan, self.root)
        self.assertEqual(raised.exception.code, "source_changed")


class PreparationReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP / "work", prefix="agent-prep-reuse-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        def cleanup_native():
            target=self.root.resolve();boundary=(APP/'work').resolve()
            assert target.is_relative_to(boundary) and target!=boundary
            if Path(common.native_path(target)).exists():shutil.rmtree(common.native_path(target))
        self.addCleanup(cleanup_native)
        self.local = self.root / "source.wav"
        self.local.write_bytes(b"local-audio-fixture")
        self.source_job = self.root / "source-job"
        self.request = {
            "version": 3, "source_kind": "local", "source_audio": str(self.local.resolve()),
            "input_file": "input/source.wav", "source_title": "复用测试",
            "language": "zh", "lyrics": "旧歌词", "backing_lyrics": "",
            "recognize_lyrics": True, "voice_mode": "single", "midi_steps": 16,
            "midi_backend": "v2", "alignment_engine": "hfa", "bpm": None,
            "prepare_only": True, "project_formats": [], "audio_formats": [], "export_pitch": False,
            "tools": {"vocal2midi": "dependencies/vocal2midi2", "separation_device": "dml",
                      "midi_device": "dml", "dml_adapter": "auto", "cpu_threads": 0},
        }
        write_json(self.source_job / "request.json", self.request)
        files = {
            "metadata": ["source-info.json", "upstream-info.json", "发布简介草稿.txt", "参考歌词.txt"],
            "download": ["input/source.wav", "import-report.json"],
            "audio": ["audio/source.wav", "audio-info.json"],
            "separation": ["audio/vocals.wav", "audio/instrumental.wav", "separation-report.json"],
            "midi": ["midi/vocals.mid", "midi/report.json"],
        }
        for stage, names in files.items():
            for name in names:
                path = self.source_job / Path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((stage + ":" + name).encode("utf-8"))
        write_json(self.source_job / "import-report.json", {
            "sha256": hashlib.sha256((self.source_job / "input/source.wav").read_bytes()).hexdigest(),
        })
        status = {"state": "done", "steps": {}}
        for stage in ("metadata", "download", "audio", "separation"):
            status["steps"][stage] = {
                "state": "done",
                "receipt": {"signature": self.signature(stage, self.request), "sha256": {}},
            }
        status["steps"]["midi"] = {"state": "done"}
        write_json(self.source_job / "status.json", status)

    def signature(self, stage, request):
        if stage == "download":
            return "download:" + str(request.get("source_audio"))
        if stage == "audio":
            return "audio:44100/stereo/float32"
        if stage == "separation":
            return "separation:" + str(request.get("voice_mode")) + ":" + str(
                (request.get("tools") or {}).get("separation_device"))
        return stage

    def completed_voice(self, job, request, voice):
        return request.get("lyrics") == self.request["lyrics"] and voice == "vocals"

    def validator_patches(self):
        return (
            patch.object(agent_preparation, "_preflight", return_value={
                "ready": True, "missing_required": [], "unverified": [],
                "tools": {"config_sha256": None, "required": []},
                "dependencies": {"features": [], "ready": True, "missing_count": 0,
                                 "missing": [], "missing_truncated": False}}),
            patch("artifact_cache.valid_receipt", return_value=True),
            patch("artifact_cache.receipt", side_effect=lambda job, request, stage, files: {
                "signature": self.signature(stage, request), "sha256": {}}),
            patch("artifact_cache.stage_signature",
                  side_effect=lambda job, request, stage: self.signature(stage, request)),
            patch.object(common, "completed_voice", side_effect=self.completed_voice),
        )

    def plan(self, options=None, reuse_stages=None):
        patches = self.validator_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            return agent_preparation.plan_preparation_reuse(
                self.source_job, options, reuse_stages)

    def test_lyrics_change_reuses_audio_separation_and_invalidates_midi(self):
        plan = self.plan({"lyrics": "新的歌词"})
        self.assertEqual(plan["kind"], "preparation_reuse")
        self.assertEqual(plan["source_job"], str(self.source_job.resolve()))
        self.assertEqual(plan["reuse"]["reuse_stages"],
                         ["metadata", "download", "audio", "separation"])
        self.assertEqual(plan["reuse"]["run_stages"], ["midi"])
        midi = next(row for row in plan["reuse"]["stages"] if row["stage"] == "midi")
        self.assertEqual(midi["reason"], "midi_options_or_lyrics_changed")
        unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
        self.assertEqual(plan["plan_sha256"], agent_preparation._digest(unsigned))

    def test_voice_mode_change_blocks_mismatched_separation(self):
        plan = self.plan({"voice_mode": "dual"}, ["separation"])
        self.assertEqual(plan["reuse"]["reuse_stages"], ["metadata", "download", "audio"])
        separation = next(row for row in plan["reuse"]["stages"]
                          if row["stage"] == "separation")
        self.assertEqual(separation["reason"], "voice_mode_changed")
        self.assertIn("separation", plan["reuse"]["run_stages"])

    def test_incomplete_source_stage_is_never_reported_as_completed(self):
        status = read_json(self.source_job / "status.json")
        status["steps"]["separation"]["state"] = "running"
        write_json(self.source_job / "status.json", status)
        plan = self.plan()
        self.assertEqual(plan["reuse"]["reuse_stages"], ["metadata", "download", "audio"])
        stage = next(row for row in plan["reuse"]["stages"]
                     if row["stage"] == "separation")
        self.assertFalse(stage["source_complete"])
        self.assertEqual(stage["source_state"], "running")
        self.assertEqual(stage["action"], "run")
        self.assertNotIn("separation", plan["reuse"]["reuse_stages"])

    def test_create_copies_only_verified_stages_without_touching_source(self):
        plan = self.plan({"lyrics": "新的歌词"}, ["separation"])
        before = {path.relative_to(self.source_job).as_posix(): path.read_bytes()
                  for path in self.source_job.rglob("*") if path.is_file()}

        def create_job(**kwargs):
            job = Path(kwargs["parent"]) / "Local_reuse_fixture"
            job.mkdir(parents=True)
            options = agent_preparation._normalise_options({
                name: kwargs[name] for name in agent_preparation.ALLOWED_OPTIONS
                if name in kwargs})
            write_json(job / "request.json", agent_preparation._target_request(self.request, options))
            return job.resolve()

        targets = self.root / "targets"
        targets.mkdir()
        patches = self.validator_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patch.object(
                pipeline, "create_job", side_effect=create_job), patch.object(
                common, "ROOT", APP), patch.object(pipeline, "ROOT", APP):
            first = agent_preparation.create_preparation_reuse(plan, targets)
            second = agent_preparation.create_preparation_reuse(plan, targets)
        self.assertTrue(second["replay"])
        self.assertEqual(first["job_path"], second["job_path"])
        self.assertEqual(first["reused_stages"],
                         ["metadata", "download", "audio", "separation"])
        self.assertEqual(first["run_stages"], ["midi"])
        target = Path(common.native_path(first["job_path"]))
        self.assertTrue((target / "audio/source.wav").is_file())
        self.assertFalse((target / "midi/vocals.mid").exists())
        status = read_json(target / "status.json")
        self.assertEqual(status["state"], "partial")
        self.assertEqual(list(status["steps"]), ["metadata", "download", "audio", "separation"])
        receipt = read_json(first["reuse_receipt"])
        self.assertEqual(receipt["receipt_sha256"], first["reuse_receipt_sha256"])
        self.assertEqual((target / "参考歌词.txt").read_text(encoding="utf-8"), "新的歌词")
        after = {path.relative_to(self.source_job).as_posix(): path.read_bytes()
                 for path in self.source_job.rglob("*") if path.is_file()}
        self.assertEqual(before, after)


class PreparationCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP / "work", prefix="agent-prep-create-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "音源.wav"
        self.source.write_bytes(b"tiny-local-source-no-decode")

    def preflight(self, options, stages, source):
        return {"ready": True, "missing_required": [], "unverified": [],
                "tools": {"config_sha256": None, "required": []},
                "dependencies": {"features": [], "ready": True, "missing_count": 0,
                                 "missing": [], "missing_truncated": False}}

    def plan(self):
        with patch.object(agent_preparation, "_preflight", side_effect=self.preflight):
            return agent_preparation.plan_preparation(str(self.source))

    def test_real_create_is_deterministic_replayable_and_does_not_remember_job(self):
        plan = self.plan()
        remembered = []
        with (patch.object(common, "ROOT", APP), patch.object(pipeline, "ROOT", APP),
              patch.object(pipeline, "remember_job", side_effect=lambda job: remembered.append(str(job)))):
            first = agent_preparation.create_preparation(plan, self.root)
            second = agent_preparation.create_preparation(plan, self.root)
        self.assertFalse(first["replay"])
        self.assertTrue(second["replay"])
        self.assertEqual(first["job_path"], second["job_path"])
        self.assertEqual(first["request_sha256"], second["request_sha256"])
        self.assertEqual(first["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(remembered, [])
        job = Path(first["job_path"])
        request = read_json(job / "request.json")
        self.assertTrue(request["prepare_only"])
        expected_source = ("@/" + self.source.resolve().relative_to(APP).as_posix()
                           if self.source.resolve().is_relative_to(APP) else str(self.source.resolve()))
        self.assertEqual(request["source_audio"], expected_source)
        self.assertEqual(request["project_formats"], [])
        self.assertEqual(request["audio_formats"], [])
        self.assertFalse(job.with_name("last_job.txt").exists())
        receipt = read_json(first["creation_receipt"])
        self.assertEqual(receipt["receipt_sha256"], first["receipt_sha256"])
        self.assertEqual(receipt["plan_sha256"], plan["plan_sha256"])
        jobs = [path for path in Path(first["job_path"]).parent.iterdir()
                if path.is_dir() and (path / "request.json").is_file()]
        self.assertEqual(len(jobs), 1)

    def test_session_collision_and_tampered_receipt_fail_without_replacement(self):
        plan = self.plan()
        with patch.object(common, "ROOT", APP), patch.object(pipeline, "ROOT", APP):
            result = agent_preparation.create_preparation(plan, self.root)
        receipt = Path(result["creation_receipt"])
        value = read_json(receipt)
        value["request_sha256"] = "0" * 64
        write_json(receipt, value)
        with self.assertRaises(agent_preparation.PreparationError) as raised:
            agent_preparation.create_preparation(plan, self.root)
        self.assertEqual(raised.exception.code, "receipt_invalid")

    def test_unknown_session_entry_is_a_collision(self):
        plan = self.plan()
        container = self.root / (agent_preparation.SESSION_PREFIX + plan["plan_sha256"])
        container.mkdir()
        (container / "foreign.txt").write_text("do not replace", encoding="utf-8")
        with self.assertRaises(agent_preparation.PreparationError) as raised:
            agent_preparation.create_preparation(plan, self.root)
        self.assertEqual(raised.exception.code, "collision")
        self.assertEqual((container / "foreign.txt").read_text(encoding="utf-8"), "do not replace")


class PreparationRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP / "work", prefix="agent-prep-run-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = self.root / "job"
        self.job.mkdir()
        write_json(self.job / "request.json", {
            "version": 3,
            "source_kind": "local",
            "source_title": "fixture",
            "voice_mode": "dual",
            "planned_steps": ["metadata", "download", "audio", "separation", "midi"],
        })

    def test_real_lock_wrapper_returns_verified_score_files_and_progress(self):
        seen = []
        original_lock = pipeline.job_lock
        original_stage = pipeline._run

        def stage(job, until):
            write_json(self.job / "progress.json", {
                "stage": "midi", "detail": "生成主唱 / 和声", "done": 2, "total": 2,
            })
            lead = self.job / "midi" / "lead" / "lead.mid"
            backing = self.job / "midi" / "backing" / "backing.mid"
            lead.parent.mkdir(parents=True)
            backing.parent.mkdir(parents=True)
            lead.write_bytes(b"MThd\\x00\\x00\\x00\\x06leader")
            backing.write_bytes(b"MThd\\x00\\x00\\x00\\x06backing")
            (self.job / "midi" / "voices.mid").write_bytes(b"MThd\\x00\\x00\\x00\\x06merged")
            time.sleep(.12)
            write_json(self.job / "status.json", {
                "state": "done",
                "planned_steps": ["metadata", "download", "audio", "separation", "midi"],
                "steps": {name: {"state": "done"} for name in
                          ("metadata", "download", "audio", "separation", "midi")},
            })

        with patch.object(pipeline, "_run", new=stage):
            result = agent_preparation.run_preparation(
                self.job, progress=lambda event: seen.append(copy.deepcopy(event)))
            self.assertEqual(pipeline.job_lock, original_lock)
            self.assertEqual(pipeline._run, stage)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["next_step"], "score_review")
        self.assertEqual(result["completed_scope"], "preparation_only")
        self.assertFalse(result["synthesis_performed"])
        self.assertTrue(seen and seen[0]["stage"] == "midi")
        rows = {Path(row["path"]).name: row for row in result["files"]}
        self.assertEqual(rows["lead.mid"]["voice"], "lead")
        self.assertEqual(rows["backing.mid"]["voice"], "backing")
        self.assertNotIn("voice", rows["voices.mid"])
        self.assertEqual(rows["lead.mid"]["role"], "midi")
        self.assertEqual(rows["lead.mid"]["sha256"],
                         hashlib.sha256((self.job / "midi" / "lead" / "lead.mid").read_bytes()).hexdigest())
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_cancel_flag_is_written_by_monitor_and_cancelled_is_not_completed(self):
        cancel = threading.Event()
        cancel.set()

        def stage(job, until):
            deadline = time.monotonic() + 3
            while not (self.job / "cancel.flag").exists():
                if time.monotonic() >= deadline:
                    raise AssertionError("owned cancellation monitor did not write cancel.flag")
                time.sleep(.01)
            write_json(self.job / "status.json", {
                "state": "cancelled", "current_step": "midi", "error": "cancelled by test",
            })
            raise InterruptedError("已取消任务；已完成的步骤可以继续使用。")

        with patch.object(pipeline, "_run", new=stage):
            result = agent_preparation.run_preparation(self.job, cancel=cancel)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["next_step"], "retry_preparation")
        self.assertEqual(result["error"]["type"], "InterruptedError")
        self.assertTrue((self.job / "cancel.flag").is_file())

        def raise_cancel():
            raise InterruptedError('MCP cancellation callback')
        with patch.object(pipeline, "_run", new=stage):
            raised_callback = agent_preparation.run_preparation(self.job, cancel=raise_cancel)
        self.assertEqual(raised_callback['status'], 'cancelled')
        self.assertEqual(raised_callback['callback_errors'], [])

    def test_competing_job_owner_never_receives_cancel_flag(self):
        code = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "import pipeline\n"
            "with pipeline.job_lock(sys.argv[1]):\n"
            "    print('ready', flush=True)\n"
            "    sys.stdin.readline()\n"
        )
        holder = subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(self.job), str(SRC)],
            cwd=str(SRC), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.stop_holder, holder)
        ready = holder.stdout.readline().strip()
        if ready != "ready":
            self.fail("lock holder failed: " + holder.stderr.read())
        calls = []
        result = agent_preparation.run_preparation(
            self.job, cancel=lambda: calls.append("cancel") or True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["next_step"], "retry_preparation")
        self.assertIn("已经在运行", result["error"]["message"])
        self.assertEqual(calls, [])
        self.assertFalse((self.job / "cancel.flag").exists())

    def test_missing_runtime_failure_and_done_without_scores_are_explicit_failures(self):
        def fail(job, until):
            write_json(self.job / "status.json", {
                "state": "failed", "current_step": "separation",
                "error": "required model missing",
            })
            raise FileNotFoundError("required model missing")

        original_lock = pipeline.job_lock
        original_stage = pipeline._run
        with patch.object(pipeline, "_run", new=fail):
            result = agent_preparation.run_preparation(self.job)
            self.assertEqual(pipeline.job_lock, original_lock)
            self.assertEqual(pipeline._run, fail)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["type"], "FileNotFoundError")
        self.assertEqual(result["next_step"], "retry_preparation")
        self.assertNotEqual(result["status"], "completed")

        def empty_done(job, until):
            write_json(self.job / "status.json", {"state": "done", "steps": {"midi": {"state": "done"}}})

        (self.job / "cancel.flag").unlink(missing_ok=True)
        with patch.object(pipeline, "_run", new=empty_done):
            empty = agent_preparation.run_preparation(self.job)
        self.assertEqual(empty["status"], "failed")
        self.assertEqual(empty["error"]["code"], "missing_score_output")
        self.assertEqual(empty["files"], [])

    def test_early_until_is_reported_partial(self):
        def partial(job, until):
            self.assertEqual(until, "separation")
            write_json(self.job / "status.json", {
                "state": "partial", "steps": {"separation": {"state": "done"}},
            })

        with patch.object(pipeline, "_run", new=partial):
            result = agent_preparation.run_preparation(self.job, until="separation")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["next_step"], "continue_preparation")
        self.assertEqual(result["files"], [])

    def stop_holder(self, process):
        if process.poll() is None:
            try:
                process.stdin.write("x\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
