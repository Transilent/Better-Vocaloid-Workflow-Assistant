"""Standalone contracts for the local semantic style worker."""
import copy
import hashlib
import json
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "src"))

import agent_style
from common import native_path

Q = 705600000


def note(onset, duration, pitch, lyric):
    return {
        "onset": onset,
        "duration": duration,
        "pitch": pitch,
        "lyrics": lyric,
        "attributes": {"paramTension": 0.1},
    }


def project(pitch=60):
    return {
        "version": "1.0.0",
        "uuid": "style-worker-fixture",
        "library": [{"name": "keep", "private": 7}],
        "renderConfig": {"setting": "keep"},
        "time": {
            "meter": [{"index": 0, "numerator": 4, "denominator": 4}],
            "tempo": [
                {"position": 0, "bpm": 120},
                {"position": 2 * Q, "bpm": 60},
            ],
        },
        "tracks": [
            {
                "name": "Lead Vocal",
                "mainRef": {
                    "blickOffset": Q,
                    "pitchOffset": 0,
                    "isInstrumental": False,
                    "database": {"name": "Test Bank", "license": "keep"},
                    "voice": {"preset": "keep", "paramTension": 0.0},
                },
                "mainGroup": {
                    "uuid": "lead-group",
                    "notes": [
                        note(0, Q, pitch, "春"),
                        note(Q, Q, 62, "风"),
                    ],
                    "parameters": {
                        "loudness": {"mode": "linear", "points": [0, -2.0, 3 * Q, 1.0]},
                        "unknownVendor": {
                            "mode": "cosine", "points": [0, 4.0, Q, 5.0], "tag": "keep",
                        },
                    },
                },
                "retakes": {"keep": True},
                "groups": [],
            },
            {
                "name": "Harmony Vocal",
                "mainRef": {
                    "blickOffset": 0,
                    "pitchOffset": 0,
                    "isInstrumental": False,
                    "database": {"name": "Harmony Bank"},
                    "voice": {"preset": "keep"},
                },
                "mainGroup": {
                    "uuid": "harmony-group",
                    "notes": [
                        note(Q, Q, 67, "花"),
                        note(4 * Q, Q, 65, "月"),
                    ],
                    "parameters": {},
                },
                "groups": [],
            },
            {
                "name": "Instrumental",
                "mainRef": {
                    "isInstrumental": True,
                    "audio": {"filename": "media/accompaniment.wav", "gain": 1.0},
                },
                "mainGroup": {"notes": [], "parameters": {}},
            },
        ],
    }


