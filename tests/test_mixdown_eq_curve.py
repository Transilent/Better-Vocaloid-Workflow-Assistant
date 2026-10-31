"""Offscreen interaction and analytic-response tests for EqCurveView."""
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
        self.assertAlmostEqual(eq.frequency_to_unit(20.0), 0.0)
        self.assertAlmostEqual(eq.frequency_to_unit(200.0), 1.0 / 3.0)
        self.assertAlmostEqual(eq.frequency_to_unit(2000.0), 2.0 / 3.0)
        self.assertAlmostEqual(eq.frequency_to_unit(20000.0), 1.0)
        for frequency in (20.0, 92.0, 440.0, 3200.0, 20000.0):
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

    def test_log_axis_and_gain_direction(self):
        plot = self.view.plot_rect()
        self.assertAlmostEqual(self.view.frequency_to_x(20.0), plot.left())
        self.assertAlmostEqual(self.view.frequency_to_x(20000.0), plot.right())
        self.assertAlmostEqual(
            self.view.frequency_to_x(200.0),
            plot.left() + plot.width() / 3.0,
        )
        sample_x = plot.left() + plot.width() * 0.57
        self.assertAlmostEqual(
            self.view.frequency_to_x(self.view.x_to_frequency(sample_x)),
            sample_x,
            places=6,
        )
        self.assertAlmostEqual(self.view.gain_to_y(18.0), plot.top())
        self.assertAlmostEqual(self.view.gain_to_y(0.0), plot.center().y())
        self.assertAlmostEqual(self.view.gain_to_y(-18.0), plot.bottom())
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
                         (20, 100, 1000, 10000))
        image = QImage(320, 180, QImage.Format_ARGB32)
        image.fill(0)
        self.view.render(image)
        self.assertFalse(image.isNull())

        index = 2
        center = self.view.band_node_position(index).toPoint()
        self.assertTrue(self.view.rect().contains(center))
        near_hit = center + QPoint(int(self.view.hit_radius()) - 2, 0)
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
