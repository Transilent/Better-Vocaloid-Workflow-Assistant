"""Acceptance tests for the pure monophonic export preflight."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from agent_monophony import (
    CONFLICT_CODE,
    MONOPHONIC_SCOPE_FIELDS,
    REQUIRED_NOTE_FIELDS,
    monophonic_conflicts,
)


def note(
    note_id,
    *,
    track=0,
    channel=0,
    pitch=60,
    onset_tick=0,
    end_tick=1000,
    onset_seconds=0.0,
    end_seconds=1.0,
):
    return {
        "note_id": note_id,
        "track_index": track,
        "channel": channel,
        "pitch": pitch,
        "onset_tick": onset_tick,
        "end_tick": end_tick,
        "onset_seconds": onset_seconds,
        "end_seconds": end_seconds,
    }


class AgentMonophonyTests(unittest.TestCase):
    def test_cross_pitch_fifteen_millisecond_overlap_is_located_exactly(self):
        score = {"notes": [
            note("a", pitch=60, onset_tick=0, end_tick=1000, end_seconds=1.0),
            note(
                "b",
                pitch=64,
                onset_tick=985,
                end_tick=1500,
                onset_seconds=0.985,
                end_seconds=1.5,
            ),
        ]}
        original = copy.deepcopy(score)
        conflicts = monophonic_conflicts(score)
        self.assertEqual(score, original)
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict["code"], CONFLICT_CODE)
        self.assertEqual(conflict["severity"], "error")
        self.assertEqual((conflict["track_index"], conflict["channel"]), (0, 0))
        self.assertEqual(conflict["voice_key"], {"track_index": 0, "channel": 0})
        self.assertEqual(conflict["note_ids"], ["a", "b"])
        self.assertEqual(conflict["pitches"], [60, 64])
        self.assertTrue(conflict["cross_pitch"])
        self.assertEqual((conflict["start_tick"], conflict["end_tick"]), (985, 1000))
        self.assertEqual(conflict["overlap_ticks"], 15)
        self.assertEqual(conflict["start_seconds"], 0.985)
        self.assertEqual(conflict["end_seconds"], 1.0)
        self.assertEqual(conflict["overlap_seconds"], 0.015)
        self.assertEqual(conflict["overlap_milliseconds"], 15.0)
        json.dumps(conflicts, ensure_ascii=False, allow_nan=False)

    def test_cross_track_and_cross_channel_voices_are_legal(self):
        score = {"notes": [
            note("a", track=0, channel=0, pitch=60),
            note("b", track=0, channel=1, pitch=64),
            note("c", track=1, channel=0, pitch=67),
            note("d", track=1, channel=None, pitch=72),
        ]}
        self.assertEqual(monophonic_conflicts(score), [])
        self.assertEqual(MONOPHONIC_SCOPE_FIELDS, ("track_index", "channel"))
        self.assertIn("note_id", REQUIRED_NOTE_FIELDS)

    def test_half_open_adjacent_notes_do_not_conflict(self):
        score = {"notes": [
            note("a", pitch=60, onset_tick=0, end_tick=480, end_seconds=0.5),
            note(
                "b",
                pitch=64,
                onset_tick=480,
                end_tick=960,
                onset_seconds=0.5,
                end_seconds=1.0,
            ),
        ]}
        self.assertEqual(monophonic_conflicts(score), [])

    def test_limit_is_bounded_deterministic_and_zero_is_empty(self):
        score = {"notes": [
            note("c", pitch=67, onset_tick=0, end_tick=1000),
            note("a", pitch=60, onset_tick=0, end_tick=1000),
            note("b", pitch=64, onset_tick=500, end_tick=1500, onset_seconds=0.5, end_seconds=1.5),
        ]}
        all_conflicts = monophonic_conflicts(score)
        self.assertEqual(len(all_conflicts), 3)
        self.assertEqual(
            [row["note_ids"] for row in all_conflicts],
            [["a", "c"], ["a", "b"], ["c", "b"]],
        )
        self.assertEqual(monophonic_conflicts(score, limit=2), all_conflicts[:2])
        self.assertEqual(monophonic_conflicts(score, limit=0), [])
        with self.assertRaisesRegex(ValueError, "非负整数"):
            monophonic_conflicts(score, limit=-1)
        with self.assertRaisesRegex(ValueError, "布尔值"):
            monophonic_conflicts(score, limit=True)

    def test_score_contract_errors_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "score.notes"):
            monophonic_conflicts({})
        with self.assertRaisesRegex(ValueError, "缺少字段"):
            monophonic_conflicts({"notes": [{"note_id": "a"}]})
        with self.assertRaisesRegex(ValueError, "重复"):
            monophonic_conflicts({"notes": [note("a"), note("a")]})
        with self.assertRaisesRegex(ValueError, "0..15"):
            monophonic_conflicts({"notes": [note("a", channel=16)]})
        with self.assertRaisesRegex(ValueError, "end_tick"):
            monophonic_conflicts({"notes": [note("a", onset_tick=10, end_tick=10)]})


if __name__ == "__main__":
    unittest.main()
