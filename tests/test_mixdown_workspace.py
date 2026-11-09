"""Physical mixer workspace, native-player ownership and export hierarchy."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP / 'src'), str(APP / 'tests')]

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

import audio_mixdown_ui as ui
from desktop_theme import apply_theme
from mixdown_realtime_player import RealtimePreviewPlayer
from test_mixdown_realtime_player import Service

QT = QApplication.instance() or QApplication([])


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.players = []

    def fresh(self, native=False):
        owner = QWidget()

        def create_player(*args, **kwargs):
            kwargs['service_factory'] = Service
            player = RealtimePreviewPlayer(*args, **kwargs)
            self.players.append(player)
            return player

        with patch.object(ui.AudioMixdownDialog, '_prepare_component_support'), \
                patch.object(ui.AudioMixdownDialog, '_start_component_support_check'), \
                patch('mixdown_realtime_player.native_output_path',
                      return_value=(Path('fixture-native-output.dll')
                                    if native else None)), \
                patch('mixdown_realtime_player.RealtimePreviewPlayer',
                      side_effect=create_player):
            dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=False)
        dialog._support_ready = False
        dialog.support_panel.hide()
        self.addCleanup(self.dispose, owner, dialog)
        return dialog

    def dispose(self, owner, dialog):
        dialog._preview_debounce.stop()
        dialog._audition_poll_timer.stop()
        dialog._player_seek_timer.stop()
        dialog._audition_closing = True
        dialog._audition_cleanup_complete = True
        for player in reversed(self.players):
            if player.is_alive:
                player.close()
        dialog.hide()
        dialog.deleteLater()
        owner.deleteLater()
        QT.processEvents()

    def settle(self, rounds=15):
        for _ in range(rounds):
            QT.processEvents()
            QTest.qWait(2)

    def test_single_native_player_is_created_when_component_is_available(self):
        d = self.fresh(native=True)
        self.assertIs(d._player, d._preview.player)
        self.assertIsInstance(d._player, RealtimePreviewPlayer)
        self.assertTrue(d._player.available)
        self.assertFalse(d._player._service.commands)

    def test_invalid_native_path_marks_single_player_unavailable(self):
        d = self.fresh()
        self.assertIs(d._player, d._preview.player)
        self.assertIsInstance(d._player, RealtimePreviewPlayer)
        self.assertFalse(d._player.available)
        self.assertFalse(d._player._service.commands)
        self.assertIn('组件缺失或校验失败', d.preview_report.text())

    def test_physical_area_and_timeline_dock_across_sizes_and_scaling(self):
        d = self.fresh()
        d.show()
        d.eq_enabled.setChecked(True)
        records = []
        for scale in (100, 130, 150):
            d._vwa_appearance = dict(ui_scale=scale, material='solid')
            apply_theme(d, 'light' if scale != 130 else 'dark')
            for width, height in ((640, 560), (1024, 768),
                                  (1280, 800), (1440, 900), (1920, 1080)):
                d.resize(width, height)
                self.settle()
                seek = d.player_position
                seek_rect = QRect(seek.mapTo(d, QPoint()), seek.size())
                self.assertTrue(d.isAncestorOf(seek))
                self.assertGreaterEqual(seek.width(), d.width() - 90)
                self.assertGreaterEqual(seek.height(), 44)
                self.assertTrue(d.rect().contains(seek_rect),
                                (scale, width, seek_rect, d.rect()))
                self.assertEqual(d._main_scroll.horizontalScrollBar().maximum(), 0)
                if width >= 1280:
                    self.assertGreaterEqual(d.eq_curve.width(), int(width * .45))
                before = seek_rect
                d._main_scroll.verticalScrollBar().setValue(
                    d._main_scroll.verticalScrollBar().maximum())
                self.settle()
                self.assertEqual(
                    QRect(seek.mapTo(d, QPoint()), seek.size()), before)
                d._main_scroll.verticalScrollBar().setValue(0)
                records.append(dict(
                    scale=scale, width=d.width(), height=d.height(),
                    curve=[d.eq_curve.width(), d.eq_curve.height()],
                    seek=[seek.width(), seek.height()], dock_y=seek_rect.y()))
        out = APP / 'work/test-results'
        out.mkdir(parents=True, exist_ok=True)
        (out / 'workspace-geometry.json').write_text(
            json.dumps(records, indent=2) + '\n', encoding='utf-8')

    def test_curve_focus_expands_physical_area_and_preserves_values(self):
        d = self.fresh()
        d._vwa_appearance = dict(ui_scale=100, material='solid')
        apply_theme(d, 'light')
        d.resize(1440, 900)
        d.show()
        self.settle()
        old_width = d.eq_curve.width()
        bands = d.eq_curve.bands()
        QTest.mouseClick(d.expand_curve_button, Qt.LeftButton)
        self.settle()
        self.assertGreater(d.eq_curve.width(), old_width * 1.5)
        self.assertGreaterEqual(d.eq_curve.width(), d.width() - 90)
        self.assertTrue(d.workspace._mixer_scroll.isHidden())
        self.assertEqual(d.eq_curve.bands(), bands)
        QTest.mouseClick(d.expand_curve_button, Qt.LeftButton)
        self.settle()
        self.assertFalse(d.workspace._mixer_scroll.isHidden())
        self.assertEqual(d.eq_curve.bands(), bands)
        self.assertFalse(d.export_details_toggle.isChecked())
        d._main_scroll.ensureWidgetVisible(d.export_details_toggle)
        self.settle()
        QTest.mouseClick(d.export_details_toggle, Qt.LeftButton)
        self.settle()
        self.assertTrue(d.fields['output'].isVisible())
        self.assertIs(d.export_details.parentWidget(), d.export_card)
        self.assertIsInstance(d.export_details.layout(), QVBoxLayout)

    def assert_export_geometry(self, d, context):
        toggle = d.export_details_toggle
        details = d.export_details
        title = next(
            widget for widget in d.export_card.findChildren(QLabel)
            if widget.property('role') == 'card-title')
        self.assertIs(details.parentWidget(), d.export_card)
        self.assertIsInstance(details.layout(), QVBoxLayout)
        self.assertTrue(d.export_card.isAncestorOf(toggle))
        self.assertTrue(d.export_card.isAncestorOf(details))
        controls = [
            toggle, d.fields['output'], d._choose_buttons['output'],
            d.output_state, d.export_button, d.register_button,
            d.to_video_button, d.result_title, d.result_text, d.export_note,
        ]
        controls = [widget for widget in controls if widget.isVisible()]
        self.assertIn(d.export_details_toggle, controls, context)
        self.assertGreaterEqual(len(controls), 8, context)
        rects = {
            widget: QRect(widget.mapTo(d.export_card, QPoint()), widget.size())
            for widget in controls
        }
        title_rect = QRect(
            title.mapTo(d.export_card, QPoint()), title.size())
        details_rect = QRect(
            details.mapTo(d.export_card, QPoint()), details.size())
        self.assertGreater(title_rect.width(), 0, context)
        self.assertGreater(details_rect.height(), 0, context)
        self.assertGreaterEqual(details_rect.top(), title_rect.bottom(), context)
        self.assertFalse(details_rect.intersects(title_rect), context)
        self.assertFalse(rects[toggle].intersects(title_rect), context)
        for widget, rect in rects.items():
            self.assertGreater(rect.width(), 0, (context, widget.objectName()))
            self.assertGreater(rect.height(), 0, (context, widget.objectName()))
            self.assertTrue(d.export_card.rect().contains(rect),
                            (context, widget.objectName(), rect,
                             d.export_card.rect()))
            self.assertFalse(rect.intersects(title_rect),
                             (context, widget.objectName(), rect, title_rect))
        items = list(rects.items())
        for index, (first, first_rect) in enumerate(items):
            for second, second_rect in items[index + 1:]:
                self.assertFalse(
                    first_rect.intersects(second_rect),
                    (context, first.objectName(), second.objectName(),
                     first_rect, second_rect))

    def test_expanded_export_controls_keep_real_geometry_at_multiple_sizes(self):
        d = self.fresh()
        d.show()
        d._main_scroll.ensureWidgetVisible(d.export_details_toggle)
        self.settle()
        self.assertFalse(d.export_details.isVisible())
        QTest.mouseClick(d.export_details_toggle, Qt.LeftButton)
        self.settle()
        self.assertTrue(d.export_details.isVisible())
        for scale, mode, material in (
                (100, 'light', 'solid'), (130, 'dark', 'glass'),
                (150, 'light', 'glass')):
            d._vwa_appearance = {
                'ui_scale': scale, 'material': material,
                'reduce_motion': False, 'ui_language': 'zh-CN'}
            apply_theme(d, mode)
            for width, height in ((1024, 768), (1280, 800),
                                  (1440, 900), (1920, 1080)):
                d.resize(width, height)
                d._main_scroll.ensureWidgetVisible(d.export_details_toggle)
                self.settle()
                self.assert_export_geometry(
                    d, (scale, mode, material, width, height))


if __name__ == '__main__':
    unittest.main()
