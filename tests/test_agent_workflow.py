"""Contract tests for the supervised Agent workflow coordinator.

The tests use synthetic operation results and tiny local MIDI fixtures. They do
not launch the MCP worker, call a model, touch the network, or use a real user
job. One combined lyric/note test uses the real MIDI workers because that is
where stable note-ID remapping must be proven.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
sys.path.insert(0, str(SRC))

import mido

from agent_control import ControlError, digest, io_path, read_json
from agent_lyrics import read_lyrics
from agent_score import read_score
from agent_workflow import Workflow
from common import native_path


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(io_path(path).read_bytes())


def write_midi(path):
    path = Path(path)
    io_path(path.parent).mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo", time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=1920),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead 歌詞", time=0),
        mido.MetaMessage("lyrics", text="风", time=0),
        mido.Message("note_on", channel=2, note=60, velocity=91, time=0),
        mido.Message("note_off", channel=2, note=60, velocity=0, time=480),
        mido.MetaMessage("lyrics", text="雨", time=0),
        mido.Message("note_on", channel=2, note=62, velocity=73, time=0),
        mido.Message("note_off", channel=2, note=62, velocity=0, time=480),
        mido.Message("note_on", channel=2, note=64, velocity=82, time=0),
        mido.Message("note_off", channel=2, note=64, velocity=0, time=480),
        mido.MetaMessage("marker", text="keep-tail", time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    with open(native_path(path), "wb") as stream:
        midi.save(file=stream)
    return path


class FakeControl:
    """Private control double; records are still JSON-copy safe and ordered."""

    code_revision = "code-rev-private"

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.state = self.root / "state"
        self.assets = [self.root / "assets"]
        self.jobs = [self.root / "jobs"]
        for path in (self.state, *self.assets, *self.jobs):
            io_path(path).mkdir(parents=True, exist_ok=True)
        for name in ("workflows", "operations", "locks", "artifacts", "plans"):
            io_path(self.state / name).mkdir(parents=True, exist_ok=True)
        self.records = {"workflows": {}, "operations": {}, "artifacts": {}, "plans": {}}
        self.next_operation = 0
        self.preparation_calls = []
        self.preparation_starts = []
        self.plan_calls = []
        self.plan_starts = []
        self.worker_calls = []

    def _record_path(self, category, identifier, prefix):
        if not isinstance(identifier, str) or not identifier.startswith(prefix + "_"):
            raise ControlError("invalid_id", "bad private fixture id")
        return self.state / category / (identifier + ".json")

    def _record(self, category, identifier, prefix):
        path = self._record_path(category, identifier, prefix)
        if not io_path(path).is_file():
            raise ControlError("not_found", "private fixture record not found")
        return read_json(path)

    def artifact(self, identifier):
        try:
            value = copy.deepcopy(self.records["artifacts"][identifier])
        except KeyError as error:
            raise ControlError("not_found", "artifact not found") from error
        if sha256_file(value["path"]) != value["sha256"]:
            raise ControlError("stale_artifact", "fixture artifact changed")
        return value

    def import_asset(self, path):
        path = Path(path).resolve()
        sha = sha256_file(path)
        identifier = "art_" + digest({"path": str(path), "sha256": sha})[:32]
        value = {"schema": 1, "id": identifier, "path": str(path), "sha256": sha,
                 "bytes": io_path(path).stat().st_size, "kind": "input",
                 "job_id": None, "origin": None}
        self.records["artifacts"][identifier] = copy.deepcopy(value)
        return copy.deepcopy(value)

    def register_artifact(self, path):
        return self.import_asset(path)

    def preparation_plan(self, source, options):
        self.preparation_calls.append((source, copy.deepcopy(options)))
        return {"id": "plan_" + digest({"source": source, "options": options})[:32]}

    def preparation_create(self, plan_id):
        self.preparation_calls.append(("create", plan_id))
        return {"job_id": "job_" + "1" * 32, "request_sha256": "a" * 64}

    def preparation_start(self, job_id, until, idempotency_key):
        self.preparation_starts.append((job_id, until, idempotency_key))
        return self._operation("preparation_run", {"job_id": job_id}, idempotency_key)

    def _operation(self, action, arguments, idempotency_key):
        request = {"action": action, "arguments": copy.deepcopy(arguments),
                   "code_revision": self.code_revision}
        for identifier, previous in self.records["operations"].items():
            if (previous["request"] == request
                    and previous.get("idempotency_key") == idempotency_key):
                return copy.deepcopy(previous)
            if (previous.get("idempotency_key") == idempotency_key
                    and previous["request"] != request):
                raise ControlError("idempotency_conflict", "operation key reused")
        self.next_operation += 1
        identifier = "op_" + ("%032x" % self.next_operation)
        value = {"schema": 1, "id": identifier, "state": "queued",
                 "request": request, "idempotency_key": idempotency_key,
                 "created": float(self.next_operation)}
        self.records["operations"][identifier] = copy.deepcopy(value)
        return copy.deepcopy(value)

    def start_operation(self, action, arguments, idempotency_key):
        return self._operation(action, arguments, idempotency_key)

    def operation_get(self, identifier):
        return copy.deepcopy(self.records["operations"][identifier])

    def update_operation(self, identifier, **changes):
        value = self.records["operations"][identifier]
        value.update(copy.deepcopy(changes))
        value["updated"] = float(self.next_operation)
        return copy.deepcopy(value)

    def set_operation(self, identifier, state, result=None, error=None):
        value = self.records["operations"][identifier]
        value["state"] = state
        if result is not None:
            value["result"] = copy.deepcopy(result)
        if error is not None:
            value["error"] = copy.deepcopy(error)
        return copy.deepcopy(value)

    def call_worker(self, action, arguments):
        self.worker_calls.append((action, copy.deepcopy(arguments)))
        score = read_score(arguments["path"])
        lyrics = read_lyrics(arguments["path"], arguments.get("language", "zh"))
        return {"score": score, "score_review": {"hints": []},
                "lyrics": lyrics, "lyric_review": {"hints": []}}

    def make_plan(self, kind, artifact_id, **arguments):
        self.plan_calls.append((kind, artifact_id, copy.deepcopy(arguments)))
        plan = {"id": "plan_" + digest({"kind": kind, "artifact": artifact_id,
                                         **arguments})[:32],
                "kind": kind, "artifact_id": artifact_id,
                "plan": {"kind": kind, "source": artifact_id}}
        return plan

    def start_plan(self, plan_id, kind, idempotency_key):
        self.plan_starts.append((plan_id, kind, idempotency_key))
        return self._operation(kind + "_render",
                               {"plan_id": plan_id}, idempotency_key)

    def review_success(self, identifier, artifact_id):
        return {"artifact_id": artifact_id, "source_sha256": self.artifact(artifact_id)["sha256"],
                "original_artifact_id": artifact_id, "rationale": "checked",
                "receipts": [], "lyric_review": {"hints": []},
                "caller_reviewed": True, "semantic_model_used": False}


class WorkflowTestBase(unittest.TestCase):
    def setUp(self):
        work = Path(native_path(APP / "work"))
        io_path(work).mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="agent-workflow-", dir=str(work))).resolve()
        self.addCleanup(self.cleanup_temporary)
        self.control = FakeControl(self.root)
        self.workflow = Workflow(self.control)
        self.midi = write_midi(self.root / "assets" / "小样.mid")

    def cleanup_temporary(self):
        target = Path(native_path(self.root)).resolve()
        work = Path(native_path(APP / "work")).resolve()
        self.assertNotEqual(target, work)
        self.assertTrue(target.is_relative_to(work), "temporary path escaped private work")
        shutil.rmtree(native_path(target))
        self.assertFalse(io_path(target).exists())

    def create_midi_flow(self, key="flow retry 1", **overrides):
        artifact = self.control.import_asset(self.midi)
        arguments = {"score_artifact_id": artifact["id"], "idempotency_key": key,
                     "brief": "", "reference_lyrics": ""}
        arguments.update(overrides)
        return self.workflow.create(**arguments), artifact

    def create_source_flow(self, key="source retry 1", **overrides):
        arguments = {"source": "BV1xx411c7mD", "idempotency_key": key,
                     "brief": "", "reference_lyrics": "", "options": {"language": "zh"}}
        arguments.update(overrides)
        return self.workflow.create(**arguments)

    def succeed(self, value, result):
        operation_id = value["operation_id"]
        self.control.set_operation(operation_id, "succeeded", result=result)
        return value

    def apply_review(self, value, **arguments):
        return self.workflow.apply_review(
            value["id"], value["revision"], arguments.pop("rationale", "试听并核对全部音符与歌词。"),
            **arguments)

    def complete_review(self, value, note_edits=None, lyric_edits=None):
        pending = self.apply_review(value, note_edits=note_edits or [],
                                    lyric_edits=lyric_edits or [])
        reviewed = self.succeed(pending, self.control.review_success(
            pending["id"], pending["score_artifact_id"]))
        accepted = self.workflow.advance(reviewed["id"], reviewed["revision"])
        self.assertEqual(accepted["stage"], "reviewed")
        return accepted

    def complete_export(self, value, artifacts):
        exporting = self.workflow.advance(value["id"], value["revision"])
        self.assertEqual(exporting["stage"], "exporting")
        operation = exporting["operation"]
        self.control.set_operation(operation["id"], "succeeded",
                                   result={"receipt": {"candidates": artifacts},
                                           "artifacts": artifacts})
        project = self.workflow.advance(exporting["id"], exporting["revision"])
        self.assertEqual(project["stage"], "project_ready")
        return project


class WorkflowCreateGetTests(WorkflowTestBase):
    def test_create_same_key_replays_and_different_request_conflicts(self):
        first, artifact = self.create_midi_flow()
        second, artifact_again = self.create_midi_flow()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(artifact["id"], artifact_again["id"])
        self.assertEqual(second["stage"], "review")
        with self.assertRaises(ControlError) as raised:
            self.create_midi_flow(brief="不同请求")
        self.assertEqual(raised.exception.code, "idempotency_conflict")
        self.assertEqual(len(list(io_path(self.control.state / "workflows").glob("flow_*.json"))), 1)

    def test_source_create_validates_and_get_exposes_revision_and_next_step(self):
        created = self.create_source_flow(options={"language": "ja"},
                                          reference_lyrics="君の声")
        self.assertEqual(created["stage"], "created")
        self.assertEqual(created["next"]["tool"], "vwa_workflow_advance")
        self.assertEqual(created["revision"], 0)
        reread = self.workflow.get(created["id"])
        self.assertEqual(reread["revision"], created["revision"])
        self.assertEqual(reread["next"], created["next"])
        self.assertFalse(reread["completed"])
        with self.assertRaises(ControlError) as raised:
            self.create_source_flow(brief="needs svp", formats=["ustx"])
        self.assertEqual(raised.exception.code, "style_requires_svp")

    def test_bad_revision_and_code_revision_are_distinct(self):
        created = self.create_source_flow()
        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(created["id"], created["revision"] + 1)
        self.assertEqual(raised.exception.code, "workflow_conflict")
        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(created["id"], True)
        self.assertEqual(raised.exception.code, "workflow_conflict")
        self.control.code_revision = "code-rev-new"
        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(created["id"], created["revision"])
        self.assertEqual(raised.exception.code, "stale_workflow")


class WorkflowReviewTests(WorkflowTestBase):
    def test_review_bundle_is_real_and_bound_to_current_revision(self):
        created, _ = self.create_midi_flow()
        bundle = self.workflow.review(created["id"], limit=2, offset=0)
        self.assertEqual(bundle["revision"], created["revision"])
        self.assertEqual(bundle["total_notes"], 3)
        self.assertEqual(len(bundle["evidence"]["score"]["notes"]), 2)
        self.assertEqual(bundle["next_offset"], 2)
        self.assertEqual(bundle["evidence"]["score"]["source_sha256"],
                         self.control.artifact(created["score_artifact_id"])["sha256"])
        self.assertEqual(bundle["decision_tool"], "vwa_workflow_apply_review")
        self.assertTrue(self.control.worker_calls)

    def test_apply_review_requires_reason_and_does_not_export_before_advance(self):
        created, _ = self.create_midi_flow()
        with self.assertRaises(ControlError) as raised:
            self.workflow.apply_review(created["id"], created["revision"], "   ")
        self.assertEqual(raised.exception.code, "review_rationale_required")
        pending = self.apply_review(created)
        self.assertEqual(pending["stage"], "reviewing")
        self.assertEqual(pending["next"]["tool"], "vwa_workflow_get")
        self.assertFalse(pending["completed"])
        observed = self.workflow.advance(pending["id"], pending["revision"])
        self.assertEqual(observed["stage"], "reviewing")
        self.assertFalse(observed["completed"])
        self.assertFalse(self.control.plan_calls)

    def test_unreviewed_preparation_cannot_advance_to_export(self):
        created = self.create_source_flow()
        preparing = self.workflow.advance(created["id"], created["revision"])
        self.assertEqual(preparing["stage"], "preparing")
        operation = preparing["operation"]
        artifact = self.control.import_asset(self.midi)
        self.control.set_operation(operation["id"], "succeeded", result={
            "receipt": {"files": [{"path": str(self.midi), "sha256": artifact["sha256"]}]},
            "job_id": "job_" + "1" * 32})
        review = self.workflow.advance(preparing["id"], preparing["revision"])
        self.assertEqual(review["stage"], "review")
        self.assertFalse(review["completed"])
        observed = self.workflow.advance(review["id"], review["revision"])
        self.assertEqual(observed["stage"], "review")
        self.assertFalse(observed["completed"])
        self.assertFalse(self.control.plan_calls)

    def test_failed_or_cancelled_review_is_not_completed_or_exportable(self):
        fresh, _ = self.create_midi_flow(key="cancel review")
        pending = self.apply_review(fresh, rationale="保留原音符并核对歌词。")
        self.control.set_operation(pending["operation_id"], "cancelled",
                                   error={"code": "cancelled", "message": "用户取消"})
        inspected = self.workflow.get(pending["id"])
        self.assertFalse(inspected["completed"])
        self.assertEqual(inspected["next"]["tool"], "vwa_workflow_advance")
        self.assertEqual(inspected["next"]["arguments"]["retry"], True)
        retryable = self.workflow.advance(inspected["id"], inspected["revision"], retry=True)
        self.assertEqual(retryable["stage"], "review")
        self.assertFalse(retryable["completed"])

    def test_changed_reviewed_midi_blocks_export(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        before = io_path(self.midi).read_bytes()
        io_path(self.midi).write_bytes(before + b"\\0")
        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(reviewed["id"], reviewed["revision"])
        self.assertEqual(raised.exception.code, "stale_artifact")
        self.assertEqual(reviewed["stage"], "reviewed")
        self.assertFalse(self.control.plan_calls)

    def test_lyrics_and_note_edits_together_remap_stable_ids(self):
        created, _ = self.create_midi_flow()
        before = read_score(self.midi)
        target = next(note for note in before["notes"] if note["pitch"] == 62)
        lyric_edit = {"note_id": target["note_id"], "lyric": "雲", "operation": "replace"}
        note_edit = {"note_id": target["note_id"], "pitch": 63}
        pending = self.apply_review(created, lyric_edits=[lyric_edit], note_edits=[note_edit])
        self.assertEqual(pending["stage"], "reviewing")
        output = self.root / "review-private-output"
        io_path(output).mkdir(parents=True, exist_ok=True)
        operation_id = pending["operation_id"]
        result = self.workflow.execute_review(operation_id,
            self.control.records["operations"][operation_id]["request"]["arguments"],
            output, lambda: None, lambda text: None)
        after = read_score(self.control.artifact(result["artifact_id"])["path"])
        by_pitch = {note["pitch"]: note for note in after["notes"]}
        self.assertEqual(by_pitch[63]["lyric"], "雲")
        self.assertNotEqual(by_pitch[63]["note_id"], target["note_id"])
        self.assertEqual(by_pitch[60]["lyric"], before["notes"][0]["lyric"])
        self.assertEqual(by_pitch[64]["lyric"], before["notes"][2]["lyric"])
        self.assertEqual(len(result["receipts"]), 2)
        self.assertTrue(result["caller_reviewed"])
        self.assertFalse(result["semantic_model_used"])

    def test_review_rejects_stale_note_id_with_real_midi(self):
        created, _ = self.create_midi_flow()
        pending = self.apply_review(created, note_edits=[{"note_id": "note-stale", "pitch": 61}])
        output = self.root / "review-stale"
        io_path(output).mkdir(parents=True, exist_ok=True)
        operation_id = pending["operation_id"]
        with self.assertRaises(ValueError) as raised:
            self.workflow.execute_review(operation_id,
                self.control.records["operations"][operation_id]["request"]["arguments"],
                output, lambda: None, lambda text: None)
        self.assertIn("note_id", str(raised.exception))

    def test_cancellation_is_visible_and_not_completed(self):
        created, _ = self.create_midi_flow()
        pending = self.apply_review(created)
        output = self.root / "review-cancel"
        io_path(output).mkdir(parents=True, exist_ok=True)
        operation_id = pending["operation_id"]
        arguments = self.control.records["operations"][operation_id]["request"]["arguments"]
        def cancel():
            raise InterruptedError("cancel fixture")
        with self.assertRaises(InterruptedError):
            self.workflow.execute_review(operation_id, arguments, output, cancel, lambda text: None)
        self.control.set_operation(operation_id, "cancelled",
                                   error={"code": "cancelled", "message": "cancel fixture"})
        current = self.workflow.get(pending["id"])
        self.assertFalse(current["completed"])
        self.assertEqual(current["next"]["tool"], "vwa_workflow_advance")


class WorkflowExportStyleTests(WorkflowTestBase):
    def test_export_success_publishes_project_and_all_candidates(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        svp = self.root / "assets" / "song.svp"
        ust = self.root / "assets" / "song.ustx"
        io_path(svp).write_text(json.dumps({"tracks": []}), encoding="utf-8")
        io_path(ust).write_text("ustx fixture", encoding="utf-8")
        artifacts = [{"schema": 1, "id": "art_" + "2" * 32, "path": str(ust),
                      "sha256": sha256_file(ust), "bytes": io_path(ust).stat().st_size},
                     {"schema": 1, "id": "art_" + "3" * 32, "path": str(svp),
                      "sha256": sha256_file(svp), "bytes": io_path(svp).stat().st_size}]
        for row in artifacts:
            self.control.records["artifacts"][row["id"]] = copy.deepcopy(row)
        project = self.complete_export(reviewed, artifacts)
        self.assertEqual(project["project_artifact_id"], artifacts[1]["id"])
        self.assertEqual(project["project_artifacts"], artifacts)
        self.assertFalse(project["completed"])

    def test_failed_export_and_style_backend_remain_resumable(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        exporting = self.workflow.advance(reviewed["id"], reviewed["revision"])
        self.control.set_operation(exporting["operation_id"], "failed",
                                   error={"code": "converter_unavailable", "message": "fixture"})
        inspected = self.workflow.get(exporting["id"])
        self.assertEqual(inspected["stage"], "exporting")
        self.assertFalse(inspected["completed"])
        self.assertIn("converter_unavailable", inspected["next"]["error"]["code"])
        retried = self.workflow.advance(inspected["id"], inspected["revision"], retry=True)
        self.assertEqual(retried["stage"], "exporting")
        self.assertNotEqual(retried["operation_id"], exporting["operation_id"])
        self.control.set_operation(retried["operation_id"], "interrupted",
                                   error={"code": "worker_interrupted", "message": "fixture"})
        interrupted = self.workflow.get(retried["id"])
        self.assertFalse(interrupted["completed"])
        self.assertIn("worker_interrupted", interrupted["next"]["error"]["code"])

    def test_successful_style_is_consumed_before_project_completion(self):
        created, _ = self.create_midi_flow(brief="明亮但克制")
        reviewed = self.complete_review(created)
        project_svp = self.root / "assets" / "project.svp"
        io_path(project_svp).write_text(json.dumps({"tracks": []}), encoding="utf-8")
        project_artifact = {"schema": 1, "id": "art_" + "5" * 32,
                            "path": str(project_svp), "sha256": sha256_file(project_svp),
                            "bytes": io_path(project_svp).stat().st_size}
        self.control.records["artifacts"][project_artifact["id"]] = copy.deepcopy(project_artifact)
        project = self.complete_export(reviewed, [project_artifact])
        proposal = {"track_indices": [1], "brief": "明亮但克制", "regions": []}
        styling = self.workflow.advance(project["id"], project["revision"],
                                        style_proposal=proposal)
        self.assertEqual(styling["stage"], "styling")
        styled_svp = self.root / "assets" / "styled-success.svp"
        io_path(styled_svp).write_text(json.dumps({"tracks": [{"mainGroup": {}}]}), encoding="utf-8")
        styled_artifact = {"schema": 1, "id": "art_" + "6" * 32,
                           "path": str(styled_svp), "sha256": sha256_file(styled_svp),
                           "bytes": io_path(styled_svp).stat().st_size}
        self.control.records["artifacts"][styled_artifact["id"]] = copy.deepcopy(styled_artifact)
        self.control.set_operation(styling["operation_id"], "succeeded",
                                   result={"artifacts": [styled_artifact]})
        not_yet = self.workflow.get(styling["id"])
        self.assertEqual(not_yet["stage"], "styling")
        self.assertFalse(not_yet["completed"])
        ready = self.workflow.advance(styling["id"], styling["revision"])
        self.assertEqual(ready["stage"], "ready")
        self.assertTrue(ready["completed"])
        self.assertEqual(ready["final_artifacts"], [styled_artifact])

    def test_success_with_no_export_candidates_remains_retryable(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        exporting = self.workflow.advance(reviewed["id"], reviewed["revision"])
        self.control.set_operation(exporting["operation_id"], "succeeded",
                                   result={"receipt": {"candidates": []}, "artifacts": []})
        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(exporting["id"], exporting["revision"])
        self.assertEqual(raised.exception.code, "missing_project")
        observed = self.workflow.get(exporting["id"])
        self.assertEqual(observed["stage"], "exporting")
        self.assertFalse(observed["completed"])
        recovered = self.workflow.advance(observed["id"], observed["revision"], retry=True)
        self.assertEqual(recovered["stage"], "exporting")
        self.assertFalse(recovered["completed"])
        self.assertNotEqual(recovered["operation_id"], exporting["operation_id"])

    def test_style_is_caller_supplied_and_finish_is_explicit(self):
        created, _ = self.create_midi_flow(brief="悲伤但克制")
        reviewed = self.complete_review(created)
        svp = self.root / "assets" / "styled.svp"
        io_path(svp).write_text(json.dumps({"tracks": []}), encoding="utf-8")
        artifact = {"schema": 1, "id": "art_" + "4" * 32, "path": str(svp),
                    "sha256": sha256_file(svp), "bytes": io_path(svp).stat().st_size}
        self.control.records["artifacts"][artifact["id"]] = copy.deepcopy(artifact)
        project = self.complete_export(reviewed, [artifact])
        self.assertEqual(project["stage"], "project_ready")
        self.assertIn("export", [row[1] for row in self.control.plan_starts])
        self.assertEqual(project["next"]["tool"], "vwa_context_read")
        without_style = self.workflow.advance(project["id"], project["revision"])
        self.assertEqual(without_style["stage"], "project_ready")
        self.assertFalse(without_style["completed"])
        proposal = {"track_indices": [1], "brief": "悲伤但克制", "regions": []}
        styling = self.workflow.advance(project["id"], project["revision"],
                                        style_proposal=proposal)
        self.assertEqual(styling["stage"], "styling")
        self.assertEqual(self.control.plan_calls[-1][0], "style")
        self.assertEqual(self.control.plan_calls[-1][2]["proposal"], proposal)
        finished = self.workflow.advance(styling["id"], styling["revision"],
                                         finish=True, retry=True)
        self.assertEqual(finished["stage"], "styling")
        self.assertFalse(finished["completed"])
        self.control.set_operation(styling["operation_id"], "failed",
                                   error={"code": "style_unavailable", "message": "fixture"})
        failed = self.workflow.advance(styling["id"], styling["revision"], retry=True)
        self.assertEqual(failed["stage"], "styling")
        self.assertFalse(failed["completed"])


if __name__ == "__main__":
    unittest.main()
