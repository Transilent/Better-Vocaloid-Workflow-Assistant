"""Focused tests for the standalone Export agent worker (no inference)."""

import copy
import errno
import hashlib
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

import mido

import agent_exports
from agent_exports import apply_export, plan_export
from common import native_path


LEGACY_RUNTIME = Path(
    r"C:\Users\DevWind\Documents\Codex\2026-10-08\or-1-2-3-midi4-midi"
    r"\outputs\Better-Vocaloid-Workflow-Assistant\dependencies\export"
)


def digest(path):
    with open(native_path(path), "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def json_ready(value):
    json.dumps(value, ensure_ascii=False, allow_nan=False)


def runtime_path():
    candidates = [
        os.environ.get("VWA_MCP_EXPORT_RUNTIME"),
        str(LEGACY_RUNTIME),
        str(APP / "dependencies/export"),
    ]
    for value in candidates:
        if value and (Path(value) / "libresvip/plugins/mid/midi_converter.py").is_file():
            return value
    return None


EXPORT_RUNTIME = runtime_path()


def write_fixture(path, *, tempo_change=False):
    """Unicode Chinese/Japanese, missing lyric, controller loss and 1000 PPQN."""
    midi = mido.MidiFile(type=1, ticks_per_beat=1000, charset="utf8")
    master = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo 3/4", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0),
        mido.MetaMessage("marker", text="keep-in-source-only", time=0),
    ])
    if tempo_change:
        master.append(mido.MetaMessage("set_tempo", tempo=400_000, time=3001))
    midi.tracks.append(master)
    midi.tracks.append(mido.MidiTrack([
        mido.Message("program_change", channel=0, program=52, time=0),
        mido.Message("control_change", channel=0, control=1, value=64, time=0),
        mido.Message("pitchwheel", channel=0, pitch=100, time=0),
        mido.MetaMessage("lyrics", text="你", time=1),
        mido.Message("note_on", channel=0, note=60, velocity=81, time=0),
        mido.Message("note_off", channel=0, note=60, velocity=0, time=999),
        mido.MetaMessage("lyrics", text="き", time=1),
        mido.Message("note_on", channel=0, note=64, velocity=92, time=0),
        mido.Message("note_off", channel=0, note=64, velocity=0, time=499),
        mido.Message("note_on", channel=0, note=67, velocity=73, time=1),
        mido.Message("note_off", channel=0, note=67, velocity=0, time=499),
    ]))
    with open(native_path(path), "wb") as stream:
        midi.save(file=stream)
    return Path(path)


