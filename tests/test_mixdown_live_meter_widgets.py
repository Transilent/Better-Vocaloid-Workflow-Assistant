"""Regression tests for live audition meters, GR history, and measured EDC."""
import os
from pathlib import Path
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("VWA_TESTING", "1")

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import mixdown_live_meters as live


APP = QApplication.instance() or QApplication([])


def envelope_report():
    return {
        "peak_envelope": {
            "tracks": [
                {"role": "vocal", "index": 0, "db": [-20.0, -10.0, -20.0], "hz": 20.0},
                {"role": "backing", "index": 0, "db": [-8.0, -5.0, -9.0],
                 "interval_ms": 50.0},
            ],
            "bus": {"db": [-6.0, -2.0, -7.0], "hz": 20.0},
        },
        "gr_envelope": {
            "tracks": [
                {"role": "vocal", "index": 0, "db": [0.0, -6.0, -3.0], "hz": 20.0},
                {"role": "backing", "index": 0, "db": [0.0, -1.5, 0.0],
                 "interval_ms": 50.0},
            ],
            "bus": {"db": [0.0, -4.0, -1.0], "hz": 20.0},
        },
    }


def row_envelope_report():
    return {
        "tracks": [
            {
                "role": "vocal", "index": 0, "label": "Lead",
                "peak_envelope": {"db": [-18.0, -12.0], "hz": 10.0},
                "gr_envelope": {"db": [0.0, -3.0], "hz": 10.0},
                "gr_db_p95": -2.5,
            },
            {
                "role": "backing", "index": 0, "peak_envelope": [-5.0, -7.0],
                "interval_ms": 100.0, "gr_db_p95": -1.2,
            },
        ],
        "bus": {
            "peak_envelope": {"db": [-4.0, -3.0], "hz": 10.0},
            "gr_envelope": {"db": [0.0, -2.0], "hz": 10.0},
            "gr_db_p95": -1.8,
        },
    }


