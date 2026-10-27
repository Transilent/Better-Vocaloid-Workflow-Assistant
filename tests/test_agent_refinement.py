"""Contract tests for post-export, explicit pronunciation refinement.

The fixture converts a tiny local MIDI through the installed read-only
LibreSVIP runtime.  No network, semantic model, synthesis, or user project is
used.  ``FakeControl`` supplies the workflow operation records; its missing
application/runtime attributes are added only inside this test module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import wave
from importlib.util import find_spec
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
TESTS = APP / "tests"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(TESTS))

import mido

from agent_control import ControlError, file_hash
from agent_export_audit import audit_export
from agent_exports import apply_export, plan_export
from agent_refinement import collect_pronunciation, refine_export
from agent_supervision import SupervisionError
from agent_workflow import Workflow
from common import native_path
from test_agent_workflow import FakeControl

PRONUNCIATION_AVAILABLE = find_spec("agent_pronunciation") is not None
if PRONUNCIATION_AVAILABLE:
    from agent_pronunciation import read_pronunciation
else:  # Keep the independent invalid-target test collectable while its peer lands.
    read_pronunciation = None


FORMATS = ["svp", "ustx", "vsqx"]
PATCH_FORMATS = ("svp", "vsqx")


def export_runtime_path():
    candidates = []
    configured = Path(
        r"C:\Users\DevWind\Documents\Codex\2026-10-23\new-chat-2\workspace\paths.json"
    )
    if configured.is_file():
        try:
            paths = json.loads(configured.read_text(encoding="utf-8"))
            legacy = paths.get("legacy_installation")
            if isinstance(legacy, str) and legacy:
                candidates.append(Path(legacy) / "dependencies" / "export")
        except (OSError, ValueError, TypeError):
            pass
    candidates.extend([
        Path(
            r"C:\Users\DevWind\Documents\Codex\2026-10-08\or-1-2-3-midi4-midi"
            r"\outputs\Better-Vocaloid-Workflow-Assistant\dependencies\export"
        ),
        APP / "dependencies" / "export",
    ])
    for candidate in candidates:
        probe = candidate / "libresvip" / "plugins" / "mid" / "midi_converter.py"
        if probe.is_file():
            return candidate.resolve()
    return None


EXPORT_RUNTIME = export_runtime_path()


def write_midi(path):
    """Two sung notes with a tempo change at the second note."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo", time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("set_tempo", tempo=600_000, time=480),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead", time=0),
        mido.MetaMessage("lyrics", text="你", time=0),
        mido.Message("note_on", channel=0, note=60, velocity=88, time=0),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=480),
        mido.MetaMessage("lyrics", text="好", time=0),
        mido.Message("note_on", channel=0, note=64, velocity=88, time=0),
        mido.Message("note_off", channel=0, note=64, velocity=0, time=480),
    ]))
    with open(native_path(path), "wb") as stream:
        midi.save(file=stream)
    return path


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review_for(project):
    summary = project["export_summary"]
    return {
        "report_sha256": summary["report_sha256"],
        "decision": "accept",
        "rationale": "已逐项核对实际音符、歌词和秒数，并认可当前可见转换损失。",
        "accepted_loss_codes": list(summary["required_loss_codes"]),
    }


def one_of(note, *names):
    for name in names:
        if name in note:
            return note[name]
    raise KeyError("/".join(names))


