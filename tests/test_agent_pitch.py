"""Acceptance tests for managed measured-F0 preservation (no inference)."""
from __future__ import annotations

import copy
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

import numpy as np

import agent_pitch
import pitch_accuracy
from pitch_accuracy import build_absolute_f0_curve
from agent_pitch import (
    apply_pitch_plan,
    build_pitch_plan,
    compare_pitch_samples,
    load_pitch_project,
    restore_native_pitch,
    verify_pitch_plan,
)


LEGACY_RUNTIME = Path(
    r"C:\Users\DevWind\Documents\Codex\2026-10-08\or-1-2-3-midi4-midi"
    r"\outputs\Better-Vocaloid-Workflow-Assistant\dependencies\export"
)


def runtime_path():
    candidates = [
        os.environ.get("VWA_MCP_EXPORT_RUNTIME"),
        str(LEGACY_RUNTIME),
        str(APP / "dependencies/export"),
    ]
    for value in candidates:
        if value and (Path(value) / "libresvip/plugins/mid/midi_converter.py").is_file():
            return Path(value)
    return None


EXPORT_RUNTIME = runtime_path()
if EXPORT_RUNTIME is not None:
    os.environ["VWA_MCP_EXPORT_RUNTIME"] = str(EXPORT_RUNTIME)
    sys.path.insert(0, str(EXPORT_RUNTIME))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_pitch(path, pitch, mask, step=0.01):
    np.savez_compressed(
        path,
        midi_pitch=np.asarray(pitch, dtype=np.float32),
        voiced_mask=np.asarray(mask, dtype=np.bool_),
        time_step=step,
        schema=1,
    )
    return Path(path)


def json_ready(value):
    json.dumps(value, ensure_ascii=False, allow_nan=False)


def pitch_fixtures(root):
    lead = np.full(700, np.nan, dtype=np.float32)
    lead_mask = np.zeros(700, dtype=bool)
    # First note: measured glide plus vibrato at 60 MIDI, while corrected MIDI
    # deliberately says key 64. Absolute F0 must remain authoritative.
    phase = np.arange(100)
    lead[100:200] = 60.0 + np.linspace(-0.35, 0.35, 100) + 0.25 * np.sin(phase * 0.31)
    lead_mask[100:200] = True
    # Second note: longer vibrato and an explicit unvoiced gap inside the note.
    phase = np.arange(200)
    lead[400:600] = 64.0 + np.linspace(-0.45, 0.45, 200) + 0.30 * np.sin(phase * 0.27)
    lead_mask[400:600] = True
    lead[450:470] = np.nan
    lead_mask[450:470] = False
    # Two voiced frames map to the deliberately short note at 2.5 s.
    lead[250] = 62.0
    lead[251] = 62.1
    lead_mask[250:252] = True
    lead_path = save_pitch(Path(root) / "lead.pitch.npz", lead, lead_mask)

    backing = np.full(300, np.nan, dtype=np.float32)
    backing_mask = np.zeros(300, dtype=bool)
    backing[100:130] = 55.0 + 0.2 * np.sin(np.arange(30) * 0.4)
    backing_mask[100:130] = True
    backing_path = save_pitch(Path(root) / "backing.pitch.npz", backing, backing_mask)
    return lead_path, backing_path, lead, lead_mask, backing, backing_mask


def make_project():
    from libresvip.model.base import (
        InstrumentalTrack, Note, Project, SingingTrack, SongTempo, TimeSignature,
    )
    tempo = [SongTempo(position=0, bpm=120), SongTempo(position=1920, bpm=60)]
    meter = [TimeSignature(bar_index=0, numerator=4, denominator=4)]
    lead = SingingTrack(
        title="Lead",
        note_list=[
            Note(start_pos=960, length=960, key_number=64, lyric="你"),
            Note(start_pos=2160, length=48, key_number=62, lyric="呀"),
            Note(start_pos=2880, length=1920, key_number=67, lyric="好"),
            Note(start_pos=4800, length=480, key_number=69, lyric="新"),
        ],
    )
    backing = SingingTrack(
        title="Backing",
        note_list=[Note(start_pos=1440, length=480, key_number=52, lyric="伴")],
    )
    instrumental = InstrumentalTrack(title="Inst", audio_file_path="none.wav", offset=0)
    return Project(
        song_tempo_list=tempo,
        time_signature_list=meter,
        track_list=[instrumental, lead, backing],
    )


