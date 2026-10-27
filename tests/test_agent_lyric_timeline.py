"""Acceptance tests for the pure lyric-timeline adapter.

No provider, model, synthesis, or MCP transport is used here.
"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

import mido

from agent_lyric_timeline import normalize_timeline, review_timeline
from common import native_path


def digest(path):
    return hashlib.sha256(Path(native_path(path)).read_bytes()).hexdigest()


def write_midi(path, specifications):
    """Write deterministic notes as (track, onset_sec, duration_sec, pitch, lyric)."""
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    highest_track = max((row[0] for row in specifications), default=1)
    for track_index in range(1, highest_track + 1):
        events = []
        rows = [row for row in specifications if row[0] == track_index]
        for _, onset, duration, pitch, lyric in rows:
            onset_tick = round(onset * 960)
            end_tick = round((onset + duration) * 960)
            if lyric is not None:
                events.append((onset_tick, 0, mido.MetaMessage("lyrics", text=lyric, time=0)))
            events.append((onset_tick, 1, mido.Message(
                "note_on", channel=0, note=pitch, velocity=80, time=0,
            )))
            events.append((end_tick, 2, mido.Message(
                "note_off", channel=0, note=pitch, velocity=0, time=0,
            )))
        track = mido.MidiTrack()
        previous = 0
        for tick, _, message in sorted(events, key=lambda item: (item[0], item[1])):
            message.time = tick - previous
            track.append(message)
            previous = tick
        track.append(mido.MetaMessage("end_of_track", time=960))
        midi.tracks.append(track)
    midi.save(str(path))
    return Path(path)


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )
    return path


class NormalizeTimelineTests(unittest.TestCase):
    def test_lrc_preserves_repeated_lines_original_text_and_estimated_ends(self):
        raw = (
            "[ar:测试歌手]\n"
            "[offset:500]\n"
            "[00:01.00][00:03.00]多字词元\n"
            "[00:03.00]多字词元\n"
            "[00:05.50]下一句\n"
            "[00:09.00]作词：署名\n"
        )
        timeline = normalize_timeline(raw, origin={"provider": "file", "version": "unknown"})

        self.assertEqual(timeline["schema"], "vwa-lyric-timeline-v1")
        self.assertEqual(timeline["kind"], "lyric-timeline")
        self.assertEqual(timeline["granularity"], "line")
        self.assertEqual(timeline["origin"]["provider"], "file")
        self.assertEqual(timeline["metadata"]["offset_ms"], 500)
        self.assertFalse(timeline["metadata"]["offset_applied"])
        self.assertTrue(any("offset=500" in warning for warning in timeline["warnings"]))
        self.assertEqual(
            [(row["start_seconds"], row["text"]) for row in timeline["lines"]],
            [(1.0, "多字词元"), (3.0, "多字词元"), (3.0, "多字词元"), (5.5, "下一句")],
        )
        self.assertEqual(timeline["lines"][0]["end_seconds"], 3.0)
        self.assertTrue(timeline["lines"][0]["end_estimated"])
        self.assertIsNone(timeline["lines"][-1]["end_seconds"])
        self.assertTrue(all(row["words"] == [] for row in timeline["lines"]))

    def test_yrc_prefers_words_keeps_multichar_tokens_same_starts_and_flags(self):
        yrc = (
            '{"t":0,"c":[{"tx":"作词: 署名"}]}\n'
            "[1000,900](1000,400,0)多字词元(1400,400,7)尾\n"
            "[1900,300](1900,100,0)作词：署名\n"
            "[2000,400](2000,200,0)你(2000,200,0)好\n"
        )
        source = {
            "code": 200,
            "yrc": {"lyric": yrc},
            "lrc": {"lyric": "[00:09.00]不应优先"},
        }
        before = json.loads(json.dumps(source))
        timeline = normalize_timeline(
            source, origin={"provider": "netease", "song_id": "123", "format": "yrc"},
        )

        self.assertEqual(source, before)
        self.assertEqual(timeline["granularity"], "word")
        self.assertEqual(timeline["metadata"]["source_format"], "yrc")
        self.assertEqual([row["text"] for row in timeline["lines"]], ["多字词元尾", "你好"])
        first = timeline["lines"][0]["words"]
        self.assertEqual([word["text"] for word in first], ["多字词元", "尾"])
        self.assertEqual([word["start_seconds"] for word in first], [1.0, 1.4])
        self.assertEqual([word["end_seconds"] for word in first], [1.4, 1.8])
        self.assertEqual(first[1]["flag"], 7)
        second = timeline["lines"][1]["words"]
        self.assertEqual([word["text"] for word in second], ["你", "好"])
        self.assertEqual([word["start_seconds"] for word in second], [2.0, 2.0])
        self.assertTrue(all(word["id"].startswith("word-") for row in timeline["lines"] for word in row["words"]))

    def test_yrc_missing_falls_back_to_lrc_without_inventing_word_times(self):
        timeline = normalize_timeline({
            "lrc": {"lyric": "[00:01.00]整句参考\n[00:02.00]第二句"},
            "yrc": {"lyric": ""},
        })
        self.assertEqual(timeline["granularity"], "line")
        self.assertEqual(timeline["metadata"]["source_format"], "lrc")
        self.assertEqual([row["text"] for row in timeline["lines"]], ["整句参考", "第二句"])
        self.assertTrue(all(row["words"] == [] for row in timeline["lines"]))

    def test_boundaries_reject_bad_encoding_size_word_count_and_timing(self):
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            normalize_timeline(b"\xff\xfe\x00")
        with self.assertRaisesRegex(ValueError, "4 MiB"):
            normalize_timeline(b"a" * (4 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "无法可靠判断"):
            normalize_timeline("只有文字，没有时间。")
        with self.assertRaisesRegex(ValueError, "所属行"):
            normalize_timeline("[1000,100](900,50,0)坏", format_hint="yrc")
        with self.assertRaisesRegex(ValueError, "有限数值"):
            normalize_timeline("[00:01.00]你", origin={"score": float("nan")})

        words = "".join(f"({index},{1},0)x" for index in range(20_001))
        too_many = f"[0,{20_001}]{words}"
        with self.assertRaisesRegex(ValueError, "20000"):
            normalize_timeline(too_many, format_hint="yrc")




class ReviewTimelineTests(unittest.TestCase):
    def test_review_uses_global_origin_and_independent_window_pagination(self):
        with tempfile.TemporaryDirectory(prefix="lyric-timeline-review-", dir=APP / "work") as temporary:
            root = Path(temporary)
            yrc = (
                "[1000,600](1000,300,0)你(1300,300,0)多字词\n"
                "[2000,500](2000,250,0)好(2250,250,0)结尾"
            )
            timeline = normalize_timeline(
                {"yrc": {"lyric": yrc}},
                origin={"provider": "netease", "song_id": "123", "version": "unverified"},
            )
            timeline_path = write_json(root / "timeline.json", timeline)
            score_path = write_midi(root / "score.mid", [
                (1, 6.0, 0.2, 60, "你"),
                (1, 6.3, 0.2, 62, "多字词"),
                (2, 6.6, 0.2, 64, "好"),
                (2, 40.0, 0.2, 65, "窗外"),
            ])
            before = digest(score_path)
            result = review_timeline(
                score_path, timeline_path, time_origin=5.0,
                start=5.9, end=35.9, limit=1, offset=0,
            )
            after = digest(score_path)
            reference_hash = digest(timeline_path)
            filtered = review_timeline(
                score_path, timeline_path, time_origin=5.0,
                start=5.9, end=35.9, track_indices=[1], limit=20,
            )

        self.assertEqual(before, after)
        self.assertEqual(result["source_sha256"], before)
        self.assertEqual(result["reference_sha256"], reference_hash)
        self.assertEqual(result["explicit_time_origin"], 5.0)
        self.assertEqual(result["window"]["duration_seconds"], 30.0)
        self.assertFalse(result["automatic_patch"])
        self.assertEqual(result["reference_status"]["role"], "reference_not_ground_truth")
        self.assertFalse(result["reference_status"]["original_source_and_version_verified"])
        self.assertEqual(result["summary"]["notes_in_window"], 3)
        self.assertEqual([note["lyric"] for note in result["notes"]], ["你"])
        self.assertEqual([line["id"] for line in result["reference_cues"]["lines"]], ["line-0"])
        self.assertNotIn("words", result["reference_cues"]["lines"][0])
        self.assertEqual(result["reference_cues"]["lines"][0]["word_count"], 2)
        self.assertEqual([word["text"] for word in result["reference_cues"]["words"]], ["你"])
        for section in ("notes", "lines", "words"):
            self.assertEqual(result["pagination"][section]["next_offset"], 1)
            self.assertTrue(result["pagination"][section]["has_more"])
        hint = result["hints"][0]
        self.assertEqual(hint["code"], "unique_exact_yrc_word_reference")
        self.assertEqual(hint["reference_start_seconds_midi"], 6.0)
        self.assertEqual(hint["delta_seconds"], 0.0)
        self.assertEqual(hint["possible_offset"], 0.0)
        self.assertFalse(hint["ambiguous"])

        self.assertEqual(filtered["summary"]["notes_in_window"], 2)
        self.assertTrue(all(note["track_index"] == 1 for note in filtered["notes"]))

    def test_lrc_review_stays_at_line_scope(self):
        with tempfile.TemporaryDirectory(prefix="lyric-lrc-review-", dir=APP / "work") as temporary:
            root = Path(temporary)
            timeline_path = write_json(
                root / "line.json",
                normalize_timeline("[00:01.00]一句歌词\n[00:03.00]下一句"),
            )
            score_path = write_midi(root / "line.mid", [
                (1, 1.2, 0.2, 60, "一句歌词"),
            ])
            result = review_timeline(score_path, timeline_path, start=0.0, end=5.0)

        self.assertEqual(result["timeline_granularity"], "line")
        self.assertEqual(result["reference_cues"]["words"], [])
        self.assertEqual(result["pagination"]["words"]["total"], 0)
        hint = result["hints"][0]
        self.assertEqual(hint["code"], "exact_lrc_line_reference")
        self.assertEqual(hint["reference_scope"], "line")
        self.assertFalse(hint["word_timing_available"])
        self.assertNotIn("possible_offset", hint)
        self.assertNotIn("delta_seconds", hint)
        self.assertIn("line_start_delta_seconds", hint)

    def test_duplicate_exact_words_are_ambiguous_without_offset(self):
        with tempfile.TemporaryDirectory(prefix="lyric-ambiguous-review-", dir=APP / "work") as temporary:
            root = Path(temporary)
            yrc = "[1000,400](1000,200,0)你\n[2000,400](2000,200,0)你"
            timeline_path = write_json(
                root / "ambiguous.json", normalize_timeline(yrc, format_hint="yrc"),
            )
            score_path = write_midi(root / "ambiguous.mid", [
                (1, 1.0, 0.2, 60, "你"),
            ])
            result = review_timeline(score_path, timeline_path, start=0.0, end=5.0)

        hint = result["hints"][0]
        self.assertEqual(hint["code"], "ambiguous_exact_yrc_word_reference")
        self.assertTrue(hint["ambiguous"])
        self.assertEqual(len(hint["candidate_word_ids"]), 2)
        self.assertNotIn("delta_seconds", hint)
        self.assertNotIn("possible_offset", hint)
        self.assertEqual(result["summary"]["unique_possible_offsets"], 0)

    def test_polyphonic_notes_do_not_claim_one_word_timing(self):
        with tempfile.TemporaryDirectory(prefix="lyric-poly-review-", dir=APP / "work") as temporary:
            root = Path(temporary)
            timeline_path = write_json(
                root / "poly.json",
                normalize_timeline("[1000,400](1000,400,0)同", format_hint="yrc"),
            )
            score_path = write_midi(root / "poly.mid", [
                (1, 1.0, 0.3, 60, "同"),
                (2, 1.0, 0.3, 64, "同"),
            ])
            result = review_timeline(score_path, timeline_path, start=0.0, end=5.0)

        self.assertEqual(len(result["notes"]), 2)
        self.assertEqual({hint["code"] for hint in result["hints"]}, {
            "polyphonic_exact_yrc_word_reference",
        })
        self.assertTrue(all(hint["ambiguous"] for hint in result["hints"]))
        self.assertTrue(all("possible_offset" not in hint for hint in result["hints"]))

    def test_window_and_nonfinite_timeline_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="lyric-window-review-", dir=APP / "work") as temporary:
            root = Path(temporary)
            score_path = write_midi(root / "window.mid", [
                (1, 1.0, 0.2, 60, "你"),
            ])
            timeline_path = write_json(
                root / "window.json", normalize_timeline("[00:01.00]你"),
            )
            with self.assertRaisesRegex(ValueError, "start 和 end"):
                review_timeline(score_path, timeline_path)
            with self.assertRaisesRegex(ValueError, "最多 30 秒"):
                review_timeline(score_path, timeline_path, start=0.0, end=30.1)
            with self.assertRaisesRegex(ValueError, "track_indices"):
                review_timeline(
                    score_path, timeline_path, start=0.0, end=5.0,
                    track_indices=["1"],
                )

            bad_text = timeline_path.read_text(encoding="utf-8")
            bad_text = bad_text.replace('"start_seconds": 1.0', '"start_seconds": NaN', 1)
            bad_path = root / "bad.json"
            bad_path.write_text(bad_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "无法解析"):
                review_timeline(score_path, bad_path, start=0.0, end=5.0)


if __name__ == "__main__":
    unittest.main()