def write_svp(path, data=None, *, nul=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        data or project(), ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    if nul:
        payload += b"\x00"
    path.write_bytes(payload)
    return path


def write_wav(path, *, seconds=0.25, rate=8000):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def region(start=0.6, end=0.7, *, preset="gentle", technique="none"):
    return {
        "start": start,
        "end": end,
        "preset": preset,
        "strength": 0.6,
        "style_pitch": False,
        "style_technique": technique,
        "technique_strength": 0.6,
        "technique_pitch": True,
        "rationale": "调用方给出的语义理由",
        "evidence_refs": [],
    }


def proposal(regions=None, tracks=None):
    return {
        "track_indices": [0] if tracks is None else tracks,
        "brief": "把调用方指定的语义落实到完整音符",
        "regions": [region()] if regions is None else regions,
    }


def fake_media_module(relocate=None):
    module = types.ModuleType("agent_media")
    module.relocate_svp_media = relocate or (
        lambda source, candidate, allowed_roots=None: {
            "included": False,
            "reason": "test_has_no_supported_media",
            "moved": [],
        }
    )
    return module


class StyleContextTests(unittest.TestCase):
    def test_chinese_lyrics_tempo_offset_presets_and_stable_ids(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-context-", dir=APP) as temp:
            source = write_svp(Path(temp) / "source.svp")
            context = agent_style.read_context(source, [0])

            self.assertEqual(context["kind"], "vwa.style-context")
            self.assertEqual(context["format"], "svp")
            self.assertEqual(context["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(context["preset_ids"], ["none", "gentle", "sad", "bold"])
            self.assertEqual(context["technique_ids"], ["none", "chinese_opera"])
            self.assertEqual([row["lyric"] for row in context["notes"]], ["春", "风"])
            self.assertTrue(all(row["lyric_known"] for row in context["notes"]))
            self.assertEqual([row["track_index"] for row in context["tracks"]], [0])
            self.assertEqual(context["tracks"][0]["offset_tick"], Q)
            self.assertAlmostEqual(context["notes"][0]["start_seconds"], 0.5, places=12)
            self.assertAlmostEqual(context["notes"][0]["end_seconds"], 1.0, places=12)
            self.assertAlmostEqual(context["notes"][1]["start_seconds"], 1.0, places=12)
            self.assertAlmostEqual(context["notes"][1]["end_seconds"], 2.0, places=12)

            first_ids = [row["note_id"] for row in context["notes"]]
            again = agent_style.read_context(source, [0])
            self.assertEqual([row["note_id"] for row in again["notes"]], first_ids)

            changed = project()
            changed["tracks"][0]["mainGroup"]["notes"][0]["pitch"] = 61
            revised = write_svp(Path(temp) / "revised.svp", changed)
            self.assertNotEqual(
                agent_style.read_context(revised, [0])["notes"][0]["note_id"],
                first_ids[0],
            )

    def test_same_pitch_notes_have_distinct_stable_ids(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-ids-", dir=APP) as temp:
            data = project()
            data["tracks"][0]["mainGroup"]["notes"][1]["pitch"] = 60
            source = write_svp(Path(temp) / "same-pitch.svp", data)
            notes = agent_style.read_context(source, [0])["notes"]
            self.assertEqual(notes[0]["pitch"], notes[1]["pitch"])
            self.assertNotEqual(notes[0]["note_id"], notes[1]["note_id"])
            self.assertEqual(
                [row["note_id"] for row in agent_style.read_context(source, [0])["notes"]],
                [row["note_id"] for row in notes],
            )

    def test_unknown_lyric_and_pitch_are_labelled(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-unknown-", dir=APP) as temp:
            data = project()
            data["tracks"][0]["mainGroup"]["notes"][0]["lyrics"] = "+"
            data["tracks"][0]["mainGroup"]["notes"][0]["pitch"] = None
            source = write_svp(Path(temp) / "unknown.svp", data)
            context = agent_style.read_context(source, [0])
            first = context["notes"][0]
            self.assertFalse(first["lyric_known"])
            self.assertIsNone(first["lyric"])
            self.assertFalse(first["pitch_known"])
            self.assertIsNone(first["pitch"])
            codes = {row["code"] for row in context["unknowns"]}
            self.assertIn("lyric.unknown", codes)
            self.assertIn("pitch.unknown", codes)

    def test_pagination_and_summary_preserve_the_legacy_default_shape(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-pages-", dir=APP) as temp:
            source = write_svp(Path(temp) / "source.svp")
            legacy = agent_style.read_context(source, [0])
            explicit_defaults = agent_style.read_context(
                source, [0], limit=None, offset=0,
                detail="full", include_note_ids=True,
            )
            self.assertEqual(explicit_defaults, legacy)

            page = agent_style.read_context(
                source, [0], limit=1, offset=1,
                detail="notes", include_note_ids=False,
            )
            self.assertEqual(len(page["notes"]), 1)
            self.assertEqual(page["notes"][0]["lyric"], "风")
            self.assertIn("note_id", page["notes"][0])
            self.assertEqual(page["pagination"], {
                "limit": 1,
                "offset": 1,
                "total": 2,
                "returned": 1,
                "has_more": False,
                "next_offset": None,
            })

            summary = agent_style.read_context(
                source, [0], limit=1, offset=0,
                detail="summary", include_note_ids=False,
            )
            self.assertEqual(summary["detail"], "summary")
            self.assertFalse(summary["notes_included"])
            self.assertEqual(summary["notes"], [])
            self.assertEqual(summary["note_summary"]["total"], 2)
            self.assertEqual(summary["note_summary"]["by_track"][0]["lyric_known"], 2)

            with self.assertRaisesRegex(ValueError, "limit"):
                agent_style.read_context(source, [0], limit=0)
            with self.assertRaisesRegex(ValueError, "detail"):
                agent_style.read_context(source, [0], detail="unknown")


class StylePlanTests(unittest.TestCase):
    def test_six_decimal_rounded_boundary_keeps_adjacent_regions_separate(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-rounding-", dir=APP) as temp:
            data = project()
            data["tracks"][0]["mainRef"]["blickOffset"] = 0
            # After the one-second tempo change to 60 BPM this tick is exactly
            # 178.8697916667s, which the public six-decimal form rounds to
            # 178.869792s.
            boundary_tick = int(round((178.8697916667 + 1.0) * Q))
            start_tick = int(round((136.8 + 1.0) * Q))
            end_tick = 203 * Q
            data["tracks"][0]["mainGroup"]["notes"] = [
                note(start_tick, boundary_tick - start_tick, 60, "前"),
                note(boundary_tick, end_tick - boundary_tick, 64, "后"),
            ]
            source = write_svp(Path(temp) / "rounded-boundary.svp", data)
            payload = proposal([
                region(136.800000, 178.869792),
                region(178.869792, 202.000000),
            ])

            plan = agent_style.plan_style(source, payload)
            first, second = plan["regions"]
            self.assertEqual(len(first["aligned_note_ids"]), 1)
            self.assertEqual(len(second["aligned_note_ids"]), 1)
            self.assertFalse(
                set(first["aligned_note_ids"]) & set(second["aligned_note_ids"])
            )
            self.assertAlmostEqual(first["end"], 178.86979166666666, places=12)
            self.assertAlmostEqual(second["start"], first["end"], places=12)

    def test_plan_expands_to_complete_notes_and_emits_legacy_engine_region(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-plan-", dir=APP) as temp:
            source = write_svp(Path(temp) / "source.svp")
            context = agent_style.read_context(source, [0])
            evidence = context["notes"][0]["note_id"]
            payload = proposal()
            payload["regions"][0]["evidence_refs"] = [evidence]

            plan = agent_style.plan_style(source, payload)
            self.assertEqual(plan["kind"], "vwa.style-plan")
            self.assertEqual(plan["source_sha256"], context["source_sha256"])
            self.assertEqual(plan["track_indices"], [0])
            self.assertEqual(len(plan["plan_sha256"]), 64)
            json.loads(json.dumps(plan, ensure_ascii=False, allow_nan=False))
            semantic = plan["regions"][0]
            self.assertAlmostEqual(semantic["requested_start"], 0.6, places=12)
            self.assertAlmostEqual(semantic["requested_end"], 0.7, places=12)
            self.assertAlmostEqual(semantic["start"], 0.5, places=12)
            self.assertAlmostEqual(semantic["end"], 1.0, places=12)
            self.assertIn(evidence, semantic["aligned_note_ids"])
            self.assertEqual(semantic["style_technique"], "none")
            self.assertEqual(semantic["technique_strength"], 0.6)
            self.assertIs(semantic["technique_pitch"], True)
            self.assertEqual(plan["evidence"][0]["ref"], evidence)

            engine_region = plan["style_regions"][0]
            self.assertEqual(
                set(engine_region),
                {"start", "end", "preset", "strength", "detail", "pitch"},
            )
            self.assertEqual(engine_region["detail"], {
                "onset": 1.0, "sustain": 1.0, "connection": 1.0, "release": 1.0,
            })
            self.assertEqual(plan, agent_style.plan_style(source, payload))

    def test_chinese_technique_is_independent_and_passes_engine_fields(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-opera-", dir=APP) as temp:
            source = write_svp(Path(temp) / "source.svp")
            technique_region = region(
                1.05, 1.15, preset="none", technique="chinese_opera",
            )
            technique_region["style_pitch"] = True
            payload = proposal([technique_region])
            plan = agent_style.plan_style(source, payload)
            semantic = plan["regions"][0]
            self.assertAlmostEqual(semantic["start"], 1.0, places=12)
            self.assertAlmostEqual(semantic["end"], 2.0, places=12)
            self.assertEqual(semantic["preset"], "none")
            self.assertEqual(semantic["style_technique"], "chinese_opera")
            engine_region = plan["style_regions"][0]
            self.assertEqual(
                set(engine_region),
                {
                    "start", "end", "preset", "strength", "detail", "pitch",
                    "technique", "technique_strength", "technique_pitch",
                },
            )
            self.assertIs(engine_region["pitch"], True)
            self.assertEqual(engine_region["technique"], "chinese_opera")
            self.assertEqual(engine_region["technique_strength"], 0.6)
            self.assertIs(engine_region["technique_pitch"], True)

    def test_silence_instrumental_overlap_and_strict_keys_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-invalid-", dir=APP) as temp:
            source = write_svp(Path(temp) / "source.svp")

            with self.assertRaisesRegex(ValueError, "静音区"):
                agent_style.plan_style(source, proposal([region(2.1, 2.2)]))
            with self.assertRaisesRegex(ValueError, "伴奏轨"):
                agent_style.plan_style(source, proposal(tracks=[2]))

            data = project()
            data["tracks"][1]["mainRef"]["isInstrumental"] = True
            instrumental_source = write_svp(Path(temp) / "instrumental.svp", data)
            with self.assertRaisesRegex(ValueError, "伴奏轨"):
                agent_style.plan_style(
                    instrumental_source, proposal(tracks=[1], regions=[region(0.5, 0.6)]),
                )

            overlap = proposal([
                region(0.55, 0.60),
                region(0.70, 0.80),
            ])
            with self.assertRaisesRegex(ValueError, "重叠并共用完整音符"):
                agent_style.plan_style(source, overlap)

            negative = proposal([region(-0.1, 0.1)])
            with self.assertRaisesRegex(ValueError, "不能为负数"):
                agent_style.plan_style(source, negative)

            bad_strength = proposal()
            bad_strength["regions"][0]["strength"] = 1.1
            with self.assertRaisesRegex(ValueError, "strength"):
                agent_style.plan_style(source, bad_strength)

            unsupported = proposal()
            unsupported["regions"][0]["target"] = 0
            with self.assertRaisesRegex(ValueError, "不支持"):
                agent_style.plan_style(source, unsupported)

            unknown_evidence = proposal()
            unknown_evidence["regions"][0]["evidence_refs"] = ["note_missing"]
            with self.assertRaisesRegex(ValueError, "证据"):
                agent_style.plan_style(source, unknown_evidence)


class StyleRenderTests(unittest.TestCase):
    def setUp(self):
        self.media_calls = []
        self.media_module = fake_media_module()
        self.media_patch = patch.dict(sys.modules, {"agent_media": self.media_module})
        self.media_patch.start()
        self.addCleanup(self.media_patch.stop)

    def make_plan(self, source):
        context = agent_style.read_context(source, [0])
        payload = proposal()
        payload["regions"][0]["evidence_refs"] = [context["notes"][0]["note_id"]]
        return agent_style.plan_style(source, payload)

    def test_tampered_and_stale_plans_are_rejected_before_session_write(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-guard-", dir=APP) as temp:
            root = Path(temp)
            source = write_svp(root / "source.svp")
            plan = self.make_plan(source)

            tampered = copy.deepcopy(plan)
            tampered["regions"][0]["strength"] = 0.9
            output = root / "tampered-output"
            with self.assertRaisesRegex(ValueError, "篡改"):
                agent_style.render_style(source, tampered, output)
            self.assertFalse(output.exists())

            changed = project(pitch=63)
            stale = write_svp(root / "source.svp", changed)
            output = root / "stale-output"
            with self.assertRaisesRegex(ValueError, "源 SVP 已改变"):
                agent_style.render_style(stale, plan, output)
            self.assertFalse(output.exists())

    def test_real_candidate_preserves_source_notes_and_bank_and_retry_is_cached(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-render-", dir=APP) as temp:
            root = Path(temp)
            source = write_svp(root / "source.svp")
            source_bytes = source.read_bytes()
            plan = self.make_plan(source)
            progress = []

            result = agent_style.render_style(
                source, plan, root / "output", progress=progress.append,
            )
            candidate = Path(native_path(result["candidate_path"]))
            self.assertTrue(candidate.is_absolute())
            self.assertEqual(candidate.suffix.lower(), ".svp")
            self.assertNotEqual(candidate, source.resolve())
            self.assertTrue(candidate.is_file())
            self.assertEqual(result["candidate_path"], result["output"])
            self.assertEqual(result["candidate_sha256"], hashlib.sha256(candidate.read_bytes()).hexdigest())
            self.assertEqual(result["source_sha256"], hashlib.sha256(source_bytes).hexdigest())
            self.assertTrue(result["source_unchanged"])
            self.assertTrue(result["receipt_verified"])
            self.assertTrue(progress)
            self.assertNotEqual(candidate, Path(result["session"]) / "inputs/source.svp")

            receipt = Path(native_path(result["receipt"]))
            self.assertTrue(receipt.is_file())
            receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt_document["output_sha256"], result["candidate_sha256"])
            self.assertEqual(receipt_document["source_sha256"], result["source_sha256"])
            self.assertIsInstance(result["partial_reason_counts"], dict)
            self.assertIsInstance(result["partial_remediation"], list)
            self.assertIn("partial_reason_counts", result["report"])
            self.assertEqual(source.read_bytes(), source_bytes)
            json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))

            original = project()
            produced = json.loads(candidate.read_text(encoding="utf-8").rstrip("\x00"))
            self.assertEqual(
                produced["tracks"][0]["mainGroup"]["notes"],
                original["tracks"][0]["mainGroup"]["notes"],
            )
            self.assertEqual(
                produced["tracks"][0]["mainRef"]["database"],
                original["tracks"][0]["mainRef"]["database"],
            )
            self.assertTrue(produced["tracks"][2]["mainRef"].pop("audio")["filename"])
            self.assertTrue(original["tracks"][2]["mainRef"].pop("audio")["filename"])
            self.assertEqual(produced["tracks"][2], original["tracks"][2])

            with patch(
                "tuning_workflow.process_project",
                side_effect=AssertionError("cached result was recomputed"),
            ):
                retried = agent_style.render_style(source, plan, root / "output")
            self.assertTrue(retried["cached"])
            self.assertEqual(retried["candidate_path"], result["candidate_path"])
            self.assertEqual(retried["candidate_sha256"], result["candidate_sha256"])
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_media_relocation_precedes_candidate_sha_and_receipt_update(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-media-", dir=APP) as temp:
            root = Path(temp)
            source = write_svp(root / "source.svp")
            plan = self.make_plan(source)

            def relocate(source_path, candidate_path, allowed_roots=None):
                self.media_calls.append((Path(source_path), Path(candidate_path), allowed_roots))
                candidate_path = Path(candidate_path)
                value = json.loads(candidate_path.read_text(encoding="utf-8").rstrip("\x00"))
                value["tracks"][2]["mainRef"]["audio"]["filename"] = "accompaniment.wav"
                candidate_path.write_text(
                    json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                return {
                    "included": True,
                    "moved": [{"relative_path": "accompaniment.wav"}],
                }

            self.media_module.relocate_svp_media = relocate
            result = agent_style.render_style(source, plan, root / "output")
            candidate = Path(native_path(result["candidate_path"]))
            final_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
            self.assertEqual(result["candidate_sha256"], final_sha)
            self.assertNotEqual(result["candidate_sha256"], result["tuning_candidate_sha256"])
            self.assertTrue(result["receipt_matches_final_candidate"])
            self.assertTrue(result["media_relocation"]["included"])
            self.assertEqual(len(self.media_calls), 1)
            self.assertEqual(self.media_calls[0][0], source.resolve())
            self.assertEqual(
                Path(native_path(self.media_calls[0][1])),
                Path(native_path(candidate)),
            )
            self.assertIn(str(source.parent), self.media_calls[0][2])

            receipt = Path(native_path(result["receipt"]))
            receipt_document = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt_document["output_sha256"], result["candidate_sha256"])
            self.assertEqual(
                receipt_document["tuning_output_sha256"],
                result["tuning_candidate_sha256"],
            )
            self.assertEqual(receipt_document["media_relocation"], result["media_relocation"])
            self.assertEqual(result["tuning_result"]["output_sha256"], result["candidate_sha256"])
            self.assertEqual(
                result["tuning_result"]["serialized_content_sha256"],
                receipt_document["serialized_content_sha256"],
            )

            retried = agent_style.render_style(source, plan, root / "output")
            self.assertTrue(retried["cached"])
            self.assertEqual(retried["candidate_sha256"], result["candidate_sha256"])
            self.assertEqual(retried["tuning_candidate_sha256"], result["tuning_candidate_sha256"])
            self.assertEqual(len(self.media_calls), 2)

    def test_partial_reason_projection_is_actionable(self):
        report = {
            "status": "partial",
            "style": {
                "regions": [{
                    "status": "partial",
                    "tracks": [{
                        "skipped_notes": [
                            {"reason": "short_note_preserved"},
                            {"reason": "short_note_preserved"},
                            {"reason": "protected_note_preserved"},
                        ],
                    }],
                }],
            },
        }
        counts, remediation, advice = agent_style._partial_evidence(report)
        self.assertEqual(counts, {
            "protected_note_preserved": 1,
            "short_note_preserved": 2,
        })
        self.assertEqual([row["reason"] for row in remediation], [
            "protected_note_preserved", "short_note_preserved",
        ])
        self.assertTrue(all(row["action"] for row in remediation))
        self.assertEqual(len(advice), 2)

    def test_render_exposes_partial_reason_counts_from_the_tuning_report(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-partial-", dir=APP) as temp:
            root = Path(temp)
            data = project()
            data["tracks"][0]["mainGroup"]["notes"][1]["duration"] = Q // 10
            source = write_svp(root / "source.svp", data)
            context = agent_style.read_context(source, [0])
            payload = proposal([region(0.55, 1.25)])
            payload["regions"][0]["evidence_refs"] = [
                context["notes"][0]["note_id"], context["notes"][1]["note_id"],
            ]
            plan = agent_style.plan_style(source, payload)

            result = agent_style.render_style(source, plan, root / "output")
            self.assertEqual(result["report"]["status"], "partial")
            self.assertEqual(result["partial_reason_counts"], {"short_note_preserved": 1})
            self.assertEqual(result["report"]["partial_reason_counts"], result["partial_reason_counts"])
            self.assertEqual(result["partial_remediation"][0]["reason"], "short_note_preserved")
            self.assertTrue(result["partial_advice"])

    def test_cancel_is_propagated_before_writing_output(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-cancel-", dir=APP) as temp:
            root = Path(temp)
            source = write_svp(root / "source.svp")
            plan = self.make_plan(source)
            output = root / "cancelled-output"

            def stop():
                raise InterruptedError("stop requested")

            with self.assertRaisesRegex(InterruptedError, "stop requested"):
                agent_style.render_style(source, plan, output, cancel=stop)
            self.assertFalse(output.exists())

            with self.assertRaisesRegex(InterruptedError, "已取消"):
                agent_style.render_style(source, plan, output, cancel=lambda: True)
            self.assertFalse(output.exists())


class StyleMediaAuthorizationTests(unittest.TestCase):
    @staticmethod
    def make_plan(source):
        context = agent_style.read_context(source, [0])
        payload = proposal()
        payload["regions"][0]["evidence_refs"] = [context["notes"][0]["note_id"]]
        return agent_style.plan_style(source, payload)

    def fixture(self, root):
        exchange = root / "exchange"
        inputs = root / "state" / "inputs"
        media = write_wav(exchange / "reference.wav")
        data = project()
        data["tracks"][2]["mainRef"]["audio"]["filename"] = str(media.resolve())
        source = write_svp(inputs / "snapshot.svp", data)
        return source, media

    def test_explicit_exchange_root_allows_imported_absolute_media(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-external-media-", dir=APP) as temp:
            root = Path(temp)
            source, media = self.fixture(root)
            source_bytes = source.read_bytes()
            media_bytes = media.read_bytes()
            plan = self.make_plan(source)

            result = agent_style.render_style(
                source, plan, root / "output", allowed_media_roots=[root / "exchange"],
            )
            candidate = Path(native_path(result["candidate_path"]))
            produced = json.loads(candidate.read_text(encoding="utf-8").rstrip("\x00"))
            copied_name = produced["tracks"][2]["mainRef"]["audio"]["filename"]
            copied = candidate.parent / copied_name
            self.assertEqual(copied_name, "reference.wav")
            self.assertTrue(copied.is_file())
            self.assertEqual(copied.read_bytes(), media_bytes)
            self.assertTrue(result["media_relocation"]["included"])
            self.assertEqual(result["candidate_sha256"], hashlib.sha256(candidate.read_bytes()).hexdigest())
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(media.read_bytes(), media_bytes)

            receipt = json.loads(Path(native_path(result["receipt"])).read_text(encoding="utf-8"))
            self.assertEqual(receipt["output_sha256"], result["candidate_sha256"])
            self.assertEqual(receipt["tuning_output_sha256"], result["tuning_candidate_sha256"])

            import tuning_workflow
            session = Path(native_path(result["session"]))
            self.assertEqual(tuning_workflow.read_session(session)["id"], result["session_id"])
            status = json.loads((session.parent / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "done")
            self.assertEqual(Path(native_path(status["output"])), candidate)

            retried = agent_style.render_style(
                source, plan, root / "output", allowed_media_roots=[root / "exchange"],
            )
            self.assertTrue(retried["cached"])
            self.assertEqual(retried["candidate_sha256"], result["candidate_sha256"])

    def test_absolute_media_outside_explicit_roots_is_rejected_before_rewrite(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-media-denied-", dir=APP) as temp:
            root = Path(temp)
            source, media = self.fixture(root)
            source_bytes = source.read_bytes()
            media_bytes = media.read_bytes()
            plan = self.make_plan(source)

            with self.assertRaises(ValueError) as raised:
                agent_style.render_style(source, plan, root / "output")
            self.assertEqual(getattr(raised.exception, "code", None), "media_path_outside_roots")
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(media.read_bytes(), media_bytes)

            candidates = list((root / "output").rglob("tuned-*.svp"))
            self.assertEqual(len(candidates), 1)
            candidate = json.loads(candidates[0].read_text(encoding="utf-8").rstrip("\x00"))
            self.assertEqual(
                candidate["tracks"][2]["mainRef"]["audio"]["filename"],
                str(media.resolve()),
            )
            self.assertFalse((candidates[0].parent / "reference.wav").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
