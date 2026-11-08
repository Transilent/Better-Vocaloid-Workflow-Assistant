"""Offscreen interaction and analytic-response tests for EqCurveView."""
import copy
import math
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_RUNTIME = Path(sys.executable).resolve().parent
_QT = _RUNTIME / 'Lib' / 'site-packages' / 'PyQt5' / 'Qt5'
os.environ.setdefault('QT_PLUGIN_PATH', str(_QT / 'plugins'))
os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', str(_QT / 'plugins' / 'platforms'))

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QImage, QMouseEvent, QPalette, QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import mixdown_eq_curve as eq
from i18n import tr as translate


app = QApplication.instance() or QApplication([])


def settle(rounds=3):
    for _ in range(rounds):
        app.processEvents()


def send_mouse(widget, event_type, position, button, buttons,
               modifiers=Qt.NoModifier):
    event = QMouseEvent(
        event_type,
        QPointF(position),
        QPointF(widget.mapToGlobal(position)),
        button,
        buttons,
        modifiers,
    )
    QApplication.sendEvent(widget, event)
    settle()


def send_wheel(widget, position, delta, modifiers=Qt.NoModifier):
    event = QWheelEvent(
        QPointF(position),
        QPointF(widget.mapToGlobal(position)),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.NoButton,
        modifiers,
        Qt.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)
    settle()


def send_pixel_wheel(widget, position, pixels, modifiers=Qt.NoModifier):
    event = QWheelEvent(
        QPointF(position),
        QPointF(widget.mapToGlobal(position)),
        QPoint(0, pixels),
        QPoint(0, 0),
        Qt.NoButton,
        modifiers,
        Qt.NoScrollPhase,
        True,
    )
    QApplication.sendEvent(widget, event)
    settle()


def spectrum_envelope(frames, frequencies=None, *, interval_ms=100.0,
                      schema=eq.SPECTRUM_SCHEMA, stage=eq.SPECTRUM_STAGE,
                      fft_size=2048):
    return {
        'schema': schema,
        'interval_ms': interval_ms,
        'frequencies_hz': list(
            (100.0, 1000.0, 10000.0) if frequencies is None else frequencies),
        'frames_dbfs': [list(frame) for frame in frames],
        'stage': stage,
        'fft_size': fft_size,
    }


def live_spectrum_frame(before_dbfs, after_dbfs=None, frequencies=None, *,
                        source='pedalboard_eq', sample_rate=48000,
                        media_frame=0):
    before = list(before_dbfs)
    after = list(before if after_dbfs is None else after_dbfs)
    return {
        'source': source,
        'sample_rate': sample_rate,
        'media_frame': media_frame,
        'freq_hz': list(
            (20.0, 200.0, 2000.0, 10000.0)
            if frequencies is None else frequencies),
        'before_dbfs': before,
        'after_dbfs': after,
    }


class AnalyticCurveTests(unittest.TestCase):
    def test_defaults_and_deep_copy_contract(self):
        first = eq.default_bands()
        second = eq.default_bands()
        self.assertEqual([band['type'] for band in first], list(eq.BAND_TYPES))
        self.assertEqual(len(first), 4)
        self.assertEqual(first, list(eq.DEFAULT_BANDS))
        first[0]['freq_hz'] = 123.0
        first.append({'type': 'bell'})
        self.assertNotEqual(first, second)

    def test_log_frequency_mapping_and_endpoints(self):
        plot = QRectF(50.0, 10.0, 600.0, 210.0)
        frequencies = eq.logarithmic_frequencies(240)
        self.assertEqual(len(frequencies), 240)
        self.assertAlmostEqual(frequencies[0], eq.MIN_FREQUENCY_HZ)
        self.assertAlmostEqual(frequencies[-1], eq.MAX_FREQUENCY_HZ)
        self.assertAlmostEqual(eq.frequency_to_unit(10.0), 0.0)
        self.assertAlmostEqual(
            eq.frequency_to_unit(100.0),
            math.log10(100.0 / 10.0) /
            math.log10(eq.MAX_FREQUENCY_HZ / eq.MIN_FREQUENCY_HZ),
        )
        self.assertAlmostEqual(eq.frequency_to_unit(20000.0), 1.0)
        for frequency in (10.0, 92.0, 440.0, 3200.0, 20000.0):
            restored = eq.unit_to_frequency(eq.frequency_to_unit(frequency))
            self.assertAlmostEqual(restored, frequency, places=6)
        self.assertAlmostEqual(plot.left() + plot.width() / 3.0, 250.0)

    def test_rbj_response_peak_direction(self):
        bell = {'type': 'bell', 'freq_hz': 1000.0, 'gain_db': 12.0,
                'q': 1.0, 'enabled': True}
        low, center, high = eq.band_response_db(
            bell, [80.0, 1000.0, 12000.0]
        )
        self.assertGreater(center, 11.5)
        self.assertLess(center, 12.5)
        self.assertGreater(center, low + 6.0)
        self.assertGreater(center, high + 6.0)

        low_shelf = {'type': 'lowshelf', 'freq_hz': 180.0,
                     'gain_db': 6.0, 'q': 0.707, 'enabled': True}
        low_side, high_side = eq.band_response_db(
            low_shelf, [40.0, 10000.0]
        )
        self.assertGreater(low_side, high_side + 3.0)

        high_shelf = {'type': 'highshelf', 'freq_hz': 5000.0,
                      'gain_db': 6.0, 'q': 0.707, 'enabled': True}
        low_side, high_side = eq.band_response_db(
            high_shelf, [40.0, 12000.0]
        )
        self.assertGreater(high_side, low_side + 3.0)

        highpass = {'type': 'highpass', 'freq_hz': 100.0,
                    'gain_db': 0.0, 'q': 0.707, 'enabled': True}
        blocked, passed = eq.band_response_db(
            highpass, [20.0, 10000.0]
        )
        self.assertLess(blocked, -20.0)
        self.assertGreater(passed, -0.2)

        # Analytic evaluation may use a lower sample rate than the display
        # widget, as long as requested frequencies remain below Nyquist.
        low_rate = eq.band_response_db(highpass, [500.0, 3000.0],
                                       sample_rate=8000.0)
        self.assertTrue(all(math.isfinite(value) for value in low_rate))

        # Gain and Q are DSP-locked for the high-pass; the node therefore
        # stays on the 0 dB line and matches the filter actually auditioned.
        locked = eq.normalize_band({
            'type': 'highpass', 'freq_hz': 100.0, 'gain_db': 12.0,
            'q': 4.0, 'enabled': True,
        })
        self.assertEqual(locked['gain_db'], 0.0)
        self.assertAlmostEqual(locked['q'], eq.HIGHPASS_FIXED_Q)

    def test_total_response_respects_bypass_and_has_240_points(self):
        bands = eq.default_bands()
        for band in bands:
            band['enabled'] = False
        bands[2]['enabled'] = True
        bands[2]['gain_db'] = 9.0
        frequencies, values = eq.response_curve(bands)
        self.assertEqual(len(frequencies), eq.RESPONSE_POINTS)
        self.assertEqual(len(values), eq.RESPONSE_POINTS)
        center_index = min(
            range(len(frequencies)),
            key=lambda index: abs(frequencies[index] - 3200.0),
        )
        self.assertGreater(values[center_index], 8.0)
        zero = eq.frequency_response_db(
            bands, [3200.0], enabled_only=False
        )
        self.assertGreater(zero[0], 8.0)
        bands[2]['enabled'] = False
        self.assertEqual(
            eq.frequency_response_db(bands, [3200.0])[0], 0.0
        )
