"""Physical editing area, docked timeline and default audition selection."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP / 'src'), str(APP / 'tests')]
from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtWidgets import QApplication, QWidget
import audio_mixdown_ui as ui
from desktop_theme import apply_theme
from mixdown_realtime_player import RealtimePreviewPlayer
from test_mixdown_realtime_player import Service

QT = QApplication.instance() or QApplication([])


class WorkspaceTests(unittest.TestCase):
    def fresh(self, native=False):
        owner = QWidget()
        original = RealtimePreviewPlayer
        def create(*args, **kwargs):
            kwargs['service_factory'] = Service
            return original(*args, **kwargs)
        with patch.object(ui.AudioMixdownDialog, '_prepare_component_support'), \
                patch.object(ui.AudioMixdownDialog, '_start_component_support_check'), \
                patch('mixdown_realtime_player.native_output_path', return_value=Path('fixture.dll') if native else None), \
                patch('mixdown_realtime_player.RealtimePreviewPlayer', side_effect=create):
            dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=False)
        dialog._audition_start_attempted = True
        dialog._support_ready = False
        dialog.support_panel.hide()
        dialog._sources_ready = lambda: False
        self.addCleanup(self.dispose, owner, dialog)
        return dialog

    def dispose(self, owner, d):
        d._preview_debounce.stop()
        d._audition_poll_timer.stop()
        native = d._native_preview_player
        if native is not None: native.close()
        d._audition_cleanup_complete = True
        d._audition_service = None
        d.done(0)
        d.deleteLater()
        owner.deleteLater()
        QT.processEvents()

    def settle(self):
        for _ in range(15): QT.processEvents()

    def test_native_is_default_without_creating_a_legacy_player(self):
        d = self.fresh(native=True)
        self.assertTrue(d.continuous_preview.isChecked())
        self.assertIs(d._player, d._native_preview_player)
        self.assertIsNone(d._legacy_preview_player)
        self.assertFalse(d._player._service.commands)

    def test_missing_component_selects_compatibility_without_restart_loop(self):
        d = self.fresh()
        self.assertFalse(d.continuous_preview.isChecked())
        self.assertFalse(d.continuous_preview.isEnabled())
        self.assertIs(d._player, d._legacy_preview_player)
        self.assertIsNone(d._native_preview_player)

    def test_physical_area_and_timeline_dock_across_sizes_and_scaling(self):
        d = self.fresh()
        d.show()
        d.eq_enabled.setChecked(True)
        records = []
        for scale in (100, 130, 150):
            d._vwa_appearance = dict(ui_scale=scale, material='solid')
            apply_theme(d, 'light' if scale != 130 else 'dark')
            for width, height in ((640,560), (1024,768), (1280,800), (1440,900), (1920,1080)):
                d.resize(width,height)
                self.settle()
                seek = d.player_position
                seek_rect = QRect(seek.mapTo(d, QPoint()), seek.size())
                self.assertGreaterEqual(seek.width(), d.width() - 90)
                self.assertGreaterEqual(seek.height(), 44)
                self.assertTrue(d.rect().contains(seek_rect), (scale,width,seek_rect,d.rect()))
                self.assertEqual(d._main_scroll.horizontalScrollBar().maximum(),0)
                if width >= 1280:
                    self.assertGreaterEqual(d.eq_curve.width(), int(width * .45))
                before = seek_rect
                d._main_scroll.verticalScrollBar().setValue(d._main_scroll.verticalScrollBar().maximum())
                self.settle()
                self.assertEqual(QRect(seek.mapTo(d, QPoint()), seek.size()),before)
                d._main_scroll.verticalScrollBar().setValue(0)
                records.append(dict(scale=scale,width=d.width(),height=d.height(),
                    curve=[d.eq_curve.width(),d.eq_curve.height()],
                    seek=[seek.width(),seek.height()],dock_y=seek_rect.y()))
        out=APP/'work/test-results';out.mkdir(parents=True,exist_ok=True)
        (out/'workspace-geometry.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')

    def test_curve_focus_expands_physical_area_and_preserves_values(self):
        d = self.fresh()
        d._vwa_appearance = dict(ui_scale=100, material='solid')
        apply_theme(d,'light')
        d.resize(1440,900)
        d.show()
        self.settle()
        old_width=d.eq_curve.width()
        bands=d.eq_curve.bands()
        d.expand_curve_button.setChecked(True)
        self.settle()
        self.assertGreater(d.eq_curve.width(),old_width*1.5)
        self.assertGreaterEqual(d.eq_curve.width(),d.width()-90)
        self.assertTrue(d.workspace._mixer_scroll.isHidden())
        self.assertEqual(d.eq_curve.bands(),bands)
        d.expand_curve_button.setChecked(False)
        self.settle()
        self.assertFalse(d.workspace._mixer_scroll.isHidden())
        self.assertEqual(d.eq_curve.bands(),bands)
        self.assertFalse(d.export_details_toggle.isChecked())
        d.export_details_toggle.setChecked(True)
        self.settle()
        self.assertTrue(d.fields['output'].isVisible())


if __name__ == '__main__':
    unittest.main()