@unittest.skipUnless(EXPORT_RUNTIME, "readonly LibreSVIP export runtime is unavailable")
class AgentRefinementTests(unittest.TestCase):
    def setUp(self):
        work = APP / "work"
        work.mkdir(parents=True, exist_ok=True)
        self.root = Path(
            tempfile.mkdtemp(prefix="agent-refinement-", dir=str(work))
        ).resolve()
        self.addCleanup(self.cleanup_temporary)
        self.source = write_midi(self.root / "assets" / "source.mid")
        self.source_bytes = self.source.read_bytes()
        self.source_sha256 = sha256_file(self.source)

        self.control = FakeControl(self.root)
        # FakeControl models the record/path surface only.  The real coordinator
        # exposes these configured fields used by the failing worker retry path.
        self.control.app = APP
        self.control.config = {"export_runtime": str(EXPORT_RUNTIME)}
        self.workflow = Workflow(self.control)

    def cleanup_temporary(self):
        target = Path(native_path(self.root)).resolve()
        work = Path(native_path(APP / "work")).resolve()
        self.assertTrue(target.is_relative_to(work))
        self.assertNotEqual(target, work)
        shutil.rmtree(native_path(target))

    def assert_source_unchanged(self):
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(sha256_file(self.source), self.source_sha256)

    def build_real_export(self):
        source_artifact = self.control.import_asset(self.source)
        created = self.workflow.create(
            score_artifact_id=source_artifact["id"],
            formats=list(FORMATS),
            brief="导出后按调用方明确指定的音素修正并重新核对。",
            idempotency_key="refinement-real-export",
        )
        reviewing = self.workflow.apply_review(
            created["id"], created["revision"],
            "已核对真实 MIDI 的音高、音符边界与歌词。",
        )
        self.control.set_operation(
            reviewing["operation_id"], "succeeded",
            result=self.control.review_success(
                reviewing["id"], reviewing["score_artifact_id"],
            ),
        )
        reviewed = self.workflow.advance(reviewing["id"], reviewing["revision"])
        self.assertEqual(reviewed["stage"], "reviewed")
        exporting = self.workflow.advance(reviewed["id"], reviewed["revision"])
        self.assertEqual(exporting["stage"], "exporting")

        plan = plan_export(self.source, FORMATS, {"language": "zh"})
        receipt = apply_export(
            self.source,
            plan,
            self.root / "bundle-original",
            export_runtime=EXPORT_RUNTIME,
        )
        audit = audit_export(
            self.source,
            receipt,
            export_runtime=EXPORT_RUNTIME,
            output_root=self.root / "audit-original",
        )
        artifacts = [
            self.control.register_artifact(
                Path(row["candidate_path"]), kind="candidate",
            )
            for row in receipt["candidates"]
        ]
        operation_result = {
            "receipt": {**receipt, "independent_audit": audit},
            "artifacts": artifacts,
            "handoff": "candidate_ready",
        }
        self.control.set_operation(
            exporting["operation_id"], "succeeded", result=operation_result,
        )
        project = self.workflow.advance(exporting["id"], exporting["revision"])
        self.assertEqual(project["stage"], "project_ready")
        self.assertFalse(project["completed"])
        self.assertEqual(
            [row["format"] for row in project["export_summary"]["candidates"]],
            FORMATS,
        )
        self.assert_source_unchanged()
        return project, operation_result

    def artifact_for(self, project, format_name):
        row = next(
            row for row in project["export_summary"]["candidates"]
            if row["format"] == format_name
        )
        artifact = self.control.artifact(row["artifact_id"])
        self.assertEqual(artifact["sha256"], row["sha256"])
        return artifact
    def changes_for(self, project):
        changes = []
        notes = {}
        for format_name in PATCH_FORMATS:
            artifact = self.artifact_for(project, format_name)
            snapshot = read_pronunciation(
                artifact["path"], limit=100, offset=0,
                export_runtime=str(EXPORT_RUNTIME),
            )
            self.assertEqual(snapshot["format"], format_name)
            self.assertEqual(snapshot["source_sha256"], artifact["sha256"])
            self.assertTrue(snapshot["notes"])
            target = snapshot["notes"][0]
            self.assertIn("note_ref", target)
            changes.append({
                "artifact_id": artifact["id"],
                "expected_sha256": artifact["sha256"],
                "edits": [{
                    "note_ref": target["note_ref"],
                    "phonemes": "ni",
                }],
            })
            notes[format_name] = {
                "artifact": artifact,
                "before": snapshot,
                "note_ref": target["note_ref"],
            }
        return changes, notes

    def test_workflow_rejects_duplicate_wrong_sha_and_foreign_targets(self):
        project, _ = self.build_real_export()
        svp = self.artifact_for(project, "svp")
        edits = [{"note_ref": "fixture-note", "phonemes": "ni"}]
        duplicate = [
            {
                "artifact_id": svp["id"],
                "expected_sha256": svp["sha256"],
                "edits": copy.deepcopy(edits),
            },
            {
                "artifact_id": svp["id"],
                "expected_sha256": svp["sha256"],
                "edits": copy.deepcopy(edits),
            },
        ]
        foreign_path = self.root / "assets" / "foreign.svp"
        foreign_path.write_bytes(Path(svp["path"]).read_bytes())
        foreign = self.control.register_artifact(foreign_path, kind="candidate")

        cases = (
            (duplicate, "invalid_pronunciation_targets"),
            ([{
                "artifact_id": svp["id"],
                "expected_sha256": "0" * 64,
                "edits": copy.deepcopy(edits),
            }], "stale_artifact"),
            ([{
                "artifact_id": foreign["id"],
                "expected_sha256": foreign["sha256"],
                "edits": copy.deepcopy(edits),
            }], "invalid_pronunciation_targets"),
        )
        for changes, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(ControlError) as raised:
                    self.workflow.pronunciation_apply(
                        project["id"], project["revision"], changes,
                        "调用方明确要求替换音素。",
                    )
                self.assertEqual(raised.exception.code, code)
                observed = self.workflow.get(project["id"])
                self.assertEqual(observed["stage"], "project_ready")
                self.assertEqual(observed["revision"], project["revision"])
        self.assert_source_unchanged()

    def test_unsupported_native_overlap_is_unknown_and_still_hash_bound(self):
        project, _ = self.build_real_export()
        artifact = self.artifact_for(project, "svp")
        document = json.loads(Path(artifact["path"]).read_bytes().rstrip(b"\x00"))
        notes = document["tracks"][0]["mainGroup"]["notes"]
        notes[1]["onset"] = notes[0]["onset"] + notes[0]["duration"] // 2
        target = self.root / "unsupported-overlap.svp"
        target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        before = target.read_bytes()
        candidate = {"format": "svp", "candidate_path": str(target),
                     "candidate_sha256": sha256_file(target)}
        evidence = collect_pronunciation([candidate], EXPORT_RUNTIME)
        report = evidence[0]["report"]
        self.assertFalse(report["evidence_available"])
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["source_sha256"], candidate["candidate_sha256"])
        self.assertIn("重叠", report["reason"])
        self.assertEqual(target.read_bytes(), before)
        candidate["candidate_sha256"] = "0" * 64
        with self.assertRaises(ControlError) as raised:
            collect_pronunciation([candidate], EXPORT_RUNTIME)
        self.assertEqual(raised.exception.code, "candidate_mismatch")
        self.assert_source_unchanged()

    @unittest.skipUnless(
        PRONUNCIATION_AVAILABLE,
        "agent_pronunciation peer contract is not available yet",
    )
    def test_refinement_itself_rejects_invalid_targets_without_output(self):
        project, previous = self.build_real_export()
        svp = self.artifact_for(project, "svp")
        edits = [{"note_ref": "fixture-note", "phonemes": "ni"}]
        duplicate = [
            {
                "artifact_id": svp["id"],
                "expected_sha256": svp["sha256"],
                "edits": copy.deepcopy(edits),
            },
            {
                "artifact_id": svp["id"],
                "expected_sha256": svp["sha256"],
                "edits": copy.deepcopy(edits),
            },
        ]
        foreign_path = self.root / "assets" / "direct-foreign.svp"
        foreign_path.write_bytes(Path(svp["path"]).read_bytes())
        foreign = self.control.register_artifact(foreign_path, kind="candidate")

        cases = (
            ("duplicate", duplicate, "invalid_pronunciation_targets"),
            ("wrong-sha", [{
                "artifact_id": svp["id"],
                "expected_sha256": "f" * 64,
                "edits": copy.deepcopy(edits),
            }], "stale_artifact"),
            ("foreign", [{
                "artifact_id": foreign["id"],
                "expected_sha256": foreign["sha256"],
                "edits": copy.deepcopy(edits),
            }], "invalid_pronunciation_targets"),
        )
        for name, changes, code in cases:
            target = self.root / ("rejected-" + name)
            with self.subTest(name=name):
                with self.assertRaises(ControlError) as raised:
                    refine_export(
                        self.source,
                        previous,
                        changes,
                        target,
                        export_runtime=EXPORT_RUNTIME,
                    )
                self.assertEqual(raised.exception.code, code)
                files = [path for path in target.rglob("*") if path.is_file()]
                self.assertEqual(files, [])
        self.assert_source_unchanged()
    @unittest.skipUnless(
        PRONUNCIATION_AVAILABLE,
        "agent_pronunciation peer contract is not available yet",
    )
    def test_phone_edit_preserves_score_retains_candidates_and_requires_new_review(self):
        project, _previous = self.build_real_export()
        reviewed = self.workflow.advance(
            project["id"], project["revision"], export_review=review_for(project),
        )
        self.assertEqual(reviewed["stage"], "project_ready")
        old_summary = copy.deepcopy(reviewed["export_summary"])
        self.assertEqual(
            reviewed["export_review"]["report_sha256"],
            old_summary["report_sha256"],
        )
        changes, before = self.changes_for(reviewed)
        original = {
            row["format"]: self.control.artifact(row["artifact_id"])
            for row in old_summary["candidates"]
        }
        original_bytes = {
            name: Path(row["path"]).read_bytes() for name, row in original.items()
        }
        self.assertEqual(
            {name: sha256_file(row["path"]) for name, row in original.items()},
            {name: row["sha256"] for name, row in original.items()},
        )

        refining = self.workflow.pronunciation_apply(
            reviewed["id"], reviewed["revision"], changes,
            "按调用方明确给出的读音将两处显式音素替换为 ni；此处不推断实际发音正确。",
        )
        self.assertEqual(refining["stage"], "refining")
        self.assertFalse(refining["export_review"])
        failed_operation = refining["operation_id"]
        self.control.set_operation(
            failed_operation,
            "failed",
            error={"code": "worker_crashed", "message": "fixture worker interruption"},
        )
        failed = self.workflow.get(refining["id"])
        self.assertEqual(failed["stage"], "refining")
        self.assertEqual(failed["next"]["tool"], "vwa_workflow_advance")
        self.assertEqual(failed["next"]["error"]["code"], "worker_crashed")

        retried = self.workflow.advance(
            refining["id"], refining["revision"], retry=True,
        )
        self.assertEqual(retried["stage"], "refining")
        self.assertNotEqual(retried["operation_id"], failed_operation)
        operation_id = retried["operation_id"]
        arguments = copy.deepcopy(
            self.control.records["operations"][operation_id]["request"]["arguments"]
        )
        result = self.workflow.execute_pronunciation(
            operation_id,
            arguments,
            self.root / "refined-operation",
            lambda: None,
            lambda _text: None,
        )
        self.assertTrue(result["receipt"]["source_unchanged"])
        self.assertFalse(result["acoustic_verification"])
        self.assertEqual(
            result["receipt"]["independent_audit"]["status"], "verified"
        )
        self.control.set_operation(operation_id, "succeeded", result=result)
        refined = self.workflow.advance(retried["id"], retried["revision"])
        self.assertEqual(refined["stage"], "project_ready")
        self.assertFalse(refined["completed"])
        self.assertFalse(refined["export_review"])
        self.assert_source_unchanged()

        new_summary = refined["export_summary"]
        self.assertNotEqual(old_summary["receipt_sha256"], new_summary["receipt_sha256"])
        self.assertNotEqual(old_summary["report_sha256"], new_summary["report_sha256"])
        self.assertIn("explicit_phonemes_unverified", new_summary["required_loss_codes"])
        self.assertEqual(len(refined["pronunciation_receipts"]), 2)
        self.assertTrue(Path(result["receipt"]["receipt_path"]).is_file())

        for name, row in original.items():
            self.assertTrue(Path(row["path"]).is_file())
            self.assertEqual(Path(row["path"]).read_bytes(), original_bytes[name])
            self.assertEqual(file_hash(row["path"]), row["sha256"])
        new_artifacts = {
            row["format"]: self.control.artifact(row["artifact_id"])
            for row in new_summary["candidates"]
        }
        self.assertEqual(set(new_artifacts), set(FORMATS))
        self.assertEqual(
            new_artifacts["ustx"]["sha256"], original["ustx"]["sha256"]
        )
        for name in PATCH_FORMATS:
            self.assertNotEqual(
                new_artifacts[name]["sha256"], original[name]["sha256"]
            )
            after = read_pronunciation(
                new_artifacts[name]["path"], limit=100, offset=0,
                export_runtime=str(EXPORT_RUNTIME),
            )
            self.assertEqual(after["format"], name)
            self.assertEqual(len(after["notes"]), len(before[name]["before"]["notes"]))
            def location(note):
                return (note['track_index'],note['group_index'],note['note_index'])
            before_refs={note['note_ref']:note for note in before[name]['before']['notes']}
            before_notes={location(note):note for note in before_refs.values()}
            after_notes={location(note):note for note in after['notes']}
            self.assertEqual(set(after_notes), set(before_notes))
            for note_location, old_note in before_notes.items():
                new_note = after_notes[note_location]
                self.assertNotEqual(new_note['note_ref'],old_note['note_ref'],
                                    'native note references must invalidate across source revisions')
                self.assertAlmostEqual(
                    float(new_note["onset"]), float(old_note["onset"]), places=12
                )
                self.assertAlmostEqual(
                    float(new_note["duration"]), float(old_note["duration"]), places=12
                )
                self.assertEqual(new_note["pitch"], old_note["pitch"])
                self.assertEqual(
                    one_of(new_note, "lyric", "original_lyric", "lyrics", "text"),
                    one_of(old_note, "lyric", "original_lyric", "lyrics", "text"),
                )
                self.assertEqual(
                    one_of(new_note, "track", "track_index"),
                    one_of(old_note, "track", "track_index"),
                )
                self.assertEqual(
                    one_of(new_note, "group", "group_ref", "group_id"),
                    one_of(old_note, "group", "group_ref", "group_id"),
                )
            before_note = before_refs[before[name]["note_ref"]]
            changed_note = after_notes[location(before_note)]
            self.assertNotEqual(changed_note.get("phonemes"), before_note.get("phonemes"))
            self.assertIn("phonemes", changed_note)

        with self.assertRaises(SupervisionError) as raised:
            self.workflow.advance(
                refined["id"], refined["revision"],
                export_review=review_for(reviewed),
            )
        self.assertEqual(raised.exception.code, "export_review_conflict")
        observed = self.workflow.get(refined["id"])
        self.assertEqual(observed["stage"], "project_ready")
        self.assertEqual(observed["revision"], refined["revision"])
        self.assertFalse(observed["export_review"])


    @unittest.skipUnless(
        PRONUNCIATION_AVAILABLE,
        "agent_pronunciation peer contract is not available yet",
    )
    def test_refine_moves_media_beside_new_svp_and_keeps_old_package(self):
        audio = self.root / "assets" / "伴奏.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(2)
            stream.setsampwidth(2)
            stream.setframerate(44100)
            stream.writeframes(b"\0\0\0\0" * 8820)
        plan = plan_export(self.source, ["svp"], {
            "include_instrumental": True,
            "instrumental_path": str(audio),
        })
        receipt = apply_export(self.source, plan, self.root / "bundle-with-media",
                               export_runtime=EXPORT_RUNTIME)
        candidate = receipt["candidates"][0]
        old_path = Path(candidate["candidate_path"])
        old_media = Path(candidate["media"]["path"])
        old_bytes = old_path.read_bytes()
        old_media_bytes = old_media.read_bytes()
        snapshot = read_pronunciation(old_path, limit=10, offset=0,
                                      export_runtime=str(EXPORT_RUNTIME))
        note = snapshot["notes"][0]
        artifact = {
            "id": "art_media_svp", "path": str(old_path),
            "sha256": candidate["candidate_sha256"], "format": "svp",
        }
        previous = {"receipt": receipt, "artifacts": [artifact]}
        changes = [{
            "artifact_id": artifact["id"],
            "expected_sha256": artifact["sha256"],
            "edits": [{"note_ref": note["note_ref"], "phonemes": "ni"}],
        }]
        refined = refine_export(
            self.source, previous, changes, self.root / "bundle-refined",
            export_runtime=EXPORT_RUNTIME, rationale="明确写入音素并保留伴奏。",
        )
        new_candidate = refined["candidates"][0]
        self.assertTrue(new_candidate["instrumental_included"])
        self.assertTrue(new_candidate["media"]["post_publish_readback_verified"])
        new_path = Path(new_candidate["candidate_path"])
        new_media = Path(new_candidate["media"]["path"])
        self.assertNotEqual(new_path, old_path)
        self.assertTrue(new_media.is_file())
        self.assertEqual(new_media.read_bytes(), old_media_bytes)
        self.assertEqual(new_candidate["media"]["sha256"], candidate["media"]["sha256"])
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(old_media.read_bytes(), old_media_bytes)


if __name__ == "__main__":
    unittest.main()