class MeterEnvelopeTests(unittest.TestCase):
    def test_interpolates_tracks_and_bus_at_playback_time(self):
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_report(envelope_report())
        self.assertEqual(feed.lane_ids, ("vocal:0", "backing:0", "bus"))
        snapshot = feed.sample_at(25)
        tracks = {item["id"]: item for item in snapshot["tracks"]}
        self.assertAlmostEqual(tracks["vocal:0"]["peak_dbfs"], -15.0, places=6)
        self.assertAlmostEqual(tracks["vocal:0"]["gr_db"], -3.0, places=6)
        self.assertAlmostEqual(snapshot["bus"]["peak_dbfs"], -4.0, places=6)
        self.assertAlmostEqual(snapshot["bus"]["gr_db"], -2.0, places=6)

    def test_position_advance_and_lost_tail_decay(self):
        feed = live.MeterEnvelope(auto_start=False)
        emitted = []
        feed.changed.connect(lambda snapshot: emitted.append(snapshot))
        feed.set_report(envelope_report())
        feed.set_position_ms(80)
        self.assertEqual(len(emitted), 2)
        value = feed.snapshot()["tracks"][0]["peak_dbfs"]
        self.assertAlmostEqual(value, -16.0, places=6)
        # The last envelope point is at 100 ms.  A position beyond it must
        # decay instead of holding a stale level forever.
        later = feed.sample_at(250)["tracks"][0]["peak_dbfs"]
        self.assertLess(later, -20.0)
        self.assertGreater(later, live.PEAK_ENVELOPE_FLOOR_DBFS - 0.01)
        self.assertEqual(feed.current_position_ms, 80.0)

    def test_per_track_shapes_and_gr_p95_fallback(self):
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_report(row_envelope_report())
        snapshot = feed.sample_at(50)
        tracks = {item["id"]: item for item in snapshot["tracks"]}
        self.assertEqual(tracks["vocal:0"]["label"], "Lead")
        self.assertAlmostEqual(tracks["vocal:0"]["peak_dbfs"], -15.0, places=6)
        self.assertAlmostEqual(tracks["vocal:0"]["gr_db"], -1.5, places=6)
        self.assertAlmostEqual(tracks["backing:0"]["peak_dbfs"], -6.0, places=6)
        self.assertAlmostEqual(tracks["backing:0"]["gr_db"], -1.2, places=6)
        self.assertAlmostEqual(snapshot["bus"]["gr_db"], -1.0, places=6)

    def test_missing_fields_and_clear_are_safe(self):
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_report({})
        snapshot = feed.snapshot()
        self.assertEqual(snapshot["tracks"], [])
        self.assertIsNone(snapshot["bus"]["peak_dbfs"])
        self.assertFalse(snapshot["bus"]["present"])
        feed.set_report(None)
        self.assertEqual(feed.lane_ids, ())

    def test_thirty_hz_coalescing_has_no_event_backlog(self):
        feed = live.MeterEnvelope(auto_start=True)
        feed.set_report(envelope_report())
        QTest.qWait(45)
        feed.reset_throttle_stats()
        for position in range(1000):
            feed.set_position_ms(position)
        stats = feed.throttle_stats
        self.assertEqual(feed.pending_event_count, 1)
        self.assertEqual(stats["received"], 1000)
        self.assertEqual(stats["coalesced"], 999)
        QTest.qWait(50)
        self.assertEqual(feed.throttle_stats["delivered"], 1)
        self.assertEqual(feed.pending_event_count, 0)
        self.assertAlmostEqual(feed.current_position_ms, 999.0)
        QTest.qWait(60)
        self.assertEqual(feed.throttle_stats["delivered"], 1)


    def test_position_seek_wrap_and_beyond_duration(self):
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_report({
            "duration_seconds": 1.0,
            "peak_envelope": {"db": [-20.0, -10.0], "hz": 10.0},
        })
        positions = []
        wraps = []
        feed.positionChanged.connect(positions.append)
        feed.positionWrapped.connect(wraps.append)

        feed.set_position_ms(375)
        self.assertAlmostEqual(feed.current_position_ms, 375.0)
        self.assertAlmostEqual(feed.position_ratio, 0.375)

        feed.set_position_ms(1375)
        self.assertAlmostEqual(feed.current_position_ms, 375.0)
        self.assertTrue(feed.snapshot()["wrapped"])
        self.assertEqual(wraps[-1], 375.0)

        feed.set_position_ms(-125)
        self.assertAlmostEqual(feed.current_position_ms, 875.0)
        self.assertTrue(feed.wrapped)

        feed.set_position_ms(250, wrapped=False, seek=True)
        self.assertAlmostEqual(feed.current_position_ms, 250.0)
        self.assertTrue(feed.seeked)
        self.assertFalse(feed.wrapped)

        feed.set_position_ms(1000)
        self.assertAlmostEqual(feed.current_position_ms, 0.0)
        self.assertAlmostEqual(feed.sample_at(1250)["position_ms"], 250.0)
        self.assertEqual(positions[-1], 0.0)
        self.assertEqual(feed.snapshot()["duration_ms"], 1000.0)

    def test_partial_side_merge_preserves_before_tracks_and_bus(self):
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_report({
            "duration_seconds": 2.0,
            "peak_envelope": {
                "tracks": [{"role": "vocal", "index": 0,
                            "db": [-18.0, -12.0], "hz": 10.0}],
                "bus": {"db": [-6.0, -3.0], "hz": 10.0},
            },
            "gr_envelope": {
                "tracks": [{"role": "vocal", "index": 0,
                            "db": [0.0, -3.0], "hz": 10.0}],
                "bus": {"db": [0.0, -2.0], "hz": 10.0},
            },
        })
        feed.set_report({
            "after": {
                "tracks": [{"role": "vocal", "index": 1,
                            "peak_dbfs": -8.0, "gr_db_p95": -1.0}],
            },
        })
        feed.set_position_ms(100)
        snapshot = feed.snapshot()
        lanes = {item["id"]: item for item in snapshot["lanes"]}
        self.assertEqual(feed.lane_ids, ("vocal:0", "vocal:1", "bus"))
        self.assertIn("vocal:0", lanes)
        self.assertIn("vocal:1", lanes)
        self.assertTrue(lanes["bus"]["present"])
        self.assertAlmostEqual(lanes["vocal:0"]["peak_dbfs"], -12.0, places=6)
        self.assertAlmostEqual(lanes["vocal:1"]["peak_dbfs"], -8.0, places=6)
        self.assertAlmostEqual(lanes["bus"]["peak_dbfs"], -3.0, places=6)

        feed.set_report({"after": {}})
        self.assertEqual(feed.lane_ids, ("vocal:0", "vocal:1", "bus"))
        feed.reset()
        self.assertEqual(feed.lane_ids, ())
        self.assertIsNone(feed.duration_ms)

    def test_multitrack_values_dbfs_keep_role_and_index(self):
        report = {
            "duration_seconds": 0.3,
            "peak_envelope": {
                "hz": 10.0,
                "time_seconds": [0.0, 0.1, 0.2],
                "tracks": [
                    {"role": "vocal", "index": 0, "values_dbfs": [-20.0, -10.0, -15.0]},
                    {"role": "vocal", "index": 1, "values_dbfs": [-22.0, -12.0, -16.0]},
                    {"role": "vocal", "index": 2, "values_dbfs": [-24.0, -14.0, -18.0]},
                    {"role": "backing", "index": 0, "values_dbfs": [-8.0, -6.0, -9.0]},
                ],
                "bus": {"role": "bus", "index": 0, "values_dbfs": [-6.0, -3.0, -7.0]},
            },
            "gr_envelope": {
                "hz": 10.0,
                "time_seconds": [0.0, 0.1, 0.2],
                "tracks": [
                    {"role": "vocal", "index": 0, "values_db": [0.0, -6.0, -3.0]},
                    {"role": "vocal", "index": 1, "values_db": [0.0, -4.0, -2.0]},
                    {"role": "vocal", "index": 2, "values_db": [0.0, -2.0, -1.0]},
                    {"role": "backing", "index": 0, "values_db": [0.0, -1.0, 0.0]},
                ],
            },
        }
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_report(report)
        feed.set_position_ms(100)
        snapshot = feed.snapshot()
        lanes = {item["id"]: item for item in snapshot["lanes"]}
        self.assertEqual(
            tuple(item["id"] for item in snapshot["tracks"]),
            ("vocal:0", "vocal:1", "vocal:2", "backing:0"),
        )
        self.assertEqual(snapshot["tracks"][2]["role"], "vocal")
        self.assertEqual(snapshot["tracks"][2]["index"], 2)
        self.assertEqual(snapshot["tracks"][3]["role"], "backing")
        self.assertAlmostEqual(lanes["vocal:0"]["peak_dbfs"], -10.0, places=6)
        self.assertAlmostEqual(lanes["vocal:1"]["peak_dbfs"], -12.0, places=6)
        self.assertAlmostEqual(lanes["vocal:2"]["peak_dbfs"], -14.0, places=6)
        self.assertAlmostEqual(lanes["backing:0"]["peak_dbfs"], -6.0, places=6)
        self.assertAlmostEqual(lanes["bus"]["peak_dbfs"], -3.0, places=6)
        self.assertAlmostEqual(lanes["vocal:1"]["gr_db"], -4.0, places=6)

    def test_position_refresh_does_not_reparse_or_repaint_per_sample(self):
        feed = live.MeterEnvelope(auto_start=True)
        feed.set_report(envelope_report())
        QTest.qWait(45)
        revision = feed.report_revision
        feed.reset_throttle_stats()
        started = time.perf_counter()
        for position in range(12000):
            feed.set_position_ms(position)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)
        self.assertEqual(feed.report_revision, revision)
        self.assertEqual(feed.pending_event_count, 1)
        self.assertEqual(feed.throttle_stats["received"], 12000)
        QTest.qWait(50)
        self.assertEqual(feed.throttle_stats["delivered"], 1)


