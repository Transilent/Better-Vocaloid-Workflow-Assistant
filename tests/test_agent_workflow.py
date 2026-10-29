"""Contract tests for the supervised Agent workflow coordinator.

The tests use synthetic operation results and tiny local MIDI fixtures. They do
not call a model, touch the network, or modify a real user job. Focused tests use
the real MIDI worker where stable note identity must be proven; one optional
test launches the dedicated SDK stdio server, and one reads an existing reviewed
song artifact without writing it.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
SDK_PYTHON = os.environ.get("VWA_MCP_SDK_PYTHON")
sys.path.insert(0, str(SRC))

import mido
import numpy as np

from agent_control import ControlError, digest, io_path, read_json, safe_path
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


def write_track_layout_midi(path):
    """MIDI fixture with metadata-only tracks and nonnumeric channel order."""
    path = Path(path)
    io_path(path.parent).mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Metadata only", time=0),
        mido.Message("program_change", channel=6, program=1, time=0),
        mido.MetaMessage("marker", text="not a vocal track", time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="First-seen channels", time=0),
        mido.Message("note_on", channel=5, note=60, velocity=90, time=0),
        mido.Message("note_off", channel=5, note=60, velocity=0, time=240),
        mido.Message("note_on", channel=2, note=62, velocity=90, time=0),
        mido.Message("note_off", channel=2, note=62, velocity=0, time=240),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Orphan changes bucket order", time=0),
        mido.Message("note_off", channel=4, note=64, velocity=0, time=0),
        mido.Message("note_on", channel=3, note=65, velocity=90, time=0),
        mido.Message("note_off", channel=3, note=65, velocity=0, time=240),
        mido.Message("note_on", channel=4, note=67, velocity=90, time=0),
        mido.Message("note_off", channel=4, note=67, velocity=0, time=240),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Trailing metadata", time=0),
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
        for name in ("workflows", "operations", "locks", "artifacts", "plans", "preparations"):
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

    def job_path(self, identifier):
        record = self._record("preparations", identifier, "job")
        path = Path(record["path"]).resolve()
        roots = [Path(native_path(root)).resolve() for root in self.jobs]
        if not any(path.is_relative_to(root) for root in roots):
            raise ControlError("outside_roots", "fixture job escaped authorized roots")
        if not io_path(path / "request.json").is_file():
            raise ControlError("not_found", "fixture job request not found")
        return path

    def register_preparation_job(self, request, name):
        job = Path(self.root / "jobs" / name).resolve()
        io_path(job).mkdir(parents=True, exist_ok=True)
        request_path = job / "request.json"
        io_path(request_path).write_text(
            json.dumps(request, ensure_ascii=False), encoding="utf-8")
        sha = sha256_file(request_path)
        identifier = "job_" + digest(str(job))[:32]
        record = {"schema": 1, "id": identifier, "path": str(job),
                  "request_sha256": sha}
        record_path = self.state / "preparations" / (identifier + ".json")
        io_path(record_path).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return identifier

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

    def score_read(self, identifier, review=False):
        artifact = self.artifact(identifier)
        result = self.call_worker("score_review" if review else "score_read",
                                  {"path": artifact["path"]})
        return result if review else result["score"]

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
    def test_direct_local_declaration_is_canonical_and_idempotent(self):
        import agent_preparation
        audio = self.root / 'assets' / 'fixture.wav'
        audio.write_bytes(b'local-fixture')
        url = 'https://www.bilibili.com/video/BV1xx411c7mD/'
        def make_plan(source, options, source_url):
            with patch.object(agent_preparation, '_preflight', return_value={}):
                plan = agent_preparation.plan_preparation(source, options, source_url)
            return {'id': 'plan_fixture', 'plan': plan}
        with patch.object(self.control, 'preparation_plan', side_effect=make_plan):
            first = self.workflow.create(source=str(audio), source_url=url + '?token=discard',
                                         idempotency_key='direct-declared')
            same = self.workflow.create(source=str(audio), source_url=url,
                                        idempotency_key='direct-declared')
            self.assertEqual(first['id'], same['id'])
            self.assertEqual(first['source_evidence']['local_audio_sha256'], sha256_file(audio))
            self.assertNotIn('token', json.dumps(first))
            with self.assertRaises(ControlError):
                self.workflow.create(source=str(audio), source_url=url.replace('BV1xx411c7mD', 'BV1r668YbEyp'),
                                     idempotency_key='direct-declared')

    def test_job_source_declaration_is_bound_and_replayed(self):
        url = 'https://www.bilibili.com/video/BV1xx411c7mD/'
        request = {'source_kind': 'local', 'local_audio_sha256': 'a' * 64,
                   'source_provenance': {'kind': 'caller_supplied_unverified', 'url': url}}
        job_id = self.control.register_preparation_job(request, 'provenance-job')
        first = self.workflow.create(preparation_job_id=job_id, idempotency_key='source-evidence')
        evidence = first['source_evidence']
        self.assertEqual(evidence['local_audio_sha256'], 'a' * 64)
        self.assertEqual(evidence['source_provenance'], request['source_provenance'])
        self.assertEqual(evidence['preparation_request_sha256'],
                         self.control._record('preparations', job_id, 'job')['request_sha256'])
        replay = self.workflow.create(preparation_job_id=job_id, idempotency_key='source-evidence',
                                      source_url=url + '?token=discarded')
        self.assertEqual(replay['id'], first['id'])
        with self.assertRaises(ControlError):
            self.workflow.create(preparation_job_id=job_id, source_url=url.replace('BV1xx411c7mD', 'BV1r668YbEyp'))
        request['local_audio_sha256'] = 'c' * 64
        io_path(self.control.job_path(job_id) / 'request.json').write_text(
            json.dumps(request), encoding='utf-8')
        with self.assertRaises(ControlError) as error:
            self.workflow.create(preparation_job_id=job_id, idempotency_key='tampered-job')
        self.assertEqual(error.exception.code, 'stale_job_request')

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
        pitch = self.control.register_artifact(
            write_pitch_npz(self.root / "assets" / "fixture.pitch.npz"),
            kind="reference_pitch")
        bindings = {
            "language": "ja",
            "default_bpm": 123.5,
            "svp_version": "2.0",
            "track_names": ["主唱"],
            "project_name": "导出试验",
            "preserve_pitch": True,
            "pitch_bindings": [{
                "pitch_artifact_id": pitch["id"],
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

    def test_preparation_job_without_reference_lyrics_returns_recovery_hint(self):
        reference = "风雪落在旧檐"
        job_id = self.control.register_preparation_job(
            {"language": "zh", "lyrics": "", "recognize_lyrics": True},
            "preparation-empty-lyrics",
        )
        job_path = self.control.job_path(job_id)
        with patch("agent_workflow.safe_path", wraps=safe_path) as guarded:
            created = self.workflow.create(
                preparation_job_id=job_id,
                reference_lyrics=reference,
                idempotency_key="preparation-empty-lyrics-flow",
            )
        guarded.assert_any_call(job_path / "request.json", [job_path])

        self.assertEqual(created["stage"], "created")
        self.assertEqual(created["job_id"], job_id)
        evidence = created["preparation_reference_lyrics"]
        self.assertEqual(evidence["status"], "not_used")
        self.assertEqual(evidence["basis"], "lyrics_field_empty")
        self.assertEqual(evidence["request_binding"], "verified")
        self.assertTrue(evidence["request_sha256_verified"])
        self.assertEqual(evidence["alignment"], {
            "status": "unknown",
            "reason": "workflow_create_does_not_verify_midi_alignment",
        })
        recovery = evidence["recovery"]
        self.assertEqual(recovery["tool"], "vwa_recover")
        request = recovery["arguments"]["request"]
        self.assertEqual(request["action"], "preparation_reuse_plan")
        self.assertEqual(request["source_job_id"], job_id)
        self.assertNotIn("options", request)
        self.assertNotIn("reuse_stages", request)
        self.assertEqual(
            recovery["argument_bindings"],
            {"request.options.lyrics": "workflow.request.reference_lyrics"},
        )
        self.assertIn("重跑 MIDI", recovery["instruction"])
        self.assertEqual(json.dumps(created, ensure_ascii=False).count(reference), 1)
        summary = self.workflow.get(created["id"], detail="summary")
        self.assertEqual(json.dumps(summary, ensure_ascii=False).count(reference), 1)

        warning = created["next"]["warning"]
        self.assertEqual(warning, {
            "code": "preparation_reference_lyrics_not_used",
            "severity": "warning",
            "details_ref": "preparation_reference_lyrics",
            "tool": "vwa_recover",
            "action": "preparation_reuse_plan",
        })
        self.assertEqual(created["warnings"], [warning])
        self.assertEqual(self.control.preparation_calls, [])
        self.assertEqual(self.control.preparation_starts, [])
        self.assertEqual(self.control.plan_calls, [])
        self.assertEqual(self.control.plan_starts, [])
        self.assertEqual(self.control.worker_calls, [])

        reread = self.workflow.get(created["id"])
        self.assertEqual(reread["preparation_reference_lyrics"], evidence)
        self.assertEqual(reread["next"]["warning"], warning)

    def test_different_preparation_reference_lyrics_is_not_treated_as_used(self):
        reference = "新提供歌词"
        job_id = self.control.register_preparation_job(
            {"language": "zh", "lyrics": "另一份歌词"},
            "preparation-different-lyrics",
        )
        created = self.workflow.create(
            preparation_job_id=job_id,
            reference_lyrics=reference,
            idempotency_key="preparation-different-lyrics-flow",
        )

        evidence = created["preparation_reference_lyrics"]
        self.assertEqual(evidence["status"], "not_used")
        self.assertEqual(evidence["basis"], "lyrics_field_different")
        self.assertEqual(evidence["alignment"]["status"], "unknown")
        self.assertEqual(
            evidence["recovery"]["argument_bindings"],
            {"request.options.lyrics": "workflow.request.reference_lyrics"},
        )
        self.assertEqual(json.dumps(created, ensure_ascii=False).count(reference), 1)
        self.assertEqual(self.control.preparation_starts, [])

    def test_missing_preparation_lyrics_data_is_unknown_without_guessing(self):
        job_id = self.control.register_preparation_job(
            {"language": "zh", "recognize_lyrics": True},
            "preparation-missing-lyrics-field",
        )
        created = self.workflow.create(
            preparation_job_id=job_id,
            reference_lyrics="待核对的参考歌词",
            idempotency_key="preparation-missing-lyrics-field-flow",
        )

        evidence = created["preparation_reference_lyrics"]
        self.assertEqual(evidence["status"], "unknown")
        self.assertEqual(evidence["basis"], "lyrics_field_missing")
        self.assertEqual(evidence["alignment"]["status"], "unknown")
        self.assertTrue(evidence["request_sha256_verified"])
        self.assertNotIn("recovery", evidence)
        self.assertIn("未知", evidence["message"])
        warning = created["next"]["warning"]
        self.assertEqual(warning, {
            "code": "preparation_reference_lyrics_unknown",
            "severity": "warning",
            "details_ref": "preparation_reference_lyrics",
        })
        self.assertEqual(self.control.preparation_starts, [])

    def test_matching_preparation_reference_lyrics_and_lines_do_not_claim_alignment(self):
        job_id = self.control.register_preparation_job(
            {"language": "zh", "lyrics": "风雪落旧檐",
             "lyric_lines": ["风雪落", "旧檐"]},
            "preparation-matching-lyrics",
        )
        created = self.workflow.create(
            preparation_job_id=job_id,
            reference_lyrics="风、雪落\r\n旧檐！",
            idempotency_key="preparation-matching-lyrics-flow",
        )

        evidence = created["preparation_reference_lyrics"]
        self.assertEqual(evidence["status"], "present_in_request")
        self.assertEqual(evidence["basis"], "lyrics_match")
        self.assertEqual(evidence["alignment"]["status"], "unknown")
        self.assertNotIn("recovery", evidence)
        self.assertNotIn("warning", created["next"])
        self.assertNotIn("warnings", created)
        self.assertEqual(self.control.preparation_starts, [])

    def test_same_lyrics_with_different_lines_are_not_treated_as_used(self):
        job_id = self.control.register_preparation_job(
            {"language": "zh", "lyrics": "风雪落",
             "lyric_lines": ["风雪", "落"]},
            "preparation-different-lyric-lines",
        )
        created = self.workflow.create(
            preparation_job_id=job_id,
            reference_lyrics="风雪落",
            idempotency_key="preparation-different-lyric-lines-flow",
        )

        evidence = created["preparation_reference_lyrics"]
        self.assertEqual(evidence["status"], "not_used")
        self.assertEqual(evidence["basis"], "lyric_lines_different")
        self.assertIn("分行不同", evidence["message"])
        self.assertIn("recovery", evidence)
        self.assertEqual(self.control.preparation_starts, [])

    def test_legacy_lyrics_without_line_information_is_unknown(self):
        job_id = self.control.register_preparation_job(
            {"language": "zh", "lyrics": "风雪落"},
            "preparation-legacy-lyrics-without-lines",
        )
        created = self.workflow.create(
            preparation_job_id=job_id,
            reference_lyrics="风雪落",
            idempotency_key="preparation-legacy-lyrics-without-lines-flow",
        )

        evidence = created["preparation_reference_lyrics"]
        self.assertEqual(evidence["status"], "unknown")
        self.assertEqual(evidence["basis"], "lyric_lines_missing")
        self.assertIn("旧版记录", evidence["message"])
        self.assertTrue(evidence["request_sha256_verified"])
        self.assertEqual(evidence["alignment"]["status"], "unknown")
        self.assertNotIn("recovery", evidence)
        self.assertEqual(created["next"]["warning"]["code"],
                         "preparation_reference_lyrics_unknown")
        self.assertEqual(self.control.preparation_starts, [])

    def test_preparation_request_digest_and_parse_share_one_snapshot(self):
        stable = {"language": "zh", "lyrics": "风雪落",
                  "lyric_lines": ["风雪落"]}
        replacement = {"language": "zh", "lyrics": "另一份歌词",
                       "lyric_lines": ["另一份歌词"]}
        job_id = self.control.register_preparation_job(
            stable, "preparation-request-race")
        stable_bytes = json.dumps(stable, ensure_ascii=False).encode("utf-8")
        replacement_text = json.dumps(replacement, ensure_ascii=False)
        request_modes = []
        real_open = Path.open

        def racing_open(path, mode="r", buffering=-1, encoding=None,
                        errors=None, newline=None):
            if path.name == "request.json":
                request_modes.append(mode)
                if "b" in mode:
                    return io.BytesIO(stable_bytes)
                return io.StringIO(replacement_text)
            return real_open(path, mode, buffering, encoding, errors, newline)

        with patch.object(Path, "open", new=racing_open):
            created = self.workflow.create(
                preparation_job_id=job_id,
                reference_lyrics="风雪落",
                idempotency_key="preparation-request-race-flow",
            )

        self.assertEqual(request_modes, ["rb"])
        evidence = created["preparation_reference_lyrics"]
        self.assertEqual(evidence["status"], "present_in_request")
        self.assertEqual(evidence["basis"], "lyrics_match")
        self.assertTrue(evidence["request_sha256_verified"])

    def test_inconsistent_preparation_lyrics_fields_are_unknown(self):
        job_id = self.control.register_preparation_job(
            {"language": "zh", "lyrics": "风雪落",
             "lyric_lines": ["风雪", "停"]},
            "preparation-inconsistent-lyrics-fields",
        )
        created = self.workflow.create(
            preparation_job_id=job_id,
            reference_lyrics="风雪落",
            idempotency_key="preparation-inconsistent-lyrics-fields-flow",
        )

        evidence = created["preparation_reference_lyrics"]
        self.assertEqual(evidence["status"], "unknown")
        self.assertEqual(evidence["basis"], "lyrics_fields_inconsistent")
        self.assertEqual(evidence["alignment"]["status"], "unknown")
        self.assertNotIn("recovery", evidence)
        self.assertEqual(self.control.preparation_starts, [])

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

    def test_review_retry_error_is_current_only_and_success_archives_it(self):
        created, _ = self.create_midi_flow(key="current review error")
        pending = self.apply_review(created)
        error = {
            "code": "business_error",
            "message": "最终状态在轨道 0、通道 0、音高 56 发生重叠；事件配对会歧义。",
        }
        self.control.set_operation(pending["operation_id"], "failed", error=error)
        retryable = self.workflow.advance(
            pending["id"], pending["revision"], retry=True)
        self.assertEqual(retryable["stage"], "review")
        self.assertEqual(retryable["last_error"], error)

        retried = self.apply_review(retryable, rationale="修正后重新核对全部音符。")
        self.assertNotIn("last_error", retried)
        succeeded = self.succeed(
            retried, self.control.review_success(retried["id"], retried["score_artifact_id"]))
        reviewed = self.workflow.advance(succeeded["id"], succeeded["revision"])
        self.assertEqual(reviewed["stage"], "reviewed")
        self.assertNotIn("last_error", reviewed)
        self.assertEqual(reviewed["error_history"][-1]["error"]["code"], "business_error")
        self.assertEqual(reviewed["error_history"][-1]["retired_by"], "review_started")

    def test_ready_status_retires_legacy_last_error_without_rewriting_record(self):
        created, _ = self.create_midi_flow(key="legacy stale review error")
        error = {"code": "business_error", "message": "旧的核对错误"}
        raw = self.workflow._write(
            self.workflow._read(created["id"]), "legacy_ready",
            stage="ready", last_error=error)
        self.assertEqual(self.workflow._read(raw["id"])["last_error"], error)

        observed = self.workflow.get(raw["id"])

        self.assertNotIn("last_error", observed)
        self.assertEqual(observed["error_history"][-1]["error"], error)
        self.assertEqual(
            observed["error_history"][-1]["retired_by"], "successful_status_ready")
        self.assertTrue(observed["error_history"][-1]["projected"])
        self.assertNotIn("retired_at", observed["error_history"][-1])
        self.assertEqual(observed["error_history"], self.workflow.get(raw["id"])["error_history"])
        self.assertEqual(self.workflow._read(raw["id"])["last_error"], error)

    def test_unrelated_write_preserves_error_and_new_error_supersedes_it(self):
        created, _ = self.create_midi_flow(key="unrelated error")
        error = {"code": "review_failure", "message": "待修复"}
        raw = self.workflow._write(self.workflow._read(created["id"]),
                                   "review_retry_required", last_error=error)
        pitch = self.control.register_artifact(write_pitch_npz(self.root / "assets" / "retry-pitch.npz"),
                                               kind="reference_pitch")
        self.workflow.references_set(raw["id"], raw["revision"], pitch_artifact_id=pitch["id"],
                                     track_indices=[1], time_origin=0.0, rationale="核对参考。")
        updated = self.workflow._read(raw["id"])
        self.assertEqual(updated["last_error"], error)
        self.assertNotIn("error_history", updated)
        replacement = {"code": "new_failure", "message": "新错误"}
        updated = self.workflow._write(updated, "review_retry_required", last_error=replacement)
        self.assertEqual(updated["last_error"], replacement)
        self.assertEqual(updated["error_history"][-1]["error"], error)

    def dry_run_worker(self, action, arguments):
        from agent_worker import dispatch
        self.assertEqual(action, "workflow_review_dry_run")
        calls = self.control.state / "calls"
        calls.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(calls)) as directory:
            try:
                return dispatch(self.control, action, {**arguments, "temporary_root": directory})
            finally:
                self.assertEqual(list((Path(directory) / "review-candidates").iterdir()), [])

    def test_dry_run_combined_edits_matches_apply_without_side_effects(self):
        created, _ = self.create_midi_flow(key="dry run combined")
        notes = {n["pitch"]: n for n in read_score(self.midi)["notes"]}
        deleted, lyric, pitch = notes[62], notes[60], notes[64]
        edits = dict(
            structure_edits=[
                {"operation": "delete", "note_id": deleted["note_id"], "lyric_policy": "delete_owned"},
                {"operation": "insert", "track_index": deleted["track_index"], "channel": deleted["channel"],
                 "pitch": 67, "onset_tick": 1440, "duration_tick": 480, "velocity": 77, "lyric": "新"}],
            lyric_edits=[{"note_id": lyric["note_id"], "lyric": "晴", "operation": "replace"}],
            note_edits=[{"note_id": pitch["note_id"], "pitch": 65}])
        raw_before = self.workflow._read(created["id"])
        artifacts_before = copy.deepcopy(self.control.records["artifacts"])
        with patch.object(self.control, "call_worker", side_effect=self.dry_run_worker):
            preview = self.apply_review(created, dry_run=True, **edits)
        self.assertTrue(preview["dry_run"])
        self.assertFalse(preview["approved"])
        self.assertEqual([s["kind"] for s in preview["steps"]], ["structure", "lyrics", "notes"])
        self.assertIn('"lyric_removed": true', json.dumps(preview["steps"][0], ensure_ascii=False))
        self.assertEqual(self.workflow._read(created["id"]), raw_before)
        self.assertEqual(self.control.records["artifacts"], artifacts_before)
        self.assertEqual(self.control.records["operations"], {})
        pending = self.apply_review(created, **edits)
        actual = self.execute_review_worker(pending, "dry-run-parity")
        self.assertEqual(preview["candidate_sha256"], actual["source_sha256"])

    def test_dry_run_rejects_overlap_after_prior_candidate_and_cleans_it(self):
        created, _ = self.create_midi_flow(key="dry run overlap")
        notes = read_score(self.midi)["notes"]
        with patch.object(self.control, "call_worker", side_effect=self.dry_run_worker):
            with self.assertRaises((ControlError, ValueError)) as raised:
                self.apply_review(created, dry_run=True,
                    lyric_edits=[{"operation": "replace", "note_id": notes[0]["note_id"], "lyric": "云"}],
                    note_edits=[{"note_id": notes[0]["note_id"], "duration_tick": 720}])
        self.assertTrue("重叠" in str(raised.exception) or getattr(raised.exception, "code", "") == "monophonic_overlap")
        self.assertEqual(self.workflow._read(created["id"])["revision"], created["revision"])
        self.assertEqual(self.control.records["operations"], {})

    def test_dry_run_checks_segment_scope_before_worker(self):
        from agent_segments import plan_segments
        original_worker = self.control.call_worker
        def segment_worker(action, arguments):
            if action == "segment_plan":
                return plan_segments(read_score(arguments["path"]))
            return original_worker(action, arguments)
        with patch.object(self.control, "call_worker", side_effect=segment_worker):
            created, _ = self.create_midi_flow(key="dry run segment", review_mode="segments")
        raw = self.workflow._read(created["id"])
        segment = raw["midi_segments"]["segments"][0]
        with patch.object(self.control, "call_worker", side_effect=self.dry_run_worker) as worker:
            with self.assertRaises(ControlError) as raised:
                self.apply_review(created, dry_run=True, segment_id=segment["id"],
                                  note_edits=[{"note_id": "not-in-segment", "pitch": 61}])
            self.assertEqual(raised.exception.code, "segment_edit_outside_scope")
            worker.assert_not_called()
            preview = self.apply_review(created, dry_run=True, segment_id=segment["id"])
        self.assertFalse(preview["approved"])
        self.assertEqual(self.workflow._read(created["id"]), raw)

    def test_dry_run_rejects_conflict_and_preserves_state(self):
        created, _ = self.create_midi_flow(key="dry run conflict")
        target = read_score(self.midi)["notes"][1]["note_id"]
        before = self.workflow._read(created["id"])
        with patch.object(self.control, "call_worker", side_effect=self.dry_run_worker):
            with self.assertRaises(ControlError) as raised:
                self.apply_review(created, dry_run=True,
                    structure_edits=[{"operation": "delete", "note_id": target, "lyric_policy": "delete_owned"}],
                    lyric_edits=[{"operation": "replace", "note_id": target, "lyric": "改"}])
        self.assertEqual(raised.exception.code, "review_edit_conflict")
        self.assertEqual(self.workflow._read(created["id"]), before)
        self.assertEqual(self.control.records["operations"], {})

    def test_dry_run_checks_revision_stage_and_source_sha(self):
        created, _ = self.create_midi_flow(key="dry run checks")
        with self.assertRaises(ControlError) as raised:
            self.workflow.apply_review(created["id"], created["revision"] + 1, "检查", dry_run=True)
        self.assertEqual(raised.exception.code, "workflow_conflict")
        with patch.object(self.control, "call_worker", side_effect=self.dry_run_worker):
            io_path(self.midi).write_bytes(io_path(self.midi).read_bytes() + b"x")
            with self.assertRaises(ControlError) as raised:
                self.apply_review(created, dry_run=True)
        self.assertEqual(raised.exception.code, "stale_artifact")
        raw = self.workflow._write(self.workflow._read(created["id"]), "fixture", stage="ready")
        with self.assertRaises(ControlError) as raised:
            self.workflow.apply_review(raw["id"], raw["revision"], "检查", dry_run=True)
        self.assertEqual(raised.exception.code, "wrong_stage")

    def test_dry_run_runs_in_real_worker_and_removes_candidates(self):
        from agent_control import Control, SCHEMA
        config = self.root / "real-control.json"
        config.write_text(json.dumps({"schema": SCHEMA, "application_root": str(SRC),
            "worker_python": sys.executable, "state_root": str(self.root / "real-state"),
            "allowed_asset_roots": [str(self.root / "assets")],
            "allowed_job_roots": [str(self.root / "jobs")]}), encoding="utf-8")
        control = Control(config)
        workflow = Workflow(control)
        artifact = control.import_asset(self.midi)
        flow = workflow.create(score_artifact_id=artifact["id"], idempotency_key="dry-real")
        note = read_score(artifact["path"])["notes"][0]
        before = workflow._read(flow["id"])
        preview = workflow.apply_review(flow["id"], flow["revision"], "试算",
                                        note_edits=[{"note_id": note["note_id"], "pitch": 61}],
                                        dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(workflow._read(flow["id"]), before)
        self.assertEqual(list((control.state / "operations").iterdir()), [])
        self.assertEqual(list((control.state / "calls").glob("*/review-candidates")), [])

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

    def test_export_preflight_rejects_stored_track_names_before_plan(self):
        invalid = {"track_names": ["主唱", "和声", "伴奏"]}
        created, _ = self.create_midi_flow(initial_export_options=invalid)
        reviewed = self.complete_review(created)
        plan_starts = len(self.control.plan_starts)
        operations = len(self.control.records["operations"])
        before = self.workflow.get(reviewed["id"])

        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(reviewed["id"], reviewed["revision"])

        self.assertEqual(raised.exception.code, "track_names_mismatch")
        self.assertEqual(raised.exception.details["expected"], 1)
        self.assertEqual(raised.exception.details["actual"], 3)
        self.assertEqual(raised.exception.details["midi_track_scopes"][0]["note_count"], 3)
        self.assertEqual(raised.exception.details["track_count_source"],
                         "control.score_read")
        self.assertFalse(raised.exception.details["order_verified_by_preflight"])
        self.assertIn("伴奏", str(raised.exception))
        observed = self.workflow.get(reviewed["id"])
        self.assertEqual(observed["revision"], before["revision"])
        self.assertEqual(observed["stage"], "reviewed")
        self.assertEqual(self.workflow._read(reviewed["id"])["export_options"], invalid)
        self.assertEqual(len(self.control.plan_starts), plan_starts)
        self.assertEqual(len(self.control.records["operations"]), operations)
        self.assertIn("score_read", [row[0] for row in self.control.worker_calls])

    def test_track_name_count_is_not_blocked_without_svp_output(self):
        created, _ = self.create_midi_flow(formats=["ustx"])
        reviewed = self.complete_review(created)

        exporting = self.workflow.advance(
            reviewed["id"], reviewed["revision"],
            export_options={"track_names": ["不会写入 USTX 一", "不会写入 USTX 二"]})

        self.assertEqual(exporting["stage"], "exporting")
        self.assertEqual(exporting["export_options"]["track_names"],
                         ["不会写入 USTX 一", "不会写入 USTX 二"])

    def test_worker_track_count_and_libresvip_channel_order_ignore_meta_tracks(self):
        path = write_track_layout_midi(self.root / "assets" / "track-layout.mid")
        score = read_score(path)

        tracks, count_verified = Workflow._score_vocal_tracks(score)

        self.assertTrue(count_verified)
        self.assertEqual(
            [(row["midi_track_index"], row["channel"], row["note_count"])
             for row in tracks],
            [(2, 2, 1), (2, 5, 1), (3, 3, 1), (3, 4, 1)],
        )
        runtime = Path(
            r"C:\Users\DevWind\Documents\Codex\2026-10-08\or-1-2-3-midi4-midi"
            r"\outputs\Better-Vocaloid-Workflow-Assistant\dependencies\export")
        if not (runtime / "libresvip/plugins/mid/midi_converter.py").is_file():
            self.skipTest("optional LibreSVIP runtime is unavailable")
        old_path = list(sys.path)
        try:
            sys.path.insert(0, str(runtime))
            from libresvip.plugins.mid.midi_converter import MidiConverter
            project = MidiConverter.load(path, {
                "import_lyrics": True, "lyric_encoding": "utf-8",
                "import_time_signatures": True, "multi_channel": "split",
                "import_pitch": False, "import_volume": False,
                "default_bpm": 120,
            })
        finally:
            sys.path[:] = old_path
        actual = [(str(track.title), len(track.note_list)) for track in project.track_list]
        self.assertEqual(
            actual,
            [("Track 3 (5)", 1), ("Track 3 (2)", 1),
             ("Track 4 (4)", 1), ("Track 4 (3)", 1)],
        )
        from agent_exports import apply_export, plan_export
        names = ["首见五", "首见二", "先见四", "后见三"]
        plan = plan_export(path, ["svp"], {"track_names": names, "language": "zh"})
        exported = apply_export(
            path, plan, self.root / "track-layout-output", export_runtime=runtime)
        candidate = Path(exported["candidates"][0]["candidate_path"])
        project = json.loads(io_path(candidate).read_bytes().removesuffix(b"\0"))
        self.assertEqual([track.get("name") for track in project["tracks"]], names)
        self.assertEqual([len(track["mainGroup"]["notes"]) for track in project["tracks"]],
                         [1, 1, 1, 1])
        self.assertFalse(exported["semantic_model_used"])
        self.assertFalse(exported["synthesized"])

    def test_real_reviewed_song_track_layout_matches_libresvip(self):
        real = Path(
            r"C:\Users\DevWind\Documents\Codex\2026-10-23\new-chat-2"
            r"\workspace\mcp\state-v0.2\outputs"
            r"\op_68d4fd43f0840e74c30410c58a174ccb\notes"
            r"\vwa-score-553a81457c538467096a2c657d1ee693bc46e9a5b981fc27fb23d4b8cb5aeb72.mid")
        runtime = Path(
            r"C:\Users\DevWind\Documents\Codex\2026-10-08\or-1-2-3-midi4-midi"
            r"\outputs\Better-Vocaloid-Workflow-Assistant\dependencies\export")
        if not real.is_file() or not (runtime / "libresvip/plugins/mid/midi_converter.py").is_file():
            self.skipTest("existing read-only real-song project and LibreSVIP runtime are unavailable")
        score = read_score(real)

        tracks, count_verified = Workflow._score_vocal_tracks(score)

        self.assertTrue(count_verified)
        self.assertEqual(
            [(row["midi_track_index"], row["channel"], row["note_count"])
             for row in tracks],
            [(1, 0, 503), (2, 1, 72)],
        )
        old_path = list(sys.path)
        try:
            sys.path.insert(0, str(runtime))
            from libresvip.plugins.mid.midi_converter import MidiConverter
            project = MidiConverter.load(real, {
                "import_lyrics": True, "lyric_encoding": "utf-8",
                "import_time_signatures": True, "multi_channel": "split",
                "import_pitch": False, "import_volume": False,
                "default_bpm": 120,
            })
        finally:
            sys.path[:] = old_path
        actual = [(str(track.title), len(track.note_list)) for track in project.track_list]
        self.assertEqual(actual, [("Track 2 (0)", 503), ("Track 3 (1)", 72)])

    @unittest.skipUnless(SDK_PYTHON, "dedicated optional SDK is not configured")
    def test_real_sdk_stdio_export_preflight_stays_on_worker_boundary(self):
        root = self.root / "stdio-preflight"
        exchange = root / "assets"
        jobs = root / "jobs"
        state = root / "state"
        drafts = root / "drafts"
        for path in (exchange, jobs, state, drafts):
            io_path(path).mkdir(parents=True, exist_ok=True)
        source = write_midi(exchange / "stdio.mid")
        config = root / "config.json"
        config.write_text(json.dumps({
            "schema": 1,
            "application_root": str(SRC),
            "worker_python": sys.executable,
            "state_root": str(state),
            "allowed_asset_roots": [str(exchange)],
            "allowed_job_roots": [str(jobs)],
            "source_commit": "stdio-preflight",
            "shared_draft_root": str(drafts),
        }, ensure_ascii=False), encoding="utf-8")
        client = root / "client.py"
        client.write_text(r'''from __future__ import annotations
import asyncio, importlib.util, json, os, sys
from pathlib import Path
from mcp import Client
from mcp.client.stdio import StdioServerParameters

config = Path(sys.argv[1])
server = Path(sys.argv[2])

def text(result):
    return "\n".join(getattr(item, "text", "") for item in (result.content or []))

async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-u", str(server), "--config", str(config)],
        env={key: os.environ[key] for key in
             ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
            | {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    async def ok(call):
        result = await call
        if result.is_error:
            raise AssertionError(text(result))
        return result.structured_content["data"]
    async with Client(params, read_timeout_seconds=90) as client:
        artifact = await ok(client.call_tool("vwa_input", {"request": {
            "action": "assets_import", "path": str(Path(config).parent / "assets/stdio.mid")}}))
        flow = await ok(client.call_tool("vwa_start", {"request": {
            "action": "workflow_create", "score_artifact_id": artifact["id"],
            "idempotency_key": "stdio-export-preflight"}}))
        decided = await ok(client.call_tool("vwa_decide", {"request": {
            "action": "workflow_apply_review", "workflow_id": flow["id"],
            "expected_revision": flow["revision"],
            "rationale": "stdio fixture reviewed without edits"}}))
        waited = await ok(client.call_tool("vwa_wait", {
            "operation_id": decided["operation"]["id"], "timeout_ms": 60000}))
        if waited["state"] != "succeeded":
            raise AssertionError(json.dumps(waited, ensure_ascii=False))
        reviewed = await ok(client.call_tool("vwa_run", {"request": {
            "action": "workflow_advance", "workflow_id": flow["id"],
            "expected_revision": decided["revision"]}}))
        if reviewed["stage"] != "reviewed":
            raise AssertionError(json.dumps(reviewed, ensure_ascii=False))
        rejected = await client.call_tool("vwa_run", {"request": {
            "action": "workflow_advance", "workflow_id": flow["id"],
            "expected_revision": reviewed["revision"],
            "export_options": {"track_names": ["one", "two"]}}})
        body = text(rejected)
        if not rejected.is_error or "track_names_mismatch" not in body or '"expected": 1' not in body:
            raise AssertionError(body)
        return {"sdk_without_mido": importlib.util.find_spec("mido") is None,
                "stage": reviewed["stage"], "error": body}

print(json.dumps(asyncio.run(main()), ensure_ascii=False))
''', encoding="utf-8")
        result = subprocess.run(
            [SDK_PYTHON, "-B", str(client), str(config), str(SRC / "agent_mcp.py")],
            cwd=str(APP), capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"),
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt["sdk_without_mido"])
        self.assertEqual(receipt["stage"], "reviewed")
        self.assertIn("track_names_mismatch", receipt["error"])
        self.assertIn('"expected": 1', receipt["error"])

    def test_unverifiable_track_count_does_not_false_block_export(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)

        with patch.object(Workflow, "_score_vocal_tracks", return_value=([], False)):
            exporting = self.workflow.advance(
                reviewed["id"], reviewed["revision"],
                export_options={"track_names": ["暂不据此拦截", "待实际导出核对", "第三轨"]})

        self.assertEqual(exporting["stage"], "exporting")
        self.assertEqual(
            exporting["export_options"]["track_names"],
            ["暂不据此拦截", "待实际导出核对", "第三轨"])
        self.assertFalse(exporting["export_preflight"]["track_count_verified"])
        self.assertEqual(exporting["export_preflight"]["midi_track_scopes"], [])
        self.assertFalse(exporting["export_preflight"]["order_verified_by_preflight"])

    def test_failed_export_repairs_options_without_re_review_or_auto_approval(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        exporting = self.workflow.advance(
            reviewed["id"], reviewed["revision"],
            export_options={"track_names": ["主唱"]})
        self.control.set_operation(
            exporting["operation_id"], "failed",
            error={"code": "track_names_mismatch", "message": "fixture"})
        failed = self.workflow.get(exporting["id"])
        self.assertEqual(failed["next"]["repair"]["reason"],
                         "reuse_reviewed_source_with_corrected_export_options")
        self.assertTrue(failed["next"]["repair"]["required_arguments"]["retry"])
        review_operations = sum(
            row["request"]["action"] == "workflow_review"
            for row in self.control.records["operations"].values())
        old_plan = failed["active_plan_id"]
        old_operation = failed["operation_id"]

        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(
                failed["id"], failed["revision"],
                export_options={"track_names": ["修正后的主唱"]})
        self.assertEqual(raised.exception.code, "retry_required")
        self.assertEqual(self.workflow.get(failed["id"])["revision"], failed["revision"])

        repaired = self.workflow.advance(
            failed["id"], failed["revision"], retry=True,
            export_options={"track_names": ["修正后的主唱"]})
        self.assertEqual(repaired["stage"], "exporting")
        self.assertEqual(repaired["export_options"], {"track_names": ["修正后的主唱"]})
        self.assertNotEqual(repaired["active_plan_id"], old_plan)
        self.assertNotEqual(repaired["operation_id"], old_operation)
        self.assertEqual(repaired["reviewed_sha256"], reviewed["reviewed_sha256"])
        self.assertEqual(repaired["review_receipt"], reviewed["review_receipt"])
        self.assertEqual(repaired["export_attempt_history"][-1]["operation_id"], old_operation)
        self.assertEqual(repaired["export_attempt_history"][-1]["state"], "failed")
        self.assertEqual(repaired["export_attempt_history"][-1]["error"]["code"],
                         "track_names_mismatch")
        self.assertEqual(
            sum(row["request"]["action"] == "workflow_review"
                for row in self.control.records["operations"].values()),
            review_operations)
        self.assertEqual(self.control.plan_calls[-1][0], "export")
        self.assertEqual(self.control.plan_calls[-1][2]["options"]["track_names"],
                         ["修正后的主唱"])

        candidate = self.export_candidate("repaired.svp")
        self.control.set_operation(
            repaired["operation_id"], "succeeded",
            result=self.build_export_result(repaired, [candidate]))
        project = self.workflow.advance(repaired["id"], repaired["revision"])
        self.assertEqual(project["stage"], "project_ready")
        self.assertIsNone(project["export_review"])
        self.assertFalse(project["completed"])
        self.assertEqual(project["next"]["reason"], "review_export_report")

    def test_failed_export_repair_rejects_changed_reviewed_midi(self):
        created, artifact = self.create_midi_flow()
        reviewed = self.complete_review(created)
        exporting = self.workflow.advance(reviewed["id"], reviewed["revision"])
        self.control.set_operation(
            exporting["operation_id"], "failed",
            error={"code": "converter_unavailable", "message": "fixture"})
        failed = self.workflow.get(exporting["id"])
        path = Path(artifact["path"])
        io_path(path).write_bytes(io_path(path).read_bytes() + b"\0")
        changed = self.control.records["artifacts"][artifact["id"]]
        changed["sha256"] = sha256_file(path)
        changed["bytes"] = io_path(path).stat().st_size
        plan_starts = len(self.control.plan_starts)
        operations = len(self.control.records["operations"])

        with self.assertRaises(ControlError) as raised:
            self.workflow.advance(
                failed["id"], failed["revision"], retry=True,
                export_options={"track_names": ["主唱"]})

        self.assertEqual(raised.exception.code, "review_invalidated")
        observed = self.workflow.get(failed["id"])
        self.assertEqual(observed["revision"], failed["revision"])
        self.assertEqual(observed["stage"], "exporting")
        self.assertEqual(len(self.control.plan_starts), plan_starts)
        self.assertEqual(len(self.control.records["operations"]), operations)

    def test_export_preflight_requires_verified_pitch_and_matching_track(self):
        created, _ = self.create_midi_flow()
        reviewed = self.complete_review(created)
        pitch_path = write_pitch_npz(self.root / "assets" / "lead.pitch.npz")
        pitch = self.control.register_artifact(pitch_path, kind="reference_pitch")
        wrong_kind = self.control.import_asset(pitch_path)
        plan_starts = len(self.control.plan_starts)

        cases = [
            ({"pitch_bindings": [{"pitch_artifact_id": wrong_kind["id"],
                                   "vocal_track_index": 0}]},
             "wrong_pitch_source"),
            ({"pitch_bindings": [{"pitch_artifact_id": pitch["id"],
                                   "vocal_track_index": 1}]},
             "invalid_pitch_bindings"),
        ]
        for options, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(ControlError) as raised:
                    self.workflow.advance(
                        reviewed["id"], reviewed["revision"], export_options=options)
                self.assertEqual(raised.exception.code, code)

        self.assertEqual(self.workflow.get(reviewed["id"])["stage"], "reviewed")
        self.assertEqual(len(self.control.plan_starts), plan_starts)

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
