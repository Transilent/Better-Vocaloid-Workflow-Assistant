"""Contract tests for immutable Agent delivery bundles.

These tests use synthetic artifacts and operation records.  They do not run a
worker, model, synthesizer, exporter, network request, or real user task.  The
media collaborator is replaced with a local fixture that writes one audio file
and updates the staged SVP, so package identity is tested after portability.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path
import wave

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
sys.path.insert(0, str(SRC))

from agent_control import ControlError, io_path
from agent_delivery import DeliveryError, build_delivery, _digest_text

try:
    import agent_media as REAL_AGENT_MEDIA
except ImportError:
    REAL_AGENT_MEDIA = None


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    return digest_bytes(io_path(path).read_bytes())


class FakeControl:
    def __init__(self, root, allowed_root):
        self.root = Path(root).resolve()
        self.state = self.root / "state"
        self.assets = [self.root / "assets"]
        self.jobs = [self.root / "jobs"]
        self.config = {"allowed_delivery_roots": [str(allowed_root)]}
        for path in (self.state, self.assets[0], self.jobs[0], allowed_root,
                     self.state / "outputs"):
            io_path(path).mkdir(parents=True, exist_ok=True)
        self.artifacts = {}
        self.operations = {}
        self.score = {"notes": [
            {"note_id": "n1", "lyric": "风", "start_seconds": 0.0, "end_seconds": 1.0},
            {"note_id": "n2", "lyric": "雨", "start_seconds": 1.0, "end_seconds": 2.0},
        ], "timing": {"tempo_events": [{"position_tick": 0, "bpm": 120}],
                      "time_signature_events": [{"position_tick": 0, "numerator": 4,
                                                 "denominator": 4}]}}

    def write(self, name, data, kind="candidate"):
        path = self.assets[0] / name
        io_path(path.parent).mkdir(parents=True, exist_ok=True)
        io_path(path).write_bytes(data)
        return self.register_artifact(path, kind=kind)

    def register_artifact(self, path, kind="candidate"):
        path = Path(path).resolve()
        raw = io_path(path).read_bytes()
        sha = digest_bytes(raw)
        identifier = "art_" + _digest_text({"path": str(path), "sha256": sha,
                                            "kind": kind})[:32]
        record = {"id": identifier, "path": str(path), "sha256": sha,
                  "bytes": len(raw), "kind": kind}
        self.artifacts[identifier] = record
        return copy.deepcopy(record)

    def artifact(self, identifier):
        if identifier not in self.artifacts:
            raise ControlError("not_found", "fixture artifact not found")
        return copy.deepcopy(self.artifacts[identifier])

    def operation_get(self, identifier):
        if identifier not in self.operations:
            raise ControlError("not_found", "fixture operation not found")
        return copy.deepcopy(self.operations[identifier])

    def score_read(self, identifier):
        self.artifact(identifier)
        return copy.deepcopy(self.score)


class MediaFixture:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def relocate_svp_media(self, source_svp, candidate_svp, allowed_roots=None):
        self.calls.append({"source": str(source_svp), "candidate": str(candidate_svp),
                           "allowed_roots": [str(path) for path in (allowed_roots or [])]})
        if self.fail:
            raise ValueError("fixture media permission failure")
        candidate = Path(candidate_svp)
        audio = candidate.parent / "伴奏_instrumental.wav"
        io_path(audio).write_bytes(b"RIFF-fixture-instrumental")
        text = io_path(candidate).read_text(encoding="utf-8")
        io_path(candidate).write_text(text.rstrip() + "\n", encoding="utf-8")
        return {"status": "relocated", "instrumental_included": True,
                "relocated": [{"name": audio.name, "role": "instrumental"}]}


class DeliveryTestBase(unittest.TestCase):
    def setUp(self):
        work = APP / "work"
        io_path(work).mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="agent-delivery-",
                                          dir=str(work))).resolve()
        self.addCleanup(self.cleanup)
        self.allowed = self.root / "allowed"
        self.control = FakeControl(self.root, self.allowed)
        self.media = MediaFixture()
        self._patch_media(self.media)

    def cleanup(self):
        target = Path(self.root).resolve()
        work = (APP / "work").resolve()
        self.assertNotEqual(target, work)
        self.assertTrue(target.is_relative_to(work))
        shutil.rmtree(io_path(target))

    def _patch_media(self, fixture):
        module = types.ModuleType("agent_media")
        module.relocate_svp_media = fixture.relocate_svp_media
        old = sys.modules.get("agent_media")
        sys.modules["agent_media"] = module

        def restore():
            if old is None:
                sys.modules["agent_media"] = None
            else:
                sys.modules["agent_media"] = old
        self.addCleanup(restore)

    def make_workflow(self, *, styled=False):
        source = self.control.write("source.mid", b"MThd-fixture", kind="input")
        project = self.control.write(
            "song.svp", json.dumps({"tracks": [], "fixture": "export"},
                                   ensure_ascii=False).encode("utf-8"))
        summary = {
            "can_accept": True,
            "blockers": [],
            "warnings": [],
            "information": [],
            "required_loss_codes": ["tempo_table_normalized"],
            "verified": {"notes": 2, "lyrics": 2},
            "report_sha256": "a" * 64,
            "receipt_sha256": "b" * 64,
            "candidates": [{"artifact_id": project["id"], "format": "svp",
                            "sha256": project["sha256"]}],
            "independent_audit": {"kind": "vwa-export-audit",
                                  "status": "verified", "blockers": [],
                                  "warnings": [], "audit_sha256": "c" * 64,
                                  "source_sha256": source["sha256"],
                                  "acoustic_or_phonetic_verified": False},
        }
        review = {"decision": "accept", "report_sha256": summary["report_sha256"],
                  "rationale": "已核对本次测试音符、歌词和实际秒数。",
                  "accepted_loss_codes": ["tempo_table_normalized"],
                  "reviewed_at": 100.0}
        workflow = {
            "id": "flow_" + "1" * 32, "revision": 7, "stage": "ready",
            "code_revision": "fixture-code", "updated": 200.0,
            "score_artifact_id": source["id"],
            "project_artifact_id": project["id"],
            "project_artifacts": [project], "final_artifacts": [project],
            "export_summary": summary, "export_review": review,
            "history": [{"action": "project_ready", "revision": 7}],
        }
        return workflow

    def add_style(self, workflow):
        project = workflow["project_artifacts"][0]
        styled = self.control.write("song-styled.svp", json.dumps(
            {"tracks": [], "fixture": "styled"}, ensure_ascii=False).encode("utf-8"))
        receipt_path = self.control.state / "outputs" / "tuning-receipt.json"
        io_path(receipt_path.parent).mkdir(parents=True, exist_ok=True)
        io_path(receipt_path).write_text(json.dumps({
            "output_sha256": styled["sha256"], "source_sha256": project["sha256"],
            "status": "partial"}, ensure_ascii=False), encoding="utf-8")
        receipt_sha = digest_file(receipt_path)
        operation_id = "op_fixture_style"
        self.control.operations[operation_id] = {
            "id": operation_id, "state": "succeeded",
            "result": {"artifacts": [styled], "receipt": {
                "candidate_path": styled["path"],
                "candidate_sha256": styled["sha256"],
                "source_sha256": project["sha256"],
                "receipt": str(receipt_path), "receipt_sha256": receipt_sha,
                "session_id": "session-fixture",
                "report": {"status": "partial",
                           "partial_reason_count": 1,
                           "partial_reason_counts": {"unsupported_curve": 1},
                           "applied_controls": 28, "skipped_controls": 2,
                           "protected_note_count": 8,
                           "style_regions": [{"start": 0.0, "end": 1.0}]}}}}
        workflow.update(project_artifact_id=styled["id"],
                        final_artifacts=[styled], operation_id=operation_id,
                        history=[{"action": "project_ready", "revision": 6},
                                 {"action": "styled_project_ready", "revision": 7}])
        return workflow, styled, receipt_path


class DeliveryIntegrityTests(DeliveryTestBase):
    def test_preparation_job_workflow_and_manifest_share_source_identity(self):
        import agent_preparation
        import common
        import pipeline
        from agent_workflow import Workflow
        sys.path.insert(0, str(APP / 'tests'))
        from test_agent_workflow import FakeControl as WorkflowControl
        control = WorkflowControl(self.root / 'workflow-fixture')
        audio = self.control.assets[0] / 'audio.wav'
        io_path(audio).write_bytes(b'local-audio-fixture')
        url = 'https://www.bilibili.com/video/BV1xx411c7mD/'
        with patch.object(agent_preparation, '_preflight', return_value={}), \
             patch.object(common, 'ROOT', APP), patch.object(pipeline, 'ROOT', APP):
            plan = agent_preparation.plan_preparation(str(audio), source_url=url + '?signature=discard')
            created = agent_preparation.create_preparation(plan, control.jobs[0])
        job = Path(created['job_path'])
        identifier = 'job_' + 'f' * 32
        record = {'id': identifier, 'path': str(job), 'request_sha256': created['request_sha256']}
        io_path(control._record_path('preparations', identifier, 'job')).write_text(
            json.dumps(record), encoding='utf-8')
        with patch.object(control, 'job_path', return_value=job):
            workflow = Workflow(control).create(preparation_job_id=identifier, idempotency_key='fixture')
        ready = self.make_workflow()
        ready['source_evidence'] = workflow['source_evidence']
        result = build_delivery(self.control, ready)
        manifest = json.loads(io_path(Path(result['manifest_path'])).read_text(encoding='utf-8'))
        self.assertEqual(manifest['source_evidence']['local_audio_sha256'], plan['source']['sha256'])
        self.assertEqual(manifest['source_evidence']['source_provenance'], plan['source_provenance'])
        self.assertEqual(manifest['source_evidence']['preparation_request_sha256'], created['request_sha256'])
        self.assertNotIn('signature=discard', json.dumps(manifest))

    def test_declared_source_is_in_manifest_and_delivery_identity(self):
        workflow = self.make_workflow()
        workflow['source_evidence'] = {
            'source_provenance': {'kind': 'caller_supplied_unverified',
                                  'url': 'https://www.bilibili.com/video/BV1xx411c7mD/'},
            'local_audio_sha256': 'a' * 64, 'preparation_request_sha256': 'b' * 64}
        result = build_delivery(self.control, workflow)
        manifest = json.loads(io_path(Path(result['manifest_path'])).read_text(encoding='utf-8'))
        self.assertEqual(manifest['source_evidence'], workflow['source_evidence'])
        self.assertIn('未验证', manifest['limitations'][-1])
        self.assertTrue(build_delivery(self.control, workflow)['reused'])
        changed = copy.deepcopy(workflow)
        changed['source_evidence']['local_audio_sha256'] = 'c' * 64
        with self.assertRaises(DeliveryError):
            build_delivery(self.control, changed)

    def assert_delivery_error(self, code, *args, **kwargs):
        with self.assertRaises(DeliveryError) as raised:
            build_delivery(self.control, *args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertIsInstance(raised.exception.details, dict)
        self.assertTrue(raised.exception.remediation)
        return raised.exception

    def test_requires_ready_stage_and_accepted_current_report(self):
        workflow = self.make_workflow()
        workflow["stage"] = "project_ready"
        error = self.assert_delivery_error("delivery_workflow_not_ready", workflow)
        self.assertFalse(error.retryable)
        self.assertEqual(self.media.calls, [])

        workflow = self.make_workflow()
        workflow["export_review"]["decision"] = "needs_revision"
        self.assert_delivery_error("delivery_export_review_required", workflow)
        self.assertEqual(self.media.calls, [])

        workflow = self.make_workflow()
        workflow["export_review"]["accepted_loss_codes"] = []
        self.assert_delivery_error("delivery_export_losses_unacknowledged", workflow)
        self.assertEqual(self.media.calls, [])

    def test_default_package_is_auditable_immutable_and_idempotent(self):
        workflow = self.make_workflow()
        first = build_delivery(self.control, workflow)
        expected = (self.control.state / "outputs" / "deliveries" / workflow["id"]
                    / str(workflow["revision"]) / "manifest.json")
        self.assertEqual(Path(first["manifest_path"]), expected.resolve())
        self.assertFalse(first["reused"])
        self.assertEqual(first["sha256"], first["manifest_sha256"])
        self.assertEqual(first['manifest_file_sha256'],digest_file(expected))
        self.assertTrue(all(Path(row['path']).is_absolute() for row in first['files']))
        roots = self.media.calls[0]["allowed_roots"]
        self.assertTrue(all(Path(path).is_dir() for path in roots[:3]))
        self.assertTrue(Path(self.media.calls[0]["candidate"]).parent.is_relative_to(Path(roots[3]).parent))

        manifest = json.loads(io_path(expected).read_text(encoding="utf-8"))
        self.assertEqual(manifest["kind"], "vwa.delivery-manifest")
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["manifest_sha256"], _digest_text({
            key: value for key, value in manifest.items() if key != "manifest_sha256"}))
        roles = {row["role"]: row for row in manifest["files"]}
        self.assertEqual(set(roles), {"final_project", "instrumental"})
        packaged_project = expected.parent / roles["final_project"]["name"]
        self.assertEqual(digest_file(packaged_project), roles["final_project"]["sha256"])
        self.assertNotEqual(roles["final_project"]["source_sha256"],
                            roles["final_project"]["sha256"])
        audio = expected.parent / roles["instrumental"]["name"]
        self.assertEqual(digest_file(audio), roles["instrumental"]["sha256"])
        sums = (expected.parent / "SHA256SUMS.txt").read_text(encoding="utf-8")
        for row in manifest["files"]:
            self.assertIn(row["sha256"] + "  " + row["name"], sums)
        self.assertIn("声库合成", manifest["not_run"])
        self.assertNotIn("accounts", expected.read_text(encoding="utf-8").lower())

        before = expected.read_bytes()
        second = build_delivery(self.control, copy.deepcopy(workflow))
        self.assertTrue(second["reused"])
        self.assertEqual(second["manifest_sha256"], first["manifest_sha256"])
        self.assertEqual(len(self.media.calls), 1)
        self.assertEqual(expected.read_bytes(), before)

    def test_external_destination_needs_config_root_and_name_stays_inside(self):
        workflow = self.make_workflow()
        package_root = self.allowed / "nested"
        result = build_delivery(self.control, workflow,
                                destination=package_root, name="交付包")
        self.assertEqual(Path(result["manifest_path"]),
                         (package_root / "交付包" / "manifest.json").resolve())
        self.assertTrue((package_root / "交付包" / "README.txt").is_file())
        self.assertTrue((package_root/'交付包'/'交付包.svp').is_file())

        outside = self.root / "outside"
        error = self.assert_delivery_error("outside_delivery_roots", workflow,
                                           destination=outside)
        self.assertFalse(io_path(outside).exists())
        self.assertEqual(error.component, "delivery")

        self.assert_delivery_error("invalid_delivery_name", workflow,
                                   destination=self.allowed, name="../escape")
        self.assertFalse((self.allowed / "escape").exists())

    def test_changed_evidence_fails_before_package_creation(self):
        workflow = self.make_workflow()
        project = self.control.artifact(workflow["project_artifact_id"])
        io_path(project["path"]).write_bytes(b"changed after supervision")
        target = self.allowed / "stale-package"
        self.assert_delivery_error("delivery_artifact_invalid", workflow,
                                   destination=target)
        self.assertFalse(io_path(target).exists())
        self.assertEqual(self.media.calls, [])

    def test_different_request_never_overwrites_existing_delivery(self):
        workflow = self.make_workflow()
        target = self.allowed / "stable-package"
        first = build_delivery(self.control, workflow, destination=target)
        original = Path(first["manifest_path"]).read_bytes()
        changed = copy.deepcopy(workflow)
        changed["revision"] += 1
        self.assert_delivery_error("delivery_conflict", changed, destination=target)
        self.assertEqual(Path(first["manifest_path"]).read_bytes(), original)

    def test_media_failure_cleans_staging_and_does_not_publish(self):
        self.media.fail = True
        workflow = self.make_workflow()
        target = self.allowed / "failed-package"
        self.assert_delivery_error("delivery_media_failed", workflow,
                                   destination=target)
        self.assertFalse(io_path(target).exists())
        leftovers = list((target.parent).glob("*.tmp-*"))
        self.assertEqual(leftovers, [])

    def test_style_partial_and_real_tuning_receipt_are_included(self):
        workflow = self.make_workflow()
        workflow, styled, receipt_path = self.add_style(workflow)
        target = self.allowed / "styled-package"
        result = build_delivery(self.control, workflow, destination=target)
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        style = manifest["audit"]["style"]
        self.assertTrue(style["applied"])
        self.assertTrue(style["partial"])
        self.assertEqual(style["partial_reason_counts"], {"unsupported_curve": 1})
        self.assertEqual(style["applied_controls"], 28)
        self.assertEqual(style["protected_note_count"], 8)
        receipt = next(row for row in manifest["files"] if row["role"] == "tuning_receipt")
        copied_receipt = Path(result["manifest_path"]).parent / receipt["name"]
        self.assertEqual(digest_file(copied_receipt), receipt["sha256"])
        self.assertEqual(io_path(copied_receipt).read_bytes(), io_path(receipt_path).read_bytes())
        project = next(row for row in manifest["files"] if row["role"] == "final_project")
        self.assertEqual(project["artifact_id"], styled["id"])
        self.assertEqual(project["source_sha256"], styled["sha256"])

    def test_missing_media_collaborator_fails_with_retryable_code(self):
        sys.modules["agent_media"] = None
        workflow = self.make_workflow()
        target = self.allowed / "no-media-module"
        error = self.assert_delivery_error("delivery_media_unavailable", workflow,
                                           destination=target)
        self.assertTrue(error.retryable)
        self.assertFalse(io_path(target).exists())




    def test_unknown_existing_files_are_preserved_and_force_conflict(self):
        workflow = self.make_workflow()
        target = self.allowed / "unknown-directory"
        io_path(target).mkdir(parents=True, exist_ok=True)
        marker = target / "keep.txt"
        io_path(marker).write_text("user data", encoding="utf-8")
        self.assert_delivery_error("delivery_conflict", workflow, destination=target)
        self.assertEqual(io_path(marker).read_text(encoding="utf-8"), "user data")
        self.assertEqual([item.name for item in io_path(target).iterdir()], [marker.name])

        package = self.allowed / "valid-package"
        build_delivery(self.control, workflow, destination=package)
        extra = package / "unexpected.bin"
        io_path(extra).write_bytes(b"keep")
        self.assert_delivery_error("delivery_conflict", copy.deepcopy(workflow),
                                   destination=package)
        self.assertEqual(io_path(extra).read_bytes(), b"keep")


@unittest.skipUnless(REAL_AGENT_MEDIA, "agent_media collaborator is unavailable")
class RealMediaContractTests(DeliveryTestBase):
    def test_real_no_media_result_is_recorded_without_fabricating_a_file(self):
        sys.modules["agent_media"] = REAL_AGENT_MEDIA
        workflow = self.make_workflow()
        target = self.allowed / "real-media-no-op"
        result = build_delivery(self.control, workflow, destination=target)
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        evidence = manifest["media"]["evidence"]
        self.assertFalse(evidence["included"])
        self.assertEqual(evidence["reason"], "source_has_no_supported_media")
        self.assertFalse(any(row["role"] == "instrumental" for row in manifest["files"]))
        self.assertEqual(len(manifest["files"]), 1)



    def test_real_media_relocation_updates_packaged_project_hash(self):
        sys.modules["agent_media"] = REAL_AGENT_MEDIA
        workflow = self.make_workflow()
        audio_path = self.control.assets[0] / "伴奏.wav"
        with io_path(audio_path).open("wb") as stream:
            with wave.open(stream, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\0\0" * 8000)
        svp = {"tracks": [
            {"name": "演唱", "mainRef": {}, "mainGroup": {"notes": []}},
            {"name": "伴奏", "mainRef": {
                "isInstrumental": True,
                "audio": {"filename": audio_path.name, "duration": 1.0},
            }, "mainGroup": {"notes": []}},
        ]}
        project = self.control.write(
            "with-media.svp",
            json.dumps(svp, ensure_ascii=False).encode("utf-8"))
        workflow.update(project_artifact_id=project["id"],
                        project_artifacts=[project], final_artifacts=[project])
        workflow["export_summary"]["candidates"] = [{
            "artifact_id": project["id"], "format": "svp",
            "sha256": project["sha256"]}]
        target = self.allowed / "real-media-package"
        result = build_delivery(self.control, workflow, destination=target)
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        evidence = manifest["media"]["evidence"]
        self.assertTrue(evidence["included"])
        self.assertEqual(evidence["count"], 1)
        project_row = next(row for row in manifest["files"] if row["role"] == "final_project")
        audio_row = next(row for row in manifest["files"] if row["role"] == "instrumental")
        packaged_svp = Path(result["manifest_path"]).parent / project_row["name"]
        packaged_audio = Path(result["manifest_path"]).parent / audio_row["name"]
        self.assertEqual(digest_file(packaged_svp), project_row["sha256"])
        self.assertEqual(digest_file(packaged_audio), audio_row["sha256"])
        self.assertNotEqual(project_row["sha256"], project["sha256"])
        self.assertEqual(project_row["source_sha256"], project["sha256"])
        relocated = json.loads(io_path(packaged_svp).read_text(encoding="utf-8"))
        self.assertEqual(relocated["tracks"][1]["mainRef"]["audio"]["filename"],
                         audio_row["name"])



if __name__ == "__main__":
    unittest.main()
