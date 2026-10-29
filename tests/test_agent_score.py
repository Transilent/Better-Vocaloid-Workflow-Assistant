"""Standalone acceptance tests for the pure Score worker."""

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

from agent_score import apply_patch, plan_patch, read_score, review_score
from common import file_hash, native_path


def digest(path):
    return hashlib.sha256(Path(native_path(path)).read_bytes()).hexdigest()


def note_events(path):
    midi = mido.MidiFile(native_path(path), charset="utf8")
    result = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for event_index, message in enumerate(track):
            tick += message.time
            result.append((track_index, event_index, tick, message))
    return midi, result


def message_signature(message):
    payload = message.dict()
    payload.pop("time", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_main_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo and cues", time=0),
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0),
        mido.MetaMessage("marker", text="keep-this-marker", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("set_tempo", tempo=1_000_000, time=480),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead 唱", time=0),
        mido.MetaMessage("lyrics", text="你", time=0),
        mido.Message("program_change", channel=2, program=41, time=0),
        mido.Message("control_change", channel=2, control=7, value=100, time=0),
        mido.Message("pitchwheel", channel=2, pitch=123, time=0),
        mido.Message("note_on", channel=2, note=60, velocity=91, time=0),
        mido.Message("note_off", channel=2, note=60, velocity=0, time=480),
        mido.MetaMessage("lyrics", text="好", time=0),
        mido.Message("note_on", channel=2, note=64, velocity=73, time=0),
        mido.Message("note_off", channel=2, note=64, velocity=0, time=480),
        mido.MetaMessage("marker", text="vocal-tail", time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.save(str(path))
    return Path(path)


def write_review_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500_000, time=0)]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=10),
        mido.Message("note_on", channel=0, note=72, velocity=80, time=0),
        mido.Message("note_off", channel=0, note=72, velocity=0, time=10),
    ]))
    midi.save(str(path))
    return Path(path)


def write_cross_pitch_overlap_fixture(path):
    # At 480 PPQ and 120 BPM, 15 ticks is exactly 15.625 ms.
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=480),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
        mido.Message("note_on", channel=0, note=64, velocity=75, time=465),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=15),
        mido.Message("note_off", channel=0, note=64, velocity=0, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.save(str(path))
    return Path(path)


def write_overlap_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500_000, time=0)]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", channel=0, note=60, velocity=90, time=0),
        mido.Message("note_on", channel=0, note=60, velocity=70, time=100),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=100),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=100),
    ]))
    midi.save(str(path))
    return Path(path)


def note_by_pitch(score, pitch, occurrence=0):
    rows = [note for note in score["notes"] if note["pitch"] == pitch]
    return rows[occurrence]


def json_ready(value):
    json.dumps(value, ensure_ascii=False, allow_nan=False)


def write_duration_sequence_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500_000, time=0)]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
        mido.Message("note_on", channel=0, note=60, velocity=0, time=100),
        mido.Message("note_on", channel=0, note=60, velocity=90, time=0),
        mido.Message("note_off", channel=0, note=60, velocity=64, time=300),
    ]))
    midi.save(str(path))
    return Path(path)


class AgentScoreReadReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_midi_read_is_stable_tempo_aware_and_json_serializable(self):
        with self.temporary("agent-score-read-") as temporary:
            source = write_main_fixture(Path(temporary) / "multi.mid")
            before = source.read_bytes()
            first = read_score(source)
            second = read_score(str(source))
            self.assertEqual(first, second)
            self.assertEqual(source.read_bytes(), before)
            json_ready(first)
            self.assertEqual(first["format"], "midi")
            self.assertEqual(first["source_sha256"], digest(source))
            self.assertEqual(first["ticks_per_beat"], 480)
            self.assertEqual(len(first["tracks"]), 2)
            self.assertEqual(len(first["notes"]), 2)
            first_note, second_note = first["notes"]
            self.assertEqual((first_note["pitch"], first_note["lyric"]), (60, "你"))
            self.assertEqual((second_note["pitch"], second_note["lyric"]), (64, "好"))
            self.assertAlmostEqual(first_note["onset_seconds"], 0.0, places=9)
            self.assertAlmostEqual(first_note["duration_seconds"], 0.5, places=9)
            self.assertAlmostEqual(second_note["onset_seconds"], 0.5, places=9)
            self.assertAlmostEqual(second_note["duration_seconds"], 1.0, places=9)
            self.assertTrue(first["write_supported"])
            self.assertTrue(all(note["note_id"].startswith("note-" + first["source_sha256"][:16]) for note in first["notes"]))
            changed = source.read_bytes() + b""
            source.write_bytes(changed)
            self.assertEqual(read_score(source)["notes"][0]["note_id"], first_note["note_id"])
            self.assertIn("timing", first)
            self.assertIn("tempo_events", first["timing"])

    def test_revision_bound_note_ids_change_with_source_bytes(self):
        with self.temporary("agent-score-ids-") as temporary:
            source = write_main_fixture(Path(temporary) / "source.mid")
            first = read_score(source)
            midi = mido.MidiFile(str(source), charset="utf8")
            midi.tracks[0].insert(3, mido.MetaMessage("text", text="revision", time=0))
            midi.save(str(source))
            second = read_score(source)
            self.assertNotEqual(first["source_sha256"], second["source_sha256"])
            self.assertNotEqual(first["notes"][0]["note_id"], second["notes"][0]["note_id"])

    def test_review_hints_are_deterministic_and_not_claims_of_error(self):
        with self.temporary("agent-score-review-") as temporary:
            source = write_review_fixture(Path(temporary) / "review.mid")
            result = review_score(source)
            json_ready(result)
            self.assertEqual(result, review_score(source))
            codes = [hint["code"] for hint in result["hints"]]
            self.assertIn("very_short_duration", codes)
            self.assertIn("large_pitch_leap", codes)
            self.assertTrue(all("note_ids" in hint and hint["note_ids"] for hint in result["hints"]))
            self.assertTrue(all(hint["severity"] in {"info", "warning"} for hint in result["hints"]))
            self.assertIn("不代表已经证实", result["disclaimer"])

    def test_review_locates_cross_pitch_fifteen_point_six_two_five_ms_overlap(self):
        with self.temporary("agent-score-monophonic-") as temporary:
            source = write_cross_pitch_overlap_fixture(Path(temporary) / "overlap.mid")
            result = review_score(source)
            conflicts = [
                hint for hint in result["hints"]
                if hint["code"] == "monophonic_overlap"
            ]
            self.assertEqual(len(conflicts), 1)
            conflict = conflicts[0]
            self.assertEqual(conflict["voice_key"], {"track_index": 1, "channel": 0})
            self.assertEqual(conflict["pitches"], [60, 64])
            self.assertEqual(conflict["overlap_ticks"], 15)
            self.assertEqual(conflict["overlap_milliseconds"], 15.625)
            self.assertEqual(len(conflict["note_ids"]), 2)
            score = read_score(source)
            self.assertEqual(
                set(conflict["note_ids"]),
                {note["note_id"] for note in score["notes"]},
            )
            json_ready(result)

    def test_svp_is_explicitly_read_only(self):
        with self.temporary("agent-score-svp-") as temporary:
            source = Path(temporary) / "source.svp"
            q = 705_600_000
            project = {
                "time": {"tempo": [{"position": 0, "bpm": 120}]},
                "tracks": [{
                    "name": "Lead",
                    "mainRef": {"isInstrumental": False},
                    "mainGroup": {"notes": [{"onset": 0, "duration": q, "pitch": 60, "lyrics": "你"}]},
                }],
            }
            source.write_bytes(json.dumps(project, ensure_ascii=False).encode("utf-8") + b"\x00")
            result = read_score(source)
            json_ready(result)
            self.assertEqual(result["format"], "svp")
            self.assertFalse(result["write_supported"])
            self.assertIn("SVP", result["write_unsupported_reason"])
            self.assertEqual(result["notes"][0]["lyric"], "你")
            self.assertAlmostEqual(result["notes"][0]["duration_seconds"], 0.5, places=9)
            with self.assertRaisesRegex(ValueError, "SVP 写入"):
                plan_patch(source, result["source_sha256"], [{"note_id": result["notes"][0]["note_id"], "pitch": 61}])

class AgentScorePatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_windows_hardlink_refusal_uses_nonoverwriting_rename_for_both_outputs(self):
        with self.temporary("agent-score-hardlink-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "input.mid")
            score = read_score(source)
            plan = plan_patch(source, score["source_sha256"], self.plan_edits(score))
            output = root / "outputs"
            refusal = PermissionError("simulated managed-sandbox hardlink refusal")
            if os.name == "nt":
                with patch("agent_score.os.link", side_effect=refusal), \
                        patch("agent_score.os.rename", wraps=os.rename) as rename:
                    receipt = apply_patch(source, plan, output)
                    self.assertEqual(rename.call_count, 2)
            else:
                with patch("agent_score.os.link", side_effect=refusal):
                    with self.assertRaisesRegex(ValueError, "无法原子发布"):
                        apply_patch(source, plan, output)
                with patch("agent_score.os.link", wraps=os.link):
                    receipt = apply_patch(source, plan, output)
            self.assertTrue(Path(native_path(receipt["candidate_path"])).is_file())
            self.assertTrue(Path(native_path(receipt["receipt_path"])).is_file())
            self.assertEqual(file_hash(receipt["candidate_path"]), receipt["candidate_sha256"])
            self.assertEqual(json.loads(Path(native_path(receipt["receipt_path"])).read_text(encoding="utf-8")), receipt)

            if os.name == "nt":
                candidate = Path(native_path(receipt["candidate_path"]))
                receipt_path = Path(native_path(receipt["receipt_path"]))
                collision = b"existing collision"
                candidate.unlink()
                receipt_path.unlink()
                candidate.write_bytes(collision)
                with patch("agent_score.os.link", side_effect=refusal), \
                        patch("agent_score.os.rename", wraps=os.rename) as rename:
                    with self.assertRaisesRegex(ValueError, "不同内容"):
                        apply_patch(source, plan, output)
                    self.assertEqual(rename.call_count, 0)
                self.assertEqual(candidate.read_bytes(), collision)

                output_again = root / "outputs-receipt-collision"
                initial_receipt = apply_patch(source, plan, output_again)
                receipt_collision_path = Path(native_path(initial_receipt["receipt_path"]))
                initial_candidate_path = Path(native_path(initial_receipt["candidate_path"]))
                # A colliding receipt is rejected as untrusted; apply never
                # replaces it and leaves the previously verified candidate.
                expected_candidate = initial_candidate_path.read_bytes()
                receipt_collision_path.write_bytes(b"receipt collision")
                with patch("agent_score.os.link", side_effect=refusal), \
                        patch("agent_score.os.rename", wraps=os.rename) as rename:
                    with self.assertRaisesRegex(ValueError, "receipt"):
                        apply_patch(source, plan, output_again)
                    self.assertEqual(rename.call_count, 0)
                self.assertEqual(receipt_collision_path.read_bytes(), b"receipt collision")
                self.assertEqual(initial_candidate_path.read_bytes(), expected_candidate)

    def plan_edits(self, score):
        first = note_by_pitch(score, 60)
        second = note_by_pitch(score, 64)
        return [
            {"note_id": first["note_id"], "pitch": 62, "onset_tick": 120, "duration_tick": 600},
            {"note_id": second["note_id"], "duration_tick": 360},
        ]

    def test_noop_fields_do_not_block_valid_note_changes(self):
        with self.temporary("agent-score-noop-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "input.mid")
            original = source.read_bytes()
            score = read_score(source)
            first = note_by_pitch(score, 60)
            edit = {"note_id": first["note_id"], "pitch": 62,
                    "onset_tick": first["onset_tick"]}
            plan = plan_patch(source, score["source_sha256"], [edit])
            self.assertEqual(plan["diff"]["changed_count"], 1)
            self.assertEqual(plan["diff"]["unchanged_count"], 1)
            self.assertEqual(plan["diff"]["unchanged_fields"],
                             [{"note_id": first["note_id"], "field": "onset_tick"}])
            receipt = apply_patch(source, plan, root / "mixed")
            self.assertEqual(note_by_pitch(read_score(receipt["candidate_path"]), 62)["onset_tick"], first["onset_tick"])
            unchanged = plan_patch(source, score["source_sha256"],
                                   [{"note_id": first["note_id"], "pitch": first["pitch"]}])
            self.assertEqual(unchanged["diff"]["changed_count"], 0)
            self.assertEqual(unchanged["diff"]["unchanged_count"], 1)
            receipt = apply_patch(source, unchanged, root / "unchanged")
            self.assertEqual(Path(native_path(receipt["candidate_path"])).read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)

    def test_plan_and_apply_preserve_all_unedited_events_and_source(self):
        with self.temporary("agent-score-apply-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "input.mid")
            original = source.read_bytes()
            original_hash = digest(source)
            score = read_score(source)
            plan = plan_patch(source, original_hash, self.plan_edits(score))
            json_ready(plan)
            self.assertEqual(plan["source_sha256"], original_hash)
            self.assertEqual(plan["expected_candidate_sha256"], plan["expected_candidate_sha256"].lower())
            self.assertEqual(len(plan["plan_sha256"]), 64)
            self.assertEqual({edit["note_id"] for edit in plan["edits"]}, {n["note_id"] for n in score["notes"]})
            operations = plan["diff"]["operations"]
            self.assertTrue(any(op["field"] == "pitch" and op["after"] == 62 for op in operations))
            self.assertTrue(any(op["field"] == "duration_tick" and op["after"] == 360 for op in operations))

            receipt = apply_patch(source, plan, root / "outputs")
            json_ready(receipt)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(digest(source), original_hash)
            candidate = Path(native_path(receipt["candidate_path"]))
            self.assertTrue(candidate.is_absolute())
            self.assertNotEqual(candidate, source.resolve())
            self.assertEqual(candidate.name, "vwa-score-" + plan["plan_sha256"] + ".mid")
            self.assertTrue(candidate.is_file())
            self.assertEqual(receipt["candidate_sha256"], digest(candidate))
            self.assertEqual(receipt["source_sha256"], original_hash)
            self.assertEqual(receipt["diff"], plan["diff"])
            receipt_file = Path(native_path(receipt["receipt_path"]))
            self.assertTrue(receipt_file.is_file())
            self.assertEqual(json.loads(receipt_file.read_text(encoding="utf-8")), receipt)

            before_midi, before_events = note_events(source)
            after_midi, after_events = note_events(candidate)
            self.assertEqual(after_midi.type, before_midi.type)
            self.assertEqual(after_midi.ticks_per_beat, before_midi.ticks_per_beat)
            self.assertEqual(len(after_midi.tracks), len(before_midi.tracks))
            self.assertEqual(len(before_events), len(after_events))
            self.assertEqual(plan["structural_changes"]["reordered_tracks"], [1])
            self.assertEqual(plan["structural_changes"]["end_of_track"], [])

            # Edits may change stable event order. Compare intact meta and
            # controller content independently, then verify the edited notes.
            def messages_by_track(events):
                result = {}
                for track_index, event_index, tick, message in events:
                    result.setdefault(track_index, []).append(message)
                return result

            before_by_track = messages_by_track(before_events)
            after_by_track = messages_by_track(after_events)
            comparisons = (
                (0, "set_tempo", "tempo", [500_000, 1_000_000]),
                (0, "time_signature", "numerator", [3]),
                (0, "marker", "text", ["keep-this-marker"]),
                (1, "program_change", "program", [41]),
                (1, "control_change", "value", [100]),
                (1, "pitchwheel", "pitch", [123]),
                (1, "marker", "text", ["vocal-tail"]),
                (1, "track_name", "name", ["Lead 唱"]),
            )
            for track_index, message_type, attribute, expected in comparisons:
                before_values = [
                    getattr(message, attribute)
                    for message in before_by_track[track_index]
                    if message.type == message_type
                ]
                after_values = [
                    getattr(message, attribute)
                    for message in after_by_track[track_index]
                    if message.type == message_type
                ]
                self.assertEqual(before_values, expected)
                self.assertEqual(after_values, expected)
            for track_index in before_by_track:
                self.assertEqual(
                    sum(message.type == "note_on" and message.velocity > 0 for message in before_by_track[track_index]),
                    sum(message.type == "note_on" and message.velocity > 0 for message in after_by_track[track_index]),
                )
                self.assertEqual(
                    sum(message.type == "lyrics" for message in before_by_track[track_index]),
                    sum(message.type == "lyrics" for message in after_by_track[track_index]),
                )

            after_score = read_score(candidate)
            first = note_by_pitch(after_score, 62)
            second = note_by_pitch(after_score, 64)
            self.assertEqual((first["onset_tick"], first["duration_tick"]), (120, 600))
            self.assertEqual(second["duration_tick"], 360)
            self.assertEqual(first["velocity"], 91)
            self.assertEqual(second["velocity"], 73)
            self.assertEqual(first["channel"], 2)
            self.assertIsNone(first["lyric"])
            self.assertEqual(second["lyric"], "好")
            lyric_events = [row for track in after_score["tracks"] for row in track["lyric_events"]]
            self.assertEqual([row["text"] for row in lyric_events], ["你", "好"])
            self.assertTrue(any("歌词" in warning for warning in plan["warnings"]))
            self.assertAlmostEqual(first["onset_seconds"], 0.125, places=9)
            self.assertAlmostEqual(first["duration_seconds"], 0.875, places=9)
            self.assertAlmostEqual(second["onset_seconds"], 0.5, places=9)
            self.assertAlmostEqual(second["duration_seconds"], 0.75, places=9)

            repeated = apply_patch(source, plan, root / "outputs")
            self.assertEqual(repeated, receipt)
            self.assertEqual(candidate.read_bytes(), Path(native_path(receipt["candidate_path"])).read_bytes())
            self.assertEqual(source.read_bytes(), original)

    def test_explicit_move_attached_lyrics_preserves_unique_association_by_default_noop(self):
        with self.temporary("agent-score-move-lyrics-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "input.mid")
            score = read_score(source)
            first = note_by_pitch(score, 60)
            edits = [{
                "note_id": first["note_id"],
                "onset_tick": 120,
                "duration_tick": 300,
            }]

            default_plan = plan_patch(source, score["source_sha256"], edits)
            self.assertNotIn("move_attached_lyrics", default_plan)
            self.assertNotIn(
                "moved_attached_lyrics",
                default_plan["structural_changes"],
            )
            default_receipt = apply_patch(source, default_plan, root / "default")
            default_score = read_score(default_receipt["candidate_path"])
            self.assertIsNone(note_by_pitch(default_score, 60)["lyric"])

            moved_plan = plan_patch(
                source,
                score["source_sha256"],
                edits,
                move_attached_lyrics=True,
            )
            self.assertTrue(moved_plan["move_attached_lyrics"])
            moved = moved_plan["structural_changes"]["moved_attached_lyrics"]
            self.assertEqual(len(moved), 1)
            self.assertEqual(moved[0]["text"], "你")
            self.assertEqual((moved[0]["before_tick"], moved[0]["after_tick"]), (0, 120))
            self.assertEqual(moved[0]["association"], "unique")

            receipt = apply_patch(source, moved_plan, root / "moved")
            candidate = read_score(receipt["candidate_path"])
            first_after = note_by_pitch(candidate, 60)
            self.assertEqual(first_after["onset_tick"], 120)
            self.assertEqual(first_after["lyric"], "你")
            self.assertEqual(note_by_pitch(candidate, 64)["lyric"], "好")
            lyric_events = candidate["tracks"][1]["lyric_events"]
            self.assertEqual([(row["tick"], row["text"]) for row in lyric_events], [(120, "你"), (480, "好")])
            with self.assertRaisesRegex(ValueError, "布尔值"):
                plan_patch(
                    source,
                    score["source_sha256"],
                    edits,
                    move_attached_lyrics=1,
                )

    def test_explicit_lyric_move_rejects_ambiguous_onset_ownership(self):
        with self.temporary("agent-score-move-ambiguous-") as temporary:
            root = Path(temporary)
            source = root / "ambiguous-lyric.mid"
            midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
            midi.tracks.append(mido.MidiTrack([
                mido.MetaMessage("set_tempo", tempo=500_000, time=0),
            ]))
            midi.tracks.append(mido.MidiTrack([
                mido.MetaMessage("lyrics", text="双", time=0),
                mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
                mido.Message("note_on", channel=0, note=64, velocity=75, time=0),
                mido.Message("note_off", channel=0, note=60, velocity=0, time=240),
                mido.Message("note_off", channel=0, note=64, velocity=0, time=0),
            ]))
            midi.save(str(source))
            score = read_score(source)
            target = note_by_pitch(score, 60)
            self.assertIsNone(target["lyric"])
            with self.assertRaisesRegex(ValueError, "歌词归属不唯一"):
                plan_patch(
                    source,
                    score["source_sha256"],
                    [{"note_id": target["note_id"], "onset_tick": 120}],
                    move_attached_lyrics=True,
                )

    def test_stale_source_and_tampered_plan_are_rejected(self):
        with self.temporary("agent-score-stale-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "input.mid")
            score = read_score(source)
            plan = plan_patch(source, score["source_sha256"], self.plan_edits(score))
            with self.assertRaisesRegex(ValueError, "SHA-256 已变化"):
                plan_patch(source, "0" * 64, self.plan_edits(score))

            tampered = json.loads(json.dumps(plan))
            tampered["edits"][0]["pitch"] = 63
            with self.assertRaisesRegex(ValueError, "plan_sha256"):
                apply_patch(source, tampered, root / "outputs")

            midi = mido.MidiFile(str(source), charset="utf8")
            midi.tracks[0].insert(3, mido.MetaMessage("text", text="source changed", time=0))
            midi.save(str(source))
            with self.assertRaisesRegex(ValueError, "当前源文件"):
                apply_patch(source, plan, root / "outputs")
            self.assertFalse((root / "outputs").exists())

    def test_duplicate_unknown_extra_and_bool_fields_are_rejected(self):
        with self.temporary("agent-score-invalid-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "input.mid")
            score = read_score(source)
            note_id = score["notes"][0]["note_id"]
            cases = [
                ([{"note_id": note_id, "pitch": 61}, {"note_id": note_id, "duration_tick": 240}], "重复"),
                ([{"note_id": "note-unknown", "pitch": 61}], "未知"),
                ([{"note_id": note_id, "pitch": 61, "velocity": 1}], "不支持字段"),
                ([{"note_id": note_id, "pitch": True}], "布尔"),
                ([{"note_id": note_id, "pitch": 61.0}], "整数"),
                ([{"note_id": note_id, "onset_tick": False}], "布尔"),
                ([{"note_id": note_id, "duration_tick": 0}], "范围内"),
                ([{"note_id": note_id}], "没有提供"),
            ]
            for edits, reason in cases:
                with self.subTest(reason=reason):
                    with self.assertRaisesRegex(ValueError, reason):
                        plan_patch(source, score["source_sha256"], edits)
            with self.assertRaisesRegex(ValueError, "非空数组"):
                plan_patch(source, score["source_sha256"], [])

    def test_mixed_note_on_off_teardown_is_not_misclassified_as_duration(self):
        with self.temporary("agent-score-noteonzero-") as temporary:
            source = write_duration_sequence_fixture(Path(temporary) / "note-on-zero.mid")
            score = read_score(source)
            expected_durations = sorted(note["duration_tick"] for note in score["notes"])
            self.assertEqual(expected_durations, [100, 300])
            plan = plan_patch(
                source,
                score["source_sha256"],
                [{"note_id": score["notes"][0]["note_id"], "pitch": 61}],
            )
            self.assertTrue(plan["edits"])

    def test_rejects_pairing_ambiguity_and_new_overlap(self):
        with self.temporary("agent-score-ambiguity-") as temporary:
            root = Path(temporary)
            ambiguous = write_overlap_fixture(root / "ambiguous.mid")
            ambiguous_score = read_score(ambiguous)
            self.assertTrue(any(note["pairing_ambiguous"] for note in ambiguous_score["notes"]))
            with self.assertRaisesRegex(ValueError, "配对"):
                plan_patch(
                    ambiguous,
                    ambiguous_score["source_sha256"],
                    [{"note_id": ambiguous_score["notes"][0]["note_id"], "pitch": 61}],
                )

            source = write_main_fixture(root / "overlap-edit.mid")
            score = read_score(source)
            second = note_by_pitch(score, 64)
            first = note_by_pitch(score, 60)
            edit = [{"note_id": second["note_id"], "onset_tick": 200}]
            if second["pitch"] != first["pitch"]:
                edit[0]["pitch"] = first["pitch"]
            with self.assertRaisesRegex(ValueError, "重叠"):
                plan_patch(source, score["source_sha256"], edit)

    def test_receipt_and_candidate_tampering_or_wrong_collisions_are_not_trusted(self):
        with self.temporary("agent-score-tamper-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "input.mid")
            score = read_score(source)
            plan = plan_patch(source, score["source_sha256"], self.plan_edits(score))
            output = root / "outputs"
            receipt = apply_patch(source, plan, output)
            candidate = Path(native_path(receipt["candidate_path"]))
            receipt_file = Path(native_path(receipt["receipt_path"]))
            good_candidate = candidate.read_bytes()
            good_receipt = receipt_file.read_bytes()

            candidate.write_bytes(good_candidate + b"tampered")
            with self.assertRaisesRegex(ValueError, "不同内容"):
                apply_patch(source, plan, output)
            candidate.write_bytes(good_candidate)

            value = json.loads(receipt_file.read_text(encoding="utf-8"))
            value["candidate_sha256"] = "0" * 64
            receipt_file.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "receipt"):
                apply_patch(source, plan, output)
            receipt_file.write_bytes(good_receipt)

            self.assertEqual(apply_patch(source, plan, output), receipt)
            candidate.write_bytes(b"different collision")
            with self.assertRaisesRegex(ValueError, "不同内容"):
                apply_patch(source, plan, output)
            self.assertEqual(candidate.read_bytes(), b"different collision")

    def test_schema_and_source_are_checked_before_publishing(self):
        with self.temporary("agent-score-schema-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "input.mid")
            score = read_score(source)
            plan = plan_patch(source, score["source_sha256"], self.plan_edits(score))
            for mutation, reason in (
                (lambda value: value.pop("diff"), "字段不完整"),
                (lambda value: value.update(extra=True), "字段不完整"),
                (lambda value: value.update(schema_version=99), "schema"),
            ):
                with self.subTest(reason=reason):
                    value = json.loads(json.dumps(plan))
                    mutation(value)
                    with self.assertRaisesRegex(ValueError, reason):
                        apply_patch(source, value, root / "outputs")


if __name__ == "__main__":
    unittest.main()