class SharedAbsolutePitchCoreTests(unittest.TestCase):
    def clock(self, seconds):
        # A stable synthetic playback clock is sufficient for the pure core.
        return int(round(seconds * 960))

    def inverse_clock(self, ticks):
        return ticks / 960.

    def short_gap_fixture(self):
        pitch = np.full(160, np.nan, dtype=np.float32)
        mask = np.zeros(160, dtype=bool)
        pitch[100:105] = 60. + np.arange(5) * .02
        pitch[106:111] = 60.3 + np.arange(5) * .02
        mask[100:105] = True
        mask[106:111] = True
        return pitch, mask

    def test_unvoiced_gap_defaults_to_silence_break(self):
        pitch, mask = self.short_gap_fixture()
        curve = build_absolute_f0_curve(
            pitch, mask, .01, [(1., 1.2)], self.clock, self.inverse_clock)
        self.assertEqual(len(curve['raw_runs']), 2)
        self.assertEqual(len(curve['segments']), 2)
        self.assertEqual(curve['bridged_gaps'], 0)
        self.assertEqual(curve['gaps_retained_silent'], 1)
        self.assertEqual(curve['retained_gap_samples'][0]['reason'], 'no_explicit_evidence')
        values = dict(curve['points'])
        first_gap_tick = self.clock(1.04) + 1920
        next_tick = self.clock(1.06) + 1920
        self.assertTrue(any(value == -100 for tick, value in values.items()
                            if first_gap_tick < tick < next_tick))

    def test_explicit_same_note_evidence_bridges_only_the_gap(self):
        pitch, mask = self.short_gap_fixture()
        curve = build_absolute_f0_curve(
            pitch, mask, .01, [(1., 1.2)], self.clock, self.inverse_clock,
            bridge_evidence_intervals=[(1.04, 1.06)])
        self.assertEqual(len(curve['raw_runs']), 2)
        self.assertEqual(len(curve['segments']), 1)
        self.assertEqual(curve['bridged_gaps'], 1)
        self.assertEqual(curve['gaps_retained_silent'], 0)
        self.assertGreater(curve['bridge_points'], 0)
        values = dict(curve['points'])
        for frame in np.flatnonzero(mask):
            self.assertEqual(values[self.clock(frame * .01) + 1920],
                             int(round(float(pitch[frame]) * 100)))
        first_tick = self.clock(1.) + 1920
        last_tick = self.clock(1.10) + 1920
        self.assertFalse(any(value == -100 for tick, value in values.items()
                             if first_tick <= tick <= last_tick))
        bridged = [value for tick, value in sorted(values.items())
                   if self.clock(1.04) + 1920 < tick < self.clock(1.06) + 1920]
        self.assertGreater(len(bridged), 1)
        self.assertEqual(bridged, sorted(bridged))

    def test_cross_note_short_gap_is_not_bridged_even_with_evidence(self):
        pitch, mask = self.short_gap_fixture()
        curve = build_absolute_f0_curve(
            pitch, mask, .01, [(1., 1.06), (1.06, 1.12)],
            self.clock, self.inverse_clock,
            bridge_evidence_intervals=[(1.04, 1.06)])
        self.assertEqual(len(curve['raw_runs']), 2)
        self.assertEqual(len(curve['segments']), 2)
        self.assertEqual(curve['bridged_gaps'], 0)
        self.assertEqual(curve['gaps_retained_silent'], 1)
        self.assertEqual(curve['retained_gap_samples'][0]['reason'], 'note_boundary')
        values = dict(curve['points'])
        self.assertTrue(any(value == -100 for tick, value in values.items()
                            if self.clock(1.04) + 1920 < tick < self.clock(1.06) + 1920))

    def test_long_rest_breaks_and_endpoint_guard_follows_tail_trend(self):
        pitch = np.full(240, np.nan, dtype=np.float32)
        mask = np.zeros(240, dtype=bool)
        pitch[100:110] = 60. + np.arange(10) * .05
        pitch[130:140] = 63. + np.arange(10) * .05
        mask[100:110] = True
        mask[130:140] = True
        curve = build_absolute_f0_curve(
            pitch, mask, .01, [(.9, 1.5)], self.clock, self.inverse_clock)
        self.assertEqual(len(curve['raw_runs']), 2)
        self.assertEqual(len(curve['segments']), 2)
        self.assertEqual(curve['bridged_gaps'], 0)
        values = dict(curve['points'])
        first_last = self.clock(1.09) + 1920
        second_first = self.clock(1.30) + 1920
        self.assertEqual(values[first_last], int(round(float(pitch[109]) * 100)))
        self.assertEqual(values[second_first], int(round(float(pitch[130]) * 100)))
        self.assertTrue(any(value == -100 for tick, value in values.items()
                            if first_last < tick < second_first))
        tail_guard = [values[tick] for tick in range(first_last + 1, first_last + 5)]
        self.assertEqual(len(tail_guard), 4)
        self.assertTrue(all(value > values[first_last] for value in tail_guard))

    def test_isolated_spike_is_smoothed_but_sustained_offset_is_retained_for_review(self):
        pitch = np.full(100, 60., dtype=np.float32)
        pitch[40] = 66.
        pitch[60:64] = 66.
        pitch[80:82] = 66.
        mask = np.ones(100, dtype=bool)
        original = pitch.copy()
        curve = build_absolute_f0_curve(
            pitch, mask, .01, [(0., 1.)], self.clock, self.inverse_clock)
        self.assertTrue(np.array_equal(pitch, original, equal_nan=True))
        policy = curve['isolated_spike_policy']
        self.assertEqual(policy['corrected_frames'], 1)
        self.assertEqual(policy['corrections'][0]['frame'], 40)
        self.assertAlmostEqual(policy['corrections'][0]['corrected_midi'], 60., places=6)
        self.assertGreater(policy['retained_large_steps'], 0)
        self.assertTrue(any(row['reason'] == 'sustained_large_offset_review'
                            for row in policy['review_hints']))
        self.assertEqual(
            {row['reason'] for row in policy['review_hints']
             if row['frame'] in (80, 82)},
            {'large_pitch_step_review'})
        values = dict(curve['points'])
        self.assertEqual(values[self.clock(.40) + 1920], 6000)
        self.assertEqual(values[self.clock(.60) + 1920], 6600)
        self.assertEqual(values[self.clock(.62) + 1920], 6600)

    def test_slow_note_center_drift_is_shared_read_only_review(self):
        from libresvip.model.base import Note
        notes = [Note(start_pos=0, length=2400, key_number=60, lyric="戏")]
        intervals, descriptors = pitch_accuracy.pitch_note_descriptors(
            notes, self.inverse_clock, vocal_track_index=0)
        pitch = np.full(250, 60., dtype=np.float32)
        pitch[:8] = 66.
        pitch[8:140] = np.linspace(60., 64., 132)
        pitch[140:242] = 64.
        pitch[242:] = 54.
        original = pitch.copy()
        self.assertLess(
            float(np.max(np.abs(np.diff(pitch[8:140])))) * 100., 300.)
        curve = build_absolute_f0_curve(
            pitch, np.ones(250, dtype=bool), .01, intervals,
            self.clock, self.inverse_clock,
            note_descriptors=descriptors, source_sha256="a" * 64,
            vocal_track_index=0,
        )
        audit = curve["pitch_offset_diagnostics"]
        self.assertEqual(audit["status"], "review_required")
        self.assertEqual(audit["warning_count"], 1)
        warning = audit["warnings"][0]
        self.assertEqual(warning["kind"], "sustained_pitch_offset_review")
        self.assertEqual(warning["review_target"], "note_pitch_or_f0_curve")
        self.assertEqual(warning["note"]["note_index"], 0)
        self.assertTrue(warning["note"]["note_id"].startswith("vocal-0:note-0:"))
        self.assertGreaterEqual(warning["interval"]["duration_seconds"], .16)
        self.assertEqual(warning["source_sha256"], "a" * 64)
        self.assertFalse(warning["truth_determined"])
        self.assertFalse(warning["automatic_change"])
        self.assertGreaterEqual(audit["edge_guard_suppressed_frames"], 8)
        self.assertEqual(dict(curve["points"])[self.clock(1.5) + 1920], 6400)
        self.assertTrue(np.array_equal(pitch, original))

    def test_spike_repair_requires_same_note_interior_and_preserves_opera_edges(self):
        pitch = np.full(200, 60., dtype=np.float32)
        pitch[100] = 66.
        pitch[5] = 66.
        pitch[195] = 66.
        mask = np.ones(200, dtype=bool)
        curve = build_absolute_f0_curve(
            pitch, mask, .01, [(0., 2.)], self.clock, self.inverse_clock)
        policy = curve['isolated_spike_policy']
        self.assertEqual(policy['corrected_frames'], 1)
        self.assertEqual(policy['corrections'][0]['frame'], 100)
        self.assertGreaterEqual(policy['boundary_protected_frames'], 2)
        self.assertTrue(any(row['reason'] == 'boundary_transition_review'
                            for row in policy['review_hints']))
        values = dict(curve['points'])
        self.assertEqual(values[self.clock(.05) + 1920], 6600)
        self.assertEqual(values[self.clock(1.95) + 1920], 6600)
        self.assertEqual(values[self.clock(1.00) + 1920], 6000)

    def test_triple_overlap_stays_ambiguous_owner(self):
        from types import SimpleNamespace
        times = np.arange(0., 1.01, .01)
        owner = pitch_accuracy.assign_note_owners(
            times, [(.1, .8), (.2, .7), (.3, .6)])
        self.assertTrue(np.all(owner[(times >= .3) & (times < .6)] == -2))
        pitch = np.full(times.size, 60., dtype=np.float32)
        pitch[40] = 66.
        mask = np.ones(times.size, dtype=bool)
        overlap_intervals = [(.1, .8), (.2, .7), (.3, .6)]
        overlap_notes = [
            SimpleNamespace(start_pos=round(start * 960), end_pos=round(end * 960),
                            length=round((end - start) * 960), key_number=60, lyric="合")
            for start, end in overlap_intervals
        ]
        intervals, descriptors = pitch_accuracy.pitch_note_descriptors(
            overlap_notes, self.inverse_clock, vocal_track_index=0)
        curve = build_absolute_f0_curve(
            pitch, mask, .01, intervals, self.clock, self.inverse_clock,
            note_descriptors=descriptors, source_sha256="b" * 64,
            vocal_track_index=0,
        )
        self.assertEqual(curve['isolated_spike_policy']['corrected_frames'], 0)
        # Any frame covered by at least two notes is ambiguous, including the
        # pairwise and triple-overlap regions. The third note must not claim
        # ownership after the second pair already marked it -2.
        self.assertEqual(
            int(np.count_nonzero(curve['note_owner'] == -2)), 50)
        self.assertTrue(np.all(curve['note_owner'][
            (np.arange(101) >= 30) & (np.arange(101) < 60)] == -2))
        # Ambiguous ownership prohibits automatic smoothing, but must not
        # erase measured voiced F0 from the exported curve.
        self.assertEqual(dict(curve['points'])[self.clock(.40) + 1920], 6600)
        self.assertTrue(curve['written_mask'][40])
        offset_audit = curve['pitch_offset_diagnostics']
        self.assertEqual(offset_audit['warning_count'], 0)
        self.assertEqual(offset_audit['status'], 'insufficient_evidence')
        self.assertEqual(offset_audit['ambiguous_note_frames'], 50)
        self.assertEqual(
            offset_audit['not_assessed_intervals'][0]['reason'],
            'concurrent_note_ownership_ambiguous')
        self.assertFalse(offset_audit['truth_determined'])

    def test_voiced_invalid_f0_is_rejected_before_spike_smoothing(self):
        owner = pitch_accuracy.assign_note_owners(
            np.arange(0., .6, .01), [(0., .6)])
        for bad in (np.nan, np.inf, -1., 128.):
            pitch = np.full(60, 60., dtype=np.float32)
            pitch[30] = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, '有声 F0'):
                pitch_accuracy.stabilize_isolated_pitch_spikes(
                    pitch, np.ones(60, dtype=bool), owner, .01)

    def test_invalid_unvoiced_sentinel_is_not_smoothed_or_written(self):
        pitch = np.full(80, 60., dtype=np.float32)
        pitch[20] = 72.
        mask = np.ones(80, dtype=bool)
        mask[20] = False
        curve = build_absolute_f0_curve(
            pitch, mask, .01, [(0., .8)], self.clock, self.inverse_clock)
        self.assertEqual(curve['isolated_spike_policy']['corrected_frames'], 0)
        self.assertNotIn(self.clock(.20) + 1920, dict(curve['points']))

    def test_agent_and_gui_share_the_same_core(self):
        self.assertIs(agent_pitch.build_absolute_f0_curve,
                      pitch_accuracy.build_absolute_f0_curve)

    def test_gui_and_mcp_emit_identical_curves_and_correction_audit(self):
        from types import SimpleNamespace
        from libresvip.model.base import (
            Note, Project, SingingTrack, SongTempo, TimeSignature,
        )

        def project():
            return Project(
                song_tempo_list=[SongTempo(position=0, bpm=120)],
                time_signature_list=[TimeSignature(
                    bar_index=0, numerator=4, denominator=4)],
                track_list=[SingingTrack(note_list=[Note(
                    start_pos=0, length=1920, key_number=60, lyric='一')])],
            )

        pitch = np.full(200, np.nan, dtype=np.float32)
        mask = np.zeros(200, dtype=bool)
        pitch[10:90] = 60.2
        pitch[100:190] = 60.2
        mask[10:90] = True
        mask[100:190] = True
        pitch[50] = 66.
        pitch[5] = 66.
        pitch[120:126] = 63.
        original_pitch = pitch.copy()
        original_mask = mask.copy()
        gui_project = project()
        gui_report = pitch_accuracy.absolute_curve(
            gui_project.track_list[0], gui_project,
            SimpleNamespace(midi_pitch=pitch, voiced_mask=mask,
                            time_step_seconds=.01))
        mcp_project = project()
        new_curve, state = agent_pitch._install_track_pitch(
            mcp_project.track_list[0], mcp_project,
            {'pitch': pitch, 'voiced_mask': mask, 'time_step_seconds': .01},
            0., lambda: None)
        gui_points = [(point.x, point.y)
                      for point in gui_project.track_list[0].edited_params.pitch.points.root]
        mcp_points = [(point.x, point.y) for point in new_curve.points.root]
        self.assertEqual(gui_points, mcp_points)
        self.assertEqual(gui_report['isolated_spike_policy'],
                         state['report']['isolated_spike_policy'])
        self.assertEqual(gui_report['pitch_offset_diagnostics'],
                         state['report']['pitch_offset_diagnostics'])
        self.assertEqual(gui_report['gaps_retained_silent'], 1)
        self.assertEqual(state['report']['gaps_retained_silent'], 1)
        self.assertIn('explicit continuous-pitch evidence',
                      state['report']['native_guard_policy'])
        self.assertIn('explicit continuous-pitch evidence',
                      gui_report['bridge_policy'])
        self.assertTrue(np.array_equal(pitch, original_pitch, equal_nan=True))
        self.assertTrue(np.array_equal(mask, original_mask))