class GrHistoryTests(unittest.TestCase):
    def test_history_rolls_to_latest_thirty_seconds(self):
        db = [0.0, -6.0, -3.0, -9.0, -12.0, 0.0]
        report = {
            "gr_envelope": {
                "tracks": [{"role": "vocal", "index": 0, "db": db, "hz": 2.0}],
                "bus": {"db": [-2.0, -4.0], "hz": 0.5},
            }
        }
        feed = live.MeterEnvelope(auto_start=False)
        view = live.GrHistoryView(feed=feed)
        view.set_report(report)
        for position in range(0, 32001, 500):
            view.set_position_ms(position)
        points = view.history_points("vocal:0")
        self.assertTrue(points)
        self.assertGreaterEqual(points[0][0], 2000.0)
        self.assertLessEqual(points[-1][0], 32000.0)
        self.assertLessEqual(len(points), 61)
        self.assertIn("分块RMS估计", view.caption)
        view.resize(520, 250)
        view.show()
        APP.processEvents()
        image = view.grab()
        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)

    def test_empty_gr_has_clear_placeholder_without_painter_errors(self):
        view = live.GrHistoryView(auto_start=False)
        self.assertEqual(view.objectName(), "mixdownGrHistory")
        view.resize(420, 210)
        view.show()
        APP.processEvents()
        self.assertFalse(view.has_data)
        self.assertEqual(view.available_lanes(), ())
        self.assertFalse(view.grab().isNull())


    def test_playhead_coordinates_follow_position_and_resize(self):
        feed = live.MeterEnvelope(auto_start=False)
        view = live.GrHistoryView(feed=feed, history_seconds=1.0)
        view.set_report({
            "duration_seconds": 2.0,
            "gr_envelope": {
                "tracks": [{"role": "vocal", "index": 0,
                            "db": [0.0, -6.0], "hz": 2.0}],
                "bus": {"db": [0.0, -3.0], "hz": 2.0},
            },
        })
        view.set_position_ms(500)
        graph = view.plot_rect()
        self.assertEqual(view.objectName(), "mixdownGrHistory")
        self.assertAlmostEqual(view.playhead_ratio(), 0.5, places=6)
        self.assertAlmostEqual(
            view.playhead_x(), graph.left() + graph.width() * 0.5, places=6)
        view.resize(420, 240)
        APP.processEvents()
        graph = view.plot_rect()
        first_x = view.playhead_x()
        self.assertAlmostEqual(
            (first_x - graph.left()) / graph.width(), 0.5, places=6)
        view.resize(680, 260)
        APP.processEvents()
        graph = view.plot_rect()
        self.assertGreater(view.playhead_x(), first_x)
        self.assertAlmostEqual(
            (view.playhead_x() - graph.left()) / graph.width(), 0.5, places=6)

    def test_shared_30hz_feed_updates_views_once(self):
        feed = live.MeterEnvelope(auto_start=True)
        gr = live.GrHistoryView(feed=feed)
        bank = live.MeterBankWidget(feed=feed)
        edc = live.ReverbDecayView(feed=feed)
        feed.set_report({
            "duration_seconds": 2.0,
            "peak_envelope": {"db": [-12.0, -8.0], "hz": 2.0},
            "gr_envelope": {"db": [0.0, -4.0], "hz": 2.0},
        })
        edc.set_edc([0.0, -12.0, -60.0], time_s=[0.0, 0.2, 0.8],
                    profile="natural", predelay_ms=20, cache_hit=True)
        QTest.qWait(45)
        gr_updates = []
        bank_updates = []
        positions = []
        gr.historyChanged.connect(lambda points: gr_updates.append(points))
        bank.levelsChanged.connect(lambda snapshot: bank_updates.append(snapshot))
        feed.positionChanged.connect(positions.append)
        feed.reset_throttle_stats()
        for position in range(1000):
            feed.set_position_ms(position)
        self.assertEqual(feed.pending_event_count, 1)
        QTest.qWait(55)
        self.assertEqual(len(positions), 1)
        self.assertEqual(len(gr_updates), 1)
        self.assertEqual(len(bank_updates), 1)
        self.assertAlmostEqual(edc.playback_position_ms, 999.0)
        self.assertEqual(feed.throttle_stats["delivered"], 1)


