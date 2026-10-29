"""Acceptance tests for the pure MIDI Lyrics worker.

These tests use tiny local MIDI fixtures and no model inference, provider
access, voicebank synthesis or MCP transport.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

import mido

from agent_lyrics import (
    apply_lyrics_patch,
    plan_lyrics_patch,
    read_lyrics,
    review_lyrics,
)
from agent_score import read_score
from common import native_path


def digest(path):
    return hashlib.sha256(Path(native_path(path)).read_bytes()).hexdigest()


def load_midi(path):
    with open(native_path(path), "rb") as stream:
        return mido.MidiFile(file=stream, charset="utf8")


def save_midi(midi, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(native_path(path), "wb") as stream:
        midi.save(file=stream)


def json_ready(value):
    json.dumps(value, ensure_ascii=False, allow_nan=False)


def write_review_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo 中文", time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.MetaMessage("marker", text="keep-review-meta", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=4000),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead 歌詞", time=0),
        mido.MetaMessage("lyrics", text="旧", time=100),
        mido.MetaMessage("lyrics", text="你", time=100),
        mido.Message("note_on", channel=2, note=60, velocity=91, time=0),
        mido.Message("note_off", channel=2, note=60, velocity=0, time=200),
        mido.MetaMessage("lyrics", text="好", time=200),
        mido.Message("note_on", channel=2, note=62, velocity=73, time=0),
        mido.Message("note_off", channel=2, note=62, velocity=0, time=200),
        mido.MetaMessage("lyrics", text="〜", time=200),
        mido.Message("note_on", channel=2, note=64, velocity=81, time=0),
        mido.Message("note_off", channel=2, note=64, velocity=0, time=200),
        mido.MetaMessage("lyrics", text="世界", time=200),
        mido.Message("note_on", channel=2, note=65, velocity=80, time=0),
        mido.Message("note_off", channel=2, note=65, velocity=0, time=200),
        mido.MetaMessage("lyrics", text="世", time=200),
        mido.Message("note_on", channel=2, note=67, velocity=80, time=0),
        mido.Message("note_off", channel=2, note=67, velocity=0, time=200),
        mido.MetaMessage("lyrics", text="世", time=200),
        mido.Message("note_on", channel=2, note=69, velocity=80, time=0),
        mido.Message("note_off", channel=2, note=69, velocity=0, time=200),
        mido.Message("note_on", channel=2, note=71, velocity=80, time=200),
        mido.Message("note_off", channel=2, note=71, velocity=0, time=150),
        mido.MetaMessage("lyrics", text="", time=50),
        mido.Message("note_on", channel=2, note=72, velocity=80, time=0),
        mido.Message("note_off", channel=2, note=72, velocity=0, time=100),
        mido.MetaMessage("lyrics", text="同", time=100),
        mido.Message("note_on", channel=2, note=74, velocity=78, time=0),
        mido.Message("note_on", channel=2, note=76, velocity=76, time=0),
        mido.Message("note_off", channel=2, note=74, velocity=0, time=200),
        mido.Message("note_off", channel=2, note=76, velocity=0, time=0),
        mido.MetaMessage("lyrics", text="[待填]", time=200),
        mido.Message("note_on", channel=2, note=79, velocity=75, time=0),
        mido.Message("note_off", channel=2, note=79, velocity=0, time=150),
        mido.MetaMessage("lyrics", text="で", time=50),
        mido.Message("note_on", channel=2, note=81, velocity=74, time=0),
        mido.Message("note_off", channel=2, note=81, velocity=0, time=200),
        mido.MetaMessage("marker", text="tail", time=0),
        mido.MetaMessage("end_of_track", time=200),
    ]))
    save_midi(midi, path)
    return Path(path)


def write_patch_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo", time=0),
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0),
        mido.MetaMessage("marker", text="keep-this-marker", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("set_tempo", tempo=1_000_000, time=480),
        mido.MetaMessage("end_of_track", time=960),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead 唱", time=0),
        mido.MetaMessage("lyrics", text="君", time=0),
        mido.Message("program_change", channel=1, program=41, time=0),
        mido.Message("control_change", channel=1, control=7, value=99, time=0),
        mido.Message("pitchwheel", channel=1, pitch=123, time=0),
        mido.Message("note_on", channel=1, note=60, velocity=91, time=0),
        mido.Message("note_off", channel=1, note=60, velocity=0, time=480),
        mido.Message("note_on", channel=1, note=62, velocity=73, time=0),
        mido.Message("note_off", channel=1, note=62, velocity=0, time=480),
        mido.MetaMessage("lyrics", text="。", time=0),
        mido.Message("note_on", channel=1, note=64, velocity=82, time=0),
        mido.Message("note_off", channel=1, note=64, velocity=0, time=480),
        mido.MetaMessage("marker", text="vocal-tail", time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    save_midi(midi, path)
    return Path(path)


def write_same_onset_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=480),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
        mido.Message("note_on", channel=0, note=64, velocity=79, time=0),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=480),
        mido.Message("note_off", channel=0, note=64, velocity=0, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    save_midi(midi, path)
    return Path(path)


def write_event_ambiguity_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("marker", text="keep-event-meta", time=0),
        mido.MetaMessage("end_of_track", time=1920),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead 事件", time=0),
        mido.MetaMessage("lyrics", text="甲", time=0),
        mido.MetaMessage("lyrics", text="乙", time=0),
        mido.Message("note_on", channel=0, note=60, velocity=90, time=0),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=480),
        mido.Message("note_on", channel=0, note=62, velocity=88, time=0),
        mido.Message("note_off", channel=0, note=62, velocity=0, time=480),
        mido.Message("note_on", channel=0, note=65, velocity=86, time=0),
        mido.Message("note_on", channel=0, note=67, velocity=84, time=0),
        mido.Message("note_off", channel=0, note=65, velocity=0, time=480),
        mido.Message("note_off", channel=0, note=67, velocity=0, time=0),
        mido.MetaMessage("lyrics", text="丁", time=0),
        mido.Message("note_on", channel=0, note=69, velocity=82, time=0),
        mido.Message("note_off", channel=0, note=69, velocity=0, time=480),
        mido.MetaMessage("marker", text="tail", time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Other", time=0),
        mido.Message("note_on", channel=1, note=70, velocity=80, time=0),
        mido.Message("note_off", channel=1, note=70, velocity=0, time=480),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    save_midi(midi, path)
    return Path(path)


def event_semantics_without_lyrics(path):
    midi = load_midi(path)
    rows = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for message in track:
            tick += message.time
            payload = message.dict()
            payload.pop("time", None)
            if message.type != "lyrics":
                rows.append((track_index, tick, payload))
    return rows


class LyricsReadReviewTests(unittest.TestCase):
    def temporary(self, prefix):
        work = APP / "work"
        work.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix=prefix, dir=work)

    def test_unicode_read_binds_source_and_reports_unknown_orphan_and_ambiguity(self):
        with self.temporary("lyrics-read-") as temporary:
            source = write_review_fixture(Path(temporary) / "入力 歌詞.mid")
            before = source.read_bytes()
            first = read_lyrics(source, language="ja")
            second = read_lyrics(str(source), language="ja")
            json_ready(first)
            self.assertEqual(first, second)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(first["kind"], "vwa-lyrics-read")
            self.assertEqual(first["source_sha256"], digest(source))
            self.assertEqual(first["language"], "ja")
            self.assertEqual(first["mapping_contract"]["worker"], "agent_score")
            self.assertTrue(all(
                note["note_id"].startswith("note-" + first["source_sha256"][:16])
                for note in first["notes"]
            ))

            by_pitch = {note["pitch"]: note for note in first["notes"]}
            self.assertEqual(by_pitch[60]["lyric"], "你")
            self.assertEqual(by_pitch[62]["lyric"], "好")
            self.assertEqual(by_pitch[64]["lyric"], "〜")
            self.assertEqual(by_pitch[65]["lyric"], "世界")
            self.assertEqual(by_pitch[72]["lyric"], "")
            self.assertEqual(by_pitch[79]["lyric"], "[待填]")
            self.assertEqual(by_pitch[81]["lyric"], "で")
            self.assertEqual(by_pitch[71]["lyric_alignment"], "missing")
            self.assertEqual(by_pitch[74]["lyric_alignment"], "ambiguous")
            self.assertEqual(by_pitch[76]["lyric_alignment"], "ambiguous")
            self.assertEqual(first["coverage"]["note_count"], 12)
            self.assertEqual(first["coverage"]["aligned_note_count"], 9)
            self.assertEqual(first["coverage"]["missing_note_count"], 1)
            self.assertEqual(first["coverage"]["ambiguous_note_count"], 2)
            self.assertEqual(first["coverage"]["orphan_event_count"], 1)
            self.assertEqual(first["coverage"]["ambiguous_event_count"], 1)
            self.assertEqual(first["orphan_lyric_events"][0]["text"], "旧")
            self.assertEqual(first["ambiguous_lyric_events"][0]["text"], "同")
            self.assertTrue(first["meta_evidence"][0]["meta_events"])

    def test_review_handles_empty_placeholder_duplicate_continuation_and_reference(self):
        with self.temporary("lyrics-review-") as temporary:
            source = write_review_fixture(Path(temporary) / "review.mid")
            first = review_lyrics(
                source,
                reference_text="你 好，天世界。世世",
                language="zh",
            )
            second = review_lyrics(
                source,
                reference_text="你 好，天世界。世世",
                language="zh",
            )
            json_ready(first)
            self.assertEqual(first, second)
            codes = [hint["code"] for hint in first["hints"]]
            for code in (
                "missing_lyric",
                "empty_lyric",
                "placeholder_lyric",
                "duplicate_adjacent_lyric",
                "continuation_mark_ambiguous",
                "orphan_lyric_event",
                "ambiguous_lyric_alignment",
                "ambiguous_lyric_event",
                "incomplete_lyric_coverage",
            ):
                self.assertIn(code, codes)
            self.assertTrue(any(
                code in {"reference_missing", "reference_mismatch"}
                for code in codes
            ))
            self.assertEqual(first["reference"]["source"], "explicit_caller_input")
            self.assertTrue(all(
                hint["severity"] in {"info", "warning"} for hint in first["hints"]
            ))
            self.assertTrue(all(
                "tick" in hint and "onset_seconds" in hint
                for hint in first["hints"]
            ))
            self.assertIn("不代表发音", first["disclaimer"])

    def test_reference_span_of_one_character_is_ambiguous_not_an_error(self):
        with self.temporary("lyrics-span-") as temporary:
            midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
            midi.tracks.append(mido.MidiTrack([
                mido.MetaMessage("set_tempo", tempo=500_000, time=0),
                mido.MetaMessage("end_of_track", time=960),
            ]))
            midi.tracks.append(mido.MidiTrack([
                mido.MetaMessage("lyrics", text="你", time=0),
                mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
                mido.Message("note_off", channel=0, note=60, velocity=0, time=480),
                mido.MetaMessage("lyrics", text="你", time=0),
                mido.Message("note_on", channel=0, note=62, velocity=80, time=0),
                mido.Message("note_off", channel=0, note=62, velocity=0, time=480),
                mido.MetaMessage("end_of_track", time=0),
            ]))
            source = Path(temporary) / "span.mid"
            save_midi(midi, source)
            review = review_lyrics(source, reference_text="你", language="zh")
            codes = [hint["code"] for hint in review["hints"]]
            self.assertIn("ambiguous_note_span", codes)
            self.assertNotIn("reference_extra", codes)


class LyricsPatchTests(unittest.TestCase):
    def test_event_ids_are_complete_stable_and_reviewable(self):
        with self.temporary("lyrics-event-read-") as temporary:
            source = write_event_ambiguity_fixture(Path(temporary) / "events.mid")
            first = read_lyrics(source)
            second = read_lyrics(source)
            self.assertEqual(first, second)
            sha = first["source_sha256"]
            self.assertEqual(
                first["mapping_contract"]["lyric_event_id_basis"],
                "lyr-{complete_source_sha}-t{track_index}-e{event_index}",
            )
            self.assertTrue(first["lyric_events"])
            for event in first["lyric_events"]:
                self.assertEqual(
                    event["event_id"],
                    f"lyr-{sha}-t{event['track_index']}-e{event['event_index']}",
                )
            by_pitch = {note["pitch"]: note for note in first["notes"]}
            ambiguous = by_pitch[60]
            self.assertEqual(ambiguous["lyric_alignment"], "ambiguous")
            self.assertEqual(len(ambiguous["competing_lyric_event_ids"]), 2)
            self.assertTrue(all(sha in event_id
                                for event_id in ambiguous["competing_lyric_event_ids"]))
            review = review_lyrics(source)
            self.assertTrue(review["lyric_events"])
            hint = next(row for row in review["hints"]
                        if row["code"] == "ambiguous_lyric_alignment")
            self.assertEqual(set(hint["event_ids"]),
                             set(ambiguous["competing_lyric_event_ids"]))

    def test_move_and_delete_event_apply_is_sha_bound_and_preserves_music(self):
        with self.temporary("lyrics-event-patch-") as temporary:
            root = Path(temporary)
            source = write_event_ambiguity_fixture(root / "events.mid")
            original = source.read_bytes()
            original_non_lyrics = event_semantics_without_lyrics(source)
            read = read_lyrics(source)
            by_pitch = {note["pitch"]: note for note in read["notes"]}
            source_event = next(row for row in read["lyric_events"] if row["text"] == "乙")
            target = by_pitch[62]
            no_op = {
                "note_id": by_pitch[69]["note_id"],
                "lyric": "丁",
                "operation": "replace",
            }
            move_edit = {
                "event_id": source_event["event_id"],
                "target_note_id": target["note_id"],
                "operation": "move_event",
            }
            plan = plan_lyrics_patch(source, read["source_sha256"], [no_op, move_edit])
            json_ready(plan)
            self.assertEqual(plan["source_sha256"], read["source_sha256"])
            self.assertEqual(plan["validation"]["move_event_count"], 1)
            self.assertEqual(plan["validation"]["delete_event_count"], 0)
            self.assertEqual(plan["validation"]["moved_count"], 1)
            self.assertEqual(plan["validation"]["deleted_count"], 0)
            self.assertEqual(plan["validation"]["changed_event_count"], 1)
            self.assertEqual(plan["validation"]["ambiguous_groups_before"], 2)
            self.assertEqual(plan["validation"]["ambiguous_groups_resolved"], 1)
            self.assertEqual(plan["validation"]["remaining_ambiguous_groups"], 1)
            self.assertEqual(plan["validation"]["unchanged_count"], 1)
            self.assertEqual(plan["validation"]["unchanged_note_ids"], [no_op["note_id"]])
            self.assertEqual(len(plan["plan_sha256"]), 64)
            self.assertIn(move_edit, plan["edits"])
            self.assertIn(no_op, plan["edits"])
            move = next(row for row in plan["diff"]["operations"]
                        if row["operation"] == "move_event")
            self.assertEqual(move["source_event_id"], source_event["event_id"])
            self.assertEqual(move["target_note_id"], target["note_id"])
            self.assertEqual(move["alignment_before"], "ambiguous")
            self.assertEqual(move["alignment_after"], "aligned")
            self.assertEqual(move["before_tick"], source_event["tick"])
            self.assertEqual(move["after_tick"], target["onset_tick"])
            self.assertTrue(move["candidate_event_id"].startswith(
                f"lyr-{plan['expected_candidate_sha256']}-t{target['track_index']}-e"))
            self.assertEqual(source.read_bytes(), original)

            receipt = apply_lyrics_patch(source, plan, root / "move")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(receipt["source_sha256"], read["source_sha256"])
            self.assertEqual(receipt["candidate_sha256"], plan["expected_candidate_sha256"])
            self.assertEqual(event_semantics_without_lyrics(receipt["candidate_path"]),
                             original_non_lyrics)
            candidate = read_lyrics(receipt["candidate_path"])
            self.assertEqual(candidate["source_sha256"], receipt["candidate_sha256"])
            candidate_by_pitch = {note["pitch"]: note for note in candidate["notes"]}
            self.assertEqual(candidate_by_pitch[60]["lyric"], "甲")
            self.assertEqual(candidate_by_pitch[60]["lyric_alignment"], "aligned")
            self.assertEqual(candidate_by_pitch[62]["lyric"], "乙")
            self.assertEqual(candidate_by_pitch[62]["lyric_alignment"], "aligned")
            self.assertTrue(candidate_by_pitch[62]["lyric_event_id"].startswith(
                f"lyr-{candidate['source_sha256']}-t1-e"))
            self.assertNotIn(source_event["event_id"],
                             {row["event_id"] for row in candidate["lyric_events"]})

            delete = {
                "event_id": source_event["event_id"],
                "operation": "delete_event",
            }
            delete_plan = plan_lyrics_patch(source, read["source_sha256"], [delete])
            self.assertEqual(delete_plan["validation"]["delete_event_count"], 1)
            self.assertEqual(delete_plan["validation"]["deleted_count"], 1)
            delete_operation = delete_plan["diff"]["operations"][0]
            self.assertIsNone(delete_operation["candidate_event_id"])
            self.assertEqual(delete_operation["before_tick"], source_event["tick"])
            delete_receipt = apply_lyrics_patch(source, delete_plan, root / "delete")
            self.assertEqual(source.read_bytes(), original)
            deleted = read_lyrics(delete_receipt["candidate_path"])
            deleted_by_pitch = {note["pitch"]: note for note in deleted["notes"]}
            self.assertEqual(deleted_by_pitch[60]["lyric"], "甲")
            self.assertEqual(deleted_by_pitch[60]["lyric_alignment"], "aligned")
            self.assertEqual(deleted["coverage"]["lyric_event_count"],
                             read["coverage"]["lyric_event_count"] - 1)
            self.assertEqual(event_semantics_without_lyrics(delete_receipt["candidate_path"]),
                             original_non_lyrics)

    def test_event_move_rejects_stale_cross_track_onset_and_ownership_conflicts(self):
        with self.temporary("lyrics-event-conflict-") as temporary:
            root = Path(temporary)
            source = write_event_ambiguity_fixture(root / "events.mid")
            original = source.read_bytes()
            read = read_lyrics(source)
            by_pitch = {note["pitch"]: note for note in read["notes"]}
            source_event = next(row for row in read["lyric_events"] if row["text"] == "乙")
            other_event = next(row for row in read["lyric_events"] if row["text"] == "甲")

            cross_track = [{
                "event_id": source_event["event_id"],
                "target_note_id": by_pitch[70]["note_id"],
                "operation": "move_event",
            }]
            with self.assertRaisesRegex(ValueError, "同一轨道"):
                plan_lyrics_patch(source, read["source_sha256"], cross_track)

            same_onset = [{
                "event_id": source_event["event_id"],
                "target_note_id": by_pitch[65]["note_id"],
                "operation": "move_event",
            }]
            with self.assertRaisesRegex(ValueError, "多个音符"):
                plan_lyrics_patch(source, read["source_sha256"], same_onset)

            occupied = [{
                "event_id": source_event["event_id"],
                "target_note_id": by_pitch[69]["note_id"],
                "operation": "move_event",
            }]
            with self.assertRaisesRegex(ValueError, "区间已有"):
                plan_lyrics_patch(source, read["source_sha256"], occupied)

            duplicate_target = [
                {
                    "event_id": source_event["event_id"],
                    "target_note_id": by_pitch[62]["note_id"],
                    "operation": "move_event",
                },
                {
                    "event_id": other_event["event_id"],
                    "target_note_id": by_pitch[62]["note_id"],
                    "operation": "move_event",
                },
            ]
            with self.assertRaisesRegex(ValueError, "重复的移动目标"):
                plan_lyrics_patch(source, read["source_sha256"], duplicate_target)

            with self.assertRaisesRegex(ValueError, "不能与 note 级"):
                plan_lyrics_patch(source, read["source_sha256"], [
                    {
                        "event_id": source_event["event_id"],
                        "target_note_id": by_pitch[62]["note_id"],
                        "operation": "move_event",
                    },
                    {
                        "note_id": by_pitch[69]["note_id"],
                        "lyric": "改",
                        "operation": "replace",
                    },
                ])

            changed = load_midi(source)
            changed.tracks[0].insert(3, mido.MetaMessage("text", text="new identity", time=0))
            save_midi(changed, source)
            changed_read = read_lyrics(source)
            changed_by_pitch = {note["pitch"]: note for note in changed_read["notes"]}
            with self.assertRaisesRegex(ValueError, "另一份源 SHA"):
                plan_lyrics_patch(source, changed_read["source_sha256"], [{
                    "event_id": source_event["event_id"],
                    "target_note_id": changed_by_pitch[62]["note_id"],
                    "operation": "move_event",
                }])
            self.assertNotEqual(source.read_bytes(), original)

    def test_event_plan_hash_and_source_guard_prevent_partial_output(self):
        with self.temporary("lyrics-event-hash-") as temporary:
            root = Path(temporary)
            source = write_event_ambiguity_fixture(root / "events.mid")
            original = source.read_bytes()
            read = read_lyrics(source)
            by_pitch = {note["pitch"]: note for note in read["notes"]}
            source_event = next(row for row in read["lyric_events"] if row["text"] == "乙")
            plan = plan_lyrics_patch(source, read["source_sha256"], [{
                "event_id": source_event["event_id"],
                "target_note_id": by_pitch[62]["note_id"],
                "operation": "move_event",
            }])
            tampered = json.loads(json.dumps(plan))
            tampered["diff"]["operations"][0]["target_note_id"] = by_pitch[69]["note_id"]
            output = root / "tampered"
            with self.assertRaisesRegex(ValueError, "plan_sha256"):
                apply_lyrics_patch(source, tampered, output)
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), original)

            changed = load_midi(source)
            changed.tracks[0].insert(3, mido.MetaMessage("text", text="stale", time=0))
            save_midi(changed, source)
            with self.assertRaisesRegex(ValueError, "SHA-256 与计划不一致"):
                apply_lyrics_patch(source, plan, root / "stale")
            self.assertFalse((root / "stale").exists())

    def test_mixed_and_all_unchanged_lyrics_are_reported_without_losing_changes(self):
        with self.temporary("lyrics-noop-") as temporary:
            root = Path(temporary)
            source = write_patch_fixture(root / "source.mid")
            original = source.read_bytes()
            read = read_lyrics(source)
            by_pitch = {note["pitch"]: note for note in read["notes"]}
            noop = {"note_id": by_pitch[60]["note_id"], "lyric": "君", "operation": "replace"}
            mixed = plan_lyrics_patch(source, read["source_sha256"], self.edits(read) + [noop])
            self.assertEqual(mixed["validation"]["changed_count"], 2)
            self.assertEqual(mixed["validation"]["unchanged_count"], 1)
            self.assertEqual(mixed["validation"]["unchanged_note_ids"], [noop["note_id"]])
            self.assertEqual(len(mixed["diff"]["operations"]), 2)
            receipt = apply_lyrics_patch(source, mixed, root / "mixed")
            after = {n["pitch"]: n["lyric"] for n in read_score(receipt["candidate_path"])["notes"]}
            self.assertEqual((after[60], after[62], after[64]), ("君", "歌", "世界"))
            only_noop = plan_lyrics_patch(source, read["source_sha256"], [noop])
            self.assertEqual(only_noop["validation"]["changed_count"], 0)
            self.assertEqual(only_noop["validation"]["unchanged_count"], 1)
            self.assertEqual(only_noop["diff"]["operations"], [])
            unchanged_receipt = apply_lyrics_patch(source, only_noop, root / "unchanged")
            self.assertEqual(Path(native_path(unchanged_receipt["candidate_path"])).read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)

    def temporary(self, prefix):
        work = APP / "work"
        work.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix=prefix, dir=work)

    def edits(self, read):
        by_pitch = {note["pitch"]: note for note in read["notes"]}
        return [
            {
                "note_id": by_pitch[64]["note_id"],
                "lyric": "世界",
                "operation": "replace",
            },
            {
                "note_id": by_pitch[62]["note_id"],
                "lyric": "歌",
                "operation": "insert",
            },
        ]

    def test_replace_insert_apply_preserves_music_and_publishes_atomically(self):
        with self.temporary("lyrics-patch-") as temporary:
            root = Path(temporary)
            source = write_patch_fixture(root / "source.mid")
            original = source.read_bytes()
            original_sha = digest(source)
            before_score = read_score(source)
            before_non_lyrics = event_semantics_without_lyrics(source)
            read = read_lyrics(source)
            plan = plan_lyrics_patch(source, original_sha, self.edits(read))
            json_ready(plan)
            self.assertEqual(plan["source_sha256"], original_sha)
            self.assertEqual(plan["source_path"], str(source.resolve()))
            self.assertNotIn("\\\\?\\", plan["source_path"])
            self.assertEqual(plan["validation"]["replace_count"], 1)
            self.assertEqual(plan["validation"]["insert_count"], 1)
            self.assertTrue(plan["validation"]["note_semantics_preserved"])
            self.assertEqual(len(plan["plan_sha256"]), 64)
            operations = plan["diff"]["operations"]
            self.assertEqual({item["operation"] for item in operations}, {"replace", "insert"})
            inserted = next(item for item in operations if item["operation"] == "insert")
            self.assertIsNone(inserted["before"])
            self.assertEqual(inserted["after"]["text"], "歌")

            output = root / "输出 候选"
            receipt = apply_lyrics_patch(source, plan, output)
            json_ready(receipt)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(digest(source), original_sha)
            candidate = Path(native_path(receipt["candidate_path"]))
            receipt_path = Path(native_path(receipt["receipt_path"]))
            self.assertTrue(candidate.is_absolute())
            self.assertNotEqual(candidate, source.resolve())
            self.assertEqual(candidate.name, "vwa-lyrics-" + plan["plan_sha256"] + ".mid")
            self.assertEqual(receipt["candidate_sha256"], digest(candidate))
            self.assertEqual(receipt["source_sha256"], original_sha)
            self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), receipt)
            self.assertEqual(event_semantics_without_lyrics(candidate), before_non_lyrics)

            after_score = read_score(candidate)
            after_by_pitch = {note["pitch"]: note for note in after_score["notes"]}
            self.assertEqual(after_by_pitch[60]["lyric"], "君")
            self.assertEqual(after_by_pitch[62]["lyric"], "歌")
            self.assertEqual(after_by_pitch[64]["lyric"], "世界")
            before_by_pitch = {note["pitch"]: note for note in before_score["notes"]}
            for pitch in (60, 62, 64):
                self.assertEqual(after_by_pitch[pitch]["channel"], before_by_pitch[pitch]["channel"])
                self.assertEqual(after_by_pitch[pitch]["velocity"], before_by_pitch[pitch]["velocity"])
                self.assertEqual(after_by_pitch[pitch]["onset_tick"], before_by_pitch[pitch]["onset_tick"])
                self.assertEqual(after_by_pitch[pitch]["end_tick"], before_by_pitch[pitch]["end_tick"])
                self.assertNotEqual(after_by_pitch[pitch]["note_id"], before_by_pitch[pitch]["note_id"])

            self.assertEqual(apply_lyrics_patch(source, plan, output), receipt)
            good_receipt = receipt_path.read_bytes()
            tampered_receipt = json.loads(good_receipt.decode("utf-8"))
            tampered_receipt["candidate_sha256"] = "0" * 64
            receipt_path.write_text(
                json.dumps(tampered_receipt, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "receipt"):
                apply_lyrics_patch(source, plan, output)
            receipt_path.write_bytes(good_receipt)

            good_candidate = candidate.read_bytes()
            candidate.write_bytes(good_candidate + b"collision")
            with self.assertRaisesRegex(ValueError, "不同内容"):
                apply_lyrics_patch(source, plan, output)
            self.assertEqual(source.read_bytes(), original)

    def test_windows_hardlink_refusal_uses_nonoverwriting_publish(self):
        with self.temporary("lyrics-hardlink-") as temporary:
            root = Path(temporary)
            source = write_patch_fixture(root / "input.mid")
            read = read_lyrics(source)
            plan = plan_lyrics_patch(source, read["source_sha256"], self.edits(read))
            output = root / "outputs"
            refusal = PermissionError("simulated managed-sandbox hardlink refusal")
            if os.name == "nt":
                with patch("agent_score.os.link", side_effect=refusal), \
                        patch("agent_score.os.rename", wraps=os.rename) as rename:
                    receipt = apply_lyrics_patch(source, plan, output)
                    self.assertEqual(rename.call_count, 2)
            else:
                with patch("agent_score.os.link", side_effect=refusal):
                    with self.assertRaisesRegex(ValueError, "无法原子发布"):
                        apply_lyrics_patch(source, plan, output)
                receipt = apply_lyrics_patch(source, plan, output)
            candidate = Path(native_path(receipt["candidate_path"]))
            receipt_path = Path(native_path(receipt["receipt_path"]))
            self.assertTrue(candidate.is_file())
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(digest(candidate), receipt["candidate_sha256"])
            collision = b"existing collision"
            candidate.write_bytes(collision)
            if os.name == "nt":
                with patch("agent_score.os.link", side_effect=refusal), \
                        patch("agent_score.os.rename", wraps=os.rename) as rename:
                    with self.assertRaisesRegex(ValueError, "不同内容"):
                        apply_lyrics_patch(source, plan, output)
                    self.assertEqual(rename.call_count, 0)
            else:
                with self.assertRaisesRegex(ValueError, "不同内容"):
                    apply_lyrics_patch(source, plan, output)
            self.assertEqual(candidate.read_bytes(), collision)

    def test_stale_source_tampered_plan_and_ambiguous_insert_are_rejected(self):
        with self.temporary("lyrics-safety-") as temporary:
            root = Path(temporary)
            source = write_patch_fixture(root / "source.mid")
            original_sha = digest(source)
            read = read_lyrics(source)
            plan = plan_lyrics_patch(source, original_sha, self.edits(read))

            tampered = json.loads(json.dumps(plan))
            tampered["diff"]["operations"][0]["after"]["text"] = "篡改"
            with self.assertRaisesRegex(ValueError, "plan_sha256"):
                apply_lyrics_patch(source, tampered, root / "tampered-output")

            other_source = write_patch_fixture(root / "other.mid")
            with self.assertRaisesRegex(ValueError, "路径"):
                apply_lyrics_patch(other_source, plan, root / "wrong-source-output")

            wrong_expected = "0" * 64
            with self.assertRaisesRegex(ValueError, "SHA-256 已变化"):
                plan_lyrics_patch(source, wrong_expected, self.edits(read))

            midi = load_midi(source)
            midi.tracks[0].insert(3, mido.MetaMessage("text", text="revision", time=0))
            save_midi(midi, source)
            with self.assertRaisesRegex(ValueError, "SHA-256 与计划不一致"):
                apply_lyrics_patch(source, plan, root / "stale-output")

            ambiguous_source = write_same_onset_fixture(root / "poly.mid")
            ambiguous = read_lyrics(ambiguous_source)
            target = ambiguous["notes"][0]
            with self.assertRaisesRegex(ValueError, "竞争|多个音符"):
                plan_lyrics_patch(
                    ambiguous_source,
                    ambiguous["source_sha256"],
                    [{
                        "note_id": target["note_id"],
                        "lyric": "声",
                        "operation": "insert",
                    }],
                )

    def test_edit_validation_rejects_unknown_duplicate_and_unsupported_fields(self):
        with self.temporary("lyrics-validation-") as temporary:
            source = write_patch_fixture(Path(temporary) / "source.mid")
            read = read_lyrics(source)
            sha = read["source_sha256"]
            actual = self.edits(read)
            unknown = dict(actual[0], note_id="note-stale-t0-n99")
            with self.assertRaisesRegex(ValueError, "未知或过期"):
                plan_lyrics_patch(source, sha, [unknown])
            missing_operation = dict(actual[0])
            missing_operation.pop("operation")
            with self.assertRaisesRegex(ValueError, "字段必须恰为"):
                plan_lyrics_patch(source, sha, [missing_operation])
            with self.assertRaisesRegex(ValueError, "非空数组"):
                plan_lyrics_patch(source, sha, [])
            with self.assertRaisesRegex(ValueError, "重复"):
                plan_lyrics_patch(source, sha, [actual[0], dict(actual[0])])
            unsupported = dict(actual[0], phoneme="a")
            with self.assertRaisesRegex(ValueError, "字段必须恰为"):
                plan_lyrics_patch(source, sha, [unsupported])
            by_pitch = {note["pitch"]: note for note in read["notes"]}
            cleared = plan_lyrics_patch(source, sha, [{
                "note_id": by_pitch[64]["note_id"],
                "lyric": "",
                "operation": "replace",
            }])
            self.assertEqual(cleared["edits"][0]["lyric"], "")
            with self.assertRaisesRegex(ValueError, "没有唯一"):
                plan_lyrics_patch(
                    source,
                    sha,
                    [{
                        "note_id": by_pitch[62]["note_id"],
                        "lyric": "歌",
                        "operation": "replace",
                    }],
                )


if __name__ == "__main__":
    unittest.main()