def non_break_values(track):
    return [point for point in track.edited_params.pitch.points.root if point.y != -100]

class AgentPitchPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_plan_is_compact_deterministic_and_stable(self):
        with self.temporary("agent-pitch-plan-") as temporary:
            lead, backing, *_ = pitch_fixtures(temporary)
            sources = [
                {"vocal_track_index": 1, "path": str(backing), "time_origin_seconds": 0.5},
                {"vocal_track_index": 0, "path": str(lead)},
            ]
            first = build_pitch_plan(sources)
            second = build_pitch_plan(list(reversed(sources)))
            self.assertEqual(first, second)
            json_ready(first)
            self.assertEqual([row["vocal_track_index"] for row in first["sources"]], [0, 1])
            self.assertEqual(first["sources"][0]["source_sha256"], digest(lead))
            self.assertEqual(first["sources"][0]["time_origin_seconds"], 0.0)
            self.assertEqual(first["sources"][1]["time_origin_seconds"], 0.5)
            self.assertEqual(first["sources"][0]["source"]["voiced_frame_count"], 282)
            self.assertEqual(first["totals"]["source_count"], 2)
            self.assertEqual(first["algorithm"]["name"], "absolute-f0-v2")
            self.assertEqual(first["algorithm"]["short_gap_bridge_seconds"], 0.08)
            self.assertEqual(first["algorithm"]["bridge_curve"],
                             "bounded_cubic_hermite_endpoint_trend_requires_explicit_evidence")
            self.assertEqual(
                first["algorithm"]["isolated_spike_edge_guard_seconds"], .08)
            self.assertEqual(
                first["algorithm"]["sustained_offset_threshold_cents"], 300.)
            self.assertEqual(
                first["algorithm"]["sustained_offset_min_duration_seconds"], .16)
            self.assertEqual(
                first["algorithm"]["sustained_offset_policy"],
                "read_only_note_center_review; never auto-correct",
            )
            self.assertFalse(first["algorithm"]["sustained_offset_truth_determined"])
            self.assertFalse(first["inference_performed"])
            self.assertFalse(first["synthesis_performed"])
            self.assertNotIn("pitch", first["sources"][0]["source"])
            self.assertNotIn("voiced_mask", first["sources"][0]["source"])
            self.assertEqual(first["plan_sha256"], agent_pitch._plan_digest(first))

    def test_strict_sources_reject_relative_duplicate_missing_and_unknown(self):
        with self.temporary("agent-pitch-inputs-") as temporary:
            lead, backing, *_ = pitch_fixtures(temporary)
            cases = [
                [],
                [{"vocal_track_index": 0, "path": "relative.npz"}],
                [{"vocal_track_index": True, "path": str(lead)}],
                [{"vocal_track_index": 0, "path": str(lead), "time_origin_seconds": math.nan}],
                [{"vocal_track_index": 0, "path": str(lead), "extra": 1}],
                [
                    {"vocal_track_index": 0, "path": str(lead)},
                    {"vocal_track_index": 0, "path": str(backing)},
                ],
                [
                    {"vocal_track_index": 0, "path": str(lead)},
                    {"vocal_track_index": 1, "path": str(lead)},
                ],
                [{"vocal_track_index": 0, "path": str(Path(temporary) / "missing.npz")}],
            ]
            for value in cases:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    build_pitch_plan(value)

    def test_plan_digest_and_metadata_tampering_are_rejected(self):
        with self.temporary("agent-pitch-tamper-") as temporary:
            lead, *_ = pitch_fixtures(temporary)
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(lead)}])
            changed = copy.deepcopy(plan)
            changed["sources"][0]["time_origin_seconds"] = 0.25
            with self.assertRaisesRegex(ValueError, "plan_sha256"):
                apply_pitch_plan(make_project(), changed)
            changed = copy.deepcopy(plan)
            changed["sources"][0]["source"]["frame_count"] += 1
            changed["totals"]["frame_count"] += 1
            changed["plan_sha256"] = agent_pitch._plan_digest(changed)
            with self.assertRaisesRegex(ValueError, "元数据"):
                apply_pitch_plan(make_project(), changed)
            changed = copy.deepcopy(plan)
            changed["algorithm"]["sustained_offset_policy"] = "auto_correct"
            changed["plan_sha256"] = agent_pitch._plan_digest(changed)
            with self.assertRaisesRegex(ValueError, "不受支持"):
                apply_pitch_plan(make_project(), changed)


