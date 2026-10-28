"""Contract tests for the supervised Agent workflow coordinator.

The tests use synthetic operation results and tiny local MIDI fixtures. They do
not launch the MCP worker, call a model, touch the network, or use a real user
job. One combined lyric/note test uses the real MIDI workers because that is
where stable note-ID remapping must be proven.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
sys.path.insert(0, str(SRC))

import mido
import numpy as np

from agent_control import ControlError, digest, io_path, read_json
from agent_lyrics import read_lyrics
from agent_score import read_score
from agent_supervision import SupervisionError, _digest as supervision_digest
from agent_workflow import Workflow
from common import native_path


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(io_path(path).read_bytes())


def write_pitch_npz(path):
    path = Path(path)
    io_path(path.parent).mkdir(parents=True, exist_ok=True)
    pitch = np.concatenate((
        np.full(50, 60.0, dtype=np.float32),
        np.full(50, 62.0, dtype=np.float32),
        np.full(50, 64.0, dtype=np.float32),
    ))
    with open(native_path(path), "wb") as stream:
        np.savez(stream, schema=np.int64(1), time_step=np.float64(0.01),
                 midi_pitch=pitch, voiced_mask=np.ones(150, dtype=np.bool_))
    return path


def write_wav(path):
    path = Path(path)
    io_path(path.parent).mkdir(parents=True, exist_ok=True)
    with open(native_path(path), "wb") as stream:
        with wave.open(stream, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 12800)
    return path


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
        return self.register_artifact(path, kind="input")

    def register_artifact(self, path, *, kind="candidate", job_id=None, origin=None):
        path = Path(path).resolve()
        sha = sha256_file(path)
        identifier = "art_" + digest({"path": str(path), "sha256": sha, "kind": kind,
                                      "job_id": job_id, "origin": origin})[:32]
        value = {"schema": 1, "id": identifier, "path": str(path), "sha256": sha,
                 "bytes": io_path(path).stat().st_size, "kind": kind,
                 "job_id": job_id, "origin": copy.deepcopy(origin)}
        self.records["artifacts"][identifier] = copy.deepcopy(value)
        return copy.deepcopy(value)

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

    def reference_pitch_review(self, score_artifact_id, pitch_artifact_id,
                               track_indices=None, time_origin=0.0, limit=100, offset=0):
        from agent_reference import review_reference_pitch
        score = self.artifact(score_artifact_id)
        pitch = self.artifact(pitch_artifact_id)
        self.worker_calls.append(("reference_pitch_review", {
            "score_artifact_id": score_artifact_id, "pitch_artifact_id": pitch_artifact_id,
            "track_indices": copy.deepcopy(track_indices), "time_origin": time_origin,
            "limit": limit, "offset": offset}))
        return review_reference_pitch(score["path"], pitch["path"], track_indices,
                                      time_origin, limit, offset)

    def reference_audio_read(self, artifact_id, start=None, end=None, make_excerpt=False):
        from agent_reference import inspect_reference_audio
        audio = self.artifact(artifact_id)
        self.worker_calls.append(("reference_audio_read", {
            "artifact_id": artifact_id, "start": start, "end": end,
            "make_excerpt": make_excerpt}))
        return inspect_reference_audio(audio["path"], start, end)

    def discover_references(self, job_id):
        return []

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

    def execute_review_worker(self, pending, directory_name):
        output = self.root / directory_name
        io_path(output).mkdir(parents=True, exist_ok=True)
        operation_id = pending["operation_id"]
        arguments = self.control.records["operations"][operation_id]["request"]["arguments"]
        return self.workflow.execute_review(
            operation_id, arguments, output, lambda: None, lambda text: None)

    def complete_review(self, value, note_edits=None, lyric_edits=None,
                        structure_edits=None):
        pending = self.apply_review(value, note_edits=note_edits or [],
                                    lyric_edits=lyric_edits or [],
                                    structure_edits=structure_edits or [])
        reviewed = self.succeed(pending, self.control.review_success(
            pending["id"], pending["score_artifact_id"]))
        accepted = self.workflow.advance(reviewed["id"], reviewed["revision"])
        self.assertEqual(accepted["stage"], "reviewed")
        return accepted

    def export_candidate(self, name, content=None):
        path = self.root / "assets" / name
        if content is None:
            suffix = path.suffix.lower()
            if suffix == ".svp":
                content = json.dumps({"tracks": [], "fixture": "workflow"}, ensure_ascii=False)
            elif suffix == ".ustx":
                content = "ustx workflow fixture"
            elif suffix == ".vsqx":
                content = "vsqx workflow fixture"
            else:
                raise AssertionError("unsupported export fixture format")
        if isinstance(content, str):
            io_path(path).write_text(content, encoding="utf-8")
        else:
            io_path(path).write_bytes(content)
        return self.control.register_artifact(path, kind="candidate")

    def build_export_result(self, value, artifacts, *, conversion_loss_codes=None,
                            audit_blockers=None, include_audit=True):
        """Build the same outer/inner receipt shape emitted by agent_exports."""
        source = self.control.artifact(value["score_artifact_id"])
        score = read_score(source["path"])
        formats = [Path(row["path"]).suffix.lower().lstrip(".") for row in artifacts]
        if not artifacts or any(fmt not in {"svp", "ustx", "vsqx"} for fmt in formats):
            raise AssertionError("export candidate fixture formats are invalid")
        conversion_loss_codes = (["tempo_table_normalized"]
                                 if conversion_loss_codes is None
                                 else list(conversion_loss_codes))
        losses = [{"code": code, "count": 1, "message": "自制格式损失夹具：" + code}
                  for code in conversion_loss_codes]
        expected_tempo = copy.deepcopy(score["timing"]["tempo_events"])
        candidates = []
        for artifact, fmt in zip(artifacts, formats):
            candidate_path = str(Path(artifact["path"]).resolve())
            codes = list(conversion_loss_codes)
            candidates.append({
                "format": fmt,
                "candidate_path": candidate_path,
                "candidate_sha256": artifact["sha256"],
                "bytes": artifact["bytes"],
                "verified_notes": len(score["notes"]),
                "verified_lyrics": sum(1 for note in score["notes"] if note.get("lyric")),
                "tempo_verified": "tempo_table_normalized" not in codes,
                "timing_verified": True,
                "time_signature_verified": "time_signature_normalized" not in codes,
                "time_signatures": [],
                "expected_tempo": expected_tempo,
                "output_tempo": expected_tempo,
                "loss_codes": codes,
                "engine": "LibreSVIP",
            })
        plan_sha256 = digest({"operation": "export",
                              "candidates": [row["candidate_sha256"] for row in candidates]})
        receipt = {
            "schema_version": 1,
            "kind": "vwa-export-receipt",
            "plan_sha256": plan_sha256,
            "source_path": source["path"],
            "source_sha256": source["sha256"],
            "output_root": str((self.root / "assets").resolve()),
            "formats": formats,
            "runtime_path": "fixture://read-only-libresvip",
            "engine": "LibreSVIP",
            "candidates": candidates,
            "verified": {
                "notes": len(score["notes"]),
                "lyrics": sum(1 for note in score["notes"] if note.get("lyric")),
                "tempo": expected_tempo,
                "time_signatures": [],
            },
            "conversion_losses": losses,
            "semantic_model_used": False,
            "synthesized": False,
            "published": False,
        }
        receipt["receipt_sha256"] = digest(receipt)
        outer = copy.deepcopy(receipt)
        outer["receipt"] = copy.deepcopy(receipt)
        outer["receipt_path"] = str(self.root / "assets" / "fixture.receipt.json")
        outer["replay"] = False
        outer["source_unchanged"] = True
        if include_audit:
            blockers = copy.deepcopy(audit_blockers or [])
            audit_candidates = []
            for artifact, fmt in zip(artifacts, formats):
                audit_candidates.append({
                    "format": fmt,
                    "status": "differences" if blockers else "verified",
                    "candidate_path": str(Path(artifact["path"]).resolve()),
                    "candidate_sha256": artifact["sha256"],
                    "declared_candidate_sha256": artifact["sha256"],
                    "source_sha256": source["sha256"],
                    "parsed": True,
                    "blockers": copy.deepcopy(blockers),
                    "warnings": [],
                })
            audit = {
                "schema_version": 1,
                "kind": "vwa-export-audit",
                "status": "differences" if blockers else "verified",
                "source_sha256": source["sha256"],
                "source": {"path": source["path"], "sha256": source["sha256"]},
                "receipt": {
                    "kind": receipt["kind"],
                    "plan_sha256": plan_sha256,
                    "declared_source_sha256": source["sha256"],
                },
                "candidates": audit_candidates,
                "blockers": blockers,
                "warnings": [],
                "acoustic_or_phonetic_verified": False,
                "inference_performed": False,
            }
            audit["audit_sha256"] = supervision_digest(audit)
            outer["independent_audit"] = audit
        return {"receipt": outer, "artifacts": artifacts}

    def complete_export(self, value, artifacts, **receipt_options):
        exporting = self.workflow.advance(value["id"], value["revision"])
        self.assertEqual(exporting["stage"], "exporting")
        operation = exporting["operation"]
        self.control.set_operation(
            operation["id"], "succeeded",
            result=self.build_export_result(value, artifacts, **receipt_options))
        project = self.workflow.advance(exporting["id"], exporting["revision"])
        self.assertEqual(project["stage"], "project_ready")
        return project

    def export_review(self, project, *, decision="accept", accepted_loss_codes=None,
                      rationale="已核对实际音符、歌词与秒数，并逐项认可可见损失。"):
        summary = project["export_summary"]
        if accepted_loss_codes is None:
            accepted_loss_codes = summary["required_loss_codes"]
        return {"report_sha256": summary["report_sha256"], "decision": decision,
                "rationale": rationale, "accepted_loss_codes": list(accepted_loss_codes)}

    def approve_export(self, project, **arguments):
        return self.workflow.advance(project["id"], project["revision"],
                                     export_review=self.export_review(project), **arguments)


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

    def test_create_without_initial_export_options_preserves_legacy_shape(self):
        created = self.create_source_flow(key="legacy-shape")
        record = self.workflow._read(created["id"])
        self.assertEqual(record["request"], {
            "source": "BV1xx411c7mD",
            "options": {"language": "zh"},
            "brief": "",
            "reference_lyrics": "",
            "score_artifact_id": None,
            "formats": ["svp"],
            "review_mode": "full",
        })
        self.assertNotIn("initial_export_options", record)
        self.assertNotIn("export_options", record)
        self.assertEqual(record["history"], [])
        self.assertEqual(self.control.preparation_starts, [])
        self.assertEqual(self.control.plan_calls, [])
        self.assertEqual(self.control.plan_starts, [])
        self.assertEqual(self.control.worker_calls, [])
        self.assertEqual([row[0] for row in self.control.preparation_calls],
                         ["BV1xx411c7mD"])

    def test_initial_export_options_are_stored_without_resolving_bindings(self):
        bindings = {
            "language": "ja",
            "default_bpm": 123.5,
            "svp_version": "2.0",
            "track_names": ["主唱", "和声"],
            "project_name": "导出试验",
            "preserve_pitch": True,
            "pitch_bindings": [{
                "pitch_artifact_id": "art_reference_pitch",
                "vocal_track_index": 0,
                "time_origin_seconds": 1.25,
            }],
            "include_instrumental": True,
            "instrumental_artifact_id": "art_instrumental",
        }
        original = copy.deepcopy(bindings)
        created, artifact = self.create_midi_flow(
            key="initial-export-options", initial_export_options=bindings)
        self.assertEqual(bindings, original)
        record = self.workflow._read(created["id"])
        self.assertEqual(record["initial_export_options"], original)
        self.assertEqual(record["export_options"], original)
        self.assertNotIn("initial_export_options", record["request"])
        self.assertEqual(self.control.preparation_starts, [])
        self.assertEqual(self.control.plan_calls, [])
        self.assertEqual(self.control.plan_starts, [])
        self.assertEqual(self.control.worker_calls, [])

        reviewed = self.complete_review(created)
        exporting = self.workflow.advance(reviewed["id"], reviewed["revision"])
        self.assertEqual(exporting["stage"], "exporting")
        export_call = next(row for row in self.control.plan_calls if row[0] == "export")
        export_options = export_call[2]["options"]
        self.assertEqual(export_options["pitch_bindings"], original["pitch_bindings"])
        self.assertEqual(export_options["instrumental_artifact_id"],
                         original["instrumental_artifact_id"])
        self.assertEqual(export_options["include_instrumental"], True)
        self.assertNotIn("instrumental_path", export_options)
        self.assertNotIn("pitch_sources", export_options)

    def test_initial_export_options_retry_binding_and_key_isolation(self):
        first_options = {"pitch_source_job_id": "job_pitch_first", "include_instrumental": False}
        first, artifact = self.create_midi_flow(
            key="bound-export-options", initial_export_options=first_options)
        path = self.control.state / "workflows" / (first["id"] + ".json")
        before = io_path(path).read_bytes()
        second, artifact_again = self.create_midi_flow(
            key="bound-export-options", initial_export_options=copy.deepcopy(first_options))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(artifact["id"], artifact_again["id"])
        self.assertEqual(io_path(path).read_bytes(), before)

        with self.assertRaises(ControlError) as raised:
            self.create_midi_flow(
                key="bound-export-options",
                initial_export_options={"pitch_source_job_id": "job_pitch_other"})
        self.assertEqual(raised.exception.code, "idempotency_conflict")
        self.assertEqual(io_path(path).read_bytes(), before)

        third, _ = self.create_midi_flow(
            key="different-export-options-key", initial_export_options=first_options)
        self.assertNotEqual(third["id"], first["id"])
        self.assertEqual(len(list(io_path(self.control.state / "workflows").glob("flow_*.json"))), 2)

    def test_initial_export_options_reject_paths_and_invalid_json_before_write(self):
        artifact = self.control.import_asset(self.midi)
        cases = [
            ({"instrumental_path": "C:/private/instrumental.wav"}, "unsafe_media_path"),
            ({"pitch_sources": [{"path": "C:/private/pitch.npz"}]}, "unsafe_pitch_path"),
            ({"pitch_bindings": [{
                "pitch_artifact_id": "art_pitch", "path": "C:/private/pitch.npz"
            }]}, "unsafe_export_path"),
            ({"pitch_bindings": [{
                "pitch_artifact_id": "art_pitch", "time_origin_seconds": float("nan")
            }]}, "invalid_export_options"),
            ({"pitch_bindings": object()}, "invalid_export_options"),
            ([], "invalid_export_options"),
            ({"unknown_option": True}, "invalid_export_options"),
            ({"language": "en"}, "invalid_export_options"),
            ({"default_bpm": True}, "invalid_export_options"),
            ({"svp_version": "3.0"}, "invalid_export_options"),
            ({"include_instrumental": "yes"}, "invalid_options"),
            ({"pitch_source_job_id": ""}, "invalid_export_options"),
            ({"pitch_source_job_id": None}, "invalid_export_options"),
            ({"pitch_artifact_id": None}, "invalid_export_options"),
            ({"instrumental_source_job_id": None}, "invalid_export_options"),
            ({"instrumental_artifact_id": None}, "invalid_export_options"),
            ({"pitch_bindings": []}, "invalid_pitch_bindings"),
            ({"pitch_bindings": [
                {"pitch_artifact_id": "art_one", "vocal_track_index": 0},
                {"pitch_artifact_id": "art_two", "vocal_track_index": 0},
            ]}, "invalid_pitch_bindings"),
            ({"pitch_bindings": [{"pitch_artifact_id": "art_one"}],
              "pitch_artifact_id": "art_two"}, "conflicting_pitch_source"),
            ({"preserve_pitch": False,
              "pitch_source_job_id": "job_not_created_yet"}, "conflicting_pitch_source"),
            ({"instrumental_artifact_id": "art_inst",
              "instrumental_source_job_id": "job_inst"}, "conflicting_instrumental_source"),
            ({"include_instrumental": False,
              "instrumental_source_job_id": "job_inst"}, "conflicting_instrumental_source"),
            ({"project_name": "bad/name"}, "invalid_export_options"),
        ]
        for index, (options, code) in enumerate(cases):
            with self.subTest(code=code, index=index):
                with self.assertRaises(ControlError) as raised:
                    self.workflow.create(
                        score_artifact_id=artifact["id"],
                        idempotency_key="invalid-export-" + str(index),
                        initial_export_options=options,
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertTrue(any(0x4e00 <= ord(char) <= 0x9fff
                                    for char in str(raised.exception)))
                self.assertEqual(list(io_path(self.control.state / "workflows").glob("*")), [])
        self.assertEqual(self.control.preparation_calls, [])
        self.assertEqual(self.control.preparation_starts, [])
        self.assertEqual(self.control.plan_calls, [])
        self.assertEqual(self.control.plan_starts, [])
        self.assertEqual(self.control.worker_calls, [])

    def test_concurrent_same_export_intent_is_one_atomic_creation(self):
        bindings = {"pitch_source_job_id": "job_pitch_concurrent",
                    "instrumental_artifact_id": "art_instrumental_concurrent"}
        workers = 6
        barrier = threading.Barrier(workers)

        def create():
            barrier.wait()
            return self.workflow.create(
                source="BV1xx411c7mD",
                options={"language": "zh"},
                idempotency_key="concurrent-export-intent",
                initial_export_options=copy.deepcopy(bindings),
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(create) for _ in range(workers)]
            results = [future.result() for future in futures]
        self.assertEqual(len({row["id"] for row in results}), 1)
        self.assertEqual(len(list(io_path(self.control.state / "workflows").glob("flow_*.json"))), 1)
        self.assertEqual(len(self.control.preparation_calls), 1)
        self.assertEqual(self.control.preparation_starts, [])
        record = self.workflow._read(results[0]["id"])
        self.assertEqual(record["initial_export_options"], bindings)
        self.assertEqual(record["export_options"], bindings)

    def test_concurrent_same_key_different_options_has_one_winner(self):
        workers = 5
        barrier = threading.Barrier(workers)

        def create(index):
            barrier.wait()
            return self.workflow.create(
                source="BV1xx411c7mD",
                options={"language": "zh"},
                idempotency_key="concurrent-export-conflict",
                initial_export_options={"pitch_source_job_id": "job_pitch_" + str(index)},
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(create, index) for index in range(workers)]
        values = []
        errors = []
        for future in futures:
            try:
                values.append(future.result())
            except ControlError as error:
                errors.append(error)
        self.assertEqual(len(values), 1)
        self.assertEqual(len(errors), workers - 1)
        self.assertTrue(all(error.code == "idempotency_conflict" for error in errors))
        self.assertEqual(len(self.control.preparation_calls), 1)
        self.assertEqual(len(list(io_path(self.control.state / "workflows").glob("flow_*.json"))), 1)

    def test_generated_key_is_stable_without_options_and_distinguishes_options(self):
        artifact = self.control.import_asset(self.midi)
        request = {
            "source": "",
            "options": {},
            "brief": "",
            "reference_lyrics": "",
            "score_artifact_id": artifact["id"],
            "formats": ["svp"],
            "review_mode": "full",
        }
        legacy = self.workflow.create(score_artifact_id=artifact["id"])
        self.assertEqual(legacy["id"], "flow_" + digest(digest(request))[:32])
        empty = self.workflow.create(
            score_artifact_id=artifact["id"], initial_export_options={})
        self.assertEqual(empty["id"], legacy["id"])

        first_options = {"pitch_source_job_id": "job_future_pitch_a"}
        second_options = {"pitch_source_job_id": "job_future_pitch_b"}
        first = self.workflow.create(
            score_artifact_id=artifact["id"], initial_export_options=first_options)
        expected = digest({"request": request,
                           "initial_export_options": first_options})
        self.assertEqual(first["id"], "flow_" + digest(expected)[:32])
        first_again = self.workflow.create(
            score_artifact_id=artifact["id"],
            initial_export_options=copy.deepcopy(first_options))
        self.assertEqual(first_again["id"], first["id"])
        second = self.workflow.create(
            score_artifact_id=artifact["id"], initial_export_options=second_options)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(len(list(io_path(self.control.state / "workflows").glob("flow_*.json"))), 3)

    def test_code_revision_change_preserves_old_workflow_before_clone(self):
        created = self.create_source_flow(key="legacy-code-revision")
        path = self.control.state / "workflows" / (created["id"] + ".json")
        before = io_path(path).read_bytes()
        self.control.code_revision = "code-rev-next"
        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(created["id"], created["revision"])
        self.assertEqual(raised.exception.code, "stale_workflow")
        self.assertEqual(io_path(path).read_bytes(), before)

        cloned = self.workflow.clone(
            created["id"], created["revision"], reason="Continue under current code")
        self.assertNotEqual(cloned["id"], created["id"])
        self.assertEqual(io_path(path).read_bytes(), before)
        self.assertEqual(self.workflow._read(cloned["id"])["code_revision"], "code-rev-next")
        self.assertEqual(self.control.preparation_starts, [])

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

    def test_reference_binding_rejects_stale_revision_and_wrong_stage(self):
        created, _ = self.create_midi_flow(key="reference binding")
        pitch_path = write_pitch_npz(self.root / "assets" / "参考 pitch.npz")
        audio_path = write_wav(self.root / "assets" / "参考 audio.wav")
        pitch = self.control.register_artifact(pitch_path, kind="reference_pitch")
        audio = self.control.register_artifact(audio_path, kind="reference_audio")

        with self.assertRaises(ControlError) as raised:
            self.workflow.references_set(
                created["id"], created["revision"] + 1,
                pitch_artifact_id=pitch["id"], audio_artifact_id=audio["id"],
                track_indices=[1], time_origin=0.0, audio_time_origin=0.0,
                rationale="自制参考，第0帧明确对齐MIDI秒0。")
        self.assertEqual(raised.exception.code, "workflow_conflict")

        bound = self.workflow.references_set(
            created["id"], created["revision"], pitch_artifact_id=pitch["id"],
            audio_artifact_id=audio["id"], track_indices=[1], time_origin=0.0,
            audio_time_origin=0.0, rationale="自制参考，第0帧明确对齐MIDI秒0。")
        self.assertEqual(bound["stage"], "review")
        self.assertEqual(bound["references"]["pitch_artifact_id"], pitch["id"])
        self.assertEqual(bound["references"]["audio_artifact_id"], audio["id"])
        self.assertEqual(bound["references"]["track_indices"], [1])

        evidence = self.workflow.review(bound["id"], limit=1, offset=0)
        self.assertEqual(evidence["reference_evidence"]["pitch"]["summary"]["notes_selected"], 3)
        self.assertFalse(evidence["reference_evidence"]["audio"]["frame_analysis_performed"])
        self.assertEqual(evidence["reference_evidence"]["audio_excerpt_next"]["tool"],
                         "vwa_reference_audio_read")

        pending = self.apply_review(bound, rationale="保留音符，参考仅作条件证据。")
        self.assertEqual(pending["stage"], "reviewing")
        with self.assertRaises(ControlError) as raised:
            self.workflow.references_set(
                pending["id"], pending["revision"], audio_artifact_id=audio["id"],
                rationale="操作启动后不应重绑参考。")
        self.assertEqual(raised.exception.code, "wrong_stage")

    def test_structure_delete_insert_lyrics_and_pitch_use_real_worker_batch(self):
        created, _ = self.create_midi_flow(key="combined structure")
        source_sha = sha256_file(self.midi)
        before = read_score(self.midi)
        deleted = next(note for note in before["notes"] if note["pitch"] == 62)
        lyric_target = next(note for note in before["notes"] if note["pitch"] == 60)
        pitch_target = next(note for note in before["notes"] if note["pitch"] == 64)
        pending = self.apply_review(
            created,
            structure_edits=[
                {"operation": "delete", "note_id": deleted["note_id"],
                 "lyric_policy": "delete_owned"},
                {"operation": "insert", "track_index": deleted["track_index"],
                 "channel": deleted["channel"], "pitch": 67, "onset_tick": 1440,
                 "duration_tick": 480, "velocity": 77, "lyric": "新"},
            ],
            lyric_edits=[{"note_id": lyric_target["note_id"], "lyric": "晴",
                          "operation": "replace"}],
            note_edits=[{"note_id": pitch_target["note_id"], "pitch": 65}])
        self.assertEqual(pending["stage"], "reviewing")
        result = self.execute_review_worker(pending, "review-combined-real")
        candidate = self.control.artifact(result["artifact_id"])
        after = read_score(candidate["path"])
        by_onset = {note["onset_tick"]: note for note in after["notes"]}
        self.assertEqual(sorted(by_onset), [0, 960, 1440])
        self.assertEqual(by_onset[0]["pitch"], 60)
        self.assertEqual(by_onset[0]["lyric"], "晴")
        self.assertEqual(by_onset[960]["pitch"], 65)
        self.assertEqual(by_onset[1440]["pitch"], 67)
        self.assertEqual(by_onset[1440]["lyric"], "新")
        self.assertEqual(len(result["receipts"]), 3)
        self.assertEqual(sha256_file(self.midi), source_sha)
        midi = mido.MidiFile(native_path(candidate["path"]), charset="utf8")
        self.assertTrue(any(message.type == "marker" and message.text == "keep-tail"
                            for track in midi.tracks for message in track))
        self.assertEqual(sum(message.type == "set_tempo"
                             for track in midi.tracks for message in track), 1)
        self.assertEqual(sum(message.type == "time_signature"
                             for track in midi.tracks for message in track), 1)

    def test_structure_delete_conflicts_with_lyric_or_pitch_edits(self):
        created, _ = self.create_midi_flow(key="combined conflict")
        before_sha = sha256_file(self.midi)
        target = next(note for note in read_score(self.midi)["notes"]
                      if note["pitch"] == 62)
        pending = self.apply_review(
            created,
            structure_edits=[{"operation": "delete", "note_id": target["note_id"],
                              "lyric_policy": "preserve"}],
            lyric_edits=[{"note_id": target["note_id"], "lyric": "改",
                          "operation": "replace"}],
            note_edits=[{"note_id": target["note_id"], "pitch": 63}])
        with self.assertRaises(ControlError) as raised:
            self.execute_review_worker(pending, "review-delete-conflict")
        self.assertEqual(raised.exception.code, "review_edit_conflict")
        self.assertEqual(sha256_file(self.midi), before_sha)

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
        created, _ = self.create_midi_flow(formats=["ustx", "svp"])
        reviewed = self.complete_review(created)
        ust = self.export_candidate("song.ustx")
        svp = self.export_candidate("song.svp")
        artifacts = [ust, svp]
        project = self.complete_export(reviewed, artifacts)
        summary = project["export_summary"]
        self.assertEqual(project["project_artifact_id"], svp["id"])
        self.assertEqual(project["project_artifacts"], artifacts)
        self.assertTrue(summary["can_accept"])
        self.assertEqual(summary["required_loss_codes"], ["tempo_table_normalized"])
        self.assertEqual(summary["independent_audit"]["kind"], "vwa-export-audit")
        self.assertEqual(len(summary["independent_audit"]["candidates"]), len(artifacts))
        self.assertFalse(project["completed"])

        without_review = self.workflow.advance(project["id"], project["revision"],
                                               finish=True)
        self.assertEqual(without_review["stage"], "project_ready")
        self.assertEqual(without_review["revision"], project["revision"])
        self.assertEqual(without_review["next"]["reason"], "review_export_report")
        self.assertFalse(without_review["completed"])

        ready = self.approve_export(project, finish=True)
        self.assertEqual(ready["stage"], "ready")
        self.assertTrue(ready["completed"])
        self.assertEqual(ready["final_artifacts"], artifacts)

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
        project_artifact = self.export_candidate("project.svp")
        project = self.complete_export(reviewed, [project_artifact])
        proposal = {"track_indices": [1], "brief": "明亮但克制", "regions": []}
        style_plans_before = sum(row[1] == "style" for row in self.control.plan_starts)
        without_review = self.workflow.advance(project["id"], project["revision"],
                                               style_proposal=proposal)
        self.assertEqual(without_review["stage"], "project_ready")
        self.assertFalse(without_review["completed"])
        self.assertEqual(sum(row[1] == "style" for row in self.control.plan_starts),
                         style_plans_before)

        styling = self.approve_export(project, style_proposal=proposal)
        self.assertEqual(styling["stage"], "styling")
        styled_artifact = self.export_candidate(
            "styled-success.svp", json.dumps({"tracks": [{"mainGroup": {}}]}))
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
        artifact = self.export_candidate("styled.svp")
        project = self.complete_export(reviewed, [artifact])
        self.assertEqual(project["stage"], "project_ready")
        self.assertIn("export", [row[1] for row in self.control.plan_starts])
        self.assertEqual(project["next"]["tool"], "vwa_workflow_advance")
        self.assertEqual(project["next"]["reason"], "review_export_report")
        without_finish = self.workflow.advance(project["id"], project["revision"],
                                               finish=True)
        self.assertEqual(without_finish["stage"], "project_ready")
        self.assertFalse(without_finish["completed"])
        proposal = {"track_indices": [1], "brief": "悲伤但克制", "regions": []}
        without_style = self.workflow.advance(project["id"], project["revision"],
                                              style_proposal=proposal)
        self.assertEqual(without_style["stage"], "project_ready")
        self.assertFalse(any(row[1] == "style" for row in self.control.plan_starts))
        styling = self.approve_export(project, style_proposal=proposal)
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

    def test_export_review_rejects_stale_report_without_advancing(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        project = self.complete_export(reviewed, [self.export_candidate("stale.svp")])
        stale = self.export_review(project)
        stale["report_sha256"] = "0" * 64
        with self.assertRaises(SupervisionError) as raised:
            self.workflow.advance(project["id"], project["revision"], finish=True,
                                  export_review=stale)
        self.assertEqual(raised.exception.code, "export_review_conflict")
        observed = self.workflow.get(project["id"])
        self.assertEqual(observed["stage"], "project_ready")
        self.assertEqual(observed["revision"], project["revision"])
        self.assertFalse(observed["export_review"])

    def test_export_review_requires_every_loss_and_missing_audit_ack(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        project = self.complete_export(
            reviewed, [self.export_candidate("loss.svp")], include_audit=False)
        required = project["export_summary"]["required_loss_codes"]
        self.assertIn("tempo_table_normalized", required)
        self.assertIn("independent_audit_unavailable", required)
        partial = self.export_review(project, accepted_loss_codes=["tempo_table_normalized"])
        with self.assertRaises(SupervisionError) as raised:
            self.workflow.advance(project["id"], project["revision"], finish=True,
                                  export_review=partial)
        self.assertEqual(raised.exception.code, "export_losses_unacknowledged")
        self.assertEqual(self.workflow.get(project["id"])["stage"], "project_ready")

        ready = self.approve_export(project, finish=True)
        self.assertEqual(ready["stage"], "ready")
        self.assertEqual(ready["export_review"]["accepted_loss_codes"], required)

    def test_export_review_cannot_accept_hard_blocker(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        blocker = {"code": "lyric_content_mismatch", "format": "svp",
                   "message": "自制审计确认歌词文本不同。"}
        project = self.complete_export(
            reviewed, [self.export_candidate("blocked.svp")],
            audit_blockers=[blocker])
        self.assertFalse(project["export_summary"]["can_accept"])
        self.assertEqual(project["next"]["reason"], "resolve_export_blockers")
        with self.assertRaises(SupervisionError) as raised:
            self.workflow.advance(project["id"], project["revision"],
                                  export_review=self.export_review(project))
        self.assertEqual(raised.exception.code, "export_blockers")
        observed = self.workflow.get(project["id"])
        self.assertEqual(observed["stage"], "project_ready")
        self.assertFalse(observed["completed"])

    def test_export_needs_revision_returns_to_review_stage(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        project = self.complete_export(reviewed, [self.export_candidate("revise.svp")])
        original_summary = copy.deepcopy(project["export_summary"])
        decision = self.export_review(
            project, decision="needs_revision", accepted_loss_codes=[],
            rationale="实际歌词与工程不一致，先退回MIDI核对。")
        returned = self.workflow.advance(project["id"], project["revision"],
                                         export_review=decision)
        self.assertEqual(returned["stage"], "review")
        self.assertFalse(returned["completed"])
        self.assertEqual(returned["export_review"]["decision"], "needs_revision")
        self.assertEqual(returned["previous_export_summary"], original_summary)
        self.assertEqual(returned["next"]["tool"], "vwa_workflow_review")

    def test_export_review_rejects_changed_candidate(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        artifact = self.export_candidate("changed.svp")
        project = self.complete_export(reviewed, [artifact])
        candidate_path = Path(artifact["path"])
        io_path(candidate_path).write_bytes(io_path(candidate_path).read_bytes() + b"\0")

        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(project["id"], project["revision"],
                                  export_review=self.export_review(project))
        self.assertEqual(raised.exception.code, "stale_artifact")

        changed = self.control.records["artifacts"][artifact["id"]]
        changed["sha256"] = sha256_file(candidate_path)
        changed["bytes"] = io_path(candidate_path).stat().st_size
        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(project["id"], project["revision"],
                                  export_review=self.export_review(project))
        self.assertEqual(raised.exception.code, "export_review_conflict")
        observed = self.workflow.get(project["id"])
        self.assertEqual(observed["stage"], "project_ready")
        self.assertFalse(observed["completed"])


if __name__ == "__main__":
    unittest.main()
