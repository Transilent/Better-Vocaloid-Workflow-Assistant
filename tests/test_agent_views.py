"""Contract tests for pure compact Agent views; no I/O or model execution."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
sys.path.insert(0, str(SRC))

from agent_views import (SUMMARY_TARGET_BYTES, ViewError, advice_summary,
                         error_summary, projection_size, review_view,
                         suggestion_summary, workflow_view)


def compact_size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


def workflow_fixture():
    long_lyrics = "牵丝戏歌词" * 5000
    page_note_ids = [f"note-fixture-t0-n{index:04d}" for index in range(300)]
    artifact = {
        "id": "art_project123",
        "kind": "project",
        "path": "C:/state/outputs/project.svp",
        "sha256": "a" * 64,
        "bytes": 123456,
        "notes": [{"note_id": note_id, "lyric": "词"} for note_id in page_note_ids],
    }
    summary = {
        "schema": 1,
        "kind": "vwa.export-supervision",
        "operation_id": "op_export123",
        "source_sha256": "b" * 64,
        "plan_sha256": "c" * 64,
        "receipt_sha256": "d" * 64,
        "report_sha256": "e" * 64,
        "can_accept": True,
        "review_required": True,
        "inference_performed": False,
        "required_loss_codes": ["tempo_table_normalized", "track_identity_normalized"],
        "candidates": [{
            "artifact_id": artifact["id"],
            "format": "svp",
            "sha256": artifact["sha256"],
            "verified_notes": 300,
            "verified_lyrics": 300,
            "tick_roundtrip_verified": True,
            "tempo_table_equal": True,
            "time_signature_equal": True,
            "expected_tempo": [{"tick": i, "bpm": 120.0} for i in range(300)],
            "output_tempo": [{"tick": i, "bpm": 120.0} for i in range(300)],
            "loss_codes": ["tempo_table_normalized"],
            "instrumental_included": True,
            "instrumental_reason": "included_in_svp_portable_package",
            "media": {
                "included": True,
                "count": 1,
                "sha256": "9" * 64,
                "duration_seconds": 241.0453514739229,
                "relative_paths": ["伴奏_instrumental.wav"],
                "project_sha256": "8" * 64,
                "readback_verified": True,
                "post_publish_readback_verified": True,
                "tracks": [{"track_index": 1, "track_name": "伴奏",
                            "relative_path": "伴奏_instrumental.wav",
                            "sha256": "9" * 64, "bytes": 999,
                            "duration_seconds": 241.0453514739229,
                            "sample_rate": 44100, "channels": 2,
                            "format": "wav", "verified": True,
                            "notes": [{"raw": "x" * 1000}] * 100}],
                "vocals": [{"track_index": 0, "name": "主唱", "notes": 300}],
            },
            "pronunciation": {"status": "unknown", "evidence_available": False,
                              "acoustic_verification": False,
                              "notes": [{"note_ref": note_id, "phonemes": "raw"}
                                        for note_id in page_note_ids]},
        }],
        "warnings": [{"code": "tempo_table_normalized", "format": "svp",
                      "message": "实际秒数需要核对", "note_ids": page_note_ids},
                     {"code": "track_identity_normalized", "message": "轨道身份归一化"}],
        "blockers": [],
        "information": [{"code": "vsqx_lyrics_restored_after_dump", "count": 3}],
        "independent_audit": {
            "status": "verified",
            "source_sha256": "b" * 64,
            "audit_sha256": "f" * 64,
            "acoustic_or_phonetic_verified": False,
            "inference_performed": False,
            "blockers": [],
            "warnings": [],
            "candidates": [{"note_rows": [{"raw": "x" * 1000} for _ in range(100)]}],
        },
    }
    return {
        "schema": 1,
        "id": "flow_fixture",
        "revision": 17,
        "stage": "project_ready",
        "code_revision": "code-sha",
        "request": {
            "source": "BV1ZL411i7YQ",
            "brief": "悲伤、克制的戏腔",
            "reference_lyrics": long_lyrics,
            "options": {"language": "zh", "voice_mode": "single", "lyrics": long_lyrics},
            "formats": ["svp"],
            "score_artifact_id": "art_score123",
        },
        "preparation_plan_id": "plan_prepare123",
        "score_artifact_id": "art_score123",
        "job_id": "job_fixture",
        "reviewed_sha256": "1" * 64,
        "project_artifact_id": artifact["id"],
        "project_artifacts": [artifact],
        "export_summary": summary,
        "previous_export_summary": copy.deepcopy(summary),
        "final_artifacts": [artifact],
        "export_review": None,
        "review_receipt": {"artifact_id": "art_score123", "source_sha256": "1" * 64,
                           "rationale": "已核对", "caller_reviewed": True,
                           "notes": [{"note_id": note_id} for note_id in page_note_ids]},
        "history": [{"action": "step-%03d" % index, "revision": index, "at": index,
                     "notes": page_note_ids} for index in range(100)],
        "operation": {"id": "op_export123", "state": "succeeded",
                      "progress": {"done": 1, "total": 1}, "cancel_requested": False,
                      "request": {"action": "export", "arguments": {"notes": page_note_ids}},
                      "result": {"notes": page_note_ids}},
        "next": {"tool": "vwa_workflow_advance",
                 "arguments": {"workflow_id": "flow_fixture", "expected_revision": 17},
                 "required_input": {"export_review": {
                     "decision": ["accept", "needs_revision"],
                     "accepted_loss_codes": ["tempo_table_normalized", "track_identity_normalized"]}}},
        "completed": False,
        "semantic_provider": "caller_supplied_plan",
        "legacy_client_resume": {"token": "resume-token", "revision": 17},
        "stage_error": None,
    }


def review_fixture():
    all_ids = [f"note-fixture-t0-n{index:04d}" for index in range(300)]
    page_ids = all_ids[:10]
    score_notes = [{
        "note_id": note_id,
        "track_index": 0,
        "channel": 0,
        "pitch": 60 + index % 7,
        "velocity": 90,
        "onset_tick": 480 * index,
        "duration_tick": 400,
        "end_tick": 480 * index + 400,
        "onset_seconds": index * 0.5,
        "duration_seconds": 0.4,
        "end_seconds": index * 0.5 + 0.4,
        "lyric": "字",
        "lyric_event_index": index,
        "pairing_ambiguous": False,
    } for index, note_id in enumerate(page_ids)]
    lyric_notes = [dict(row, lyric_alignment="aligned",
                        lyric_event_id=f"lyr-{'1' * 64}-t0-e{index}",
                        competing_lyric_event_indexes=[],
                        competing_lyric_event_ids=[])
                   for index, row in enumerate(score_notes)]
    return {
        "workflow_id": "flow_fixture",
        "revision": 4,
        "artifact_id": "art_score123",
        "total_notes": 300,
        "next_offset": 10,
        "total_evidence_rows": 300,
        "decision_tool": "vwa_workflow_apply_review",
        "full_evidence_tools": ["vwa_score_read", "vwa_score_review",
                                "vwa_lyrics_read", "vwa_lyrics_review"],
        "instructions": "先核对当前页音符与歌词。",
        "references": {"pitch_artifact_id": "art_pitch", "time_origin": 0.0,
                       "rationale": "参考音高"},
        "reference_evidence": {
            "pitch": {"summary": {"notes_selected": 2, "reviewed": 1, "unknown": 1},
                      "rows": [{"note_ids": all_ids, "status": "review"}]},
            "audio": {"duration_seconds": 240.0, "frame_analysis_performed": False,
                      "note_ids": all_ids},
            "audio_excerpt_next": {"tool": "vwa_reference_audio_read",
                                   "arguments": {"artifact_id": "art_audio", "start": 0.0,
                                                 "end": 1.0, "make_excerpt": True}},
        },
        "available_references": [{"id": "art_pitch", "kind": "reference_pitch"},
                                 {"id": "art_audio", "kind": "reference_audio"}],
        "evidence": {
            "score": {
                "schema_version": 1,
                "format": "midi",
                "source_sha256": "1" * 64,
                "ticks_per_beat": 480,
                "timing": {"tempo_events": [{"tick": index, "bpm": 120.0}
                                             for index in range(30)]},
                "tracks": [{"track_index": 0, "name": "唱", "channels": [0],
                            "note_count": 300, "lyric_count": 300,
                            "note_ids": all_ids,
                            "lyric_events": [{"event_id": f"lyr-{'1' * 64}-t0-e{index}",
                                              "event_index": index, "text": "字",
                                              "note_ids": all_ids[index:index + 1]}
                                             for index in range(300)]}],
                "notes": score_notes,
                "warnings": ["测试"],
            },
            "score_review": {
                "source_sha256": "1" * 64,
                "track_count": 1,
                "note_count": 300,
                "hint_count": 2,
                "hints": [
                    {"code": "very_short_duration", "severity": "info",
                     "note_ids": [page_ids[0]], "reason": "短音"},
                    {"code": "global_hint", "severity": "warning",
                     "note_ids": all_ids, "first_note_id": all_ids[-1],
                     "second_note_id": page_ids[1], "reason": "跨页提示"},
                ],
                "disclaimer": "提示不等于错误",
            },
            "lyrics": {
                "schema_version": 1,
                "kind": "vwa-lyrics-read",
                "source_sha256": "1" * 64,
                "language": "zh",
                "notes": lyric_notes,
                "coverage": {"note_count": 300, "aligned_note_count": 300},
                "lyric_events": [{"event_id": f"lyr-{'1' * 64}-t0-e{index}",
                                  "event_index": index, "text": "字",
                                  "note_ids": [page_ids[index % len(page_ids)]]}
                                 for index in range(10)],
                "lyric_events_total": 300,
                "orphan_lyric_events": [],
                "ambiguous_lyric_events": [],
                "meta_evidence": [{"track_index": 0,
                                   "meta_events": [{"type": "marker"}] * 50}],
                "tracks": [{"track_index": 0, "note_ids": all_ids,
                            "lyric_events": [{
                                "event_id": f"lyr-{'1' * 64}-t0-e{index}",
                                "event_index": index} for index in range(300)]}],
                "unknown_notes": [{"note_id": all_ids[-1], "lyric_alignment": "missing"}],
                "ambiguous_notes": [],
            },
            "lyric_review": {
                "source_sha256": "1" * 64,
                "note_count": 300,
                "lyric_event_count": 300,
                "hint_count": 1,
                "hints": [{"code": "duplicate_adjacent_lyric", "severity": "info",
                           "note_ids": page_ids[:2],
                           "event_ids": [f"lyr-{'1' * 64}-t0-e0",
                                         f"lyr-{'1' * 64}-t0-e1"],
                           "message": "相邻重复"}],
            },
        },
    }


class WorkflowViewTests(unittest.TestCase):
    def test_summary_is_bounded_keeps_continuation_and_does_not_mutate(self):
        value = workflow_fixture()
        before = copy.deepcopy(value)
        view = workflow_view(value)
        self.assertEqual(value, before)
        self.assertLess(projection_size(view), SUMMARY_TARGET_BYTES)
        self.assertEqual(view["id"], value["id"])
        self.assertEqual(view["revision"], value["revision"])
        self.assertEqual(view["stage"], value["stage"])
        self.assertEqual(view["next"], value["next"])
        self.assertEqual(view["project_artifact_id"], value["project_artifact_id"])
        self.assertEqual([row["id"] for row in view["project_artifacts"]],
                         [row["id"] for row in value["project_artifacts"]])
        self.assertEqual([row["id"] for row in view["final_artifacts"]],
                         [row["id"] for row in value["final_artifacts"]])
        self.assertEqual(view["legacy_client_resume"], value["legacy_client_resume"])
        self.assertEqual(view["request"]["source"], value["request"]["source"])
        self.assertEqual(view["request"]["brief"], value["request"]["brief"])
        self.assertEqual(view["request"]["options"]["language"], "zh")
        self.assertNotIn("reference_lyrics", view["request"])
        self.assertIn("reference_lyrics_summary", view["request"])
        self.assertNotIn("lyrics", view["request"]["options"])
        self.assertIn("lyrics_summary", view["request"]["options"])

        summary = view["export_summary"]
        source_summary = value["export_summary"]
        self.assertEqual(summary["report_sha256"], source_summary["report_sha256"])
        self.assertEqual(summary["required_loss_codes"], source_summary["required_loss_codes"])
        self.assertIs(summary["can_accept"], True)
        self.assertEqual(summary["independent_audit"]["status"], "verified")
        self.assertIn("status", summary)
        self.assertEqual(summary["candidates"][0]["artifact_id"], value["project_artifact_id"])
        self.assertEqual(summary["candidates"][0]["verified_notes"], 300)
        self.assertIs(summary["candidates"][0]["instrumental_included"], True)
        self.assertEqual(summary["candidates"][0]["instrumental_reason"],
                         "included_in_svp_portable_package")
        self.assertEqual(summary["candidates"][0]["media"]["count"], 1)
        self.assertEqual(summary["candidates"][0]["media"]["relative_paths"],
                         ["伴奏_instrumental.wav"])
        self.assertEqual(summary["candidates"][0]["media"]["files"][0]["sha256"], "9" * 64)
        self.assertNotIn("notes", summary["candidates"][0]["media"]["files"][0])
        self.assertIn("pronunciation", summary["candidates"][0])
        self.assertEqual(summary["candidates"][0]["pronunciation"]["status"], "unknown")
        self.assertNotIn("notes", summary["candidates"][0]["pronunciation"])
        self.assertNotIn("notes", summary["candidates"][0])
        self.assertNotIn("expected_tempo", summary["candidates"][0])
        self.assertNotIn("native_notes", json.dumps(view, ensure_ascii=False))
        self.assertLessEqual(len(view["history"]), 8)
        self.assertEqual(view["history_count"], 100)

    def test_full_is_complete_detached_copy(self):
        value = workflow_fixture()
        view = workflow_view(value, detail="full")
        self.assertEqual(view, value)
        self.assertIsNot(view, value)
        view["request"]["reference_lyrics"] = "changed"
        self.assertNotEqual(value["request"]["reference_lyrics"], "changed")

    def test_summary_can_explicitly_keep_raw_evidence_lists(self):
        value = workflow_fixture()
        view = workflow_view(value, include_raw_evidence=True, include_note_ids=True)
        self.assertEqual(len(view["history"]), len(value["history"]))
        self.assertEqual(len(view["review_receipt"]["notes"]), 300)

    def test_invalid_projection_arguments_have_stable_error_shape(self):
        with self.assertRaises(ViewError) as raised:
            workflow_view({"id": "flow"}, detail="everything")
        error = raised.exception
        self.assertEqual(error.code, "invalid_detail")
        self.assertEqual(error.component, "agent_views")
        self.assertFalse(error.retryable)
        self.assertTrue(error.remediation)
        with self.assertRaises(ViewError) as raised:
            workflow_view({"id": "flow"}, include_note_ids=1)
        self.assertEqual(raised.exception.code, "invalid_projection_flag")


class ReviewViewTests(unittest.TestCase):
    def test_summary_has_counts_and_diff_context_but_no_note_table(self):
        value = review_fixture()
        before = copy.deepcopy(value)
        view = review_view(value, detail="summary")
        self.assertEqual(value, before)
        self.assertEqual(view["workflow_id"], value["workflow_id"])
        self.assertEqual(view["revision"], value["revision"])
        self.assertEqual(view["artifact_id"], value["artifact_id"])
        self.assertEqual(view["decision_tool"], "vwa_workflow_apply_review")
        self.assertEqual(view["next_offset"], 10)
        self.assertEqual(view["note_page"]["returned"], 0)
        self.assertEqual(view["note_page"]["available_in_page"], 10)
        self.assertEqual(view["counts"]["total_notes"], 300)
        self.assertFalse(view["review_complete"])
        self.assertEqual(view["diff_context"]["scope"], "current_page_hints")
        self.assertEqual(view["diff_context"]["page_hint_count"], 3)
        self.assertTrue(view["diff_context"]["preview"])
        self.assertIn(all_note_id(299), json.dumps(view["diff_context"], ensure_ascii=False))
        self.assertFalse(view["continuation"]["review_complete"])
        self.assertFalse(view["continuation"]["acceptance_evidence_complete"])
        self.assertNotIn("notes", view["evidence"]["score"])
        self.assertNotIn("hints", view["evidence"]["score_review"])

    def test_notes_page_is_bounded_and_hides_evidence_note_id_arrays_by_default(self):
        value = review_fixture()
        view = review_view(value)
        self.assertLess(projection_size(view), SUMMARY_TARGET_BYTES)
        self.assertEqual(len(view["evidence"]["score"]["notes"]), 10)
        self.assertEqual(view["note_page"]["returned"], 10)
        self.assertEqual(view["note_page"]["next_offset"], 10)
        self.assertEqual(view["counts"]["total_notes"], 300)
        self.assertNotIn("note_ids", view["evidence"]["score"]["tracks"][0])
        self.assertTrue(all("note_ids" not in hint for hint in view["evidence"]["score_review"]["hints"]))
        self.assertIn(all_note_id(299), json.dumps(view["diff_context"], ensure_ascii=False))
        self.assertTrue(view["continuation"]["has_more"])
        self.assertFalse(view["continuation"]["acceptance_evidence_complete"])
        self.assertIn("reference_evidence", view)
        self.assertEqual(view["reference_evidence"]["pitch"]["summary"]["notes_selected"], 2)
        self.assertFalse(view["reference_evidence"]["audio"]["frame_analysis_performed"])
        self.assertEqual(view["reference_evidence"]["audio_excerpt_next"]["tool"],
                         "vwa_reference_audio_read")
        self.assertNotIn("lyric_events", view["evidence"]["lyrics"]["tracks"][0])

    def test_include_note_ids_exposes_only_current_page(self):
        value = review_fixture()
        page = {f"note-fixture-t0-n{index:04d}" for index in range(10)}
        view = review_view(value, include_note_ids=True)
        track_refs = view["evidence"]["score"]["tracks"][0]["note_ids"]
        self.assertEqual(track_refs, sorted(page))
        for hint in view["evidence"]["score_review"]["hints"]:
            self.assertTrue(set(hint.get("note_ids", [])) <= page)
            self.assertNotIn("note-fixture-t0-n0299", hint.get("note_ids", []))
            self.assertNotIn("note-fixture-t0-n0299", hint.get("first_note_id", ""))
        self.assertEqual(view["evidence"]["score_review"]["hints"][1]["second_note_id"], all_note_id(1))
        self.assertTrue(all(set(event.get("note_ids", [])) <= page
                            for event in view["evidence"]["lyrics"]["lyric_events"]))

    def test_summary_prioritizes_actionable_diffs_with_bounded_note_ids(self):
        value = review_fixture()
        all_ids = [all_note_id(index) for index in range(300)]
        hints = [{
            "hint_id": f"review-lyrics-{index:02d}",
            "code": "reference_mismatch",
            "severity": "warning",
            "track_index": 0,
            "tick": index * 480,
            "onset_seconds": index * 0.5,
            "expected": "雪",
            "actual": "血",
            "message": "m" * 400,
            "note_ids": all_ids if index == 0 else [all_note_id(index % 10)],
        } for index in range(25)]
        value["evidence"]["lyric_review"]["hints"] = hints
        value["evidence"]["lyric_review"]["hint_count"] = len(hints)
        value["evidence"]["score_review"]["hints"] = []
        value["evidence"]["score_review"]["hint_count"] = 0

        view = review_view(value, detail="summary")
        context = view["diff_context"]
        self.assertEqual(context["page_hint_count"], 25)
        self.assertEqual(context["total_hint_count"], 25)
        self.assertEqual(len(context["preview"]), 10)
        self.assertEqual(context["preview_omitted_in_page"], 15)
        self.assertEqual(context["preview"][0]["expected"], "雪")
        self.assertEqual(context["preview"][0]["actual"], "血")
        self.assertIn(all_note_id(0), context["preview"][0]["note_ids"])
        self.assertIn(all_note_id(299), context["preview"][0]["note_ids"])
        self.assertEqual(context["preview"][0]["note_ids_total"], 300)
        self.assertGreater(context["preview"][0]["note_ids_omitted"], 0)
        self.assertLess(projection_size(view), SUMMARY_TARGET_BYTES)
        self.assertFalse(view["continuation"]["review_complete"])
        self.assertFalse(view["continuation"]["acceptance_evidence_complete"])

    def test_notes_page_retains_every_page_hint_and_expands_diff_context(self):
        value = review_fixture()
        hints = [{
            "hint_id": f"review-page-{index:03d}",
            "code": "reference_mismatch" if index < 20 else "reference_extra",
            "severity": "warning" if index < 20 else "info",
            "track_index": 0,
            "tick": index * 30,
            "onset_seconds": index * 0.03,
            "expected": "雪",
            "actual": "血" if index < 20 else "雪血",
            "message": "page-local diff",
            "note_ids": [all_note_id(index % 10)],
        } for index in range(150)]
        value["evidence"]["lyric_review"]["hints"] = hints
        value["evidence"]["lyric_review"]["hint_count"] = len(hints)

        view = review_view(value)
        section = view["evidence"]["lyric_review"]
        self.assertEqual(len(section["hints"]), 150)
        self.assertEqual(section["hints_returned"], 150)
        self.assertEqual(section["hints_total"], 150)
        self.assertEqual(section["hints_omitted_in_page"], 0)
        self.assertEqual(len(view["diff_context"]["preview"]), 10)
        self.assertEqual(view["diff_context"]["preview"][0]["section"], "lyric_review")
        self.assertEqual(view["diff_context"]["preview"][0]["expected"], "雪")
        self.assertTrue(view["diff_context"]["preview"][0]["note_context"])
        encoded = json.dumps(view, ensure_ascii=False)
        self.assertLess(encoded.index('"diff_context"'), encoded.index('"evidence"'))
        self.assertEqual(view["next_offset"], 10)
        self.assertFalse(view["continuation"]["acceptance_evidence_complete"])

        last_page = review_fixture()
        last_page["next_offset"] = None
        last_view = review_view(last_page)
        self.assertFalse(last_view["continuation"]["has_more"])
        self.assertEqual(last_view["continuation"]["next_action"], "review_returned_pages")
        self.assertFalse(last_view["view"]["acceptance_evidence_complete"])
        self.assertFalse(last_view["view"]["review_complete"])

    def test_compact_review_keeps_lyric_event_identity(self):
        value = review_fixture()
        view = review_view(value)
        lyrics = view["evidence"]["lyrics"]
        event_id = f"lyr-{'1' * 64}-t0-e0"
        self.assertEqual(lyrics["lyric_events"][0]["event_id"], event_id)
        self.assertEqual(lyrics["notes"][0]["lyric_event_id"], event_id)
        hint = view["evidence"]["lyric_review"]["hints"][0]
        self.assertEqual(hint["event_ids"], [event_id, f"lyr-{'1' * 64}-t0-e1"])
        preview = next(row for row in view["diff_context"]["preview"]
                       if row["section"] == "lyric_review")
        self.assertEqual(preview["event_ids_total"], 2)
        self.assertIn(event_id, preview["event_ids"])

    def test_full_keeps_current_page_and_filters_outside_ids(self):
        value = review_fixture()
        before = copy.deepcopy(value)
        view = review_view(value, detail="full")
        self.assertEqual(value, before)
        page = {f"note-fixture-t0-n{index:04d}" for index in range(10)}
        self.assertIn("meta_evidence", view["evidence"]["lyrics"])
        self.assertEqual(view["evidence"]["score"]["tracks"][0]["note_ids"],
                         [f"note-fixture-t0-n{index:04d}" for index in range(10)])
        self.assertTrue(set(view["evidence"]["score_review"]["hints"][1]["note_ids"]) <= page)
        self.assertIn("reference_evidence", view)
        self.assertNotIn(all_note_id(299), json.dumps(view, ensure_ascii=False))

    def test_raw_flag_only_affects_notes_projection(self):
        value = review_fixture()
        view = review_view(value, include_raw_evidence=True, include_note_ids=True)
        self.assertIn("reference_evidence", view)
        # Note rows remain the current page even when explicitly requesting raw evidence.
        self.assertEqual(len(view["evidence"]["score"]["notes"]), 10)
        self.assertTrue(set(view["reference_evidence"]["pitch"]["rows"][0]["note_ids"])
                        <= {f"note-fixture-t0-n{index:04d}" for index in range(10)})


def all_note_id(index):
    return f"note-fixture-t0-n{index:04d}"


class GenericSummaryTests(unittest.TestCase):
    def test_error_and_suggestion_summaries_are_bounded_and_stable(self):
        error = ViewError("workflow_conflict", "版本已改变",
                          details={"values": ["x" * 5000 for _ in range(100)]})
        summary = error_summary(error)
        self.assertEqual(summary["code"], "workflow_conflict")
        self.assertEqual(summary["component"], "agent_views")
        self.assertFalse(summary["retryable"])
        self.assertIn("重新读取", summary["remediation"])
        self.assertLess(projection_size(summary), 16 * 1024)

        value = workflow_fixture()
        value["stage_error"] = {"code": "export_blockers", "message": "有阻断"}
        value["suggestions"] = [{"code": "retry_after_read", "message": "重读 revision"}]
        result = suggestion_summary(value)
        self.assertEqual(result["id"], "flow_fixture")
        self.assertEqual(result["revision"], 17)
        self.assertTrue(any(row["code"] == "export_blockers" for row in result["errors"]))
        self.assertTrue(any(row["source"] == "next" for row in result["suggestions"]))
        self.assertTrue(any(row["source"] == "suggestions" for row in result["suggestions"]))
        self.assertIs(advice_summary, suggestion_summary)

    def test_bad_review_detail_is_a_view_error(self):
        with self.assertRaises(ViewError) as raised:
            review_view(review_fixture(), detail="raw")
        self.assertEqual(raised.exception.code, "invalid_detail")
        self.assertEqual(raised.exception.component, "agent_views")


if __name__ == "__main__":
    unittest.main()
