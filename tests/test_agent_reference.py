"""Acceptance tests for deterministic reference audio and F0 evidence."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

import mido
import numpy as np

import agent_reference
from agent_reference import (
    create_audio_excerpt,
    inspect_reference_audio,
    inspect_reference_pitch,
    review_reference_pitch,
)
from common import native_path


def digest(path):
    return hashlib.sha256(Path(native_path(path)).read_bytes()).hexdigest()


def json_ready(value):
    json.dumps(value, ensure_ascii=False, allow_nan=False)


def write_pcm_wav(path, samples, sample_rate=8000, width=2):
    values = np.asarray(samples)
    if values.ndim == 1:
        values = values[:, None]
    channels = values.shape[1]
    values = values.astype(np.int64, copy=False)
    if width == 1:
        raw = np.clip(values + 128, 0, 255).astype(np.uint8).tobytes()
    elif width == 2:
        raw = np.clip(values, -32768, 32767).astype("<i2").tobytes()
    elif width == 3:
        encoded = values.reshape(-1)
        unsigned = encoded & 0xFFFFFF
        triples = np.column_stack((
            unsigned & 0xFF,
            (unsigned >> 8) & 0xFF,
            (unsigned >> 16) & 0xFF,
        )).astype(np.uint8)
        raw = triples.tobytes()
    elif width == 4:
        raw = values.astype("<i4").tobytes()
    else:
        raise AssertionError("fixture width")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(sample_rate)
        stream.setcomptype("NONE", "not compressed")
        stream.writeframes(raw)
    return Path(path)


def write_float_wav(path):
    data = np.array([0.25, -0.25], dtype="<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, 8000, 32000, 4, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"data" + struct.pack("<I", len(data)) + data
    Path(path).write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)


def save_pitch(path, pitch, mask, step=0.01, schema=1, extra=False):
    payload = {
        "midi_pitch": np.asarray(pitch),
        "voiced_mask": np.asarray(mask),
        "time_step": step,
        "schema": schema,
    }
    if extra:
        payload["extra"] = np.array([1], dtype=np.int16)
    np.savez_compressed(path, **payload)
    return Path(path)


def simple_score(path, notes, ticks_per_beat=480):
    midi = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    events = []
    for pitch, onset, duration in notes:
        events.append((onset, 1, mido.Message("note_on", channel=0, note=pitch, velocity=90)))
        events.append((onset + duration, 0, mido.Message("note_off", channel=0, note=pitch, velocity=0)))
    messages = []
    previous = 0
    for tick, _, message in sorted(events, key=lambda row: (row[0], row[1], row[2].note)):
        messages.append(message.copy(time=tick - previous))
        previous = tick
    messages.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(mido.MidiTrack(messages))
    midi.save(str(path))
    return Path(path)


def tempo_change_score(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("set_tempo", tempo=1_000_000, time=480),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", channel=0, note=60, velocity=80, time=480),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=480),
        mido.Message("note_on", channel=0, note=64, velocity=80, time=0),
        mido.Message("note_off", channel=0, note=64, velocity=0, time=480),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.save(str(path))
    return Path(path)


class AgentReferenceAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_header_only_and_range_metrics_are_frame_aligned(self):
        with self.temporary("reference-audio-inspect-") as temporary:
            root = Path(temporary)
            samples = np.column_stack((
                np.full(1000, 1000, dtype=np.int16),
                np.full(1000, -1000, dtype=np.int16),
            ))
            samples[250:375, :] = 0
            source = write_pcm_wav(root / "source.wav", samples, sample_rate=1000, width=2)
            source_sha = digest(source)

            header = inspect_reference_audio(source)
            json_ready(header)
            self.assertFalse(header["frame_analysis_performed"])
            self.assertIsNone(header["metrics"])
            self.assertEqual(header["sample_rate_hz"], 1000)
            self.assertEqual(header["channels"], 2)
            self.assertEqual(header["bits_per_sample"], 16)
            self.assertEqual(header["duration_seconds"], 1.0)

            report = inspect_reference_audio(source, 0.125, 0.5)
            json_ready(report)
            self.assertTrue(report["frame_analysis_performed"])
            self.assertEqual(report["range"]["effective"]["start_frame"], 125)
            self.assertEqual(report["range"]["effective"]["end_frame"], 500)
            self.assertEqual(report["metrics"]["frames_measured"], 375)
            self.assertAlmostEqual(report["metrics"]["silent_frame_fraction"], 125 / 375, places=9)
            self.assertAlmostEqual(report["metrics"]["peak_normalized"], 1000 / 32768, places=9)
            self.assertEqual(digest(source), source_sha)

            for start, end in ((-0.1, 0.2), (0.5, 0.5), (0.5, 0.4), (0.0, 1.1), (0.0, 31.0)):
                with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                    inspect_reference_audio(source, start, end)
            with self.assertRaises(ValueError):
                inspect_reference_audio(source, float("nan"), 0.5)
            with self.assertRaises(ValueError):
                inspect_reference_audio(source, True, 0.5)
    def test_pcm_excerpt_is_exact_retry_safe_and_never_overwrites(self):
        with self.temporary("reference-audio-excerpt-") as temporary:
            root = Path(temporary)
            values = np.arange(1000, dtype=np.int16) - 500
            samples = np.column_stack((values, -values))
            source = write_pcm_wav(root / "voice.wav", samples, sample_rate=1000, width=2)
            source_sha = digest(source)
            output = root / "out"
            refusal = PermissionError("simulated hardlink refusal")
            if os.name == "nt":
                link_patch = patch("agent_reference.os.link", side_effect=refusal)
                rename_patch = patch("agent_reference.os.rename", wraps=os.rename)
            else:
                link_patch = patch("agent_reference.os.link", wraps=os.link)
                rename_patch = patch("agent_reference.os.rename", wraps=os.rename)
            with link_patch, rename_patch as rename:
                receipt = create_audio_excerpt(source, 0.125, 0.5, output)
                if os.name == "nt":
                    self.assertEqual(rename.call_count, 2)
            json_ready(receipt)
            self.assertEqual(receipt["effective_range"]["start_frame"], 125)
            self.assertEqual(receipt["effective_range"]["end_frame"], 500)
            self.assertEqual(receipt["effective_range"]["frame_count"], 375)
            self.assertFalse(receipt["frame_preservation"]["resampled"])
            self.assertFalse(receipt["frame_preservation"]["pitch_changed"])
            candidate = Path(receipt["candidate_path"])
            receipt_path = Path(receipt["receipt_path"])
            self.assertTrue(candidate.is_file())
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(digest(candidate), receipt["candidate_sha256"])
            self.assertEqual(digest(source), source_sha)
            self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), receipt)

            with wave.open(str(source), "rb") as source_stream:
                source_stream.setpos(125)
                expected = source_stream.readframes(375)
            with wave.open(str(candidate), "rb") as candidate_stream:
                self.assertEqual(candidate_stream.getframerate(), 1000)
                self.assertEqual(candidate_stream.getnchannels(), 2)
                self.assertEqual(candidate_stream.getsampwidth(), 2)
                self.assertEqual(candidate_stream.readframes(375), expected)
                self.assertEqual(candidate_stream.getnframes(), 375)

            repeat = create_audio_excerpt(source, 0.125, 0.5, output)
            self.assertEqual(repeat, receipt)
            stable_bytes = candidate.read_bytes()
            candidate.write_bytes(b"independent collision")
            with self.assertRaisesRegex(ValueError, "不同内容"):
                create_audio_excerpt(source, 0.125, 0.5, output)
            self.assertEqual(candidate.read_bytes(), b"independent collision")
            self.assertEqual(digest(source), source_sha)
            candidate.write_bytes(stable_bytes)

    def test_24_bit_pcm_is_explicitly_supported_and_non_pcm_is_rejected(self):
        with self.temporary("reference-audio-width-") as temporary:
            root = Path(temporary)
            samples = np.array([0, 1, -1, 1000, -1000, 8_388_607, -8_388_608], dtype=np.int32)
            source = write_pcm_wav(root / "source24.wav", samples, sample_rate=1000, width=3)
            report = inspect_reference_audio(source, 0.001, 0.005)
            self.assertEqual(report["bits_per_sample"], 24)
            self.assertEqual(report["sample_width_bytes"], 3)
            receipt = create_audio_excerpt(source, 0.001, 0.005, root / "out24")
            with wave.open(str(source), "rb") as original, wave.open(receipt["candidate_path"], "rb") as excerpt:
                original.setpos(receipt["effective_range"]["start_frame"])
                self.assertEqual(excerpt.readframes(receipt["effective_range"]["frame_count"]),
                                 original.readframes(receipt["effective_range"]["frame_count"]))

            floating = root / "float.wav"
            write_float_wav(floating)
            with self.assertRaisesRegex(ValueError, "PCM"):
                inspect_reference_audio(floating)


class AgentReferencePitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_tempo_change_time_origin_and_half_open_frame_selection(self):
        with self.temporary("reference-pitch-tempo-") as temporary:
            root = Path(temporary)
            score = tempo_change_score(root / "score.mid")
            pitch = np.full(320, np.nan, dtype=np.float32)
            pitch[25:125] = 60.0
            pitch[125:225] = 64.0
            mask = np.zeros(320, dtype=bool)
            mask[25:125] = True
            mask[125:225] = True
            sidecar = save_pitch(root / "voice.pitch.npz", pitch, mask, step=0.01)
            report = review_reference_pitch(score, sidecar, track_indices=[1], time_origin=0.25)
            json_ready(report)
            self.assertEqual(report["status"], "reviewed")
            self.assertEqual(report["summary"]["notes_selected"], 2)
            self.assertEqual(report["summary"]["reviewed"], 2)
            self.assertEqual([row["score_onset_seconds"] for row in report["notes"]], [0.5, 1.5])
            self.assertEqual([row["frames"]["start_frame"] for row in report["notes"]], [25, 125])
            self.assertEqual([row["frames"]["end_frame"] for row in report["notes"]], [125, 225])
            self.assertEqual([row["reference"]["voiced_frames"] for row in report["notes"]], [100, 100])
            self.assertTrue(all(abs(row["reference"]["cents_from_score"]) < 1e-6 for row in report["notes"]))
            self.assertEqual(report["pitch"]["provenance"]["status"], "unverified_source")
            self.assertFalse(report["inference_performed"])
            self.assertFalse(report["patch_applied"])
            self.assertEqual(digest(score), report["score"]["sha256"])
            self.assertEqual(digest(sidecar), report["pitch"]["sha256"])
    def test_octave_and_glide_are_stable_conditional_hints(self):
        with self.temporary("reference-pitch-hints-") as temporary:
            root = Path(temporary)
            octave_score = simple_score(root / "octave.mid", [(60, 0, 480)])
            octave_pitch = np.full(100, 72.0, dtype=np.float32)
            octave_mask = np.ones(100, dtype=bool)
            octave_sidecar = save_pitch(root / "octave.pitch.npz", octave_pitch, octave_mask)
            first = review_reference_pitch(octave_score, octave_sidecar)
            second = review_reference_pitch(octave_score, octave_sidecar)
            self.assertEqual(first, second)
            self.assertEqual(first["review_sha256"], second["review_sha256"])
            self.assertEqual(first["notes"][0]["status"], "reviewed")
            octave_hints = [
                hint for hint in first["hints"]
                if hint["kind"] == "reference_octave_discrepancy"
            ]
            self.assertEqual(len(octave_hints), 1)
            self.assertEqual(octave_hints[0]["hint_id"], second["hints"][0]["hint_id"])
            self.assertFalse(octave_hints[0]["automatic_patch"])
            self.assertIsNone(octave_hints[0]["patch_plan"])
            self.assertAlmostEqual(octave_hints[0]["observed"]["cents_from_score"], 1200.0, places=6)

            glide_score = simple_score(root / "glide.mid", [(60, 0, 1920)])
            glide = np.linspace(60.0, 63.0, 200, dtype=np.float32)
            glide_mask = np.ones(200, dtype=bool)
            glide_sidecar = save_pitch(root / "glide.pitch.npz", glide, glide_mask)
            glide_report = review_reference_pitch(glide_score, glide_sidecar)
            self.assertEqual(glide_report["notes"][0]["status"], "unknown")
            self.assertIn("unstable_reference_pitch", glide_report["notes"][0]["reason_codes"])
            glide_hints = [
                hint for hint in glide_report["hints"]
                if hint["kind"] == "reference_glide_or_wide_pitch_motion"
            ]
            self.assertEqual(len(glide_hints), 1)
            self.assertFalse(glide_hints[0]["automatic_patch"])
            self.assertGreater(glide_hints[0]["observed"]["central_10_90_span_semitones"], 2.0)

    def test_polyphony_is_ambiguity_and_unvoiced_reference_stays_unknown(self):
        with self.temporary("reference-pitch-ambiguity-") as temporary:
            root = Path(temporary)
            chord = simple_score(root / "chord.mid", [(60, 0, 480), (64, 0, 480)])
            chord_pitch = np.full(100, 60.0, dtype=np.float32)
            chord_mask = np.ones(100, dtype=bool)
            chord_sidecar = save_pitch(root / "chord.pitch.npz", chord_pitch, chord_mask)
            chord_report = review_reference_pitch(chord, chord_sidecar)
            self.assertEqual(chord_report["status"], "unknown")
            self.assertEqual(chord_report["summary"]["ambiguous"], 2)
            self.assertEqual(chord_report["summary"]["unknown"], 0)
            self.assertEqual(chord_report["hints"], [])
            self.assertTrue(all(row["status"] == "ambiguous" for row in chord_report["notes"]))
            self.assertTrue(all(row["overlap"]["count"] == 1 for row in chord_report["notes"]))
            self.assertTrue(all("polyphonic_overlap" in row["reason_codes"] for row in chord_report["notes"]))

            silent_mask = np.zeros(100, dtype=bool)
            silent_sidecar = save_pitch(root / "silent.pitch.npz", chord_pitch, silent_mask)
            silent_report = review_reference_pitch(chord, silent_sidecar, track_indices=[1])
            self.assertEqual(silent_report["status"], "unknown")
            self.assertTrue(all(row["status"] == "ambiguous" for row in silent_report["notes"]))

            single = simple_score(root / "single.mid", [(60, 0, 480)])
            single_report = review_reference_pitch(single, silent_sidecar)
            self.assertEqual(single_report["status"], "unknown")
            self.assertEqual(single_report["notes"][0]["status"], "unknown")
            self.assertIn("insufficient_voiced_frames", single_report["notes"][0]["reason_codes"])
            self.assertEqual(single_report["hints"], [])

    def test_npz_schema_mask_nan_object_and_size_guards(self):
        with self.temporary("reference-pitch-npz-") as temporary:
            root = Path(temporary)
            score = simple_score(root / "score.mid", [(60, 0, 480)])
            good_pitch = np.full(100, 60.0, dtype=np.float32)
            good_mask = np.ones(100, dtype=bool)

            cases = {}
            nan_pitch = good_pitch.copy()
            nan_pitch[10] = np.nan
            cases["voiced_nan"] = save_pitch(root / "nan.pitch.npz", nan_pitch, good_mask)
            cases["object"] = save_pitch(
                root / "object.pitch.npz",
                np.array([60.0, 61.0], dtype=object),
                np.array([True, True], dtype=bool),
            )
            cases["bad_mask"] = save_pitch(
                root / "mask.pitch.npz",
                good_pitch,
                good_mask.astype(np.uint8),
            )
            cases["bad_schema"] = save_pitch(root / "schema.pitch.npz", good_pitch, good_mask, schema=2)
            cases["bad_step"] = save_pitch(root / "step.pitch.npz", good_pitch, good_mask, step=0.0)
            cases["extra"] = save_pitch(root / "extra.pitch.npz", good_pitch, good_mask, extra=True)
            for name, path in cases.items():
                with self.subTest(name=name), self.assertRaises(ValueError):
                    review_reference_pitch(score, path)

            valid = save_pitch(root / "valid.pitch.npz", good_pitch, good_mask)
            inspection = inspect_reference_pitch(valid)
            json_ready(inspection)
            self.assertEqual(inspection["source_sha256"], digest(valid))
            self.assertEqual(inspection["format"], "pitch-npz-schema1")
            self.assertEqual(inspection["pitch_schema"], 1)
            self.assertEqual(np.dtype(inspection["pitch_dtype"]), np.dtype(np.float32))
            self.assertEqual(np.dtype(inspection["mask_dtype"]), np.dtype(np.bool_))
            self.assertEqual(inspection["time_step_seconds"], 0.01)
            self.assertEqual(inspection["frame_count"], 100)
            self.assertEqual(inspection["voiced_frame_count"], 100)
            self.assertEqual(inspection["voiced_frame_index_range"], {"first": 0, "last_inclusive": 99})
            self.assertEqual(inspection["voiced_midi_pitch_range"], {"minimum_midi": 60.0, "maximum_midi": 60.0})
            self.assertFalse(inspection["arrays_returned"])
            self.assertFalse(inspection["midi_read"])
            self.assertFalse(inspection["inference_performed"])
            self.assertIsNone(inspection["time_origin_seconds"])
            self.assertNotIn("midi_pitch", inspection)
            self.assertNotIn("voiced_mask", inspection)
            with self.assertRaises(ValueError):
                inspect_reference_pitch(cases["object"])

            with patch.object(agent_reference, "MAX_NPZ_MEMBER_BYTES", 1):
                with self.assertRaisesRegex(ValueError, "单个成员"):
                    review_reference_pitch(score, valid)
            with patch.object(agent_reference, "MAX_NPZ_COMPRESSION_RATIO", 0.01):
                with self.assertRaisesRegex(ValueError, "压缩比"):
                    review_reference_pitch(score, valid)

    def test_forged_npy_allocation_is_rejected_before_numpy_load(self):
        with self.temporary('reference-forged-header-') as temporary:
            root=Path(temporary)
            valid=save_pitch(root/'valid.npz',np.ones(10,dtype=np.float32),np.ones(10,dtype=np.bool_))
            with zipfile.ZipFile(valid) as archive:
                members={name:archive.read(name) for name in archive.namelist()}
            for label,shape in (('huge',(10**12,)),('truncated',(10,))):
                header=io.BytesIO()
                np.lib.format.write_array_header_1_0(header,{'descr':'<f4','fortran_order':False,'shape':shape})
                forged=root/(label+'.npz')
                with zipfile.ZipFile(forged,'w') as archive:
                    for name,data in members.items():
                        archive.writestr(name,header.getvalue() if name=='midi_pitch.npy' else data)
                with patch.object(agent_reference.np,'load',side_effect=AssertionError('np.load must not run')):
                    with self.assertRaisesRegex(ValueError,'header|payload'):
                        inspect_reference_pitch(forged)

    def test_pagination_strict_inputs_and_finite_json(self):
        with self.temporary("reference-pitch-page-") as temporary:
            root = Path(temporary)
            score = tempo_change_score(root / "score.mid")
            pitch = np.full(320, 60.0, dtype=np.float32)
            mask = np.ones(320, dtype=bool)
            sidecar = save_pitch(root / "voice.pitch.npz", pitch, mask)
            report = review_reference_pitch(
                score, sidecar, track_indices=[1], time_origin=0.25, limit=1, offset=0
            )
            json_ready(report)
            self.assertEqual(report["summary"]["notes_selected"], 2)
            self.assertEqual(report["pagination"]["returned"], 1)
            self.assertEqual(report["pagination"]["next_offset"], 1)
            self.assertEqual(len(report["notes"]), 1)
            for invalid in (0, 501, True, 1.5):
                with self.subTest(limit=invalid), self.assertRaises(ValueError):
                    review_reference_pitch(score, sidecar, limit=invalid)
            for invalid in ([], [1, 1], [99], "1"):
                with self.subTest(tracks=invalid), self.assertRaises(ValueError):
                    review_reference_pitch(score, sidecar, track_indices=invalid)

            conflicting = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
            conflicting.tracks.append(mido.MidiTrack([
                mido.MetaMessage("set_tempo", tempo=500_000, time=0),
                mido.MetaMessage("set_tempo", tempo=600_000, time=0),
                mido.MetaMessage("end_of_track", time=0),
            ]))
            conflicting.tracks.append(mido.MidiTrack([
                mido.Message("note_on", channel=0, note=60, velocity=90, time=0),
                mido.Message("note_off", channel=0, note=60, velocity=0, time=480),
                mido.MetaMessage("end_of_track", time=0),
            ]))
            conflict_path = root / "conflicting-tempo.mid"
            conflicting.save(str(conflict_path))
            with self.assertRaisesRegex(ValueError, "tempo"):
                review_reference_pitch(conflict_path, sidecar)


if __name__ == "__main__":
    unittest.main(verbosity=2)