@unittest.skipUnless(EXPORT_RUNTIME, "optional LibreSVIP export runtime is unavailable")
class AgentPitchApplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def make_case(self, root):
        lead, backing, *_ = pitch_fixtures(root)
        plan = build_pitch_plan([
            {"vocal_track_index": 0, "path": str(lead)},
            {"vocal_track_index": 1, "path": str(backing), "time_origin_seconds": 0.5},
        ])
        return make_project(), plan, lead, backing

    def test_apply_preserves_absolute_f0_breaks_and_track_mapping(self):
        with self.temporary("agent-pitch-apply-") as temporary:
            project, plan, _lead, _backing = self.make_case(temporary)
            tracks = [track for track in project.track_list if hasattr(track, "note_list")]
            lead, backing = tracks
            original_keys = [note.key_number for note in lead.note_list]
            original_backing = [(note.start_pos, note.key_number) for note in backing.note_list]
            report = apply_pitch_plan(project, plan)
            json_ready(report)
            self.assertEqual(report["status"], "applied")
            self.assertTrue(report["fidelity"]["source_npz_sha_unchanged"])
            self.assertFalse(report["fidelity"]["rendered_curve_pointwise_lossless"])
            self.assertEqual(
                report["fidelity"]["raw_f0_pointwise_preserved"],
                report["fidelity"]["isolated_spike_corrected_frames"] == 0)
            self.assertIn("bridge only same-note",
                          report["tracks"][0]["native_guard_policy"])
            self.assertIn("explicit continuous-pitch evidence",
                          report["tracks"][0]["native_guard_policy"])
            self.assertEqual([row["vocal_track_index"] for row in report["tracks"]], [0, 1])
            self.assertTrue(all(row["unchanged"] for row in report["source_verification"]))
            self.assertEqual([note.key_number for note in lead.note_list], original_keys)
            self.assertEqual(
                [(note.start_pos, note.key_number) for note in backing.note_list],
                original_backing,
            )
            lead_values = non_break_values(lead)
            backing_values = non_break_values(backing)
            self.assertGreater(len(lead_values), 200)
            self.assertGreaterEqual(len(backing_values), 30)
            self.assertTrue(all(5900 <= point.y <= 6500 for point in lead_values))
            self.assertTrue(all(5450 <= point.y <= 5550 for point in backing_values))
            self.assertFalse(any(abs(point.y - 5500) < 5 for point in lead_values))
            self.assertFalse(any(point.y >= 5900 for point in backing_values))
            lead_report, backing_report = report["tracks"]
            self.assertGreaterEqual(lead_report["runs"], 3)
            self.assertEqual(lead_report["short_runs_dropped"], 1)
            self.assertGreaterEqual(lead_report["notes_missing_local_reference"], 2)
            statuses = {row["status"] for row in lead_report["local_missing_samples"]}
            self.assertIn("too_short_for_curve", statuses)
            self.assertIn("outside_reference_frames", statuses)
            self.assertLess(lead_report["max_error_cents"], 1.0)
            self.assertGreater(lead_report["curve_pitch_span_cents"], 90)
            self.assertEqual(lead_report["curve_points"], 280)
            self.assertEqual(lead_report["curve_segments"], 3)
            self.assertEqual(lead_report["native_guard_points"],
                             6 * lead_report["curve_segments"])
            self.assertEqual(lead_report["bridged_gaps"], 0)
            self.assertLessEqual(
                lead_report["native_guard_extension_seconds_max"], 0.0100001)
            self.assertGreaterEqual(
                lead_report["object_points"],
                lead_report["curve_points"] + lead_report["native_guard_points"])
            direct = verify_pitch_plan(project, plan, tolerance_cents=2.0)
            json_ready(direct)
            self.assertEqual(direct["status"], "verified")
            self.assertTrue(direct["verified"])
            self.assertEqual(direct["summary"]["missing_samples"], 0)
            self.assertLessEqual(direct["summary"]["max_error_cents"], 2.0)
    def test_changed_source_cancel_and_out_of_range_never_replace_pitch(self):
        with self.temporary("agent-pitch-refusal-") as temporary:
            project, plan, lead, backing = self.make_case(temporary)
            tracks = [track for track in project.track_list if hasattr(track, "note_list")]
            before = copy.deepcopy([non_break_values(track) for track in tracks])
            with self.assertRaises(InterruptedError):
                apply_pitch_plan(project, plan, cancel=lambda: True)
            self.assertEqual([non_break_values(track) for track in tracks], before)

            bad = build_pitch_plan([{"vocal_track_index": 2, "path": str(lead)}])
            with self.assertRaisesRegex(ValueError, "越界"):
                apply_pitch_plan(project, bad)
            self.assertEqual([non_break_values(track) for track in tracks], before)

            _lead_path, _backing_path, lead_pitch, lead_mask, _backing_pitch, _backing_mask = pitch_fixtures(temporary)
            save_pitch(lead, np.full_like(lead_pitch, 61.0), lead_mask)
            with self.assertRaisesRegex(ValueError, "SHA-256|元数据"):
                apply_pitch_plan(project, plan)
            self.assertEqual([non_break_values(track) for track in tracks], before)

    def test_omitting_a_track_source_never_copies_another_tracks_f0(self):
        with self.temporary("agent-pitch-explicit-track-") as temporary:
            lead_path, _backing_path, *_ = pitch_fixtures(temporary)
            project = make_project()
            lead, backing = [track for track in project.track_list if hasattr(track, "note_list")]
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(lead_path)}])
            report = apply_pitch_plan(project, plan)
            self.assertGreater(len(non_break_values(lead)), 100)
            self.assertEqual(non_break_values(backing), [])
            self.assertEqual(report["totals"]["tracks_modified"], 1)

    def test_raw_sample_helper_reports_actual_misses(self):
        with self.temporary("agent-pitch-raw-compare-") as temporary:
            lead_path, *_rest, lead_pitch, _lead_mask, _backing_pitch, _backing_mask = pitch_fixtures(temporary)
            project = make_project()
            lead = [track for track in project.track_list if hasattr(track, "note_list")][0]
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(lead_path)}])
            apply_pitch_plan(project, plan)
            samples = [
                {"seconds": 1.20, "midi_pitch": float(lead_pitch[120])},
                {"seconds": 1.75, "midi_pitch": float(lead_pitch[175])},
            ]
            matched = compare_pitch_samples(project, samples, tolerance_cents=2.0)
            json_ready(matched)
            self.assertEqual(matched["status"], "match")
            self.assertEqual(matched["summary"]["missing_or_outside"], 0)

            mismatched = compare_pitch_samples(
                project,
                samples + [{"seconds": 3.0, "midi_pitch": 60.0}],
                tolerance_cents=2.0,
            )
            self.assertEqual(mismatched["status"], "mismatch")
            self.assertEqual(mismatched["summary"]["missing_or_outside"], 1)
            self.assertFalse(mismatched["synthesis_performed"])

    def test_verify_is_honest_when_curve_is_missing_or_notes_move_away(self):
        with self.temporary("agent-pitch-verify-honesty-") as temporary:
            lead_path, *_ = pitch_fixtures(temporary)
            project = make_project()
            lead = [track for track in project.track_list if hasattr(track, "note_list")][0]
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(lead_path)}])
            apply_pitch_plan(project, plan)
            from libresvip.model.base import ParamCurve
            lead.edited_params.pitch = ParamCurve(points=[])
            missing = verify_pitch_plan(project, plan, tolerance_cents=2.0)
            self.assertEqual(missing["status"], "failed")
            self.assertEqual(missing["reason"], "continuous_pitch_missing")
            self.assertFalse(missing["verified"])

            moved = make_project()
            moved_lead = [track for track in moved.track_list if hasattr(track, "note_list")][0]
            for note in moved_lead.note_list:
                note.start_pos += 1920 * 20
            apply_pitch_plan(moved, plan)
            unverifiable = verify_pitch_plan(moved, plan, tolerance_cents=2.0)
            self.assertEqual(unverifiable["status"], "unverifiable")
            self.assertEqual(unverifiable["reason"], "no_gated_reference_frames")
            self.assertFalse(unverifiable["verified"])


    def test_verify_reports_no_gated_reference_instead_of_success(self):
        with self.temporary("agent-pitch-unverifiable-") as temporary:
            path = Path(temporary) / "silent.pitch.npz"
            save_pitch(path, np.full(100, np.nan), np.zeros(100, dtype=bool))
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(path)}])
            project = make_project()
            applied = apply_pitch_plan(project, plan)
            self.assertEqual(applied["status"], "partial_no_curve")
            result = verify_pitch_plan(project, plan)
            self.assertEqual(result["status"], "unverifiable")
            self.assertEqual(result["reason"], "no_gated_reference_frames")
            self.assertFalse(result["verified"])
            self.assertFalse(result["detail_verified"])

    def test_unvoiced_mask_gap_is_preserved_without_explicit_evidence(self):
        with self.temporary("agent-pitch-short-gap-") as temporary:
            pitch = np.full(300, np.nan, dtype=np.float32)
            mask = np.zeros(300, dtype=bool)
            pitch[100:105] = 60.0 + np.arange(5) * .02
            pitch[106:111] = 60.3 + np.arange(5) * .02
            mask[100:105] = True
            mask[106:111] = True
            path = save_pitch(Path(temporary) / "gap.pitch.npz", pitch, mask)
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(path)}])
            project = make_project()
            lead = [track for track in project.track_list if hasattr(track, "note_list")][0]
            report = apply_pitch_plan(project, plan)["tracks"][0]
            self.assertEqual(report["runs"], 2)
            self.assertEqual(report["curve_segments"], 2)
            self.assertEqual(report["bridged_gaps"], 0)
            self.assertEqual(report["gaps_retained_silent"], 1)
            self.assertEqual(report["retained_gap_samples"][0]["reason"],
                             "no_explicit_evidence")
            self.assertLessEqual(report["native_guard_extension_seconds_max"], 0.0100001)
            runs = []
            current = []
            for point in lead.edited_params.pitch.points.root:
                if point.y == -100:
                    if current:
                        runs.append(current)
                    current = []
                elif point.x >= 0:
                    current.append(point)
            if current:
                runs.append(current)
            self.assertEqual(len(runs), 2)
            self.assertTrue(all(right.x > left.x
                                for run in runs for left, right in zip(run, run[1:])))

    def test_sustained_note_center_drift_warns_without_modifying_source_or_note(self):
        from libresvip.model.base import (
            Note, Project, SingingTrack, SongTempo, TimeSignature,
        )
        with self.temporary("agent-pitch-offset-review-") as temporary:
            pitch = np.full(250, 60., dtype=np.float32)
            pitch[:8] = 66.
            pitch[8:140] = np.linspace(60., 64., 132)
            pitch[140:242] = 64.
            pitch[242:] = 54.
            path = save_pitch(
                Path(temporary) / "slow-drift.pitch.npz",
                pitch, np.ones(250, dtype=bool),
            )
            before_sha = digest(path)
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(path)}])
            project = Project(
                song_tempo_list=[SongTempo(position=0, bpm=120)],
                time_signature_list=[TimeSignature(
                    bar_index=0, numerator=4, denominator=4)],
                track_list=[SingingTrack(
                    title="Lead",
                    note_list=[Note(start_pos=0, length=2400, key_number=60, lyric="戏")],
                )],
            )
            applied = apply_pitch_plan(project, plan)
            json_ready(applied)
            audit = applied["pitch_offset_diagnostics"]
            self.assertEqual(audit["status"], "review_required")
            self.assertEqual(audit["warning_count"], 1)
            self.assertEqual(audit["source_sha256s"], [before_sha])
            self.assertEqual(audit["plan_sha256"], plan["plan_sha256"])
            warning = audit["warnings"][0]
            self.assertEqual(warning["note"]["note_index"], 0)
            self.assertEqual(warning["note"]["lyric"], "戏")
            self.assertEqual(warning["source_sha256"], before_sha)
            self.assertEqual(warning["review_target"], "note_pitch_or_f0_curve")
            self.assertFalse(warning["truth_determined"])
            self.assertFalse(warning["automatic_change"])
            self.assertEqual(
                applied["fidelity"]["sustained_offsets_auto_corrected"], 0)
            self.assertFalse(applied["fidelity"]["pitch_offset_truth_determined"])
            self.assertTrue(applied["fidelity"]["source_npz_sha_unchanged"])
            self.assertEqual(project.track_list[0].note_list[0].key_number, 60)
            self.assertTrue(any(point.y == 6400
                                for point in non_break_values(project.track_list[0])))
            self.assertEqual(digest(path), before_sha)

            verified = verify_pitch_plan(project, plan)
            json_ready(verified)
            self.assertTrue(verified["verified"], verified)
            self.assertEqual(
                verified["pitch_offset_diagnostics"]["warning_count"], 1)
            self.assertEqual(
                verified["pitch_offset_diagnostics"]["warnings"][0]["source_sha256"],
                before_sha,
            )
            self.assertEqual(
                verified["fidelity"]["sustained_offsets_auto_corrected"], 0)
            self.assertTrue(verified["fidelity"]["source_npz_sha_unchanged"])
            self.assertEqual(digest(path), before_sha)

    def test_declared_spike_correction_verifies_without_claiming_lossless_f0(self):
        with self.temporary("agent-pitch-spike-receipt-") as temporary:
            pitch = np.full(600, np.nan, dtype=np.float32)
            mask = np.zeros(600, dtype=bool)
            pitch[100:200] = 60.
            pitch[130] = 65.
            mask[100:200] = True
            path = save_pitch(Path(temporary) / "spike.pitch.npz", pitch, mask)
            before = digest(path)
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(path)}])
            project = make_project()
            applied = apply_pitch_plan(project, plan)
            policy = applied["tracks"][0]["isolated_spike_policy"]
            self.assertEqual(policy["corrected_frames"], 1)
            self.assertEqual(policy["corrections"][0]["frame"], 130)
            self.assertTrue(applied["fidelity"]["source_npz_sha_unchanged"])
            self.assertFalse(applied["fidelity"]["raw_f0_pointwise_preserved"])
            self.assertFalse(applied["fidelity"]["rendered_curve_pointwise_lossless"])
            self.assertEqual(digest(path), before)
            verified = verify_pitch_plan(project, plan)
            self.assertTrue(verified["verified"], verified)
            self.assertEqual(
                verified["summary"]["isolated_spike_corrected_frames"], 1)
            self.assertFalse(verified["fidelity"]["raw_f0_pointwise_preserved"])
            self.assertFalse(verified["fidelity"]["rendered_curve_pointwise_lossless"])