class ReverbDecayTests(unittest.TestCase):
    def test_empty_state_and_measured_edc_curve(self):
        view = live.ReverbDecayView()
        self.assertEqual(view.objectName(), "mixdownReverbDecay")
        self.assertFalse(view.has_data)
        self.assertIn("暂无实测 EDC", view.placeholder_text)
        report = {
            "reverb_edc": {
                "db": [0.0, -6.0, -18.0, -36.0, -60.0],
                "time_s": [0.0, 0.15, 0.35, 0.7, 1.05],
                "key": "natural:pre20:room0",
                "profile": "natural",
                "predelay_ms": 20,
                "cache_state": "hit",
            }
        }
        view.set_report(report)
        self.assertTrue(view.has_data)
        self.assertEqual(view.profile, "natural")
        self.assertEqual(view.predelay_ms, 20.0)
        self.assertIn("命中", view.cache_status)
        self.assertEqual(view.cache_key, "natural:pre20:room0")
        points = view.curve_points()
        self.assertEqual(len(points), 5)
        self.assertAlmostEqual(points[1][0], 150.0, places=6)
        self.assertAlmostEqual(points[-1][1], -60.0, places=6)
        view.resize(520, 250)
        view.show()
        APP.processEvents()
        self.assertFalse(view.grab().isNull())

    def test_computing_cache_miss_keeps_previous_curve(self):
        view = live.ReverbDecayView()
        view.set_edc([0.0, -12.0, -60.0], interval_ms=50.0,
                     profile="wide", predelay_ms=40, cache_state="hit",
                     key="wide:pre40")
        old = view.curve_points()
        self.assertTrue(old)
        view.set_report({
            "reverb_edc": {
                "key": "wide:pre40", "profile": "wide", "predelay_ms": 40,
                "cache_state": "miss", "computing": True,
            }
        })
        self.assertTrue(view.computing)
        self.assertEqual(view.curve_points(), old)
        self.assertEqual(view.cache_status, "计算中")
        view.set_report({"reverb_edc": None})
        self.assertFalse(view.has_data)
        self.assertIn("暂无实测 EDC", view.placeholder_text)


    def test_feed_position_drives_edc_playhead_and_resize(self):
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_report({"duration_seconds": 2.0})
        view = live.ReverbDecayView(feed=feed)
        view.set_edc([0.0, -12.0, -60.0], time_s=[0.0, 0.2, 1.0],
                     profile="wide", predelay_ms=20, cache_hit=True)
        view.set_position_ms(500)
        graph = view.plot_rect()
        self.assertEqual(view.objectName(), "mixdownReverbDecay")
        self.assertAlmostEqual(view.playback_position_ms, 500.0)
        self.assertAlmostEqual(view.playhead_ratio(), 0.25, places=6)
        self.assertAlmostEqual(
            view.playhead_x(), graph.left() + graph.width() * 0.25, places=6)
        view.resize(420, 260)
        APP.processEvents()
        graph = view.plot_rect()
        first_x = view.playhead_x()
        self.assertAlmostEqual(
            (first_x - graph.left()) / graph.width(), 0.25, places=6)
        view.resize(680, 280)
        APP.processEvents()
        graph = view.plot_rect()
        self.assertGreater(view.playhead_x(), first_x)
        self.assertAlmostEqual(
            (view.playhead_x() - graph.left()) / graph.width(), 0.25, places=6)

    def test_actual_edc_format_metadata_and_partial_merge(self):
        view = live.ReverbDecayView()
        view.set_report({
            "reverb_edc": {
                "edc_db": [0.0, -6.0, -18.0, -60.0],
                "time_seconds": [0.0, 0.1, 0.3, 1.0],
                "profile": "natural",
                "predelay_ms": 20,
                "cache_hit": True,
                "measured": True,
            },
        })
        self.assertTrue(view.has_data)
        self.assertTrue(view.measured)
        self.assertTrue(view.cache_hit)
        self.assertEqual(view.measurement_status, "实测")
        self.assertIn("0.00–1.00 s", view.time_range_text)
        self.assertEqual(view.curve_points()[1][0], 100.0)
        self.assertEqual(view.curve_points()[-1][0], 1000.0)
        self.assertAlmostEqual(view.predelay_x(), view.plot_rect().left()
                               + view.plot_rect().width() * 0.02, places=6)
        old_points = view.curve_points()
        view.set_report({"after": {}})
        self.assertEqual(view.curve_points(), old_points)
        view.set_report({"reverb_edc": None})
        self.assertFalse(view.has_data)
        self.assertFalse(view.measured)

    def test_transparent_light_and_dark_theme_render_remain_readable(self):
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_report({
            "duration_seconds": 2.0,
            "gr_envelope": {"db": [0.0, -12.0], "hz": 2.0},
        })
        feed.set_position_ms(500)
        widgets = [
            live.GrHistoryView(feed=feed),
            live.ReverbDecayView(feed=feed),
        ]
        widgets[1].set_edc([0.0, -12.0, -60.0], time_s=[0.0, 0.2, 1.0],
                           predelay_ms=20, cache_hit=True)
        palettes = [
            (QColor(0, 0, 0, 0), QColor(0, 0, 0, 0)),
            (QColor("#20232b"), QColor("#f7f7fb")),
            (QColor("#f7f8fa"), QColor("#171a21")),
        ]
        for widget in widgets:
            widget.resize(480, 240)
            widget.show()
            for base_color, text_color in palettes:
                palette = widget.palette()
                palette.setColor(QPalette.Base, base_color)
                palette.setColor(QPalette.Window, base_color)
                palette.setColor(QPalette.Text, text_color)
                palette.setColor(QPalette.WindowText, text_color)
                widget.setPalette(palette)
                widget.setAttribute(Qt.WA_TranslucentBackground, True)
                APP.processEvents()
                surface, text, grid, secondary = live._drawing_colors(widget)
                self.assertGreaterEqual(surface.alpha(), 180)
                self.assertGreaterEqual(live._contrast_ratio(surface, text), 4.0)
                self.assertFalse(widget.grab().isNull())
                del grid, secondary


