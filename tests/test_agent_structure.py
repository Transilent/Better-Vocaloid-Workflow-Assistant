"""Acceptance tests for safe MIDI structure delete/insert patches."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

import mido

from agent_score import read_score
from agent_structure import apply_structure_patch, plan_structure_patch
from common import file_hash, native_path


def digest(path):
    return hashlib.sha256(Path(native_path(path)).read_bytes()).hexdigest()


def message_signature(message):
    payload = message.dict()
    payload.pop("time", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_rows(path):
    midi = mido.MidiFile(native_path(path), charset="utf8")
    rows = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for event_index, message in enumerate(track):
            tick += message.time
            rows.append((track_index, event_index, tick, message_signature(message)))
    return midi, rows


def write_main_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Conductor", time=0),
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0),
        mido.MetaMessage("marker", text="keep-cue", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("set_tempo", tempo=1_000_000, time=480),
        mido.MetaMessage("end_of_track", time=480),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead", time=0),
        mido.Message("program_change", channel=2, program=41, time=0),
        mido.Message("control_change", channel=2, control=7, value=100, time=0),
        mido.MetaMessage("lyrics", text="你", time=0),
        mido.Message("note_on", channel=2, note=60, velocity=91, time=0),
        mido.Message("note_off", channel=2, note=60, velocity=0, time=480),
        mido.MetaMessage("lyrics", text="好", time=0),
        mido.Message("note_on", channel=2, note=64, velocity=73, time=0),
        mido.Message("note_off", channel=2, note=64, velocity=0, time=480),
        mido.MetaMessage("marker", text="vocal-tail", time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Harmony", time=0),
        mido.MetaMessage("lyrics", text="低", time=0),
        mido.Message("note_on", channel=1, note=48, velocity=67, time=0),
        mido.Message("note_off", channel=1, note=48, velocity=0, time=240),
        mido.MetaMessage("marker", text="second-voice", time=0),
        mido.Message("note_on", channel=1, note=43, velocity=62, time=120),
        mido.Message("note_off", channel=1, note=43, velocity=0, time=120),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    destination = Path(native_path(path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    midi.save(filename=native_path(destination))
    return Path(path)


def write_polyphony_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=960),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("lyrics", text="双", time=0),
        mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
        mido.Message("note_on", channel=0, note=64, velocity=75, time=0),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=240),
        mido.Message("note_off", channel=0, note=64, velocity=0, time=0),
        mido.MetaMessage("end_of_track", time=720),
    ]))
    destination = Path(native_path(path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    midi.save(filename=native_path(destination))
    return Path(path)


def write_plain_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=480),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Voice", time=0),
        mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=240),
        mido.MetaMessage("end_of_track", time=240),
    ]))
    destination = Path(native_path(path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    midi.save(filename=native_path(destination))
    return Path(path)


def write_lyric_window_fixture(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=480),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", channel=0, note=60, velocity=80, time=0),
        mido.MetaMessage("lyrics", text="原", time=300),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=180),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    destination = Path(native_path(path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    midi.save(filename=native_path(destination))
    return Path(path)


def note_by_pitch(score, pitch, track_index=None):
    return next(
        note for note in score["notes"]
        if note["pitch"] == pitch
        and (track_index is None or note["track_index"] == track_index)
    )


def canonical_digest(value):
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def refresh_plan_digest(plan):
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    plan["plan_sha256"] = canonical_digest(payload)
    return plan


def json_ready(value):
    json.dumps(value, ensure_ascii=False, allow_nan=False)


class AgentStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_multi_track_delete_insert_preserves_tempo_meta_and_unmodified_notes(self):
        with self.temporary("agent-structure-main-") as temporary:
            root = Path(temporary)
            source = write_main_fixture(root / "multi.mid")
            source_bytes = source.read_bytes()
            source_sha = digest(source)
            source_score = read_score(source)
            before_midi, before_events = event_rows(source)
            lead_60 = note_by_pitch(source_score, 60, 1)
            lead_64 = note_by_pitch(source_score, 64, 1)
            harmony_48 = note_by_pitch(source_score, 48, 2)
            harmony_43 = note_by_pitch(source_score, 43, 2)
            self.assertEqual((lead_60["lyric"], lead_64["lyric"]), ("你", "好"))
            self.assertEqual(harmony_48["lyric"], "低")

            edits = [
                {
                    "operation": "delete",
                    "note_id": lead_60["note_id"],
                    "lyric_policy": "preserve",
                },
                {
                    "operation": "delete",
                    "note_id": lead_64["note_id"],
                    "lyric_policy": "delete_owned",
                },
                {
                    "operation": "insert",
                    "track_index": 1,
                    "channel": 2,
                    "pitch": 67,
                    "onset_tick": 960,
                    "duration_tick": 180,
                    "velocity": 88,
                    "lyric": "新",
                },
                {
                    "operation": "insert",
                    "track_index": 2,
                    "channel": 1,
                    "pitch": 55,
                    "onset_tick": 600,
                    "duration_tick": 120,
                    "velocity": 65,
                },
            ]
            plan = plan_structure_patch(source, source_sha, edits)
            self.assertEqual(plan, plan_structure_patch(str(source), source_sha, edits))
            self.assertEqual(source.read_bytes(), source_bytes)
            json_ready(plan)
            self.assertEqual(plan["format"], "midi")
            self.assertEqual(plan["track_count"], 3)
            self.assertEqual(plan["source_sha256"], source_sha)
            self.assertFalse(plan["inference_performed"])
            self.assertEqual(len(plan["diff"]["deleted_notes"]), 2)
            self.assertEqual(len(plan["diff"]["inserted_notes"]), 2)
            self.assertEqual(
                [row["op"] for row in plan["diff"]["operations"]],
                ["delete_note", "delete_note", "insert_note", "insert_note"],
            )
            self.assertEqual(
                [row["lyric_removed"] for row in plan["diff"]["deleted_notes"]],
                [False, True],
            )
            self.assertEqual(
                [row["lyric_created"] for row in plan["diff"]["inserted_notes"]],
                [True, False],
            )

            output = root / "outputs"
            receipt = apply_structure_patch(source, plan, output)
            json_ready(receipt)
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(receipt["source_sha256"], source_sha)
            self.assertEqual(receipt["candidate_sha256"], digest(receipt["candidate_path"]))
            self.assertEqual(receipt["plan_sha256"], plan["plan_sha256"])
            self.assertFalse(receipt["inference_performed"])
            receipt_file = Path(native_path(receipt["receipt_path"]))
            self.assertEqual(
                json.loads(receipt_file.read_text(encoding="utf-8")),
                receipt,
            )
            self.assertEqual(receipt["receipt_sha256"], canonical_digest({
                key: value for key, value in receipt.items() if key != "receipt_sha256"
            }))

            candidate = receipt["candidate_path"]
            candidate_score = read_score(candidate)
            candidate_midi, candidate_events = event_rows(candidate)
            self.assertEqual(candidate_midi.type, before_midi.type)
            self.assertEqual(candidate_midi.ticks_per_beat, before_midi.ticks_per_beat)
            self.assertEqual(len(candidate_midi.tracks), len(before_midi.tracks))
            self.assertEqual(candidate_score["timing"]["tempo_events"], source_score["timing"]["tempo_events"])
            self.assertTrue(any(
                message.type == "time_signature"
                for track in candidate_midi.tracks
                for message in track
            ))

            conductor_before = [row[2:] for row in before_events if row[0] == 0]
            conductor_after = [row[2:] for row in candidate_events if row[0] == 0]
            self.assertEqual(conductor_after, conductor_before)
            self.assertIn("keep-cue", [message.text for track in candidate_midi.tracks for message in track if message.type == "marker"])
            self.assertIn("vocal-tail", [message.text for track in candidate_midi.tracks for message in track if message.type == "marker"])

            represented = {
                (note["track_index"], note["channel"], note["pitch"], note["onset_tick"], note["duration_tick"], note["velocity"])
                for note in candidate_score["notes"]
            }
            self.assertNotIn((1, 2, 60, 0, 480, 91), represented)
            self.assertNotIn((1, 2, 64, 480, 480, 73), represented)
            self.assertIn((1, 2, 67, 960, 180, 88), represented)
            self.assertIn((2, 1, 55, 600, 120, 65), represented)
            self.assertIn((2, 1, 48, 0, 240, 67), represented)
            self.assertIn((2, 1, 43, 360, 120, 62), represented)
            inserted_with_lyric = note_by_pitch(candidate_score, 67, 1)
            self.assertEqual(inserted_with_lyric["lyric"], "新")

            lyrics_by_track = []
            for track in candidate_midi.tracks:
                lyrics_by_track.append([
                    message.text for message in track if message.type == "lyrics"
                ])
            self.assertEqual(lyrics_by_track[1], ["你", "新"])
            self.assertEqual(lyrics_by_track[2], ["低"])

    def test_lyric_ownership_is_explicit_and_ambiguous_delete_owned_is_rejected(self):
        with self.temporary("agent-structure-lyrics-") as temporary:
            root = Path(temporary)
            source = write_polyphony_fixture(root / "lyrics.mid")
            score = read_score(source)
            first = note_by_pitch(score, 60, 1)
            second = note_by_pitch(score, 64, 1)
            self.assertIsNone(first["lyric"])
            self.assertIsNone(second["lyric"])
            with self.assertRaisesRegex(ValueError, "歌词归属不唯一"):
                plan_structure_patch(source, score["source_sha256"], [{
                    "operation": "delete",
                    "note_id": first["note_id"],
                    "lyric_policy": "delete_owned",
                }])

            plan = plan_structure_patch(source, score["source_sha256"], [{
                "operation": "delete",
                "note_id": first["note_id"],
                "lyric_policy": "preserve",
            }])
            receipt = apply_structure_patch(source, plan, root / "preserve-output")
            candidate = read_score(receipt["candidate_path"])
            self.assertEqual([note["pitch"] for note in candidate["notes"]], [64])
            midi, _events = event_rows(receipt["candidate_path"])
            self.assertEqual([message.text for message in midi.tracks[1] if message.type == "lyrics"], ["双"])
            self.assertTrue(any("孤立歌词" in warning for warning in plan["warnings"]))

    def test_insert_polyphony_is_allowed_without_lyrics_but_ambiguity_is_refused(self):
        with self.temporary("agent-structure-polyphony-") as temporary:
            root = Path(temporary)
            source = write_plain_fixture(root / "polyphony.mid")
            source_score = read_score(source)
            base = source_score["source_sha256"]
            allowed = [{
                "operation": "insert",
                "track_index": 1,
                "channel": 2,
                "pitch": 67,
                "onset_tick": 0,
                "duration_tick": 120,
                "velocity": 70,
            }]
            plan = plan_structure_patch(source, base, allowed)
            receipt = apply_structure_patch(source, plan, root / "poly-output")
            candidate = read_score(receipt["candidate_path"])
            self.assertTrue(any(
                note["track_index"] == 1
                and note["pitch"] == 67
                and note["onset_tick"] == 0
                for note in candidate["notes"]
            ))

            adjacent = [
                {
                    "operation": "insert",
                    "track_index": 1,
                    "channel": 0,
                    "pitch": 62,
                    "onset_tick": 480,
                    "duration_tick": 60,
                    "velocity": 70,
                },
                {
                    "operation": "insert",
                    "track_index": 1,
                    "channel": 0,
                    "pitch": 62,
                    "onset_tick": 540,
                    "duration_tick": 60,
                    "velocity": 71,
                },
            ]
            adjacent_plan = plan_structure_patch(source, base, adjacent)
            adjacent_receipt = apply_structure_patch(
                source,
                adjacent_plan,
                root / "adjacent-output",
            )
            adjacent_score = read_score(adjacent_receipt["candidate_path"])
            adjacent_notes = sorted(
                (note["onset_tick"], note["duration_tick"], note["velocity"])
                for note in adjacent_score["notes"]
                if note["track_index"] == 1 and note["pitch"] == 62
            )
            self.assertEqual(adjacent_notes, [(480, 60, 70), (540, 60, 71)])

            overlap = [{
                "operation": "insert",
                "track_index": 1,
                "channel": 0,
                "pitch": 60,
                "onset_tick": 0,
                "duration_tick": 120,
                "velocity": 70,
            }]
            with self.assertRaisesRegex(ValueError, "重叠"):
                plan_structure_patch(source, base, overlap)

            competing_note = [{
                "operation": "insert",
                "track_index": 1,
                "channel": 2,
                "pitch": 67,
                "onset_tick": 0,
                "duration_tick": 120,
                "velocity": 70,
                "lyric": "新",
            }]
            with self.assertRaisesRegex(ValueError, "同 onset"):
                plan_structure_patch(source, base, competing_note)

            lyric_source = write_lyric_window_fixture(root / "competing-lyric.mid")
            lyric_score = read_score(lyric_source)
            competing_lyric = [{
                "operation": "insert",
                "track_index": 1,
                "channel": 2,
                "pitch": 67,
                "onset_tick": 300,
                "duration_tick": 60,
                "velocity": 70,
                "lyric": "新",
            }]
            with self.assertRaisesRegex(ValueError, "归属窗口已有歌词|唯一归属"):
                plan_structure_patch(
                    lyric_source,
                    lyric_score["source_sha256"],
                    competing_lyric,
                )

    def test_strict_input_stale_ids_sha_and_limits_are_rejected(self):
        with self.temporary("agent-structure-strict-") as temporary:
            root = Path(temporary)
            source = write_plain_fixture(root / "strict.mid")
            score = read_score(source)
            note = score["notes"][0]
            good_insert = {
                "operation": "insert",
                "track_index": 1,
                "channel": 0,
                "pitch": 62,
                "onset_tick": 480,
                "duration_tick": 120,
                "velocity": 80,
            }
            cases = [
                ([], "非空数组"),
                ([{"operation": "move", "note_id": note["note_id"]}], "只允许"),
                ([{
                    "operation": "delete",
                    "note_id": note["note_id"],
                    "lyric_policy": "preserve",
                    "path": "x",
                }], "字段"),
                ([{
                    "operation": "delete",
                    "note_id": note["note_id"],
                    "lyric_policy": "guess",
                }], "lyric_policy"),
                ([{
                    "operation": "delete",
                    "note_id": note["note_id"],
                    "lyric_policy": ["delete_owned"],
                }], "lyric_policy"),
                ([{**good_insert, "channel": True}], "布尔值"),
                ([{**good_insert, "track_index": 9}], "范围内"),
                ([{**good_insert, "velocity": 0}], "范围内"),
                ([{**good_insert, "pitch": 128}], "范围内"),
                ([{**good_insert, "duration_tick": 0}], "范围内"),
                ([{**good_insert, "lyric": ""}], "非空字符串"),
                ([{**good_insert, "lyric": "x\x00y"}], "NUL"),
                ([{**good_insert, "command": "calc.exe"}], "字段"),
            ]
            for edits, reason in cases:
                with self.subTest(reason=reason):
                    with self.assertRaisesRegex(ValueError, reason):
                        plan_structure_patch(source, score["source_sha256"], edits)

            stale_id = note["note_id"][:-1] + ("0" if note["note_id"][-1] != "0" else "1")
            with self.assertRaisesRegex(ValueError, "未知或过期"):
                plan_structure_patch(source, score["source_sha256"], [{
                    "operation": "delete",
                    "note_id": stale_id,
                    "lyric_policy": "preserve",
                }])
            with self.assertRaisesRegex(ValueError, "SHA-256 已变化"):
                plan_structure_patch(source, "0" * 64, [good_insert])
            svp = root / "not-midi.svp"
            svp.write_bytes(b"{}\x00")
            with self.assertRaisesRegex(ValueError, "标准 MIDI"):
                plan_structure_patch(svp, score["source_sha256"], [good_insert])

    def test_tamper_retry_collision_and_source_staleness_are_not_trusted(self):
        with self.temporary("agent-structure-tamper-") as temporary:
            root = Path(temporary)
            source = write_plain_fixture(root / "source.mid")
            original = source.read_bytes()
            score = read_score(source)
            plan = plan_structure_patch(source, score["source_sha256"], [{
                "operation": "insert",
                "track_index": 1,
                "channel": 0,
                "pitch": 64,
                "onset_tick": 480,
                "duration_tick": 120,
                "velocity": 70,
                "lyric": "后",
            }])
            output = root / "outputs"
            receipt = apply_structure_patch(source, plan, output)
            candidate = Path(native_path(receipt["candidate_path"]))
            receipt_path = Path(native_path(receipt["receipt_path"]))
            good_candidate = candidate.read_bytes()
            good_receipt = receipt_path.read_bytes()
            json_plan = json.loads(json.dumps(plan))
            self.assertEqual(apply_structure_patch(source, json_plan, output), receipt)
            self.assertEqual(source.read_bytes(), original)

            bad_digest_plan = json.loads(json.dumps(plan))
            bad_digest_plan["diff"]["inserted_notes"][0]["pitch"] = 65
            with self.assertRaisesRegex(ValueError, "plan_sha256"):
                apply_structure_patch(source, bad_digest_plan, output)

            forged = json.loads(json.dumps(plan))
            forged["edits"][0]["pitch"] = 65
            refresh_plan_digest(forged)
            with self.assertRaisesRegex(ValueError, "完全不一致"):
                apply_structure_patch(source, forged, output)

            unsupported_edit = json.loads(json.dumps(plan))
            unsupported_edit["edits"][0]["command"] = "not-allowed"
            refresh_plan_digest(unsupported_edit)
            with self.assertRaisesRegex(ValueError, "不支持字段"):
                apply_structure_patch(source, unsupported_edit, output)

            malformed_policy = json.loads(json.dumps(plan))
            malformed_policy["edits"][0]["lyric_policy"] = ["delete_owned"]
            refresh_plan_digest(malformed_policy)
            with self.assertRaisesRegex(ValueError, "lyric_policy"):
                apply_structure_patch(source, malformed_policy, output)

            candidate.write_bytes(good_candidate + b"tampered")
            with self.assertRaisesRegex(ValueError, "不同内容"):
                apply_structure_patch(source, plan, output)
            self.assertEqual(candidate.read_bytes(), good_candidate + b"tampered")
            candidate.write_bytes(good_candidate)

            receipt_value = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_value["candidate_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt_value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "receipt"):
                apply_structure_patch(source, plan, output)
            receipt_path.write_bytes(good_receipt)

            source.write_bytes(original + b"\x00")
            with self.assertRaisesRegex(ValueError, "与计划不一致"):
                apply_structure_patch(source, plan, output)
            source.write_bytes(original)
            self.assertEqual(apply_structure_patch(source, plan, output), receipt)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(candidate.read_bytes(), good_candidate)

    @unittest.skipUnless(os.name == "nt", "Windows long-path behavior")
    def test_windows_long_output_and_hardlink_fallback_use_native_paths(self):
        root = Path(tempfile.mkdtemp(prefix="agent-structure-long-", dir=self.work))
        try:
            source = write_plain_fixture(root / "source.mid")
            score = read_score(source)
            plan = plan_structure_patch(source, score["source_sha256"], [{
                "operation": "delete",
                "note_id": score["notes"][0]["note_id"],
                "lyric_policy": "preserve",
            }, {
                "operation": "insert",
                "track_index": 1,
                "channel": 0,
                "pitch": 67,
                "onset_tick": 480,
                "duration_tick": 120,
                "velocity": 77,
            }])
            segment_a = "a" * 105
            segment_b = "b" * 105
            output = root / segment_a / segment_b
            Path(native_path(output)).mkdir(parents=True, exist_ok=True)
            self.assertGreater(len(str(output)), 260)
            refusal = PermissionError("simulated managed-sandbox hardlink refusal")
            with patch("agent_score.os.link", side_effect=refusal), \
                    patch("agent_score.os.rename", wraps=os.rename) as rename:
                receipt = apply_structure_patch(source, plan, output)
                self.assertEqual(rename.call_count, 2)
            self.assertEqual(file_hash(receipt["candidate_path"]), receipt["candidate_sha256"])
            self.assertTrue(Path(native_path(receipt["candidate_path"])).is_file())
            self.assertTrue(Path(native_path(receipt["receipt_path"])).is_file())
            candidate = read_score(receipt["candidate_path"])
            self.assertEqual([note["pitch"] for note in candidate["notes"]], [67])
        finally:
            shutil.rmtree(native_path(root), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
