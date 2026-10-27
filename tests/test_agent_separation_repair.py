"""Separation-repair backend tests. No model, network, or synthesis is used."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
sys.path.insert(0, str(SRC))

import agent_preparation
import artifact_cache
import agent_separation_repair as repair_backend
import common
import pipeline


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    common.atomic_json(path, value)


def _read_json(path):
    return json.loads(Path(common.native_path(path)).read_text(encoding="utf-8"))


def _preflight(*_args, **_kwargs):
    return {
        "ready": True,
        "missing_required": [],
        "alternatives": [],
        "suggested_options": {},
        "readiness_summary": {"tools_ready": True, "dependencies_ready": True,
                              "preparation_ready": True, "note": "test fixture"},
        "voice_plan": agent_preparation._voice_role_summary("dual"),
        "unverified": [],
        "tools": {"config_sha256": None, "required": []},
        "dependencies": {"features": [], "ready": True, "missing_count": 0,
                         "missing": [], "missing_truncated": False},
    }


class SeparationRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        import soundfile as sf
        cls.np = np
        cls.sf = sf

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=APP, prefix="s-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source-job"
        self.parent = self.root / "p"
        self.source.mkdir()
        self.parent.mkdir()
        with self.app_context():
            self._build_source()

    def _audio(self, frames=2048):
        np = self.np
        t = np.arange(frames, dtype=np.float64) / 44100.0
        main = (0.20 * np.sin(2 * math.pi * 220 * t)
                + 0.08 * np.sin(2 * math.pi * 440 * t))
        harmony = (0.06 * np.sin(2 * math.pi * 330 * t)
                   + 0.03 * np.sin(2 * math.pi * 660 * t))
        # The fixture deliberately gives lead and backing a shared copy.
        lead = 0.5 * main
        backing = 0.5 * main + harmony
        vocals = lead + backing
        stereo = lambda signal: np.column_stack((signal, signal * 0.9)).astype("float32")
        source = np.column_stack((main + harmony, (main + harmony) * 0.9)).astype("float32")
        return {
            "source": source,
            "vocals": stereo(vocals),
            "lead": stereo(lead),
            "backing": stereo(backing),
            "instrumental": stereo(np.zeros_like(t)),
        }

    def _write_audio(self, job, waves=None):
        waves = waves or self._audio()
        audio = Path(job) / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        for name, value in waves.items():
            self.sf.write(str(audio / f"{name}.wav"), value, 44100, subtype="FLOAT")

    def _request(self):
        return {
            "version": 3,
            "source_kind": "local",
            "source_audio": str(self.root / "input.wav"),
            "source_title": "repair fixture",
            "input_file": "input/source.wav",
            "language": "zh",
            "lyrics": "",
            "lyric_lines": [],
            "backing_lyrics": "",
            "backing_lyric_lines": [],
            "zh_lyric_mode": "hanzi",
            "recognize_lyrics": False,
            "tools": {
                "vocal2midi": "dependencies/vocal2midi2",
                "separation_device": "cpu",
                "midi_device": "cpu",
                "dml_adapter": "auto",
                "cpu_threads": 0,
                "mdx_model": "dependencies/models/UVR-MDX-NET-Inst_HQ_3.onnx",
                "mdx_metadata": "dependencies/models/UVR-MDX-NET-Inst_HQ_3.json",
                "chorus_model": "models/UVR_MDXNET_KARA_2.onnx",
                "chorus_metadata": "models/UVR_MDXNET_KARA_2.json",
            },
            "voice_mode": "dual",
            "imported_stems": {},
            "separation_flow": "cascade",
            "midi_steps": 16,
            "bpm": None,
            "project_formats": [],
            "audio_formats": [],
            "project_export_enabled": False,
            "audio_export_enabled": False,
            "export_pitch": False,
            "include_instrumental": True,
            "alignment_engine": "hfa",
            "reference_dynamics": False,
            "dynamics_strength": 0.6,
            "expression_density": "original",
            "performance_style": "none",
            "style_strength": 0.6,
            "style_scope": "lead",
            "style_pitch": False,
            "prepare_only": True,
            "reference_tension": False,
            "reference_breathiness": False,
            "reference_voicing": False,
            "reference_mode": "local",
            "subtitles_mode": "midi",
            "midi_backend": "v2",
        }

    def _build_source(self):
        request = self._request()
        _write_json(self.source / "request.json", request)
        self._write_audio(self.source)
        input_path = self.source / "input/source.wav"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        self.sf.write(str(input_path), self._audio()["source"], 44100, subtype="FLOAT")
        input_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
        _write_json(self.source / "import-report.json", {
            "sha256": input_sha, "samples": len(self._audio()["source"]),
        })
        source_sha = agent_preparation._hash_file(self.source / "audio/source.wav")
        _write_json(self.source / "source-info.json", {"title": "fixture"})
        _write_json(self.source / "upstream-info.json", {})
        (self.source / "发布简介草稿.txt").write_text("fixture\n", encoding="utf-8")
        _write_json(self.source / "audio-info.json", {
            "format": "WAV", "subtype": "FLOAT", "sample_rate": 44100,
            "channels": 2, "frames": len(self._audio()["source"]), "sha256": source_sha,
        })
        stages = {}
        for stage in ("metadata", "download", "audio"):
            files = list(pipeline.requirements(stage, request))
            if stage == "metadata":
                files += ["发布简介草稿.txt"]
            from artifact_cache import receipt
            stages[stage] = {"state": "done", "seconds": 0,
                             "receipt": receipt(self.source, request, stage, files)}
        stages["separation"] = {
            "state": "failed", "started": 1.0,
            "error": "声部检查输出包含无效采样。",
            "error_details": {"code": "nonfinite_partition_output"},
        }
        status = {
            "schema": 1, "state": "failed", "steps": stages,
            "current_step": "separation",
            "error": "声部检查输出包含无效采样。",
            "planned_steps": ["metadata", "download", "audio", "separation", "midi"],
        }
        _write_json(self.source / "status.json", status)
        import task_contract
        task_contract.read(self.source)
        for stage in ("metadata", "download", "audio"):
            task_contract.index_stage(
                self.source, stage, pipeline.requirements(stage, request),
                receipt=stages[stage]["receipt"], replace=True)
        task_contract.fail_stage(
            self.source, "separation", "failed", "声部检查输出包含无效采样。")

    def app_context(self):
        from contextlib import ExitStack
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (common, pipeline, agent_preparation):
            stack.enter_context(patch.object(module, "ROOT", APP))
        return stack

    def plan(self):
        with self.app_context(), patch.object(
                agent_preparation, "_preflight", side_effect=_preflight):
            return repair_backend.plan_separation_repair(self.source)

    def apply(self, plan, *, cancel=None, progress=None):
        identity = lambda value: [("fixture", hashlib.sha256(
            str(value).encode("utf-8")).hexdigest(), 1)]
        with self.app_context(), patch.object(
                artifact_cache, "model_identity", side_effect=identity), patch.object(
                agent_preparation, "_preflight", side_effect=_preflight):
            return repair_backend.apply_separation_repair(
                plan, self.parent, cancel=cancel, progress=progress)

    def test_plan_binds_source_stems_and_real_reuse_prefix(self):
        plan = self.plan()
        self.assertEqual(plan["kind"], "separation_repair")
        self.assertEqual(plan["source_job"], str(self.source.resolve()))
        self.assertEqual(set(plan["audio"]), {"vocals", "lead", "backing"})
        self.assertEqual(plan["mode"], "dual")
        self.assertFalse(plan["model_execution"])
        self.assertEqual(plan["validation"]["frames"], len(self._audio()["source"]))
        self.assertLess(plan["validation"]["max_complementarity_error"], 1e-6)
        self.assertEqual(plan["reuse_plan"]["reuse"]["reuse_stages"],
                         ["metadata", "download", "audio"])
        self.assertEqual(plan["reuse_plan"]["reuse"]["run_stages"], ["separation", "midi"])
        unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
        self.assertEqual(plan["plan_sha256"], agent_preparation._digest(unsigned))
        json.dumps(plan, ensure_ascii=False, allow_nan=False)
        self.assertEqual(plan, self.plan())

    def test_apply_repairs_copy_registers_partial_and_replays_exactly(self):
        plan = self.plan()
        source_before = {
            path.relative_to(self.source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source.rglob("*") if path.is_file()
        }
        events = []
        first = self.apply(plan, progress=lambda done, total: events.append((done, total)))
        self.assertEqual(first["state"], "partial")
        self.assertEqual(first["status"], "repaired_partial")
        self.assertEqual(first["next_stage"], "midi")
        self.assertFalse(first["model_execution"])
        self.assertFalse(first["midi_executed"])
        job = Path(first["job_path"])
        self.assertTrue(job.is_dir())
        self.assertTrue(job.is_relative_to(self.parent.resolve()))
        self.assertEqual(first["source_job"], str(self.source.resolve()))
        self.assertEqual(first["reused_stages"], ["metadata", "download", "audio"])
        self.assertEqual(first["run_stages"], ["midi"])
        self.assertEqual(events[-1], (len(self._audio()["source"]), len(self._audio()["source"])))
        status = _read_json(job / "status.json")
        self.assertEqual(status["state"], "partial")
        self.assertNotIn("current_step", status)
        self.assertEqual(status["steps"]["separation"]["state"], "done")
        self.assertNotIn("midi", status["steps"])
        report = _read_json(job / "separation-report.json")
        self.assertEqual(report["method"], "reused_rawstems_partition_repair")
        self.assertFalse(report["model_execution"])
        receipt = _read_json(job / "separation-repair.json")
        self.assertFalse(receipt["model_execution"])
        self.assertEqual(receipt["mode"], "dual")
        self.assertEqual(receipt["source_job"], str(self.source.resolve()))
        self.assertIn("partition", receipt["diagnostics"])
        self.assertEqual(receipt["separation_receipt"],
                         status["steps"]["separation"]["receipt"])
        self.assertTrue(all(receipt["outputs"][name] ==
                            common.file_hash(job / name) for name in receipt["outputs"]))
        self.sf.info(str(job / "audio/vocals.wav"))
        self.assertEqual(self.sf.info(str(job / "audio/lead.wav")).frames,
                         len(self._audio()["source"]))
        source_after = {
            path.relative_to(self.source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source.rglob("*") if path.is_file()
        }
        self.assertEqual(source_before, source_after)
        second = self.apply(plan)
        self.assertTrue(second["replay"])
        self.assertEqual(second["job_path"], first["job_path"])
        self.assertEqual(second["repair_receipt_sha256"], first["repair_receipt_sha256"])
        self.assertEqual(second["request_sha256"], first["request_sha256"])
        self.assertEqual(second["separation_receipt_sha256"],
                         first["separation_receipt_sha256"])

    def test_plan_and_apply_reject_changed_source_or_audio(self):
        plan = self.plan()
        path = self.source / "audio/backing.wav"
        data = bytearray(path.read_bytes())
        data[-24] ^= 1
        path.write_bytes(data)
        with self.assertRaises(repair_backend.SeparationRepairError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, "separation_repair_plan_stale")
        self.assertTrue(caught.exception.details.get("action_suggestions"))
        changed_plan = self.plan()
        self.assertNotEqual(changed_plan["plan_sha256"], plan["plan_sha256"])

    def test_tampered_plan_is_rejected_before_new_job(self):
        plan = self.plan()
        changed = copy.deepcopy(plan)
        changed["audio"]["lead"]["sha256"] = "0" * 64
        before = tuple(self.parent.iterdir())
        with self.assertRaises(repair_backend.SeparationRepairError) as caught:
            self.apply(changed)
        self.assertEqual(caught.exception.code, "separation_repair_plan_tampered")
        self.assertEqual(tuple(self.parent.iterdir()), before)

    def test_audio_cache_checks_long_managed_paths(self):
        owned = self.root / ('nested-a-' + 'a'*65)
        job = owned / ('nested-b-' + 'b'*65) / ('nested-c-' + 'c'*65) / 'job'
        audio = job/'audio'
        Path(common.native_path(audio)).mkdir(parents=True)
        self.assertTrue(owned.resolve().is_relative_to(self.root.resolve()))
        self.addCleanup(shutil.rmtree, common.native_path(owned))
        self.assertGreater(len(str(audio/'instrumental.wav')), 260)
        for name, value in self._audio().items():
            self.sf.write(str(common.native_path(audio/f'{name}.wav')), value, 44100, subtype='FLOAT')
        self.assertTrue(artifact_cache.audio_valid(job, 'separation',
            ['audio/'+name+'.wav' for name in ('vocals', 'lead', 'backing', 'instrumental')], scan=True))

    def test_nonfinite_rawstem_fails_with_locations_and_no_nan_cleanup(self):
        waves = self._audio()
        waves["lead"][17, 1] = float("nan")
        self._write_audio(self.source, waves)
        with self.assertRaises(repair_backend.SeparationRepairError) as caught:
            self.plan()
        error = caught.exception
        self.assertEqual(error.code, "rawstem_nonfinite")
        self.assertEqual(error.details["role"], "lead")
        self.assertEqual(error.details["sample_range"], [17, 18])
        self.assertEqual(error.details["channels"], [1])
        suggestions = {row["action"] for row in error.details["action_suggestions"]}
        self.assertEqual(suggestions, {"rerun_separation", "new_single_job"})
        self.assertFalse(tuple(self.parent.iterdir()))
        lead = self.sf.read(str(self.source / "audio/lead.wav"), dtype="float32",
                            always_2d=True)[0]
        self.assertTrue(self.np.isnan(lead[17, 1]))

    def test_noncomplementary_rawstem_is_rejected(self):
        waves = self._audio()
        waves["backing"][3, 0] += 0.01
        self._write_audio(self.source, waves)
        with self.assertRaises(repair_backend.SeparationRepairError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "rawstem_non_complementary")
        self.assertGreater(caught.exception.details["max_abs_error"],
                           repair_backend.COMPLEMENTARITY_TOLERANCE)
        self.assertGreater(caught.exception.details["samples_over_threshold"], 0)
        self.assertFalse(tuple(self.parent.iterdir()))

    def test_completed_or_missing_separation_is_not_accepted_as_cache(self):
        status = _read_json(self.source / "status.json")
        status["steps"]["separation"]["state"] = "done"
        _write_json(self.source / "status.json", status)
        with self.assertRaises(repair_backend.SeparationRepairError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "separation_already_complete")
        status["steps"]["separation"] = {}
        _write_json(self.source / "status.json", status)
        with self.assertRaises(repair_backend.SeparationRepairError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "separation_not_recoverable")

    def test_cancellation_leaves_cancelled_state_and_never_claims_success(self):
        plan = self.plan()

        def cancel():
            return True

        with self.assertRaises(InterruptedError):
            self.apply(plan, cancel=cancel)
        self.assertFalse(tuple(self.parent.rglob("status.json")))
        self.assertFalse(tuple(self.parent.rglob("separation-repair.json")))

    def test_half_result_is_not_replayed_as_success(self):
        plan = self.plan()
        first = self.apply(plan)
        job = Path(first["job_path"])
        (job / "separation-repair.json").unlink()
        (job / "separation-report.json").write_text("{}", encoding="utf-8")
        before = tuple(self.parent.rglob("*"))
        with self.assertRaises(repair_backend.SeparationRepairError) as caught:
            self.apply(plan)
        self.assertEqual(caught.exception.code, "repair_target_incomplete")
        self.assertEqual(tuple(self.parent.rglob("*")), before)

    def test_missing_stage_receipt_blocks_repair_instead_of_trusting_failed_stage(self):
        status = _read_json(self.source / "status.json")
        status["steps"]["audio"].pop("receipt", None)
        _write_json(self.source / "status.json", status)
        (self.source / "audio-info.json").unlink()
        with self.assertRaises(repair_backend.SeparationRepairError) as caught:
            self.plan()
        self.assertEqual(caught.exception.code, "separation_prefix_incomplete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