class MeterBankTests(unittest.TestCase):
    def test_numeric_peak_hold_and_clip_latch(self):
        report = {
            "peak_envelope": {
                "tracks": [{"role": "vocal", "index": 0,
                            "db": [-6.0, -18.0, -24.0], "hz": 1.0}],
                "bus": {"db": [-0.5, -10.0, -12.0], "hz": 1.0},
            },
            "gr_envelope": {
                "tracks": [{"role": "vocal", "index": 0,
                            "db": [-2.0, -1.0, 0.0], "hz": 1.0}],
            },
        }
        feed = live.MeterEnvelope(auto_start=False)
        bank = live.MeterBankWidget(feed=feed, peak_hold_ms=1500)
        bank.set_report(report)
        bank.set_position_ms(1000)
        values = bank.formatted_lane_values
        self.assertIn("vocal:0", values)
        self.assertIn("-18.0 dBFS", values["vocal:0"])
        self.assertAlmostEqual(bank.peak_hold_db("vocal:0"), -6.0, places=6)
        bank.set_position_ms(2000)
        # 500 ms after the hold expiry, the held marker has started falling,
        # while remaining above the current -24 dBFS bar.
        self.assertLess(bank.peak_hold_db("vocal:0"), -6.0)
        self.assertGreater(bank.peak_hold_db("vocal:0"), -24.0)
        self.assertTrue(bank.clip_latched("bus"))
        bank.resize(520, 250)
        bank.show()
        APP.processEvents()
        self.assertFalse(bank.grab().isNull())

    def test_partial_report_merge_preserves_bus_and_lane_cursors(self):
        feed = live.MeterEnvelope(auto_start=False)
        bank = live.MeterBankWidget(feed=feed)
        bank.set_report({
            "duration_seconds": 2.0,
            "peak_envelope": {
                "tracks": [{"role": "vocal", "index": 0,
                            "db": [-18.0, -12.0], "hz": 10.0}],
                "bus": {"db": [-6.0, -3.0], "hz": 10.0},
            },
        })
        bank.set_report({
            "after": {
                "tracks": [{"role": "vocal", "index": 1, "peak_dbfs": -8.0}],
            },
        })
        bank.set_position_ms(500)
        self.assertEqual(bank.objectName(), "mixdownMeterBank")
        self.assertIn("vocal:0", bank.formatted_lane_values)
        self.assertIn("vocal:1", bank.formatted_lane_values)
        self.assertIn("bus", bank.formatted_lane_values)
        self.assertAlmostEqual(bank.playback_position_ms, 500.0)
        self.assertAlmostEqual(bank.playhead_ratio(), 0.25, places=6)
        rect = QRectF(10.0, 0.0, 200.0, 2.0)
        self.assertAlmostEqual(bank.lane_playhead_x(rect), 60.0, places=6)


    def test_clear_placeholder(self):
        view = live.MeterBankWidget(auto_start=False)
        self.assertEqual(view.objectName(), "mixdownMeterBank")
        view.resize(420, 220)
        view.show()
        APP.processEvents()
        self.assertFalse(view.grab().isNull())
        view.set_report({})
        self.assertFalse(any(item["present"] for item in view.snapshot["lanes"]))
        view.clear()
        self.assertEqual(view.snapshot["tracks"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