class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.view = eq.EQCurveView()
        self.view.resize(640, 320)
        self.view.show()
        settle()
        self.painted = []

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        settle()

    def test_initial_four_bands_node_size_focus_and_accessible_name(self):
        bands = self.view.bands()
        self.assertEqual(len(bands), 4)
        self.assertEqual([band['type'] for band in bands], list(eq.BAND_TYPES))
        self.assertEqual(self.view.objectName(), 'mixdownEqCurve')
        self.assertTrue(self.view.accessibleName())
        self.assertTrue(self.view.accessibleDescription())
        self.assertTrue(self.view.focusPolicy() & Qt.TabFocus)
        self.assertEqual(self.view.node_rect(0).width(), eq.NODE_DIAMETER)
        self.assertEqual(self.view.node_rect(0).height(), eq.NODE_DIAMETER)
        self.assertEqual(self.view.selected_band_index(), 0)
        self.assertEqual(self.view.minimumSizeHint(), QSize(320, 190))
        self.assertGreaterEqual(self.view.hit_radius(), 18.0)
        self.assertEqual(self.view.visible_frequency_grid()[-1], 20000)
        help_text = self.view.controls_help()
        for token in ('Shift', 'Ctrl', 'Space', 'Alt', '旁路', '恢复默认'):
            self.assertIn(token, help_text)
        summary = self.view.accessible_summary(2)
        self.assertIn('第 3 段', summary)
        self.assertIn('Q', summary)
        self.assertIn('频率', summary)

    def test_render_rate_shrinks_axis_to_nyquist_safe_limit(self):
        self.view.set_sample_rate(32000)
        self.assertAlmostEqual(self.view.sample_rate(), 32000.0)
        self.assertAlmostEqual(self.view.frequency_limit_hz(), 15200.0)
        frequencies = eq.logarithmic_frequencies(
            32, sample_rate=self.view.sample_rate())
        self.assertAlmostEqual(frequencies[-1], 15200.0)
        self.assertTrue(all(value < 16000.0 for value in frequencies))
        self.assertAlmostEqual(
            self.view.frequency_to_x(20000.0),
            self.view.plot_rect().right(),
        )
        self.assertAlmostEqual(
            self.view.x_to_frequency(self.view.plot_rect().right()),
            15200.0,
        )
        bands = self.view.bands()
        bands[3]['freq_hz'] = 20000.0
        self.view.set_bands(bands)
        self.assertAlmostEqual(self.view.bands()[3]['freq_hz'], 15200.0)
        self.assertEqual(self.view.band_limits(2)['freq_hz'],
                         (eq.MIN_FREQUENCY_HZ, 15200.0))
        self.assertIn(15200, self.view.visible_frequency_grid())

    def test_set_bands_and_bands_are_independent(self):
        replacement = [
            {'type': 'highpass', 'freq_hz': 70.0, 'gain_db': 0.0,
             'q': 0.75, 'enabled': True},
            {'type': 'lowshelf', 'freq_hz': 220.0, 'gain_db': -2.0,
             'q': 0.7, 'enabled': True},
            {'type': 'bell', 'freq_hz': 1800.0, 'gain_db': 3.0,
             'q': 1.2, 'enabled': True},
            {'type': 'highshelf', 'freq_hz': 11000.0, 'gain_db': 1.0,
             'q': 0.68, 'enabled': False},
        ]
        emitted = []
        self.view.bandsChanged.connect(emitted.append)
        self.view.set_bands(replacement)
        replacement[2]['freq_hz'] = 9999.0
        bands = self.view.bands()
        self.assertEqual(bands[2]['freq_hz'], 1800.0)
        self.assertEqual(emitted, [])
        bands[2]['q'] = 17.0
        self.assertNotEqual(self.view.bands()[2]['q'], 17.0)

    def test_spectrum_uses_relative_time_pause_and_segment_reset(self):
        frames = [
            [-60.0, -40.0, -20.0],
            [-50.0, -30.0, -10.0],
            [None, None, None],
            [-90.0, -80.0, -70.0],
        ]
        emitted = []
        self.view.bandsChanged.connect(emitted.append)
        self.assertTrue(self.view.set_spectrum_envelope(
            spectrum_envelope(frames)))
        self.assertIsNone(self.view.spectrum_frame())
        self.assertIsNone(self.view.spectrum_playhead_ms())
        self.assertFalse(self.view.spectrum_is_playing())

        self.assertTrue(self.view.set_spectrum_playhead(0))
        self.assertEqual(self.view.spectrum_frame(), frames[0])
        self.assertTrue(self.view.set_spectrum_playhead(99.9))
        self.assertEqual(self.view.spectrum_frame(), frames[0])
        self.assertTrue(self.view.set_spectrum_playhead(100))
        self.assertEqual(self.view.spectrum_frame(), frames[1])
        self.assertFalse(self.view.set_spectrum_playhead(200))
        self.assertIsNone(self.view.spectrum_frame())

        # Pause freezes automatic time progression, but an explicit seek still
        # selects the measured frame at that position.
        self.assertTrue(self.view.set_spectrum_playhead(
            310, playing=False))
        self.assertEqual(self.view.spectrum_frame(), frames[3])
        self.assertEqual(self.view.spectrum_playhead_ms(), 310.0)
        self.assertFalse(self.view.spectrum_is_playing())
        self.assertTrue(self.view.set_spectrum_playhead(
            0, playing=False))
        self.assertEqual(self.view.spectrum_frame(), frames[0])
        self.assertEqual(self.view.spectrum_playhead_ms(), 0.0)
        self.assertFalse(self.view.spectrum_is_playing())
        self.assertTrue(self.view.set_spectrum_playhead(
            110, playing=False))
        self.assertEqual(self.view.spectrum_frame(), frames[1])

        # A replacement envelope is a new audition segment; no old index or
        # frame survives. An already-paused A/B switch can position immediately.
        new_frames = [[-12.0, -8.0], [-14.0, -6.0]]
        self.assertTrue(self.view.set_spectrum_envelope(
            spectrum_envelope(new_frames, [200.0, 2000.0])))
        self.assertIsNone(self.view.spectrum_frame())
        self.assertIsNone(self.view.spectrum_playhead_ms())
        self.assertTrue(self.view.set_spectrum_playhead(
            50, playing=False))
        self.assertEqual(self.view.spectrum_frame(), new_frames[0])
        self.assertFalse(self.view.spectrum_is_playing())
        self.assertTrue(self.view.set_spectrum_playhead(
            150, playing=False))
        self.assertEqual(self.view.spectrum_frame(), new_frames[1])

        snapshot = self.view.spectrum_envelope()
        snapshot['frames_dbfs'][0][0] = 0.0
        self.assertEqual(self.view.spectrum_frame()[0], new_frames[1][0])
        self.view.clear_spectrum_envelope()
        self.assertIsNone(self.view.spectrum_envelope())
        self.assertIsNone(self.view.spectrum_frame())
        self.assertEqual(emitted, [])

    def test_invalid_empty_and_low_rate_spectrum_data_is_safe(self):
        valid = spectrum_envelope([[-12.0, -8.0, -4.0]])
        self.assertTrue(self.view.set_spectrum_envelope(valid))
        self.assertTrue(self.view.set_spectrum_playhead(0))

        cases = []
        bad = spectrum_envelope([[-12.0, -8.0, -4.0]], schema='bad')
        cases.append(bad)
        bad = spectrum_envelope([[-12.0, -8.0, -4.0]], stage='raw')
        cases.append(bad)
        bad = spectrum_envelope([[-12.0, -8.0, -4.0]], interval_ms=0.0)
        cases.append(bad)
        bad = spectrum_envelope([[-12.0, -8.0, -4.0]], fft_size=0)
        cases.append(bad)
        bad = spectrum_envelope([[-12.0, -8.0, -4.0]],
                                frequencies=[100.0, 200.0, 200.0])
        cases.append(bad)
        bad = spectrum_envelope([[-12.0, -8.0, -4.0]])
        bad['frames_dbfs'] = 'not frames'
        cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad):
                self.assertFalse(self.view.set_spectrum_envelope(bad))
                self.assertIsNone(self.view.spectrum_envelope())
                self.assertIsNone(self.view.spectrum_frame())

        empty = spectrum_envelope([], frequencies=[20.0, 1000.0])
        self.assertTrue(self.view.set_spectrum_envelope(empty))
        self.assertFalse(self.view.set_spectrum_playhead(0))
        self.assertIsNone(self.view.spectrum_frame())

        dirty = spectrum_envelope([
            [None, float('nan'), 'not-a-level'],
            [-60.0],
        ])
        self.assertTrue(self.view.set_spectrum_envelope(dirty))
        self.assertFalse(self.view.set_spectrum_playhead(0))
        self.assertIsNone(self.view.spectrum_frame())
        self.assertEqual(self.view._spectrum_segments(), [])

        # An 8 kHz-style capture finishes at 3.9 kHz. The renderer covers only
        # measured bins and never extends the curve to the widget's 20 kHz end.
        low_rate = spectrum_envelope(
            [[-80.0, -60.0, -40.0]], [20.0, 1000.0, 3900.0])
        self.assertTrue(self.view.set_spectrum_envelope(low_rate))
        self.assertTrue(self.view.set_spectrum_playhead(0))
        segments = self.view._spectrum_segments()
        self.assertEqual(len(segments), 1)
        self.assertLess(segments[0][-1].x(),
                        self.view.frequency_to_x(eq.MAX_FREQUENCY_HZ))
        self.assertAlmostEqual(
            self.view.spectrum_db_to_y(eq.SPECTRUM_MAX_DBFS),
            self.view.plot_rect().top(),
        )
        self.assertAlmostEqual(
            self.view.spectrum_db_to_y(eq.SPECTRUM_MIN_DBFS),
            self.view.plot_rect().bottom(),
        )

    def test_spectrum_is_background_and_preserves_nodes_and_editing(self):
        centers_before = [
            QPointF(self.view.band_node_position(index))
            for index in range(4)
        ]

        def render():
            image = QImage(640, 320, QImage.Format_ARGB32)
            image.fill(QColor('#14161c'))
            self.view.render(image)
            settle()
            return image

        baseline = render()
        node_pixels = [
            baseline.pixelColor(center.toPoint())
            for center in centers_before
        ]
        high_frame = [-5.0, -8.0, -12.0]
        low_frame = [-94.0, -92.0, -90.0]
        self.assertTrue(self.view.set_spectrum_envelope(
            spectrum_envelope([high_frame, low_frame])))
        self.assertTrue(self.view.set_spectrum_playhead(0))
        high = render()
        for index in (0, 2):
            self.assertEqual(
                high.pixelColor(centers_before[index].toPoint()),
                node_pixels[index],
            )
        self.assertEqual(
            [self.view.band_node_position(index) for index in range(4)],
            centers_before,
        )

        plot = self.view.plot_rect()
        sample = QPoint(
            round(self.view.frequency_to_x(1000.0)),
            round(plot.top() + plot.height() * 0.45),
        )
        high_pixel = high.pixelColor(sample)
        self.assertTrue(self.view.set_spectrum_playhead(100))
        low = render()
        self.assertNotEqual(low.pixelColor(sample), high_pixel)

        segments = self.view._spectrum_segments()
        self.assertEqual(len(segments), 1)
        for point, frequency in zip(
                segments[0], (100.0, 1000.0, 10000.0)):
            self.assertAlmostEqual(
                point.x(), self.view.frequency_to_x(frequency), places=6)

        index = 2
        start = self.view.band_node_position(index).toPoint()
        before = self.view.bands()[index]
        send_mouse(self.view, QEvent.MouseButtonPress, start,
                   Qt.LeftButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseMove, start + QPoint(18, -6),
                   Qt.NoButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseButtonRelease,
                   start + QPoint(18, -6), Qt.LeftButton, Qt.NoButton)
        after = self.view.bands()[index]
        self.assertGreater(after['freq_hz'], before['freq_hz'])
        self.assertGreater(after['gain_db'], before['gain_db'])
        self.assertEqual(self.view.spectrum_frame(), low_frame)

    def test_spectrum_accepts_and_distinguishes_before_after_stages(self):
        frame = [[-30.0, -18.0, -6.0]]
        before = spectrum_envelope(
            frame, stage=eq.SPECTRUM_STAGE_BEFORE)
        after = spectrum_envelope(
            frame, stage=eq.SPECTRUM_STAGE_AFTER)

        self.assertTrue(self.view.set_spectrum_envelope(before))
        self.assertEqual(
            self.view.spectrum_stage(), eq.SPECTRUM_STAGE_BEFORE)
        self.assertEqual(
            self.view.spectrum_envelope()['stage'],
            eq.SPECTRUM_STAGE_BEFORE,
        )
        self.assertEqual(
            self.view.spectrum_stage_label(), translate('A  处理前'))
        self.assertIn(
            translate('A  处理前'), self.view.accessibleDescription())
        before_color = self.view._spectrum_stage_color()

        self.assertTrue(self.view.set_spectrum_envelope(after))
        self.assertEqual(
            self.view.spectrum_stage(), eq.SPECTRUM_STAGE_AFTER)
        self.assertEqual(
            self.view.spectrum_envelope()['stage'],
            eq.SPECTRUM_STAGE_AFTER,
        )
        self.assertEqual(
            self.view.spectrum_stage_label(), translate('B  处理后'))
        self.assertIn(
            translate('B  处理后'), self.view.accessibleDescription())
        self.assertNotEqual(
            self.view.spectrum_stage_label(), translate('A  处理前'))
        self.assertNotEqual(
            self.view._spectrum_stage_color(), before_color)

    def test_quiet_partial_band_spectrum_stays_visible_while_paused(self):
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor('#1c1f27'))
        palette.setColor(QPalette.Base, QColor('#20242d'))
        palette.setColor(QPalette.WindowText, QColor('#eef1f8'))
        palette.setColor(QPalette.Text, QColor('#eef1f8'))
        palette.setColor(QPalette.Highlight, QColor('#8a72ff'))
        self.view.setPalette(palette)

        def render():
            image = QImage(640, 320, QImage.Format_ARGB32)
            image.fill(0)
            self.view.render(image)
            settle()
            return image

        baseline = render()
        quiet_frame = [-84.0, -90.0, -88.0]
        frequencies = [100.0, 1000.0, 3900.0]
        x = round(self.view.frequency_to_x(1000.0))
        y = round(self.view.spectrum_db_to_y(quiet_frame[1]))

        def color_distance(first, second):
            return (abs(first.red() - second.red())
                    + abs(first.green() - second.green())
                    + abs(first.blue() - second.blue()))

        for stage in (eq.SPECTRUM_STAGE_BEFORE, eq.SPECTRUM_STAGE_AFTER):
            with self.subTest(stage=stage):
                envelope = spectrum_envelope(
                    [quiet_frame], frequencies, stage=stage)
                self.assertTrue(self.view.set_spectrum_envelope(envelope))
                self.assertTrue(self.view.set_spectrum_playhead(
                    0, playing=False))
                paused = render()
                differences = []
                for delta_x in (-1, 0, 1):
                    for delta_y in (-1, 0, 1):
                        pixel = QPoint(x + delta_x, y + delta_y)
                        differences.append(color_distance(
                            baseline.pixelColor(pixel),
                            paused.pixelColor(pixel),
                        ))
                self.assertGreaterEqual(max(differences), 20)
                self.assertLess(
                    self.view._spectrum_segments()[-1][-1].x(),
                    self.view.frequency_to_x(eq.MAX_FREQUENCY_HZ),
                )
                self.assertFalse(self.view.spectrum_is_playing())

    def test_live_spectrum_contract_is_explicit_and_independent(self):
        emitted = []
        self.view.bandsChanged.connect(emitted.append)
        frame = live_spectrum_frame(
            [-80.0, -55.0, -28.0, -14.0],
            [-76.0, -48.0, -20.0, -9.0],
            source='pedalboard_rbj_dsp',
            media_frame=4321,
        )
        self.assertTrue(self.view.set_live_spectrum(frame))
        self.assertTrue(self.view.has_live_spectrum())
        self.assertEqual(self.view.live_spectrum_source(),
                         'pedalboard_rbj_dsp')
        self.assertEqual(self.view.live_spectrum_media_frame(), 4321)
        self.assertAlmostEqual(self.view.sample_rate(), 48000.0)
        self.assertIsNone(self.view.spectrum_stage())
        self.assertIn('实时频谱', self.view.live_spectrum_status_text())
        self.assertIn('A 处理前', self.view.live_spectrum_status_text())
        self.assertIn('B 处理后', self.view.live_spectrum_status_text())
        self.assertIn('pedalboard_rbj_dsp',
                      self.view.live_spectrum_status_text())
        self.assertIn('实时频谱', self.view.accessibleDescription())
        self.assertEqual(emitted, [])

        snapshot = self.view.live_spectrum()
        self.assertEqual(snapshot['freq_hz'], frame['freq_hz'])
        self.assertEqual(snapshot['before_dbfs'], frame['before_dbfs'])
        self.assertEqual(snapshot['after_dbfs'], frame['after_dbfs'])
        frame['before_dbfs'][0] = 0.0
        snapshot['after_dbfs'][0] = 0.0
        self.assertEqual(self.view.live_spectrum()['before_dbfs'][0], -80.0)
        self.assertEqual(self.view.live_spectrum()['after_dbfs'][0], -76.0)

        # A leading DC bin is real FFT input, but cannot be placed on the log
        # frequency axis. It is dropped without fabricating another bin.
        dc_frame = live_spectrum_frame(
            [-120.0, -100.0, -70.0, -20.0],
            frequencies=[0.0, 20.0, 2000.0, 24000.0],
            media_frame=4400,
        )
        self.assertTrue(self.view.set_live_spectrum(dc_frame))
        normalized = self.view.live_spectrum()
        self.assertEqual(normalized['freq_hz'][0], 20.0)
        self.assertEqual(len(normalized['before_dbfs']), 3)
        self.assertAlmostEqual(
            self.view.spectrum_db_to_y(-120.0),
            self.view.plot_rect().bottom(),
        )

        self.assertFalse(self.view.set_live_spectrum(None))
        self.assertFalse(self.view.has_live_spectrum())
        self.assertIsNone(self.view.live_spectrum())
        self.assertIsNone(self.view.spectrum_envelope())

    def test_live_spectrum_bounded_smoothing_tracks_changes_and_silence(self):
        old_before = (-90.0, -60.0, -30.0, -12.0)
        old_after = (-88.0, -52.0, -22.0, -6.0)
        first = live_spectrum_frame(
            old_before, old_after, media_frame=1000)
        self.assertTrue(self.view.set_live_spectrum(first))
        self.assertEqual(self.view.live_spectrum_display()['before_dbfs'],
                         list(old_before))

        new_before = (-20.0, -10.0, -3.0, -1.0)
        new_after = (-18.0, -8.0, -2.0, -0.5)
        louder = live_spectrum_frame(
            new_before, new_after, media_frame=1480)
        self.assertTrue(self.view.set_live_spectrum(louder))
        display = self.view.live_spectrum_display()
        attack = min(eq.LIVE_SPECTRUM_ATTACK_WEIGHT,
                     eq.LIVE_SPECTRUM_MAX_SMOOTHING_WEIGHT)
        for previous, current, actual in zip(
                old_before, new_before, display['before_dbfs']):
            expected = attack * previous + (1.0 - attack) * current
            self.assertAlmostEqual(actual, expected, places=9)
            self.assertGreater(actual, previous)
            self.assertLess(actual, current)
        louder_display = display

        silence_before = (-96.0,) * 4
        silence_after = (-96.0,) * 4
        silent = live_spectrum_frame(
            silence_before, silence_after, media_frame=1960)
        self.assertTrue(self.view.set_live_spectrum(silent))
        display = self.view.live_spectrum_display()
        release = min(eq.LIVE_SPECTRUM_RELEASE_WEIGHT,
                      eq.LIVE_SPECTRUM_MAX_SMOOTHING_WEIGHT)
        for previous, actual in zip(louder_display['after_dbfs'],
                                    display['after_dbfs']):
            self.assertTrue(math.isfinite(previous))
            self.assertTrue(math.isfinite(actual))
        self.assertAlmostEqual(display['before_dbfs'][0],
                               release * louder_display['before_dbfs'][0]
                               + (1.0 - release) * -96.0,
                               places=9)
        self.assertAlmostEqual(display['after_dbfs'][0],
                               release * louder_display['after_dbfs'][0]
                               + (1.0 - release) * -96.0,
                               places=9)

        # Continued real silence converges exactly to the measured floor; no
        # noise or synthetic spectrum is added to make a trace look active.
        for offset in range(1, 81):
            silent['media_frame'] = 1960 + offset
            self.assertTrue(self.view.set_live_spectrum(silent))
        steady = self.view.live_spectrum_display()
        for value in steady['before_dbfs'] + steady['after_dbfs']:
            self.assertAlmostEqual(value, -96.0, places=5)

        # A transport gap starts a new segment instead of carrying an old
        # smoothed line across a seek.
        resumed = live_spectrum_frame(
            new_before, new_after, media_frame=60000)
        self.assertTrue(self.view.set_live_spectrum(resumed))
        self.assertEqual(self.view.live_spectrum_display()['before_dbfs'],
                         list(new_before))

    def test_live_spectrum_invalid_values_clear_and_never_use_old_report(self):
        legacy = spectrum_envelope([[-20.0, -10.0, -5.0]])
        valid = live_spectrum_frame(
            [-80.0, -60.0, -40.0, -20.0],
            [-82.0, -58.0, -35.0, -12.0],
            media_frame=2000,
        )
        cases = []
        bad = copy.deepcopy(valid)
        bad.pop('source')
        cases.append(bad)
        cases.append({**copy.deepcopy(valid), 'source': '   '})
        cases.append({**copy.deepcopy(valid), 'sample_rate': 0})
        cases.append({**copy.deepcopy(valid), 'sample_rate': float('nan')})
        cases.append({**copy.deepcopy(valid), 'media_frame': -1})
        cases.append({**copy.deepcopy(valid), 'media_frame': 1.5})
        cases.append({**copy.deepcopy(valid), 'freq_hz': []})
        cases.append({**copy.deepcopy(valid),
                      'freq_hz': [20.0, 200.0, 200.0, 10000.0]})
        cases.append({**copy.deepcopy(valid),
                      'freq_hz': [-20.0, 200.0, 2000.0, 10000.0]})
        cases.append({**copy.deepcopy(valid),
                      'freq_hz': [20.0, 200.0, 2000.0, 24100.0]})
        cases.append({**copy.deepcopy(valid),
                      'before_dbfs': [-80.0, -60.0, -40.0]})
        cases.append({**copy.deepcopy(valid),
                      'before_dbfs': [-80.0, float('nan'), -40.0, -20.0]})
        cases.append({**copy.deepcopy(valid),
                      'after_dbfs': [-80.0, -60.0, float('inf'), -20.0]})
        cases.append({**copy.deepcopy(valid), 'freq_hz': 'not bins'})

        for bad in cases:
            with self.subTest(bad=bad):
                self.assertTrue(self.view.set_spectrum_envelope(legacy))
                self.assertTrue(self.view.set_live_spectrum(valid))
                self.assertFalse(self.view.set_live_spectrum(bad))
                self.assertFalse(self.view.has_live_spectrum())
                self.assertIsNone(self.view.live_spectrum())
                self.assertIsNone(self.view.live_spectrum_display())
                self.assertIsNone(self.view.spectrum_envelope())
                self.assertIsNone(self.view.spectrum_frame())
                self.assertEqual(self.view._spectrum_segments(), [])

    def test_live_spectrum_draws_both_stages_dbfs_axis_and_keeps_editing(self):
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor('#1c1f27'))
        palette.setColor(QPalette.Base, QColor('#20242d'))
        palette.setColor(QPalette.WindowText, QColor('#eef1f8'))
        palette.setColor(QPalette.Text, QColor('#eef1f8'))
        palette.setColor(QPalette.Highlight, QColor('#8a72ff'))
        self.view.setPalette(palette)

        def render():
            image = QImage(640, 320, QImage.Format_ARGB32)
            image.fill(QColor('#14161c'))
            self.view.render(image)
            settle()
            return image

        def max_distance(first, second, point):
            distances = []
            for delta_x in (-1, 0, 1):
                for delta_y in (-1, 0, 1):
                    position = QPoint(point.x() + delta_x,
                                      point.y() + delta_y)
                    one = first.pixelColor(position)
                    two = second.pixelColor(position)
                    distances.append(
                        abs(one.red() - two.red())
                        + abs(one.green() - two.green())
                        + abs(one.blue() - two.blue()))
            return max(distances)

        centers = [QPointF(self.view.band_node_position(index))
                   for index in range(4)]
        baseline = render()
        node_pixels = [baseline.pixelColor(center.toPoint())
                       for center in centers]
        frame = live_spectrum_frame(
            [-70.0, -38.0, -22.0, -16.0],
            [-74.0, -30.0, -16.0, -5.0],
            media_frame=9000,
        )
        self.assertTrue(self.view.set_live_spectrum(frame))
        before = self.view._live_spectrum_segments(eq.SPECTRUM_STAGE_BEFORE)
        after = self.view._live_spectrum_segments(eq.SPECTRUM_STAGE_AFTER)
        self.assertEqual(len(before), 1)
        self.assertEqual(len(after), 1)
        for point, frequency in zip(before[0], frame['freq_hz']):
            self.assertAlmostEqual(
                point.x(), self.view.frequency_to_x(frequency), places=6)
        self.assertNotEqual(before[0][2].y(), after[0][2].y())

        painted = render()
        for index in (0, 2):
            self.assertEqual(
                painted.pixelColor(centers[index].toPoint()),
                node_pixels[index],
            )
        self.assertEqual(
            [self.view.band_node_position(index) for index in range(4)],
            centers,
        )
        after_point = after[0][2].toPoint()
        self.assertGreaterEqual(
            max_distance(baseline, painted, after_point), 20)

        plot = self.view.plot_rect()
        axis_point = QPoint(
            round(plot.right() + 1.5),
            round(self.view.spectrum_db_to_y(-48.0)),
        )
        self.assertGreaterEqual(
            max_distance(baseline, painted, axis_point), 20)
        self.assertIn(eq.SPECTRUM_MIN_DBFS, eq.SPECTRUM_DBFS_GRID)
        self.assertIn(eq.SPECTRUM_MAX_DBFS, eq.SPECTRUM_DBFS_GRID)

        index = 2
        start = self.view.band_node_position(index).toPoint()
        old_band = self.view.bands()[index]
        send_mouse(self.view, QEvent.MouseButtonPress, start,
                   Qt.LeftButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseMove, start + QPoint(18, -6),
                   Qt.NoButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseButtonRelease,
                   start + QPoint(18, -6), Qt.LeftButton, Qt.NoButton)
        new_band = self.view.bands()[index]
        self.assertGreater(new_band['freq_hz'], old_band['freq_hz'])
        self.assertGreater(new_band['gain_db'], old_band['gain_db'])
        self.assertEqual(self.view.live_spectrum()['after_dbfs'],
                         frame['after_dbfs'])

    def test_log_axis_and_gain_direction(self):
        plot = self.view.plot_rect()
        self.assertAlmostEqual(
            self.view.frequency_to_x(eq.MIN_FREQUENCY_HZ), plot.left())
        self.assertAlmostEqual(self.view.frequency_to_x(20000.0), plot.right())
        third_frequency = eq.unit_to_frequency(1.0 / 3.0)
        self.assertAlmostEqual(
            self.view.frequency_to_x(third_frequency),
            plot.left() + plot.width() / 3.0,
        )
        sample_x = plot.left() + plot.width() * 0.57
        self.assertAlmostEqual(
            self.view.frequency_to_x(self.view.x_to_frequency(sample_x)),
            sample_x,
            places=6,
        )
        self.assertAlmostEqual(
            self.view.gain_to_y(eq.MAX_GAIN_DB), plot.top())
        self.assertAlmostEqual(self.view.gain_to_y(0.0), plot.center().y())
        self.assertAlmostEqual(
            self.view.gain_to_y(eq.MIN_GAIN_DB), plot.bottom())
        frequencies, response = self.view.response_curve()
        self.assertEqual(len(frequencies), eq.RESPONSE_POINTS)
        self.assertEqual(len(response), eq.RESPONSE_POINTS)
        self.assertEqual(len(self.view.frequency_response([1000.0])), 1)

    def test_drag_is_continuous_across_host_roundtrip_and_has_lifecycle(self):
        index = 2
        start = self.view.band_node_position(index).toPoint()
        before = self.view.bands()[index]
        changes = []
        selected = []
        started = []
        finished = []
        self.view.bandsChanged.connect(changes.append)
        # Mimic the real host: it normalises and calls set_bands synchronously
        # while handling every realtime change.
        self.view.bandsChanged.connect(lambda payload: self.view.set_bands(payload))
        self.view.bandSelected.connect(selected.append)
        self.view.editingStarted.connect(started.append)
        self.view.editingFinished.connect(lambda *args: finished.append(args))

        send_mouse(self.view, QEvent.MouseButtonPress, start,
                   Qt.LeftButton, Qt.LeftButton)
        for delta in (QPoint(14, -8), QPoint(29, -17), QPoint(44, -25)):
            send_mouse(self.view, QEvent.MouseMove, start + delta,
                       Qt.NoButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseButtonRelease, start + QPoint(44, -25),
                   Qt.LeftButton, Qt.NoButton)

        after = self.view.bands()[index]
        self.assertGreater(after['freq_hz'], before['freq_hz'])
        self.assertGreater(after['gain_db'], before['gain_db'])
        self.assertEqual(len(changes), 3)
        self.assertEqual(started, [index])
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0][0], index)
        self.assertEqual(finished[0][1], self.view.bands())
        self.assertTrue(selected)
        self.assertEqual(selected[0], index)
        for payload in changes:
            self.assertEqual(len(payload), 4)
        changes[0][index]['freq_hz'] = 20000.0
        self.assertNotEqual(self.view.bands()[index]['freq_hz'], 20000.0)

    def test_wheel_changes_q_and_shift_is_finer(self):
        index = 1
        position = self.view.band_node_position(index).toPoint()
        before = self.view.bands()[index]['q']
        emitted = []
        self.view.bandsChanged.connect(emitted.append)
        send_wheel(self.view, position, 120)
        normal_delta = self.view.bands()[index]['q'] - before
        self.assertGreater(normal_delta, 0.0)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(self.view.selected_band_index(), index)

        self.view.reset_band(index)
        before_fine = self.view.bands()[index]['q']
        send_wheel(self.view, position, 120, Qt.ShiftModifier)
        fine_delta = self.view.bands()[index]['q'] - before_fine
        self.assertGreater(fine_delta, 0.0)
        self.assertLess(fine_delta, normal_delta)

    def test_trackpad_pixel_wheel_changes_q(self):
        index = 1
        position = self.view.band_node_position(index).toPoint()
        before = self.view.bands()[index]['q']
        send_pixel_wheel(self.view, position, 1)
        self.assertGreater(self.view.bands()[index]['q'], before)

    def test_visible_bypass_alt_click_keyboard_and_accessible_text(self):
        index = 1
        position = self.view.band_node_position(index).toPoint()
        before = self.view.bands()[index]['enabled']
        emitted = []
        self.view.bandsChanged.connect(emitted.append)
        button = self.view.bypass_rect()
        self.assertTrue(QRectF(self.view.rect()).contains(button))
        self.assertGreaterEqual(button.height(), 20.0)

        send_mouse(self.view, QEvent.MouseButtonPress, position,
                   Qt.LeftButton, Qt.LeftButton, Qt.AltModifier)
        self.assertEqual(self.view.bands()[index]['enabled'], not before)
        send_mouse(self.view, QEvent.MouseButtonRelease, position,
                   Qt.LeftButton, Qt.NoButton, Qt.AltModifier)

        center = button.center().toPoint()
        send_mouse(self.view, QEvent.MouseButtonPress, center,
                   Qt.LeftButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseButtonRelease, center,
                   Qt.LeftButton, Qt.NoButton)
        self.assertEqual(self.view.bands()[index]['enabled'], before)

        self.view.setFocus()
        QTest.keyClick(self.view, Qt.Key_Space)
        settle()
        self.assertEqual(self.view.bands()[index]['enabled'], not before)
        self.assertGreaterEqual(len(emitted), 3)
        summary = self.view.accessible_summary(index)
        self.assertIn('旁路', summary)
        self.assertIn('Space', summary)
        self.assertIn('Alt', self.view.toolTip())

    def test_double_click_and_enter_restore_band_defaults(self):
        index = 2
        bands = eq.default_bands()
        bands[index].update({'freq_hz': 1800.0, 'gain_db': -5.0,
                             'q': 4.0, 'enabled': False})
        self.view.set_bands(bands)
        position = self.view.band_node_position(index).toPoint()
        emitted = []
        self.view.bandsChanged.connect(emitted.append)
        send_mouse(self.view, QEvent.MouseButtonDblClick, position,
                   Qt.LeftButton, Qt.LeftButton)
        self.assertEqual(self.view.bands()[index], eq.DEFAULT_BANDS[index])
        self.assertEqual(len(emitted), 1)

        bands[index].update({'freq_hz': 900.0, 'gain_db': 7.0,
                             'q': 2.0, 'enabled': True})
        self.view.set_bands(bands)
        self.view.select_band(index)
        self.view.setFocus()
        QTest.keyClick(self.view, Qt.Key_Return)
        settle()
        self.assertEqual(self.view.bands()[index], eq.DEFAULT_BANDS[index])
        self.assertEqual(len(emitted), 2)

    def test_arrow_keys_adjust_frequency_gain_and_q_with_precision(self):
        index = 2
        self.view.select_band(index)
        self.view.setFocus()
        settle()
        before = self.view.bands()[index]
        emitted = []
        self.view.bandsChanged.connect(emitted.append)

        QTest.keyClick(self.view, Qt.Key_Right)
        settle()
        after_right = self.view.bands()[index]
        normal_ratio = after_right['freq_hz'] / before['freq_hz']
        self.assertGreater(normal_ratio, 1.0)

        self.view.reset_band(index)
        before_fine = self.view.bands()[index]['freq_hz']
        QTest.keyClick(self.view, Qt.Key_Right, Qt.ShiftModifier)
        settle()
        fine_ratio = self.view.bands()[index]['freq_hz'] / before_fine
        self.assertGreater(fine_ratio, 1.0)
        self.assertLess(fine_ratio, normal_ratio)

        before_up = self.view.bands()[index]['gain_db']
        QTest.keyClick(self.view, Qt.Key_Up, Qt.ShiftModifier)
        settle()
        after_up = self.view.bands()[index]
        self.assertAlmostEqual(
            after_up['gain_db'] - before_up, 0.1, places=5
        )

        self.view.reset_band(index)
        before_q = self.view.bands()[index]['q']
        QTest.keyClick(self.view, Qt.Key_PageUp)
        settle()
        normal_q_ratio = self.view.bands()[index]['q'] / before_q
        self.assertGreater(normal_q_ratio, 1.0)
        self.view.reset_band(index)
        before_fine_q = self.view.bands()[index]['q']
        QTest.keyClick(self.view, Qt.Key_Up,
                       Qt.ControlModifier | Qt.ShiftModifier)
        settle()
        fine_q_ratio = self.view.bands()[index]['q'] / before_fine_q
        self.assertGreater(fine_q_ratio, 1.0)
        self.assertLess(fine_q_ratio, normal_q_ratio)
        self.assertGreaterEqual(len(emitted), 5)
        self.assertIn('Hz', self.view.bubble_text(index))
        self.assertIn('Q', self.view.bubble_text(index))

    def test_highpass_gain_and_q_are_locked_and_other_gain_is_clamped(self):
        bands = eq.default_bands()
        bands[0].update({'gain_db': 15.0, 'q': 3.0})
        self.view.set_bands(bands)
        self.assertEqual(self.view.bands()[0]['gain_db'], 0.0)
        self.assertAlmostEqual(self.view.bands()[0]['q'], 0.707)
        self.assertIsNone(self.view.band_limits(0)['gain_db'])
        self.assertIsNone(self.view.band_limits(0)['q'])
        self.assertEqual(self.view.band_limits(2)['freq_hz'],
                         (eq.MIN_FREQUENCY_HZ, eq.MAX_FREQUENCY_HZ))
        self.assertEqual(self.view.band_limits(2)['gain_db'],
                         (eq.MIN_GAIN_DB, eq.MAX_GAIN_DB))
        self.assertEqual(self.view.band_limits(2)['q'], (eq.MIN_Q, eq.MAX_Q))

        start = self.view.band_node_position(0).toPoint()
        send_mouse(self.view, QEvent.MouseButtonPress, start,
                   Qt.LeftButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseMove, start + QPoint(20, -80),
                   Qt.NoButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseButtonRelease, start + QPoint(20, -80),
                   Qt.LeftButton, Qt.NoButton)
        self.assertEqual(self.view.bands()[0]['gain_db'], 0.0)
        self.assertAlmostEqual(self.view.bands()[0]['q'], 0.707)
        self.assertGreater(self.view.bands()[0]['freq_hz'],
                           eq.DEFAULT_BANDS[0]['freq_hz'])

        index = 2
        start = self.view.band_node_position(index).toPoint()
        send_mouse(self.view, QEvent.MouseButtonPress, start,
                   Qt.LeftButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseMove, start + QPoint(0, -1000),
                   Qt.NoButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseButtonRelease, start + QPoint(0, -1000),
                   Qt.LeftButton, Qt.NoButton)
        self.assertEqual(self.view.bands()[index]['gain_db'], eq.MAX_GAIN_DB)

    def test_compact_320_by_180_renders_and_drags_with_touch_hit_radius(self):
        # The host deliberately lowers only the height constraint to 180.
        self.view.setMinimumHeight(180)
        self.view.resize(320, 180)
        settle()
        self.assertEqual(self.view.size(), QSize(320, 180))
        self.assertEqual(self.view.minimumSizeHint(), QSize(320, 190))
        self.assertEqual(self.view.visible_frequency_grid(),
                         (10, 100, 1000, 10000))
        image = QImage(320, 180, QImage.Format_ARGB32)
        image.fill(0)
        self.view.render(image)
        self.assertFalse(image.isNull())

        index = 2
        center = self.view.band_node_position(index).toPoint()
        self.assertTrue(self.view.rect().contains(center))
        near_hit = center - QPoint(int(self.view.hit_radius()) - 2, 0)
        self.assertEqual(self.view._hit_index(QPointF(near_hit)), index)
        started = []
        self.view.editingStarted.connect(started.append)
        send_mouse(self.view, QEvent.MouseButtonPress, center,
                   Qt.LeftButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseMove, center + QPoint(18, -6),
                   Qt.NoButton, Qt.LeftButton)
        send_mouse(self.view, QEvent.MouseButtonRelease, center + QPoint(18, -6),
                   Qt.LeftButton, Qt.NoButton)
        self.assertEqual(started, [index])
        button = self.view.bypass_rect()
        self.assertTrue(QRectF(self.view.rect()).contains(button))
        self.assertGreaterEqual(button.height(), 20.0)

    def test_response_matches_position_and_highpass_node_stays_zero(self):
        plot = self.view.plot_rect()
        self.assertAlmostEqual(self.view.band_node_position(0).y(),
                               plot.center().y())
        highpass = self.view.bands()[0]
        passed = eq.band_response_db(
            highpass, [10000.0], sample_rate=self.view.sample_rate())[0]
        self.assertGreater(passed, -0.2)

        bell = self.view.bands()[2]
        center_gain = eq.band_response_db(
            bell, [bell['freq_hz']], sample_rate=self.view.sample_rate())[0]
        self.assertAlmostEqual(center_gain, bell['gain_db'], places=2)
        self.assertAlmostEqual(self.view.band_node_position(2).y(),
                               self.view.gain_to_y(center_gain), places=2)
        frequencies, _ = self.view.response_curve()
        self.assertEqual(len(frequencies), eq.RESPONSE_POINTS)

    def test_curve_samples_are_cached_between_repaints(self):
        self.view.set_bands(self.view.bands())
        with patch.object(eq, 'logarithmic_frequencies',
                          wraps=eq.logarithmic_frequencies) as frequencies:
            first = QImage(640, 320, QImage.Format_ARGB32)
            first.fill(0)
            self.view.render(first)
            second = QImage(640, 320, QImage.Format_ARGB32)
            second.fill(0)
            self.view.render(second)
            settle()
            self.assertEqual(frequencies.call_count, 1)

            self.view.select_band(2)
            self.view.setFocus()
            QTest.keyClick(self.view, Qt.Key_Up)
            settle()
            third = QImage(640, 320, QImage.Format_ARGB32)
            third.fill(0)
            self.view.render(third)
            settle()
            self.assertEqual(frequencies.call_count, 2)

    def test_dark_light_and_transparent_palettes_remain_clear(self):
        def render(window, base, text_color):
            palette = QPalette()
            palette.setColor(QPalette.Window, QColor(*window))
            palette.setColor(QPalette.Base, QColor(*base))
            palette.setColor(QPalette.WindowText, QColor(*text_color))
            palette.setColor(QPalette.Text, QColor(*text_color))
            palette.setColor(QPalette.Highlight, QColor('#8a72ff'))
            self.view.setPalette(palette)
            image = QImage(640, 320, QImage.Format_ARGB32)
            image.fill(0)
            self.view.render(image)
            return image

        dark = render((28, 30, 36, 255), (32, 35, 42, 255),
                      (238, 241, 248, 255))
        light = render((244, 246, 250, 255), (252, 253, 255, 255),
                       (28, 31, 38, 255))
        dark_pixel = dark.pixelColor(5, 15)
        light_pixel = light.pixelColor(5, 15)
        self.assertLess(dark_pixel.lightness(), light_pixel.lightness())
        self.assertGreater(dark_pixel.alpha(), 200)
        self.assertGreater(light_pixel.alpha(), 200)

        transparent = render((0, 0, 0, 0), (0, 0, 0, 0),
                             (238, 241, 248, 255))
        transparent_pixel = transparent.pixelColor(5, 15)
        self.assertGreaterEqual(transparent_pixel.alpha(), 120)
        self.assertLess(transparent_pixel.alpha(), 200)
        plot_pixel = transparent.pixelColor(120, 70)
        self.assertLess(plot_pixel.alpha(), 220)

    def test_playhead_and_render_are_local_display_state(self):
        emitted = []
        self.view.bandsChanged.connect(emitted.append)
        self.view.set_playhead(0.4)
        self.assertEqual(self.view.playhead(), 0.4)
        image = QImage(640, 320, QImage.Format_ARGB32)
        image.fill(0)
        self.view.render(image)
        self.assertFalse(image.isNull())
        self.view.set_playhead(None)
        self.assertIsNone(self.view.playhead())
        self.assertEqual(emitted, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
