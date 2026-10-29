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
from agent_errors import error_payload
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


def note_region(start_note_id, end_note_id, *, preset="gentle", technique="none"):
    value = region(preset=preset, technique=technique)
    value.pop("start")
    value.pop("end")
    value["start_note_id"] = start_note_id
    value["end_note_id"] = end_note_id
    return value


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
            self.assertEqual(semantic["aligned_note_count"], 1)
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
            coverage = plan["coverage"]
            self.assertEqual(coverage["covered_notes"], "1/2")
            self.assertEqual(coverage["selected_note_count"], 2)
            self.assertEqual(coverage["covered_note_count"], 1)
            self.assertEqual(coverage["uncovered_note_count"], 1)
            self.assertFalse(coverage["fully_covered"])
            self.assertEqual(coverage["uncovered_note_ids"], [context["notes"][1]["note_id"]])
            self.assertEqual(coverage["uncovered_gap_count"], 1)
            self.assertEqual(coverage["uncovered_gaps"][0]["note_count"], 1)

    def test_note_id_boundaries_include_complete_endpoints_and_current_identity(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-note-ids-", dir=APP) as temp:
            source = write_svp(Path(temp) / "source.svp")
            context = agent_style.read_context(source, [0])
            first, second = context["notes"]
            payload = proposal([
                note_region(first["note_id"], second["note_id"]),
            ])

            plan = agent_style.plan_style(source, payload)
            semantic = plan["regions"][0]
            normalized = plan["proposal"]["regions"][0]
            aligned = plan["aligned_ranges"][0]
            self.assertEqual(plan["source_sha256"], context["source_sha256"])
            self.assertEqual(semantic["range_mode"], "note_ids")
            self.assertEqual(semantic["start_note_id"], first["note_id"])
            self.assertEqual(semantic["end_note_id"], second["note_id"])
            self.assertAlmostEqual(semantic["start"], 0.5, places=12)
            self.assertAlmostEqual(semantic["end"], 2.0, places=12)
            self.assertEqual(semantic["aligned_note_count"], 2)
            self.assertEqual(
                set(semantic["aligned_note_ids"]), {first["note_id"], second["note_id"]}
            )
            self.assertEqual(normalized["start_note_id"], first["note_id"])
            self.assertEqual(normalized["end_note_id"], second["note_id"])
            self.assertNotIn("start", normalized)
            self.assertNotIn("end", normalized)
            self.assertEqual(aligned["range_mode"], "note_ids")
            self.assertEqual(aligned["note_count"], 2)
            engine_region = plan["style_regions"][0]
            self.assertAlmostEqual(engine_region["start"], 0.5, places=12)
            self.assertAlmostEqual(engine_region["end"], 2.0, places=12)
            self.assertNotIn("start_note_id", engine_region)
            self.assertEqual(plan["coverage"]["covered_notes"], "2/2")
            self.assertTrue(plan["coverage"]["fully_covered"])
            self.assertEqual(plan["coverage"]["uncovered_gaps"], [])
            self.assertEqual(plan["plan_sha256"], agent_style.plan_style(source, payload)["plan_sha256"])

    def test_note_id_boundaries_reject_stale_cross_track_and_reversed_ids(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-note-errors-", dir=APP) as temp:
            source = write_svp(Path(temp) / "source.svp")
            context = agent_style.read_context(source, [0, 1])
            by_lyric = {row["lyric"]: row for row in context["notes"]}

            stale_id = by_lyric["春"]["note_id"]
            revised = project()
            revised["tracks"][0]["mainGroup"]["notes"][0]["pitch"] = 61
            revised_source = write_svp(Path(temp) / "revised.svp", revised)
            with self.assertRaises(agent_style.StyleNoteBoundaryError) as stale:
                agent_style.plan_style(
                    revised_source,
                    proposal([note_region(stale_id, stale_id)]),
                )
            self.assertEqual(stale.exception.code, "style_note_boundary_stale")
            self.assertEqual(
                stale.exception.details["current_source_sha256"],
                agent_style.read_context(revised_source, [0])["source_sha256"],
            )
            self.assertEqual(stale.exception.details["unresolved_note_ids"], [stale_id])
            self.assertFalse(stale.exception.details["changes_applied"])
            stale_payload = error_payload(stale.exception)
            self.assertEqual(stale_payload["code"], "style_note_boundary_stale")
            self.assertFalse(stale_payload["retryable"])
            self.assertIn("vwa_context_read", stale_payload["remediation"])
            self.assertEqual(
                stale_payload["details"]["current_source_sha256"],
                stale.exception.details["current_source_sha256"],
            )

            cross_track = proposal([
                note_region(by_lyric["春"]["note_id"], by_lyric["花"]["note_id"]),
            ], tracks=[0, 1])
            with self.assertRaises(agent_style.StyleNoteBoundaryError) as crossed:
                agent_style.plan_style(source, cross_track)
            self.assertEqual(crossed.exception.code, "style_note_boundary_cross_track")
            self.assertEqual(crossed.exception.details["start_note"]["track_index"], 0)
            self.assertEqual(crossed.exception.details["end_note"]["track_index"], 1)
            self.assertEqual(
                error_payload(crossed.exception)["code"],
                "style_note_boundary_cross_track",
            )

            reversed_ids = proposal([
                note_region(by_lyric["风"]["note_id"], by_lyric["春"]["note_id"]),
            ])
            with self.assertRaises(agent_style.StyleNoteBoundaryError) as reversed_error:
                agent_style.plan_style(source, reversed_ids)
            self.assertEqual(reversed_error.exception.code, "style_note_boundary_order")

            unselected = proposal([
                note_region(by_lyric["花"]["note_id"], by_lyric["花"]["note_id"]),
            ])
            with self.assertRaises(agent_style.StyleNoteBoundaryError) as not_selected:
                agent_style.plan_style(source, unselected)
            self.assertEqual(
                not_selected.exception.code,
                "style_note_boundary_track_not_selected",
            )

    def test_note_id_regions_still_detect_a_shared_complete_note(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-note-overlap-", dir=APP) as temp:
            data = project()
            data["tracks"][0]["mainRef"]["blickOffset"] = 0
            data["tracks"][0]["mainGroup"]["notes"] = [
                note(0, Q, 60, "甲"),
                note(Q, Q, 62, "乙"),
                note(2 * Q, Q, 64, "丙"),
            ]
            source = write_svp(Path(temp) / "note-overlap.svp", data)
            context = agent_style.read_context(source, [0])
            note_ids = {row["lyric"]: row["note_id"] for row in context["notes"]}
            payload = proposal([
                note_region(note_ids["甲"], note_ids["乙"]),
                note_region(note_ids["乙"], note_ids["丙"]),
            ])

            with self.assertRaises(agent_style.StyleRegionOverlap) as raised:
                agent_style.plan_style(source, payload)

            details = raised.exception.details
            self.assertEqual(details["shared_note_ids"], [note_ids["乙"]])
            self.assertEqual(details["region_note_ids_totals"], {
                "previous": 2,
                "current": 2,
            })
            self.assertEqual(details["source_sha256"], context["source_sha256"])

    def test_note_id_boundary_modes_are_strict(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-note-mode-", dir=APP) as temp:
            source = write_svp(Path(temp) / "source.svp")
            first = agent_style.read_context(source, [0])["notes"][0]["note_id"]

            mixed = proposal([note_region(first, first)])
            mixed["regions"][0]["start"] = 0.6
            mixed["regions"][0]["end"] = 0.7
            with self.assertRaisesRegex(ValueError, "只能选择一种边界模式"):
                agent_style.plan_style(source, mixed)

            incomplete = proposal([note_region(first, first)])
            incomplete["regions"][0].pop("end_note_id")
            with self.assertRaisesRegex(ValueError, "必须同时提供"):
                agent_style.plan_style(source, incomplete)

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

    def test_shared_note_overlap_reports_verified_actionable_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-overlap-", dir=APP) as temp:
            data = project()
            data["tracks"][0]["mainRef"]["blickOffset"] = 0
            data["tracks"][0]["mainGroup"]["notes"] = [
                note(0, Q, 60, "甲"),
                note(Q, Q, 62, "乙"),
                note(2 * Q, 2 * Q, 64, "丙"),
            ]
            source = write_svp(Path(temp) / "overlap.svp", data)
            context = agent_style.read_context(source, [0])
            note_ids = {row["lyric"]: row["note_id"] for row in context["notes"]}
            payload = proposal([
                region(0.1, 0.7),
                region(0.6, 2.9),
            ])
            original = copy.deepcopy(payload)

            with self.assertRaises(agent_style.StyleRegionOverlap) as raised:
                agent_style.plan_style(source, payload)

            error = raised.exception
            details = error.details
            self.assertEqual(payload, original)
            self.assertFalse(details["changes_applied"])
            self.assertEqual(details["source_sha256"], context["source_sha256"])
            self.assertEqual(details["shared_note_ids"], [note_ids["乙"]])
            self.assertAlmostEqual(details["conflict"]["seconds"], 0.5, places=12)
            self.assertEqual(
                set(details["region_note_ids"]["previous"]),
                {note_ids["甲"], note_ids["乙"]},
            )
            self.assertEqual(
                set(details["region_note_ids"]["current"]),
                {note_ids["乙"], note_ids["丙"]},
            )
            shared = details["shared_notes"][0]
            self.assertEqual(shared["note_id"], note_ids["乙"])
            self.assertAlmostEqual(shared["start_seconds"], 0.5, places=12)
            self.assertAlmostEqual(shared["end_seconds"], 1.0, places=12)
            self.assertEqual(shared["lyric"], "乙")
            self.assertEqual(details["shared_note_span_seconds"], {
                "start": 0.5, "end": 1.0,
            })

            previous = details["boundary_candidates"]["previous"]
            current = details["boundary_candidates"]["current"]
            self.assertEqual(previous["edit"], {
                "region": "previous", "field": "end", "from": 1.0, "to": 0.5,
            })
            self.assertEqual(current["edit"], {
                "region": "current", "field": "start", "from": 0.5, "to": 1.0,
            })
            for candidate in (previous, current):
                self.assertTrue(candidate["selects_complete_notes"])
                self.assertTrue(candidate["resolves_overlap"])
                self.assertTrue(candidate["preserves_original_note_coverage"])
                self.assertTrue(candidate["usable"])
                self.assertEqual(candidate["lost_note_ids"], [])
            self.assertEqual(
                set(previous["retained_note_ids"]), {note_ids["甲"]},
            )
            self.assertEqual(
                set(current["retained_note_ids"]), {note_ids["丙"]},
            )
            self.assertIn("前一片段 end 改为 0.500000s", str(error))
            self.assertIn("后一片段 start 改为 1.000000s", str(error))
            self.assertIn("本次未修改任何片段或音符", str(error))
            reported = error_payload(error)
            self.assertEqual(reported["code"], "style_region_overlap")
            self.assertEqual(
                reported["details"]["boundary_candidates"], details["boundary_candidates"],
            )
            json.dumps(reported, ensure_ascii=False, allow_nan=False)

            keep_in_current = agent_style.plan_style(source, proposal([
                region(0.1, previous["edit"]["to"]),
                region(0.6, 2.9),
            ]))
            keep_in_previous = agent_style.plan_style(source, proposal([
                region(0.1, 0.7),
                region(current["edit"]["to"], 2.9),
            ]))
            all_ids = set(note_ids.values())
            for plan in (keep_in_current, keep_in_previous):
                first, second = [set(row["aligned_note_ids"]) for row in plan["regions"]]
                self.assertFalse(first & second)
                self.assertEqual(first | second, all_ids)

    def test_overlap_candidate_that_drops_a_real_note_is_not_usable(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-overlap-loss-", dir=APP) as temp:
            data = project()
            data["tracks"][0]["mainRef"]["blickOffset"] = 0
            data["tracks"][0]["mainGroup"]["notes"] = [
                note(0, Q, 60, "甲"),
                note(Q, Q, 62, "乙"),
                note(3 * Q, Q, 64, "丙"),
            ]
            source = write_svp(Path(temp) / "overlap-loss.svp", data)
            context = agent_style.read_context(source, [0])
            note_ids = {row["lyric"]: row["note_id"] for row in context["notes"]}
            payload = proposal([
                region(0.1, 2.5),
                region(0.6, 1.2),
            ])

            with self.assertRaises(agent_style.StyleRegionOverlap) as raised:
                agent_style.plan_style(source, payload)

            details = raised.exception.details
            previous = details["boundary_candidates"]["previous"]
            current = details["boundary_candidates"]["current"]
            self.assertFalse(previous["usable"])
            self.assertFalse(previous["preserves_original_note_coverage"])
            self.assertEqual(previous["lost_note_ids"], [note_ids["丙"]])
            self.assertFalse(current["usable"])
            self.assertIn("两个单侧边界候选均不安全", str(raised.exception))
            self.assertEqual(details["shared_note_ids"], [note_ids["乙"]])

    def test_5000_note_overlap_bounds_each_response_list_without_changing_candidates(self):
        with tempfile.TemporaryDirectory(prefix="agent-style-overlap-5000-", dir=APP) as temp:
            data = project()
            data["tracks"][0]["mainRef"]["blickOffset"] = 0
            data["time"]["tempo"] = [{"position": 0, "bpm": 120}]
            track_notes = [
                note(index * Q, Q, 60, "音") for index in range(5000)
            ]
            track_notes[1000]["lyrics"] = "长" * 4096
            data["tracks"][0]["mainGroup"]["notes"] = track_notes
            source = write_svp(Path(temp) / "overlap-5000.svp", data)
            payload = proposal([
                region(0.1, 2499.6),
                region(500.1, 1000.1),
            ])

            with self.assertRaises(agent_style.StyleRegionOverlap) as raised:
                agent_style.plan_style(source, payload)

            details = raised.exception.details
            limit = agent_style._MAX_STYLE_ERROR_ITEMS
            self.assertEqual(details["note_evidence_limit"], limit)
            self.assertEqual(details["shared_note_ids_total"], 1001)
            self.assertTrue(details["shared_note_ids_truncated"])
            self.assertEqual(details["shared_notes_total"], 1001)
            self.assertTrue(details["shared_notes_truncated"])
            self.assertEqual(len(details["shared_note_ids"]), limit)
            self.assertEqual(len(details["shared_notes"]), limit)
            self.assertEqual(details["region_note_ids_totals"], {
                "previous": 5000,
                "current": 1001,
            })
            self.assertEqual(details["region_note_ids_truncated"], {
                "previous": True,
                "current": True,
            })
            self.assertTrue(all(
                len(details["region_note_ids"][name]) <= limit
                for name in ("previous", "current")
            ))
            first_shared = details["shared_notes"][0]
            self.assertEqual(first_shared["track_index"], 0)
            self.assertAlmostEqual(first_shared["start_seconds"], 500.0, places=12)
            self.assertAlmostEqual(first_shared["end_seconds"], 500.5, places=12)
            self.assertEqual(
                len(first_shared["lyric"]), agent_style._MAX_STYLE_ERROR_TEXT,
            )
            self.assertTrue(first_shared["lyric_truncated"])
            self.assertLessEqual(
                len(first_shared["note_id"]),
                agent_style._MAX_STYLE_ERROR_IDENTIFIER,
            )
            self.assertFalse(first_shared["note_id_truncated"])
            reported = error_payload(raised.exception)
            self.assertLess(
                len(json.dumps(reported, ensure_ascii=False)),
                65536,
            )

            previous = details["boundary_candidates"]["previous"]
            current = details["boundary_candidates"]["current"]
            self.assertFalse(previous["usable"])
            self.assertFalse(previous["preserves_original_note_coverage"])
            self.assertEqual(previous["lost_note_ids_total"], 2999)
            self.assertTrue(previous["lost_note_ids_truncated"])
            self.assertEqual(len(previous["lost_note_ids"]), limit)
            self.assertEqual(previous["candidate_interval"]["note_ids_total"], 1000)
            self.assertTrue(previous["candidate_interval"]["note_ids_truncated"])
            self.assertFalse(current["selects_complete_notes"])
            self.assertFalse(current["usable"])
            candidate_lists = (
                "retained_note_ids",
                "removed_from_edited_region",
                "shared_note_ids_moved_to_unchanged_region",
                "lost_note_ids",
            )
            for candidate in (previous, current):
                for field in candidate_lists:
                    self.assertLessEqual(len(candidate[field]), limit)
                    self.assertIn(field + "_total", candidate)
                    self.assertIn(field + "_truncated", candidate)
                self.assertLessEqual(
                    len(candidate["candidate_interval"]["note_ids"])
                    if candidate["candidate_interval"] else 0,
                    limit,
                )

            paging = details["context_read"]
            self.assertEqual(paging["tool"], "vwa_evidence")
            request = paging["arguments"]["request"]
            self.assertEqual(request["action"], "context_read")
            self.assertEqual(request["track_indices"], [0])
            self.assertEqual(request["detail"], "notes")
            self.assertEqual(request["limit"], agent_style._MAX_CONTEXT_PAGE)
            self.assertEqual(request["offset"], 0)
            self.assertEqual(
                set(paging),
                {"tool", "arguments", "continuation", "verification"},
            )
            continuation = paging["continuation"]
            self.assertEqual(continuation["request"]["action"], "context_read")
            self.assertEqual(continuation["request"]["track_indices"], [0])
            self.assertEqual(continuation["request"]["detail"], "notes")
            self.assertEqual(continuation["request"]["limit"], agent_style._MAX_CONTEXT_PAGE)
            self.assertEqual(continuation["request"]["offset"], "<当前 context_read 响应的 pagination.next_offset>")
            self.assertEqual(continuation["until"], "pagination.has_more 为 false")
            self.assertIn("details.context_read", str(raised.exception))
            json.dumps(reported, ensure_ascii=False, allow_nan=False)

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
            with self.assertRaises(agent_style.StyleRegionOverlap) as raised:
                agent_style.plan_style(source, overlap)
            candidates = raised.exception.details["boundary_candidates"]
            self.assertFalse(candidates["previous"]["usable"])
            self.assertFalse(candidates["current"]["usable"])
            self.assertIn("均不安全", str(raised.exception))
            self.assertIn("本次未修改任何片段或音符", str(raised.exception))

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
