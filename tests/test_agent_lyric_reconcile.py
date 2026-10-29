"""Pure tests for timed lyric line reconciliation previews."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from agent_lyric_reconcile import reconcile_lyrics


def note(
    note_id,
    onset,
    end,
    lyric,
    alignment="aligned",
    *,
    track_index=1,
    pitch=60,
    channel=0,
    event_id=None,
):
    if alignment == "aligned" and event_id is None:
        event_id = f"lyr-{'a' * 64}-t{track_index}-e{note_id.rsplit('n', 1)[-1]}"
    return {
        "note_id": note_id,
        "track_index": track_index,
        "channel": channel,
        "pitch": pitch,
        "velocity": 80,
        "onset_tick": int(round(onset * 480)),
        "end_tick": int(round(end * 480)),
        "duration_tick": int(round((end - onset) * 480)),
        "onset_seconds": onset,
        "duration_seconds": round(end - onset, 9),
        "end_seconds": end,
        "lyric": lyric,
        "lyric_event_index": 10 if alignment == "aligned" else None,
        "lyric_event_id": event_id,
        "lyric_alignment": alignment,
        "competing_lyric_event_indexes": [],
        "competing_lyric_event_ids": [],
        "pairing_ambiguous": False,
    }


def read(notes, *, track_name="Lead"):
    return {
        "schema_version": 1,
        "kind": "vwa-lyrics-read",
        "format": "midi",
        "source_sha256": "a" * 64,
        "language": "zh",
        "tracks": [{"track_index": 1, "track_name": track_name}],
        "notes": notes,
        "lyric_events": [],
        "warnings": [],
    }


class ReconcileLyricsTests(unittest.TestCase):
    def test_real_read_includes_unique_event_in_competing_evidence(self):
        row = note("note-a-t1-n0", 0.2, 0.6, "旧")
        row["competing_lyric_event_ids"] = [row["lyric_event_id"]]
        preview = reconcile_lyrics(read([row]), [{"start": 0.0, "end": 1.0, "text": "新"}], 1)
        self.assertEqual(preview["edits"], [{"note_id": row["note_id"],
                                             "lyric": "新", "operation": "replace"}])
        row["competing_lyric_event_ids"].append("another-event")
        self.assertEqual(reconcile_lyrics(read([row]), [{"start": 0.0, "end": 1.0, "text": "新"}], 1)["edits"], [])

    def test_existing_holds_map_each_note_as_uncertain_patch_candidates(self):
        source = read([
            note("note-a-t1-n1", 0.1, 0.2, "旧字"),
            note("note-a-t1-n2", 0.2, 0.3, "-"),
            note("note-a-t1-n3", 0.3, 0.4, "-"),
            note("note-a-t1-n4", 0.4, 0.5, "歌"),
            note("note-a-t1-n5", 0.5, 0.6, "-"),
            note("note-a-t1-n6", 1.1, 1.2, None, "missing"),
        ])
        original = copy.deepcopy(source)
        preview = reconcile_lyrics(source, [
            {"start": 0, "end": 1, "text": "新，歌！"},
            {"start": 1, "end": 2, "text": "句"},
        ], 1)
        self.assertEqual(source, original)
        self.assertEqual(preview["status"], "manual_required")
        self.assertFalse(preview["ready_for_patch_plan"])
        self.assertEqual(preview["edits"], [
            {"note_id": "note-a-t1-n6", "lyric": "句", "operation": "insert"},
        ])
        line = preview["lines"][0]
        self.assertEqual((line["char_count"], line["note_count"]), (2, 5))
        self.assertEqual(line["coverage"]["covered_char_count"], 0)
        self.assertEqual(line["conditional_hold_blockers"], [])
        self.assertEqual(line["after_lyrics"], None)
        candidate = line["conditional_suggestion"]
        self.assertEqual(candidate["kind"], "conditional_preserve_existing_holds")
        self.assertTrue(candidate["uncertain"])
        self.assertTrue(candidate["requires_confirmation"])
        self.assertFalse(candidate["included_in_edits"])
        self.assertTrue(candidate["compatible_with_plan_lyrics_patch"])
        self.assertEqual(candidate["before_lyrics"], ["旧字", "-", "-", "歌", "-"])
        self.assertEqual(candidate["after_lyrics"], ["新", "-", "-", "歌", "-"])
        self.assertEqual([row["operation"] for row in candidate["proposals"]],
                         ["replace", "keep", "keep", "keep", "keep"])
        self.assertEqual(candidate["conditional_edits"], [
            {"note_id": "note-a-t1-n1", "lyric": "新", "operation": "replace"},
        ])
        self.assertIn("漏词", candidate["rationale"])
        json.dumps(preview, ensure_ascii=False, allow_nan=False)

    def test_existing_holds_that_already_match_are_still_uncertain(self):
        preview = reconcile_lyrics(read([
            note("note-a-t1-n1", 0, 0.2, "呀"),
            note("note-a-t1-n2", 0.2, 0.4, "-"),
        ]), [{"start": 0, "end": 0.4, "text": "呀！"}], 1)
        candidate = preview["lines"][0]["conditional_suggestion"]
        self.assertEqual(candidate["conditional_edits"], [])
        self.assertEqual(candidate["after_lyrics"], ["呀", "-"])
        self.assertEqual(preview["status"], "manual_required")
        self.assertFalse(preview["no_changes_required"])

    def test_existing_hold_candidate_rejects_unreliable_evidence_and_boundaries(self):
        def base():
            return [note("note-a-t1-n1", 0.1, 0.2, "旧"),
                    note("note-a-t1-n2", 0.2, 0.3, "-"),
                    note("note-a-t1-n3", 0.3, 0.4, "字")]

        cases = {}
        cases["leading hold"] = [note("note-a-t1-n1", 0.1, 0.2, "-"),
                                  note("note-a-t1-n2", 0.2, 0.3, "旧"),
                                  note("note-a-t1-n3", 0.3, 0.4, "字")]
        cases["missing event"] = base()
        cases["missing event"][2].update(lyric=None, lyric_alignment="missing", lyric_event_id=None)
        cases["missing event id"] = base()
        cases["missing event id"][1]["lyric_event_id"] = None
        cases["malformed event id"] = base()
        cases["malformed event id"][1]["lyric_event_id"] = {}
        cases["duplicate event"] = base()
        cases["duplicate event"][2]["lyric_event_id"] = cases["duplicate event"][0]["lyric_event_id"]
        cases["competing event"] = base()
        cases["competing event"][1]["competing_lyric_event_ids"] = ["another"]
        cases["punctuation only"] = base()
        cases["punctuation only"][2]["lyric"] = "，"
        cases["wrong count"] = base()
        cases["wrong count"][2]["lyric"] = "-"
        cases["crosses window"] = base()
        cases["crosses window"][2]["end_seconds"] = 1.1
        cases["unknown end"] = base()
        cases["unknown end"][2]["end_seconds"] = None
        cases["ambiguous pairing"] = base()
        cases["ambiguous pairing"][0]["pairing_ambiguous"] = True
        cases["same onset"] = base()
        cases["same onset"][1].update(onset_seconds=0.1, onset_tick=48)
        for name, notes in cases.items():
            with self.subTest(name=name):
                preview = reconcile_lyrics(read(notes), [
                    {"start": 0, "end": 1, "text": "新字"},
                ], 1)
                self.assertEqual(preview["edits"], [])
                self.assertIsNone(preview["lines"][0]["conditional_suggestion"])
                self.assertTrue(preview["lines"][0]["conditional_hold_blockers"])
                self.assertEqual(preview["status"], "manual_required")

    def test_shared_event_outside_line_still_blocks_candidate(self):
        rows = [note("note-a-t1-n1", 0.1, 0.2, "旧"),
                note("note-a-t1-n2", 0.2, 0.3, "-"),
                note("note-a-t1-n3", 0.3, 0.4, "字"),
                note("note-a-t1-n4", 1.1, 1.2, "别")]
        rows[3]["lyric_event_id"] = rows[0]["lyric_event_id"]
        preview = reconcile_lyrics(read(rows), [{"start": 0, "end": 1, "text": "新字"}], 1)
        self.assertIn("shared_event", preview["lines"][0]["conditional_hold_blockers"])
        self.assertIsNone(preview["lines"][0]["conditional_suggestion"])

    def test_existing_holds_do_not_cross_overlapping_windows_or_overwrite_leading_hold(self):
        source = read([
            note("note-a-t1-n1", 0.1, 0.2, "旧"),
            note("note-a-t1-n2", 0.2, 0.3, "-"),
            note("note-a-t1-n3", 0.3, 0.4, "字"),
        ])
        preview = reconcile_lyrics(source, [
            {"start": 0, "end": 1, "text": "新字"},
            {"start": 0.5, "end": 1.5, "text": "其他"},
        ], 1)
        self.assertIsNone(preview["lines"][0]["conditional_suggestion"])
        self.assertIn("overlapping_line_windows", preview["lines"][0]["manual_decision_codes"])
        leading = reconcile_lyrics(read([
            note("note-a-t1-n1", 0.1, 0.2, "-"),
            note("note-a-t1-n2", 0.2, 0.3, "字"),
        ]), [{"start": 0, "end": 1, "text": "新字"}], 1)
        self.assertEqual(leading["edits"], [])
        self.assertIn("leading_hold", leading["lines"][0]["manual_decision_codes"])

    def test_safe_replace_insert_keep_and_strict_track_filter(self):
        source = read([
            note("note-a-t0-n0", 0.4, 0.9, "别", track_index=0),
            note("note-a-t1-n0", 0.05, 0.10, None, "missing"),
            note("note-a-t1-n1", 0.2, 0.6, "旧", pitch=60),
            note("note-a-t1-n2", 0.8, 1.1, None, "missing", pitch=62),
            note("note-a-t1-n3", 1.3, 2.1, "世界", pitch=64),
            note("note-a-t1-n4", 2.4, 2.8, "界", pitch=65),
        ])
        untouched = copy.deepcopy(source)
        lines = [
            {"start": 0.2, "end": 1.2, "text": "新，歌"},
            {"start": 1.2, "end": 2.2, "text": "界"},
        ]

        preview = reconcile_lyrics(source, lines, 1)

        self.assertEqual(source, untouched)
        self.assertEqual(preview["kind"], "vwa-lyrics-reconcile-preview")
        self.assertTrue(preview["dry_run"])
        self.assertTrue(preview["preview_only"])
        self.assertFalse(preview["applied"])
        self.assertFalse(preview["apply_supported"])
        self.assertEqual(preview["status"], "ready")
        self.assertTrue(preview["ready_for_patch_plan"])
        self.assertEqual(preview["track_name"], "Lead")
        self.assertEqual(preview["edits"], [
            {"note_id": "note-a-t1-n1", "lyric": "新", "operation": "replace"},
            {"note_id": "note-a-t1-n2", "lyric": "歌", "operation": "insert"},
            {"note_id": "note-a-t1-n3", "lyric": "界", "operation": "replace"},
        ])
        first = preview["lines"][0]
        self.assertEqual(first["char_count"], 2)
        self.assertEqual(first["raw_char_count"], 3)
        self.assertEqual(first["note_count"], 2)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["after_lyrics"], ["新", "歌"])
        self.assertEqual(first["diff"]["replace_count"], 1)
        self.assertEqual(first["diff"]["insert_count"], 1)
        self.assertEqual(preview["lines"][1]["note_count"], 1)
        self.assertEqual(preview["lines"][1]["coverage"]["uncovered_char_count"], 0)
        self.assertEqual(preview["coverage"]["total_char_count"], 3)
        self.assertEqual(preview["coverage"]["covered_char_count"], 3)
        self.assertEqual(preview["coverage"]["outside_line_note_count"], 2)
        outside_ids = {
            row["note_id"] for row in preview["uncovered"]["outside_line_notes"]
        }
        self.assertEqual(outside_ids, {"note-a-t1-n0", "note-a-t1-n4"})
        self.assertEqual(len(preview["diff"]["operations"]), 3)
        json.dumps(preview, ensure_ascii=False, allow_nan=False)

    def test_one_character_many_notes_is_conditional_and_never_an_edit(self):
        source = read([
            note("note-a-t1-n1", 0.1, 0.3, "呀", pitch=60),
            note("note-a-t1-n2", 0.4, 0.6, "呀", pitch=62),
            note("note-a-t1-n3", 0.7, 0.9, "呀", pitch=64),
        ])
        preview = reconcile_lyrics(
            source, [{"start": 0.0, "end": 1.0, "text": "呀"}], 1,
        )

        self.assertEqual(preview["edits"], [])
        self.assertFalse(preview["ready_for_patch_plan"])
        self.assertEqual(preview["status"], "manual_required")
        line = preview["lines"][0]
        self.assertEqual(line["char_count"], 1)
        self.assertEqual(line["note_count"], 3)
        self.assertEqual(line["relationship"], "one_character_many_notes")
        self.assertIn("one_character_many_notes", line["manual_decision_codes"])
        self.assertEqual(line["coverage"]["uncovered_char_count"], 1)
        candidate = line["conditional_suggestion"]
        self.assertEqual(candidate["kind"], "conditional_hold_sequence")
        self.assertTrue(candidate["requires_confirmation"])
        self.assertFalse(candidate["included_in_edits"])
        self.assertEqual(
            [row["lyric"] for row in candidate["conditional_edits"]],
            ["呀", "-", "-"],
        )
        self.assertEqual(preview["uncovered"]["characters"][0]["character"], "呀")

    def test_more_characters_than_notes_returns_unapplied_split_capacity(self):
        source = read([
            note("note-a-t1-n1", 0.0, 0.5, None, "missing", pitch=60),
            note("note-a-t1-n2", 0.5, 2.0, None, "missing", pitch=62),
        ])
        preview = reconcile_lyrics(
            source, [{"start": 0.0, "end": 2.5, "text": "甲乙丙丁"}], 1,
        )

        self.assertEqual(preview["edits"], [])
        line = preview["lines"][0]
        self.assertEqual(line["char_count"], 4)
        self.assertEqual(line["note_count"], 2)
        self.assertEqual(line["relationship"], "more_characters_than_notes")
        self.assertEqual(line["proposals"], [])
        self.assertIn("more_characters_than_notes", line["manual_decision_codes"])
        suggestion = line["conditional_suggestion"]
        self.assertEqual(suggestion["additional_note_count_needed"], 2)
        self.assertEqual(suggestion["suggested_additional_note_count"], 2)
        self.assertTrue(suggestion["requires_confirmation"])
        self.assertFalse(suggestion["compatible_with_plan_lyrics_patch"])
        by_id = {row["note_id"]: row for row in suggestion["suggestions"]}
        self.assertEqual(by_id["note-a-t1-n1"]["split_into"], 2)
        self.assertEqual(by_id["note-a-t1-n2"]["split_into"], 2)
        self.assertEqual(
            by_id["note-a-t1-n2"]["pieces"][-1]["end_seconds"], 2.0,
        )
        self.assertNotIn("conditional_edits", suggestion)

    def test_empty_line_does_not_steal_notes_across_boundary(self):
        source = read([
            note("note-a-t1-n0", 0.75, 0.95, None, "missing"),
            note("note-a-t1-n1", 1.5, 1.9, None, "missing", pitch=62),
        ])
        preview = reconcile_lyrics(source, [
            {"start": 1.0, "end": 1.5, "text": "词"},
            {"start": 1.5, "end": 2.5, "text": "句"},
        ], 1)

        first, second = preview["lines"]
        self.assertEqual(first["note_count"], 0)
        self.assertEqual(first["relationship"], "more_characters_than_notes")
        self.assertIn("no_notes_in_line_window", first["manual_decision_codes"])
        self.assertEqual(first["coverage"]["uncovered_char_count"], 1)
        self.assertEqual(second["note_count"], 1)
        self.assertTrue(second["safe_to_map"])
        self.assertEqual(preview["edits"], [{
            "note_id": "note-a-t1-n1", "lyric": "句", "operation": "insert",
        }])
        outside_ids = {
            row["note_id"] for row in preview["uncovered"]["outside_line_notes"]
        }
        self.assertEqual(outside_ids, {"note-a-t1-n0"})

    def test_same_onset_notes_block_even_equal_counts(self):
        first = note("note-a-t1-n1", 1.0, 1.4, None, "missing", pitch=60)
        second = note("note-a-t1-n2", 1.0, 1.4, None, "missing", pitch=64)
        preview = reconcile_lyrics(
            read([first, second]),
            [{"start": 1.0, "end": 2.0, "text": "双声"}],
            1,
        )

        self.assertEqual(preview["lines"][0]["char_count"], 2)
        self.assertEqual(preview["lines"][0]["note_count"], 2)
        self.assertEqual(preview["edits"], [])
        self.assertIn("same_onset_notes", preview["lines"][0]["manual_decision_codes"])
        self.assertEqual(len(preview["uncovered"]["unresolved_line_notes"]), 2)

    def test_overlapping_lines_mark_the_shared_note_unresolved(self):
        source = read([note("note-a-t1-n1", 1.2, 1.4, "旧", pitch=60)])
        preview = reconcile_lyrics(source, [
            {"start": 1.0, "end": 1.6, "text": "甲"},
            {"start": 1.2, "end": 2.0, "text": "乙"},
        ], 1)

        self.assertEqual(preview["edits"], [])
        self.assertEqual(preview["status"], "manual_required")
        self.assertEqual(
            [row["manual_decision_codes"] for row in preview["lines"]],
            [["overlapping_line_windows"], ["overlapping_line_windows"]],
        )
        self.assertEqual(preview["coverage"]["unique_matched_note_count"], 1)
        unresolved = preview["uncovered"]["unresolved_line_notes"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["line_indexes"], [0, 1])

    def test_invalid_line_and_track_inputs_are_rejected(self):
        source = read([])
        with self.assertRaisesRegex(ValueError, "end 必须大于 start"):
            reconcile_lyrics(source, [{"start": 2, "end": 1, "text": "词"}], 1)
        with self.assertRaisesRegex(ValueError, "track_index"):
            reconcile_lyrics(source, [{"start": 0, "end": 1, "text": "词"}], True)
        with self.assertRaisesRegex(ValueError, "lines 不能为空"):
            reconcile_lyrics(source, [], 1)


if __name__ == "__main__":
    unittest.main()
