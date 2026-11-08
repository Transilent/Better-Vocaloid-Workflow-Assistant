"""Regression tests for actual live meter snapshots and their consumers."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("VWA_TESTING", "1")

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import mixdown_live_meters as live


APP = QApplication.instance() or QApplication([])


def live_frame(
    position_ms=0.0,
    *,
    vocal_peak=-18.0,
    vocal_rms=-24.0,
    vocal_gr=-1.0,
    bus_peak=-6.0,
    bus_rms=-12.0,
    bus_limiter_gr=-0.5,
    audible=True,
    source="continuous-preview:after",
    parameter_revision=1,
    history_epoch=4,
    duration_ms=60000.0,
):
    return {
        "tracks": [
            {
                "role": "vocal",
                "index": 0,
                "peak_dbfs": vocal_peak,
                "rms_dbfs": vocal_rms,
                "gr_db": vocal_gr,
                "audible": audible,
            }
        ],
        "bus": {
            "peak_dbfs": bus_peak,
            "rms_dbfs": bus_rms,
            "limiter_gr_db": bus_limiter_gr,
        },
        "position_ms": position_ms,
        "duration_ms": duration_ms,
        "history_epoch": history_epoch,
        "source": source,
        "parameter_revision": parameter_revision,
    }


class LiveSnapshotTests(unittest.TestCase):
    def test_publishes_actual_values_and_never_interpolates_old_measurements(self):
        feed = live.MeterEnvelope(auto_start=False)
        emitted = []
        feed.changed.connect(emitted.append)
        frame = live_frame(125.0)
        frame["tracks"].append({
            "role": "backing",
            "index": 0,
            "peak_dbfs": -8.25,
            "rms_dbfs": -14.5,
            "gr_db": None,
            "audible": False,
        })

        feed.set_live_snapshot(frame)

        self.assertEqual(len(emitted), 1)
        snapshot = feed.snapshot()
        self.assertEqual(feed.lane_ids, ("vocal:0", "backing:0", "bus"))
        self.assertEqual(snapshot["position_ms"], 125.0)
        self.assertEqual(snapshot["duration_ms"], 60000.0)
        self.assertEqual(snapshot["history_epoch"], 4)
        self.assertEqual(snapshot["source"], "continuous-preview:after")
        self.assertEqual(snapshot["parameter_revision"], 1)
        lanes = {row["id"]: row for row in snapshot["lanes"]}
        self.assertEqual(lanes["vocal:0"]["peak_dbfs"], -18.0)
        self.assertEqual(lanes["vocal:0"]["rms_dbfs"], -24.0)
        self.assertEqual(lanes["vocal:0"]["gr_db"], -1.0)
        self.assertTrue(lanes["vocal:0"]["audible"])
        self.assertIsNone(lanes["backing:0"]["gr_db"])
        self.assertFalse(lanes["backing:0"]["audible"])
        self.assertEqual(lanes["bus"]["peak_dbfs"], -6.0)
        self.assertEqual(lanes["bus"]["rms_dbfs"], -12.0)
        self.assertEqual(lanes["bus"]["limiter_gr_db"], -0.5)
        self.assertEqual(lanes["bus"]["gr_db"], -0.5)

        # The second frame omits backing entirely and omits vocal RMS,
        # audible, and bus RMS.  Those fields must become None/disappear in
        # the current frame instead of carrying values from 125 ms.
        next_frame = live_frame(
            250.0, vocal_peak=-9.5, vocal_gr=-2.25,
            bus_peak=-3.0, bus_limiter_gr=-1.75,
            history_epoch=4,
        )
        del next_frame["tracks"][0]["rms_dbfs"]
        del next_frame["tracks"][0]["audible"]
        del next_frame["bus"]["rms_dbfs"]
        feed.set_live_snapshot(next_frame)

        self.assertEqual(len(emitted), 2)
        snapshot = feed.snapshot()
        self.assertEqual(tuple(row["id"] for row in snapshot["tracks"]),
                         ("vocal:0",))
        vocal = snapshot["tracks"][0]
        self.assertEqual(vocal["peak_dbfs"], -9.5)
        self.assertIsNone(vocal["rms_dbfs"])
        self.assertIsNone(vocal["audible"])
        self.assertIsNone(snapshot["bus"]["rms_dbfs"])
        self.assertEqual(snapshot["bus"]["limiter_gr_db"], -1.75)
        self.assertEqual(snapshot["bus"]["gr_db"], -1.75)

        # Asking the report sampler for another time yields the unchanged live
        # frame.  It must not interpolate a synthetic value for 500 ms.
        sampled = feed.sample_at(500.0)
        self.assertEqual(sampled["position_ms"], 250.0)
        self.assertEqual(sampled["tracks"][0]["peak_dbfs"], -9.5)
        self.assertIsNone(feed.peak_db("vocal:0", 500.0))

    def test_missing_fields_and_reset_drop_all_previous_live_lanes(self):
        feed = live.MeterEnvelope(auto_start=False)
        feed.set_live_snapshot(live_frame(400.0))
        old_epoch = feed.history_epoch

        feed.set_live_snapshot({
            "tracks": [{"role": "vocal", "index": 0}],
            "position_ms": 450.0,
        })
        snapshot = feed.snapshot()
        self.assertEqual(feed.lane_ids, ("vocal:0",))
        self.assertEqual(snapshot["tracks"][0]["peak_dbfs"], None)
        self.assertEqual(snapshot["tracks"][0]["rms_dbfs"], None)
        self.assertEqual(snapshot["tracks"][0]["gr_db"], None)
        self.assertEqual(snapshot["tracks"][0]["audible"], None)
        self.assertEqual(snapshot["bus"]["peak_dbfs"], None)
        self.assertEqual(snapshot["bus"]["rms_dbfs"], None)
        self.assertEqual(snapshot["bus"]["limiter_gr_db"], None)
        self.assertFalse(snapshot["bus"]["present"])
        self.assertIsNone(snapshot["duration_ms"])
        self.assertIsNone(snapshot["source"])
        self.assertIsNone(snapshot["parameter_revision"])
        self.assertGreater(feed.history_epoch, old_epoch)

        cleared_epoch = feed.history_epoch
        feed.set_live_snapshot(None)
        snapshot = feed.snapshot()
        self.assertEqual(feed.lane_ids, ())
        self.assertEqual(snapshot["tracks"], [])
        self.assertEqual(snapshot["lanes"], [snapshot["bus"]])
        self.assertIsNone(snapshot["bus"]["peak_dbfs"])
        self.assertEqual(snapshot["position_ms"], 0.0)
        self.assertIsNone(snapshot["duration_ms"])
        self.assertGreater(feed.history_epoch, cleared_epoch)

    def test_pending_static_report_cannot_overwrite_live_output(self):
        feed = live.MeterEnvelope(auto_start=True)
        feed.set_report({
            "duration_seconds": 1.0,
            "peak_envelope": {"db": [-50.0, -50.0], "hz": 20.0},
        })
        feed.set_live_snapshot(live_frame(20.0, vocal_peak=-11.0))
        QTest.qWait(50)
        self.assertEqual(feed.current_position_ms, 20.0)
        self.assertEqual(feed.snapshot()["tracks"][0]["peak_dbfs"], -11.0)

        # Once live, a separate player position tick is not paired with stale
        # meter values to manufacture a new measurement.
        feed.set_position_ms(500.0)
        self.assertEqual(feed.current_position_ms, 20.0)


class LiveGrHistoryTests(unittest.TestCase):
    def test_live_gr_history_uses_actual_track_and_bus_values_and_cuts_epochs(self):
        feed = live.MeterEnvelope(auto_start=False)
        view = live.GrHistoryView(feed=feed)

        feed.set_live_snapshot(live_frame(
            0.0, vocal_gr=-0.5, bus_limiter_gr=-0.25))
        view.set_lane("vocal:0")
        feed.set_live_snapshot(live_frame(
            100.0, vocal_gr=-1.25, bus_limiter_gr=-0.5))
        feed.set_live_snapshot(live_frame(
            200.0, vocal_gr=-2.0, bus_limiter_gr=-0.75))

        self.assertEqual(view.history_points("vocal:0"), (
            (0.0, -0.5), (100.0, -1.25), (200.0, -2.0)))
        self.assertEqual(view.history_points("bus"), (
            (0.0, -0.25), (100.0, -0.5), (200.0, -0.75)))

        # A missing measurement changes only the current reading and does not
        # invent a zero history point.
        frame = live_frame(300.0)
        frame["tracks"][0]["gr_db"] = None
        feed.set_live_snapshot(frame)
        self.assertIsNone(view.current_gr_db)
        self.assertEqual(view.history_points("vocal:0")[-1], (200.0, -2.0))

        epoch_before_seek = feed.history_epoch
        feed.set_live_snapshot(live_frame(
            50.0, vocal_gr=-3.0, bus_limiter_gr=-1.0))
        self.assertTrue(feed.seeked)
        self.assertGreater(feed.history_epoch, epoch_before_seek)
        self.assertEqual(view.history_points("vocal:0"), ((50.0, -3.0),))

        epoch_before_forward_seek = feed.history_epoch
        feed.set_live_snapshot(live_frame(
            3050.0, vocal_gr=-0.75, bus_limiter_gr=-0.25))
        self.assertTrue(feed.seeked)
        self.assertGreater(feed.history_epoch, epoch_before_forward_seek)
        self.assertEqual(view.history_points("vocal:0"), ((3050.0, -0.75),))

        epoch_before_revision = feed.history_epoch
        feed.set_live_snapshot(live_frame(
            3060.0, vocal_gr=-1.0, bus_limiter_gr=-0.5,
            parameter_revision=2))
        self.assertGreater(feed.history_epoch, epoch_before_revision)
        self.assertEqual(view.history_points("vocal:0"), ((3060.0, -1.0),))

        epoch_before_source = feed.history_epoch
        feed.set_live_snapshot(live_frame(
            3070.0, vocal_gr=-2.0, source="continuous-preview:before"))
        self.assertGreater(feed.history_epoch, epoch_before_source)
        self.assertEqual(view.history_points("vocal:0"), ((3070.0, -2.0),))

        epoch_before_backend_epoch = feed.history_epoch
        feed.set_live_snapshot(live_frame(
            3080.0, vocal_gr=-4.0, history_epoch=5))
        self.assertGreater(feed.history_epoch, epoch_before_backend_epoch)
        self.assertEqual(view.history_points("vocal:0"), ((3080.0, -4.0),))


class LiveReverbStateTests(unittest.TestCase):
    def test_live_meters_do_not_invent_an_edc_curve(self):
        feed = live.MeterEnvelope(auto_start=False)
        view = live.ReverbDecayView(feed=feed)
        feed.set_live_snapshot(live_frame(1000.0))

        self.assertFalse(view.has_data)
        self.assertFalse(view.measured)
        self.assertEqual(view.curve_points(), ())
        self.assertIn("暂无实测 EDC", view.placeholder_text)
        self.assertEqual(view.playback_position_ms, 1000.0)
        view.resize(480, 240)
        view.show()
        APP.processEvents()
        self.assertFalse(view.grab().isNull())

        # The offline report API still accepts an explicitly measured impulse
        # EDC and renders it without changing the live-meter contract.
        view.set_report({
            "reverb_edc": {
                "edc_db": [0.0, -6.0, -18.0, -60.0],
                "time_seconds": [0.0, 0.1, 0.3, 1.0],
                "profile": "measured-impulse",
                "predelay_ms": 20,
                "measured": True,
                "cache_hit": True,
            }
        })
        self.assertTrue(view.has_data)
        self.assertTrue(view.measured)
        self.assertEqual(view.measurement_status, "实测")
        self.assertEqual(len(view.curve_points()), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