def plan_digest(plan):
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    text = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AgentExportPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_plan_is_stable_strict_serializable_and_honest_about_losses(self):
        with self.temporary("agent-export-plan-") as temporary:
            source = write_fixture(Path(temporary) / "中文 歌.mid", tempo_change=True)
            before = digest(source)
            plan = plan_export(
                source,
                ["svp", "ustx", "vsqx"],
                {"language": "ja", "default_bpm": 90, "svp_version": "2.0"},
            )
            self.assertEqual(digest(source), before)
            json_ready(plan)
            self.assertEqual(plan["source_path"], str(source.resolve()))
            self.assertEqual(plan["formats"], ["svp", "ustx", "vsqx"])
            self.assertEqual(
                plan["options"],
                {"language": "ja", "default_bpm": 90.0, "svp_version": "2.0"},
            )
            self.assertEqual(plan["plan_sha256"], plan_digest(plan))
            self.assertEqual(plan_export(
                source, ["svp", "ustx", "vsqx"],
                {"language": "ja", "default_bpm": 90, "svp_version": "2.0"},
            ), plan)

            expected = plan["expected"]
            self.assertEqual(expected["note_count"], 3)
            self.assertEqual(expected["explicit_lyric_note_count"], 2)
            self.assertEqual(expected["missing_lyric_note_count"], 1)
            self.assertEqual(expected["lyric_event_count"], 2)
            self.assertEqual(expected["controller_event_count"], 1)
            self.assertEqual(expected["pitchwheel_event_count"], 1)
            self.assertEqual(expected["program_change_count"], 1)
            self.assertEqual(expected["marker_count"], 1)
            self.assertEqual([row["bpm"] for row in expected["tempos"]], [120.0, 150.0])
            self.assertEqual(expected["time_signatures"][0]["numerator"], 3)
            self.assertTrue(set(["你", "き", "la"]).issubset(expected["unique_lyrics"]))
            self.assertFalse(expected["timing_exact"])
            self.assertGreater(expected["max_onset_quantization_ms"], 0)
            self.assertGreater(expected["max_duration_quantization_ms"], 0)

            codes = {row["code"] for row in plan["conversion_losses"]}
            self.assertIn("midi_controllers_not_automation", codes)
            self.assertIn("timing_quantized_to_480_ppqn", codes)
            self.assertIn("missing_lyric_filled", codes)
            self.assertIn("vsqx_lyrics_restored_after_dump", codes)
            self.assertFalse(plan["semantic_model_used"])
            self.assertFalse(plan["synthesized"])

    def test_defaults_and_strict_input_validation(self):
        with self.temporary("agent-export-strict-") as temporary:
            root = Path(temporary)
            source = write_fixture(root / "source.mid")
            self.assertEqual(plan_export(source)["formats"], ["svp"])
            self.assertEqual(plan_export(source)["options"]["language"], "zh")

            with self.assertRaisesRegex(ValueError, "非空列表"):
                plan_export(source, "svp")
            with self.assertRaisesRegex(ValueError, "重复"):
                plan_export(source, ["svp", "svp"])
            with self.assertRaisesRegex(ValueError, "不支持"):
                plan_export(source, ["pdf"])
            for options, reason in (
                ({"bpm": 120}, "未支持"),
                ({"default_bpm": math.nan}, "finite|1.*1000|有限"),
                ({"default_bpm": math.inf}, "finite|1.*1000|有限"),
                ({"default_bpm": True}, "有限"),
                ({"language": "en"}, "zh 或 ja"),
                ({"svp_version": "3.0"}, "1.9"),
                ("not-an-object", "对象"),
            ):
                with self.subTest(options=options):
                    with self.assertRaisesRegex(ValueError, reason):
                        plan_export(source, None, options)

            svp = root / "source.svp"
            with open(native_path(svp), "wb") as stream:
                stream.write(b"{}")
            with self.assertRaisesRegex(ValueError, "must|必须是修正后|mid"):
                plan_export(svp)


    def test_unclosed_note_and_zero_length_after_quantization_are_rejected(self):
        with self.temporary("agent-export-invalid-midi-") as temporary:
            root = Path(temporary)
            unclosed = root / "unclosed.mid"
            midi = mido.MidiFile(type=0, ticks_per_beat=480, charset="utf8")
            midi.tracks.append(mido.MidiTrack([
                mido.MetaMessage("set_tempo", tempo=500_000, time=0),
                mido.Message("note_on", note=60, velocity=80, time=0),
            ]))
            with open(native_path(unclosed), "wb") as stream:
                midi.save(file=stream)
            with self.assertRaisesRegex(ValueError, "未闭合"):
                plan_export(unclosed)

            tiny = root / "tiny.mid"
            midi = mido.MidiFile(type=0, ticks_per_beat=1000, charset="utf8")
            midi.tracks.append(mido.MidiTrack([
                mido.MetaMessage("set_tempo", tempo=500_000, time=0),
                mido.Message("note_on", note=60, velocity=80, time=0),
                mido.Message("note_off", note=60, velocity=0, time=1),
            ]))
            with open(native_path(tiny), "wb") as stream:
                midi.save(file=stream)
            with self.assertRaisesRegex(ValueError, "长度为零"):
                plan_export(tiny)


