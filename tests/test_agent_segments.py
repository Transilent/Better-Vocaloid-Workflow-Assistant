"""Standalone tests for the pure Agent segmentation helpers."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from agent_segments import (LONG_NOTE_REASON, SegmentError, adjacent_context,
                            plan_segments, segment_notes)


def midi_note(index, start, end, *, track=0, channel=0, pitch=60,
              lyric="词", tick=None, prefix="fixture"):
    if tick is None:
        onset_tick = int(round(start * 960))
        end_tick = int(round(end * 960))
    else:
        onset_tick, end_tick = tick
    return {
        "note_id": f"note-{prefix}-t{track}-c{channel}-n{index}",
        "track_index": track,
        "channel": channel,
        "pitch": pitch,
        "velocity": 90,
        "onset_tick": onset_tick,
        "duration_tick": end_tick - onset_tick,
        "end_tick": end_tick,
        "onset_seconds": float(start),
        "duration_seconds": float(end - start),
        "end_seconds": float(end),
        "lyric": lyric,
    }


def midi_score(notes, *, source_sha="a" * 64, tracks=None):
    return {
        "schema_version": 1,
        "format": "midi",
        "source_sha256": source_sha,
        "tracks": tracks if tracks is not None else [],
        "notes": notes,
    }


def svp_note(index, start, end, *, track=0, pitch=60, lyric="词",
             tick=None, prefix="svp"):
    note = {
        "note_id": f"note-{prefix}-t{track}-n{index}",
        "track_index": track,
        "pitch": pitch,
        "onset_tick": tick[0] if tick else None,
        "duration_tick": (tick[1] - tick[0]) if tick else None,
        "start_seconds": float(start),
        "end_seconds": float(end),
        "lyric": lyric,
        "lyric_known": lyric is not None,
    }
    if tick is None:
        note.pop("onset_tick")
        note.pop("duration_tick")
    return note


def svp_score(notes, *, source_sha="d" * 64, tracks=None):
    return {
        "schema": 1,
        "kind": "vwa.style-context",
        "format": "svp",
        "source_sha256": source_sha,
        "tracks": tracks if tracks is not None else [
            {"track_index": 0, "vocal": True, "instrumental": False},
        ],
        "notes": notes,
    }


class SegmentPlanTests(unittest.TestCase):
    def test_331_note_fixture_is_complete_bounded_and_does_not_emit_rows(self):
        notes = []
        voice_sizes = (111, 110, 110)
        for (track, channel), count in zip(((0, 0), (1, 0), (2, 1)), voice_sizes):
            for index in range(count):
                start = track * 0.25 + index * 0.11
                notes.append(midi_note(
                    index, start, start + 0.1,
                    track=track, channel=channel, pitch=48 + (index % 12),
                    lyric=f"词{index}", prefix="three-one-one",
                ))
        score = midi_score(notes)
        before = copy.deepcopy(score)
        plan = plan_segments(score)

        self.assertEqual(score, before)
        self.assertEqual(plan["total_notes"], 331)
        self.assertGreater(plan["total_segments"], 1)
        self.assertEqual(sum(row["note_count"] for row in plan["segments"]), 331)
        self.assertNotIn("notes", plan)
        for segment in plan["segments"]:
            self.assertLessEqual(segment["note_count"], 16)
            self.assertLessEqual(segment["end_seconds"] - segment["start_seconds"], 10.0 + 1e-9)
            self.assertIn("start_tick", segment)
            self.assertIn("end_tick", segment)
            rows = segment_notes(score, segment)
            self.assertEqual(segment["start_tick"], min(row["onset_tick"] for row in rows))
            self.assertEqual(segment["end_tick"], max(row["end_tick"] for row in rows))
            self.assertTrue(all("pitch" not in row for row in plan["segments"]))

    def test_fixed_windows_exact_gap_boundary_and_adjacent_context(self):
        notes = [
            midi_note(0, 0.0, 0.1, tick=(0, 96), lyric="一"),
            midi_note(1, 0.1, 0.2, tick=(96, 192), lyric="二"),
            midi_note(2, 0.85, 0.95, tick=(816, 912), lyric="三"),
            midi_note(3, 9.9, 10.0, tick=(9504, 9600), lyric="四"),
            midi_note(4, 10.0, 10.1, tick=(9600, 9696), lyric="五"),
            midi_note(5, 10.1, 10.2, tick=(9696, 9792), lyric="六"),
        ]
        score = midi_score(notes)
        plan = plan_segments(score, max_notes=16, max_seconds=10.0)
        segments = plan["segments"]

        self.assertEqual(
            [segment["note_count"] for segment in segments],
            [2, 1, 1, 2],
        )
        self.assertEqual([segment["start_seconds"] for segment in segments],
                         [0.0, 0.85, 9.9, 10.0])
        context = adjacent_context(score, segments[1], limit=2)
        self.assertEqual([row["lyric"] for row in context["before"]], ["一", "二"])
        self.assertEqual([row["lyric"] for row in context["after"]], ["四", "五"])
        self.assertTrue(context["has_more_after"])
        self.assertEqual(len(context["before"]), 2)
        self.assertEqual(len(context["after"]), 2)

    def test_long_note_is_not_split_and_is_explained(self):
        score = midi_score([
            midi_note(0, 0.0, 12.0, tick=(0, 11520), lyric="长"),
            midi_note(1, 1.0, 1.1, tick=(960, 1056), lyric="短"),
        ])
        plan = plan_segments(score)
        segments = plan["segments"]
        long_segment = next(row for row in segments if row["start_seconds"] == 0.0)
        self.assertEqual(long_segment["note_count"], 1)
        self.assertEqual(long_segment["reason"], LONG_NOTE_REASON)
        self.assertGreater(long_segment["end_seconds"] - long_segment["start_seconds"], 10.0)
        self.assertEqual(long_segment["start_tick"], 0)
        self.assertEqual(long_segment["end_tick"], 11520)

    def test_svp_fields_ticks_and_instrumental_filter(self):
        notes = [
            svp_note(0, 0.1, 0.3, track=0, tick=(100, 300), lyric="春"),
            svp_note(1, 0.4, 0.6, track=1, tick=(400, 600), lyric="伴奏"),
            svp_note(2, 0.7, 0.9, track=2, tick=(700, 900), lyric="非声部"),
        ]
        tracks = [
            {"track_index": 0, "vocal": True, "instrumental": False},
            {"track_index": 1, "vocal": False, "instrumental": True},
            {"track_index": 2, "vocal": False, "instrumental": None},
        ]
        score = svp_score(notes, tracks=tracks)
        plan = plan_segments(score)
        self.assertEqual(plan["total_notes"], 1)
        segment = plan["segments"][0]
        self.assertIsNone(segment["channel"])
        self.assertEqual(segment["start_tick"], 100)
        self.assertEqual(segment["end_tick"], 300)
        self.assertEqual([row["lyric"] for row in segment_notes(score, segment)], ["春"])

        no_ticks = svp_score([svp_note(0, 1.0, 1.2, lyric="无刻度")])
        output = plan_segments(no_ticks)["segments"][0]
        self.assertNotIn("start_tick", output)
        self.assertNotIn("end_tick", output)

    def test_no_note_conductor_is_naturally_absent(self):
        score = midi_score(
            [midi_note(0, 0.0, 0.1, lyric="声")],
            tracks=[
                {"track_index": 99, "name": "conductor", "note_count": 0},
                {"track_index": 0, "name": "vocal"},
            ],
        )
        plan = plan_segments(score)
        self.assertEqual(plan["total_segments"], 1)
        self.assertEqual(plan["segments"][0]["track_index"], 0)


class SegmentIdentityTests(unittest.TestCase):
    def test_fingerprint_survives_unrelated_id_and_source_revision(self):
        notes = [
            midi_note(0, 0.0, 0.1, lyric="一", prefix="one"),
            midi_note(1, 1.0, 1.1, lyric="二", prefix="one"),
            midi_note(2, 3.0, 3.1, lyric="三", prefix="one"),
        ]
        score_a = midi_score(notes, source_sha="a" * 64)
        score_b = midi_score(copy.deepcopy(notes), source_sha="b" * 64)
        for index, note in enumerate(score_b["notes"]):
            note["note_id"] = f"note-revised-{index}"
        score_b["notes"][2]["pitch"] = 72
        before = copy.deepcopy(score_a)

        plan_a = plan_segments(score_a)
        plan_b = plan_segments(score_b)
        self.assertEqual(score_a, before)
        self.assertEqual(plan_a["total_segments"], 3)
        self.assertEqual(plan_b["total_segments"], 3)
        for index in (0, 1):
            self.assertEqual(plan_a["segments"][index]["fingerprint"],
                             plan_b["segments"][index]["fingerprint"])
            self.assertNotEqual(plan_a["segments"][index]["id"],
                                plan_b["segments"][index]["id"])
            self.assertTrue(plan_a["segments"][index]["id"].startswith(
                "segment-" + ("a" * 16)))
        self.assertNotEqual(plan_a["segments"][2]["fingerprint"],
                            plan_b["segments"][2]["fingerprint"])

    def test_segment_notes_are_read_only_and_stale_segments_are_rejected(self):
        score = midi_score([
            midi_note(0, 0.0, 0.1, lyric="一"),
            midi_note(1, 1.0, 1.1, lyric="二"),
        ])
        plan = plan_segments(score)
        segment = plan["segments"][0]
        original_score = copy.deepcopy(score)
        rows = segment_notes(score, segment)
        self.assertEqual([row["note_id"] for row in rows], segment["note_ids"])
        self.assertEqual(len(rows), 1)
        rows[0]["lyric"] = "changed"
        self.assertEqual(score, original_score)

        tampered = copy.deepcopy(segment)
        tampered["end_seconds"] += 0.01
        with self.assertRaises(SegmentError) as raised:
            segment_notes(score, tampered)
        self.assertEqual(raised.exception.code, "stale_segment")
        self.assertIn("mismatches", raised.exception.details)

        revised = copy.deepcopy(score)
        revised["notes"][0]["pitch"] = 70
        with self.assertRaises(SegmentError) as raised:
            segment_notes(revised, segment)
        self.assertEqual(raised.exception.code, "stale_segment")


class SegmentValidationTests(unittest.TestCase):
    def test_stable_error_codes_and_midi_tick_requirement(self):
        with self.assertRaises(SegmentError) as raised:
            plan_segments({})
        self.assertEqual(raised.exception.code, "invalid_source_sha256")

        with self.assertRaises(SegmentError) as raised:
            plan_segments(midi_score([]), max_notes=0)
        self.assertEqual(raised.exception.code, "invalid_max_notes")
        self.assertEqual(raised.exception.component, "agent_segments")

        note = midi_note(0, 0.0, 0.1)
        note.pop("end_tick")
        with self.assertRaises(SegmentError) as raised:
            plan_segments(midi_score([note]))
        self.assertEqual(raised.exception.code, "invalid_note_timing")
        self.assertEqual(raised.exception.details["field"], "end_tick")

        score = midi_score([midi_note(0, 0.0, 0.1)])
        segment = plan_segments(score)["segments"][0]
        with self.assertRaises(SegmentError) as raised:
            adjacent_context(score, segment, limit=-1)
        self.assertEqual(raised.exception.code, "invalid_limit")

    def test_output_is_json_ready_and_has_no_full_song_payload(self):
        score = svp_score([svp_note(0, 0.0, 0.2, lyric="词")])
        plan = plan_segments(score)
        json.dumps(plan, ensure_ascii=False, allow_nan=False)
        self.assertEqual(set(plan), {"source_sha256", "segments",
                                     "total_segments", "total_notes"})
        self.assertNotIn("onset_seconds", plan["segments"][0])
        self.assertEqual(plan["segments"][0]["index"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)