@unittest.skipUnless(EXPORT_RUNTIME, "optional LibreSVIP export runtime is unavailable")
class AgentPitchFormatRoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)
        from libresvip.plugins.svp.synthv_studio_converter import SynthVStudioConverter
        from libresvip.plugins.ustx.ustx_converter import OpenUtauConverter
        from libresvip.plugins.vsqx.vsqx_converter import VsqxConverter
        cls.converters = {
            "svp": SynthVStudioConverter,
            "ustx": OpenUtauConverter,
            "vsqx": VsqxConverter,
        }

    def temporary(self, prefix):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=self.work)

    def test_native_svp_adjacent_notes_meter_and_playback_tempo(self):
        from libresvip.core.tick_counter import shift_beat_list, shift_tempo_list
        from libresvip.model.base import Note, Project, SingingTrack, SongTempo, TimeSignature
        from libresvip.model.point import Point

        with self.temporary("agent-pitch-native-") as temporary:
            root = Path(temporary)
            values = 62 + .4 * np.sin(np.arange(400) * .3)
            source = save_pitch(root / "measured.npz", values, np.ones(400, dtype=bool))
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(source)}])
            for numerator, version in ((3, 100), (4, 100), (4, 135), (4, 182)):
                with self.subTest(numerator=numerator, version=version):
                    project = Project(
                        song_tempo_list=[SongTempo(position=0, bpm=120),
                                         SongTempo(position=960, bpm=60),
                                         SongTempo(position=1440, bpm=180)],
                        time_signature_list=[TimeSignature(bar_index=0, numerator=numerator, denominator=4)],
                        track_list=[SingingTrack(note_list=[
                            Note(start_pos=480, length=480, key_number=64, lyric="你"),
                            Note(start_pos=960, length=480, key_number=72, lyric="好"),
                            Note(start_pos=1440, length=1440, key_number=55, lyric="呀"),
                        ])],
                    )
                    project.track_list[0].edited_params.strength.points.root = [
                        Point(1920, 0), Point(2500, 200)]
                    apply_pitch_plan(project, plan)
                    first_bar = round(project.time_signature_list[0].bar_length())
                    adapted = project.model_copy(deep=True)
                    adapted.song_tempo_list = shift_tempo_list(project.song_tempo_list, first_bar)
                    adapted.time_signature_list = shift_beat_list(project.time_signature_list, 1)
                    path = root / f"meter-{numerator}-{version}.svp"
                    self.converters["svp"].dump(path, adapted, {
                        "down_sample": 0, "version_compatibility": version})
                    before = json.loads(path.read_bytes().rstrip(b"\0"))
                    before["userCustom"] = {"keep": "unchanged"}
                    path.write_bytes(json.dumps(before).encode() + b"\0")
                    unchanged = path.read_bytes()
                    with self.assertRaises(InterruptedError):
                        restore_native_pitch(path, "svp", project, plan, cancel=lambda: True)
                    self.assertEqual(path.read_bytes(), unchanged)
                    result = restore_native_pitch(path, "svp", project, plan)
                    self.assertTrue(result["applied"])
                    after = json.loads(path.read_bytes().rstrip(b"\0"))
                    for data in (before, after):
                        data["tracks"][0]["mainGroup"]["parameters"].pop("pitchDelta")
                        data["tracks"][0]["mainGroup"].pop("pitchControls", None)
                    self.assertEqual(before, after)
                    loaded = load_pitch_project(path, "svp", self.converters)
                    loaded.song_tempo_list = project.song_tempo_list
                    audit = verify_pitch_plan(loaded, plan)
                    self.assertTrue(audit["verified"], audit)
                    self.assertLessEqual(audit["summary"]["max_error_cents"], 5)

    def test_real_cross_format_readback_keeps_changed_midi_notes_and_absolute_f0(self):
        from libresvip.core.tick_counter import shift_beat_list, shift_tempo_list
        from libresvip.model.base import SongTempo

        with self.temporary("agent-pitch-roundtrip-") as temporary:
            root = Path(temporary)
            lead_path, *_rest, lead_pitch, _lead_mask, _backing_pitch, _backing_mask = pitch_fixtures(root)
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(lead_path)}])
            project = make_project()
            lead = [track for track in project.track_list if hasattr(track, "note_list")][0]
            apply_pitch_plan(project, plan)
            expected_keys = [64, 62, 67, 69]
            self.assertEqual([note.key_number for note in lead.note_list], expected_keys)
            first_bar = round(project.time_signature_list[0].bar_length())
            input_tempo = [
                SongTempo(position=tempo.position, bpm=tempo.bpm)
                for tempo in project.song_tempo_list
            ]
            summaries = {}
            for fmt, converter in self.converters.items():
                with self.subTest(format=fmt):
                    adapted = project.model_copy(deep=True)
                    adapted.song_tempo_list = shift_tempo_list(
                        project.song_tempo_list,
                        first_bar * (2 if fmt == "vsqx" else 1),
                    )
                    if fmt == "svp":
                        adapted.time_signature_list = shift_beat_list(
                            project.time_signature_list, 1)
                    path = root / ("voices." + fmt)
                    options = (
                        {"down_sample": 0, "language_override": "mandarin"}
                        if fmt == "svp" else {}
                    )
                    converter.dump(path, adapted, options)
                    restore_native_pitch(path, fmt, project, plan)
                    loaded = load_pitch_project(path, fmt, self.converters)
                    if fmt == "svp":
                        regular = converter.load(
                            path,
                            {"import_pitch": True, "import_instrumental_track": False},
                        )
                        regular.song_tempo_list = [
                            SongTempo(position=tempo.position, bpm=tempo.bpm)
                            for tempo in input_tempo
                        ]
                        regular_result = verify_pitch_plan(regular, plan)
                        self.assertEqual(regular_result["status"], "verified")
                        self.assertLessEqual(
                            regular_result["summary"]["max_error_cents"], 5.0)

                    if fmt in {"svp", "ustx"}:
                        wrong_axis = verify_pitch_plan(loaded, plan)
                        self.assertFalse(wrong_axis["verified"], wrong_axis)
                        self.assertFalse(
                            wrong_axis["time_mapping"]["automatic_prefix_search"])
                    loaded.song_tempo_list = [
                        SongTempo(position=tempo.position, bpm=tempo.bpm)
                        for tempo in input_tempo
                    ]
                    loaded_lead = [
                        track for track in loaded.track_list
                        if hasattr(track, "note_list")
                    ][0]
                    if fmt == "svp":
                        native_result = verify_pitch_plan(loaded, plan)
                        self.assertEqual(native_result["status"], "verified")
                        self.assertLessEqual(
                            native_result["summary"]["max_error_cents"], 0.5)
                    self.assertEqual(
                        [note.key_number for note in loaded_lead.note_list],
                        expected_keys,
                    )
                    result = verify_pitch_plan(loaded, plan)
                    json_ready(result)
                    self.assertEqual(result["status"], "verified", result)
                    self.assertTrue(result["verified"], result)
                    self.assertTrue(result["detail_verified"], result)
                    self.assertIsNone(result["reason"])
                    self.assertEqual(result["summary"]["missing_samples"], 0)
                    self.assertLessEqual(result["summary"]["max_error_cents"], 5.0, result)
                    self.assertLessEqual(
                        result["summary"]["interior"]["max_error_cents"], 5.0,
                        result,
                    )
                    self.assertLessEqual(
                        result["summary"]["boundary"]["max_error_cents"], 5.0,
                        result,
                    )
                    self.assertEqual(result["sources"][0]["expected_runs"], 3)
                    self.assertGreater(
                        result["sources"][0]["curve_points"],
                        result["sources"][0]["eligible_frames"] // 2,
                     )
                    samples = [
                        {"seconds": 1.20, "midi_pitch": float(lead_pitch[120])},
                        {"seconds": 1.75, "midi_pitch": float(lead_pitch[175])},
                        {"seconds": 4.35, "midi_pitch": float(lead_pitch[435])},
                        {"seconds": 5.45, "midi_pitch": float(lead_pitch[545])},
                    ]
                    compared = compare_pitch_samples(
                        loaded, samples, tolerance_cents=5.0)
                    self.assertEqual(compared["status"], "match", compared)
                    summaries[fmt] = {
                        "max_error_cents": result["summary"]["max_error_cents"],
                        "mean_error_cents": result["summary"]["mean_error_cents"],
                        "tempo_skip_ticks": result["selected_time_mapping"]["tempo_skip_ticks"],
                        "curve_runs": result["sources"][0]["curve_runs"],
                    }
            self.assertEqual(set(summaries), {"svp", "ustx", "vsqx"})
            json_ready(summaries)

    def test_native_svp_rebuilds_baseline_after_note_pitch_edits(self):
        from libresvip.core.tick_counter import shift_beat_list, shift_tempo_list
        with self.temporary("agent-pitch-native-edit-") as temporary:
            root = Path(temporary)
            values = np.full(300, np.nan, dtype=np.float32)
            mask = np.zeros(300, dtype=bool)
            values[100:200] = 60. + .2 * np.sin(np.arange(100) * .25)
            values[250:255] = 60.
            mask[100:200] = True
            mask[250:255] = True
            source = save_pitch(root / "measured.pitch.npz", values, mask)
            plan = build_pitch_plan([{"vocal_track_index": 0, "path": str(source)}])
            project = make_project()
            lead = [track for track in project.track_list if hasattr(track, "note_list")][0]
            apply_pitch_plan(project, plan)
            # Simulate editor edits after the measured F0 was installed. The
            # native reconstruction must use the current note keys as its new
            # baseline while retaining the measured absolute F0 target.
            lead.note_list[0].key_number = 59
            lead.note_list[1].key_number = 64
            path = root / "edited.svp"
            first_bar = round(project.time_signature_list[0].bar_length())
            adapted = project.model_copy(deep=True)
            adapted.song_tempo_list = shift_tempo_list(
                project.song_tempo_list, first_bar)
            adapted.time_signature_list = shift_beat_list(
                project.time_signature_list, 1)
            self.converters["svp"].dump(
                path, adapted, {"down_sample": 0, "language_override": "mandarin"})
            restored = restore_native_pitch(path, "svp", project, plan)
            self.assertTrue(restored["applied"])
            row = restored["tracks"][0]
            self.assertEqual(row["baseline_source"],
                             "native_svp_note_pitch_and_transition_model")
            self.assertEqual(row["delta_encoding"],
                             "target_curve_minus_recomputed_native_baseline")
            native = json.loads(path.read_bytes().rstrip(b"\0"))
            native_track = next(
                track for track in native["tracks"]
                if not track.get("mainRef", {}).get("isInstrumental", False))
            deltas = native_track["mainGroup"]["parameters"]["pitchDelta"]["points"][1::2]
            self.assertLess(min(deltas), -300.)
            self.assertGreater(max(deltas), 0.)
            loaded = load_pitch_project(path, "svp", self.converters)
            loaded.song_tempo_list = project.song_tempo_list
            audit = verify_pitch_plan(loaded, plan)
            self.assertTrue(audit["verified"], audit)
            self.assertEqual(audit["summary"]["missing_samples"], 0)
            self.assertLessEqual(audit["summary"]["max_error_cents"], 5.0)
            self.assertEqual(
                [note.key_number for note in loaded.track_list[0].note_list],
                [59, 64, 67, 69],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
