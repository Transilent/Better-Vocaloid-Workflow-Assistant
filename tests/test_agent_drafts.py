"""Contract tests for the MCP/GUI shared style-draft bridge.

These tests exercise the public ``agent_drafts`` boundary with a tiny real SVP
fixture.  They make no model, network, synthesis, or user-state calls.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src"
sys.path.insert(0, str(SRC))

from agent_drafts import plan_from_draft, publish_draft, read_draft
from common import native_path
from style_drafts import DraftConflict, DraftError, DraftStore


SV_QUARTER = 705600000


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def write_svp(path):
    """One 120 BPM vocal track with two adjacent complete notes."""
    document = {
        "version": "1.0.0",
        "uuid": "agent-drafts-fixture",
        "library": [{"name": "keep", "private": 7}],
        "renderConfig": {"setting": "keep"},
        "time": {
            "meter": [{"index": 0, "numerator": 4, "denominator": 4}],
            "tempo": [{"position": 0, "bpm": 120}],
        },
        "tracks": [
            {
                "name": "Lead Vocal",
                "mainRef": {
                    "blickOffset": 0,
                    "pitchOffset": 0,
                    "isInstrumental": False,
                    "database": {"name": "Test Bank", "license": "keep"},
                    "voice": {"preset": "keep"},
                },
                "mainGroup": {
                    "uuid": "lead-group",
                    "notes": [
                        {
                            "onset": 0,
                            "duration": SV_QUARTER,
                            "pitch": 60,
                            "lyrics": "春",
                            "attributes": {"keep": 1},
                        },
                        {
                            "onset": SV_QUARTER,
                            "duration": SV_QUARTER,
                            "pitch": 62,
                            "lyrics": "风",
                            "attributes": {"keep": 2},
                        },
                    ],
                    "parameters": {},
                },
                "groups": [],
            }
        ],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\x00"
    )
    return path


def engine_region(start, end, detail=None):
    return {
        "start": start,
        "end": end,
        "preset": "gentle",
        "strength": 0.7,
        "detail": copy.deepcopy(detail or {
            "onset": 1.0,
            "sustain": 1.0,
            "connection": 1.0,
            "release": 1.0,
        }),
        "pitch": True,
    }


def draft_document(input_sha256, regions):
    return {
        "schema_version": 1,
        "input_sha256": input_sha256,
        "style_regions": copy.deepcopy(regions),
        "mode": "regions",
        "selection": [0.5, 1.0],
        "selected_index": 0,
        "track_indices": [0],
        "tool": {
            "performance_style": "gentle",
            "style_strength": 0.7,
            "style_detail": {
                "onset": 1.0,
                "sustain": 1.0,
                "connection": 1.0,
                "release": 1.0,
            },
            "style_pitch": True,
            "style_technique": "none",
            "technique_strength": 0.6,
            "technique_pitch": True,
        },
        "editor_intent": "new",
    }


class AgentDraftTests(unittest.TestCase):
    def setUp(self):
        work = APP / "work"
        work.mkdir(parents=True, exist_ok=True)
        self.root = Path(
            tempfile.mkdtemp(prefix="agent-drafts-", dir=str(work))
        ).resolve()
        self.addCleanup(self.cleanup_temporary)
        self.source = write_svp(self.root / "assets" / "draft.svp")
        self.raw = self.source.read_bytes()
        self.input_sha256 = sha256_bytes(self.raw)
        self.store = DraftStore(self.root / "drafts")

    def cleanup_temporary(self):
        target = Path(native_path(self.root)).resolve()
        work = Path(native_path(APP / "work")).resolve()
        self.assertTrue(target.is_relative_to(work))
        self.assertNotEqual(target, work)
        shutil.rmtree(native_path(target))

    def new_draft(self, regions):
        return self.store.write(
            self.input_sha256,
            None,
            draft_document(self.input_sha256, regions),
            writer="gui:test",
        )

    def assert_source_unchanged(self):
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(sha256_bytes(self.source.read_bytes()), self.input_sha256)

    def test_validated_style_plan_is_required_before_publish(self):
        initial = self.new_draft([engine_region(0.5, 1.0)])
        bundle = plan_from_draft(
            self.source,
            self.store.root,
            self.input_sha256,
            initial["revision"],
            "调用方明确选择第二个完整音符的柔和表达。",
        )
        self.assertEqual(bundle["input_sha256"], self.input_sha256)
        self.assertEqual(bundle["revision"], initial["revision"])

        tampered = copy.deepcopy(bundle["plan"])
        tampered["style_regions"][0]["strength"] = 0.15
        with self.assertRaises(DraftError) as raised:
            publish_draft(
                self.source,
                self.store.root,
                self.input_sha256,
                tampered,
                initial["revision"],
                "mcp:tampered",
            )
        self.assertEqual(raised.exception.code, "stale_plan")
        self.assertEqual(self.store.read(self.input_sha256)["revision"], initial["revision"])

        published = publish_draft(
            self.source,
            self.store.root,
            self.input_sha256,
            bundle["plan"],
            initial["revision"],
            "mcp:validated",
        )
        self.assertEqual(published["data"]["mode"], "regions")
        self.assertEqual(published["data"]["track_indices"], [0])
        self.assertEqual(published["data"]["style_regions"], bundle["plan"]["style_regions"])
        # An identical validated payload may reuse the exact existing revision;
        # in that case the storage writer label is intentionally unchanged.
        self.assertEqual(published["revision"], initial["revision"])
        self.assertEqual(published["data"]["_sync"]["writer"], "gui:test")

        snapshot = read_draft(self.source, self.store.root, self.input_sha256)
        self.assertEqual(snapshot, published)
        self.assert_source_unchanged()

    def test_stale_cas_keeps_primary_and_both_recovery_sides(self):
        initial = self.new_draft([engine_region(0.5, 1.0)])
        bundle = plan_from_draft(
            self.source,
            self.store.root,
            self.input_sha256,
            initial["revision"],
            "先发布的完整音符柔和方案。",
        )
        first = publish_draft(
            self.source,
            self.store.root,
            self.input_sha256,
            bundle["plan"],
            initial["revision"],
            "mcp:first",
        )

        external_payload = copy.deepcopy(first["data"])
        external_payload.pop("_sync", None)
        external_payload["style_regions"][0]["detail"]["sustain"] = 0.42
        external = self.store.write(
            self.input_sha256,
            first["revision"],
            external_payload,
            writer="gui:second",
        )
        self.assertNotEqual(external["revision"], first["revision"])

        with self.assertRaises(DraftConflict) as raised:
            publish_draft(
                self.source,
                self.store.root,
                self.input_sha256,
                bundle["plan"],
                first["revision"],
                "mcp:stale",
            )
        error = raised.exception
        self.assertEqual(error.code, "draft_conflict")
        self.assertEqual(error.details["expected_revision"], first["revision"])
        self.assertEqual(error.details["current_revision"], external["revision"])
        recovery = Path(error.details["recovery_path"])
        self.assertTrue(recovery.is_file())
        recovered = json.loads(recovery.read_text(encoding="utf-8"))
        self.assertEqual(recovered["kind"], "style_draft_conflict")
        self.assertEqual(recovered["expected_revision"], first["revision"])
        self.assertEqual(recovered["current_revision"], external["revision"])
        self.assertEqual(recovered["current_data"], external["data"])
        self.assertEqual(
            recovered["incoming_data"]["style_regions"],
            bundle["plan"]["style_regions"],
        )
        self.assertNotEqual(
            recovered["incoming_data"]["style_regions"],
            recovered["current_data"]["style_regions"],
        )
        self.assertEqual(self.store.read(self.input_sha256), external)
        self.assert_source_unchanged()

    def test_gui_subdivision_detail_survives_plan_and_publish(self):
        detail = {
            "onset": 0.125,
            "sustain": 0.3333,
            "connection": 0.625,
            "release": 0.0,
        }
        initial = self.new_draft([engine_region(0.5, 1.0, detail)])
        bundle = plan_from_draft(
            self.source,
            self.store.root,
            self.input_sha256,
            initial["revision"],
            "保留 GUI 中四个独立分段细调值。",
        )
        self.assertEqual(self.store.read(self.input_sha256)["revision"], initial["revision"])
        self.assertEqual(bundle["plan"]["proposal"]["regions"][0]["detail"], detail)
        self.assertEqual(bundle["plan"]["style_regions"][0]["detail"], detail)

        published = publish_draft(
            self.source,
            self.store.root,
            self.input_sha256,
            bundle["plan"],
            initial["revision"],
            "mcp:detail",
        )
        self.assertEqual(published["data"]["style_regions"][0]["detail"], detail)
        self.assert_source_unchanged()

    def test_half_note_draft_is_rejected_without_revision_change(self):
        initial = self.new_draft([engine_region(0.25, 0.35)])
        with self.assertRaises(DraftError) as raised:
            plan_from_draft(
                self.source,
                self.store.root,
                self.input_sha256,
                initial["revision"],
                "这是一个只覆盖半个音符的无效选区。",
            )
        self.assertEqual(raised.exception.code, "incomplete_note_boundary")
        current = self.store.read(self.input_sha256)
        self.assertEqual(current["revision"], initial["revision"])
        self.assertEqual(current["data"], initial["data"])
        self.assert_source_unchanged()


if __name__ == "__main__":
    unittest.main()