@unittest.skipUnless(EXPORT_RUNTIME, "optional LibreSVIP export runtime is unavailable")
class AgentExportRealConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_real_unicode_export_replay_and_independent_outputs(self):
        with self.temporary("agent-export-real-") as temporary:
            root = Path(temporary)
            source = write_fixture(root / "中文 歌.mid")
            before = digest(source)
            plan = plan_export(source, ["svp", "ustx", "vsqx"], {"language": "ja"})
            progress = []
            result = apply_export(
                source, plan, root / "输出", export_runtime=EXPORT_RUNTIME,
                progress=progress.append,
            )
            self.assertFalse(result["replay"])
            self.assertTrue(result["source_unchanged"])
            self.assertEqual(digest(source), before)
            self.assertFalse(result["semantic_model_used"])
            self.assertFalse(result["synthesized"])
            self.assertEqual(
                [row["format"] for row in result["candidates"]],
                ["svp", "ustx", "vsqx"],
            )
            self.assertIn("verified svp", progress)
            self.assertIn("verified vsqx", progress)
            self.assertTrue((root / "输出/.export-runtime-cache").is_dir())
            self.assertEqual(result["verified"]["notes"], 3)
            self.assertEqual(result["receipt"]["receipt_sha256"], result["receipt_sha256"])
            json_ready(result)

            for row in result["candidates"]:
                candidate = Path(row["candidate_path"])
                self.assertTrue(candidate.is_file())
                self.assertNotEqual(candidate, source)
                self.assertEqual(candidate.parent, (root / "输出").resolve())
                self.assertEqual(digest(candidate), row["candidate_sha256"])
                self.assertEqual(row["bytes"], candidate.stat().st_size)
                self.assertEqual(row["verified_notes"], 3)
                self.assertEqual(row["verified_lyrics"], 3)
                self.assertTrue(row["timing_verified"])
                self.assertTrue(row["time_signature_verified"])
                self.assertEqual(row["time_signatures"], [[0, 3, 4]])
                self.assertEqual(row["engine"], "LibreSVIP")

            receipt_path = Path(result["receipt_path"])
            self.assertTrue(receipt_path.is_file())
            with open(native_path(receipt_path), encoding="utf-8") as stream:
                saved = json.load(stream)
            self.assertEqual(saved, result["receipt"])

            second = apply_export(
                source, plan, root / "输出", export_runtime=EXPORT_RUNTIME
            )
            self.assertTrue(second["replay"])
            self.assertEqual(second["receipt_sha256"], result["receipt_sha256"])

    def test_tempo_normalization_is_reported_without_hiding_musical_data(self):
        with self.temporary("agent-export-tempo-") as temporary:
            root = Path(temporary)
            source = write_fixture(root / "tempo.mid", tempo_change=True)
            plan = plan_export(source, ["svp", "ustx", "vsqx"])
            self.assertFalse(plan["expected"]["timing_exact"])
            result = apply_export(
                source, plan, root / "outputs", export_runtime=EXPORT_RUNTIME
            )
            for row in result["candidates"]:
                self.assertEqual(row["verified_notes"], 3)
                self.assertIn("expected_tempo", row)
                self.assertIn("output_tempo", row)
                self.assertFalse(row["tempo_verified"])
                self.assertIn("tempo_table_normalized", row["loss_codes"])


    def test_stale_tampered_cancelled_and_colliding_requests_are_rejected(self):
        with self.temporary("agent-export-safety-") as temporary:
            root = Path(temporary)
            source = write_fixture(root / "source.mid")
            plan = plan_export(source, ["svp", "ustx"])
            output = root / "candidates"

            with self.assertRaisesRegex(InterruptedError, "取消"):
                apply_export(
                    source, plan, output, export_runtime=EXPORT_RUNTIME,
                    cancel=lambda: True,
                )
            self.assertFalse(any(output.glob("vwa-export-*")))

            tampered = copy.deepcopy(plan)
            tampered["formats"] = ["svp"]
            with self.assertRaisesRegex(ValueError, "摘要"):
                apply_export(source, tampered, output, export_runtime=EXPORT_RUNTIME)

            rebuilt = plan_export(source, ["svp"])
            tampered = dict(rebuilt)
            tampered["expected"] = dict(rebuilt["expected"], note_count=999)
            tampered["plan_sha256"] = plan_digest(tampered)
            with self.assertRaisesRegex(ValueError, "重新计算|不一致|计划"):
                apply_export(source, tampered, output, export_runtime=EXPORT_RUNTIME)

            changed_plan = copy.deepcopy(plan)
            changed_plan["source_path"] = str((root / "changed.mid").resolve())
            changed_plan.pop("plan_sha256")
            changed_plan["plan_sha256"] = plan_digest(changed_plan)
            with self.assertRaisesRegex(ValueError, "不一致|已改变|重新计算"):
                apply_export(source, changed_plan, output,
                             export_runtime=EXPORT_RUNTIME)

            source_bytes = source.read_bytes()
            with open(native_path(source), "ab") as stream:
                stream.write(b"\n")
            with self.assertRaisesRegex(ValueError, "已改变"):
                apply_export(source, plan, output, export_runtime=EXPORT_RUNTIME)
            with open(native_path(source), "wb") as stream:
                stream.write(source_bytes)

            result = apply_export(
                source, plan, output, export_runtime=EXPORT_RUNTIME
            )
            candidate = Path(result["candidates"][0]["candidate_path"])
            good = candidate.read_bytes()
            with open(native_path(candidate), "wb") as stream:
                stream.write(good + b"collision")
            with self.assertRaisesRegex(ValueError, "不同内容|缺失|哈希"):
                apply_export(source, plan, output, export_runtime=EXPORT_RUNTIME)
            with open(native_path(candidate), "wb") as stream:
                stream.write(good)

            receipt_path = Path(result["receipt_path"])
            document = json.loads(receipt_path.read_text(encoding="utf-8"))
            document["plan_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "receipt"):
                apply_export(source, plan, output, export_runtime=EXPORT_RUNTIME)


class AgentExportPublicationTests(unittest.TestCase):
    def test_windows_sharing_error_retries_and_existing_target_is_never_replaced(self):
        with tempfile.TemporaryDirectory(
            prefix="agent-export-publish-", dir=APP / "work"
        ) as temporary:
            root = Path(temporary)
            target = root / "candidate.svp"
            original_rename = os.rename
            attempts = []

            def local_rename(source_value, target_value):
                attempts.append((source_value, target_value))
                if len(attempts) < 3:
                    error = OSError(errno.EACCES, "sharing violation")
                    error.winerror = 32
                    raise error
                return original_rename(source_value, target_value)

            with patch.object(agent_exports.os, "name", "nt"), patch.object(
                agent_exports.os, "link", side_effect=OSError(errno.EPERM, "no hard links")
            ), patch.object(agent_exports.os, "rename", side_effect=local_rename), patch.object(
                agent_exports.time, "sleep"
            ) as sleep:
                agent_exports._publish_exclusive(target, b"verified bytes")
            self.assertEqual(len(attempts), 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(target.read_bytes(), b"verified bytes")

            with patch.object(agent_exports.os, "name", "nt"), patch.object(
                agent_exports.os, "link", side_effect=OSError(errno.EPERM, "no hard links")
            ), patch.object(agent_exports.os, "rename") as rename:
                agent_exports._publish_exclusive(target, b"replacement")
            rename.assert_not_called()
            self.assertEqual(target.read_bytes(), b"verified bytes")

    def test_missing_runtime_is_explicit(self):
        with tempfile.TemporaryDirectory(
            prefix="agent-export-runtime-", dir=APP / "work"
        ) as temporary:
            root = Path(temporary)
            source = write_fixture(root / "source.mid")
            plan = plan_export(source, ["svp"])
            with self.assertRaisesRegex(FileNotFoundError, "运行库不可用|LibreSVIP"):
                apply_export(
                    source, plan, root / "outputs",
                    export_runtime=root / "missing-runtime",
                )


if __name__ == "__main__":
    unittest.main()
