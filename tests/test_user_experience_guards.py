"""User-centered UI/UX guardrails: WCAG dual-theme contrast, QML layout overflow, and Chorus E2E flow."""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

import numpy as np
import soundfile as sf

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QColor, QGuiApplication

from appearance_state import save_appearance
from chorus_analysis import Features, Suggestion, _timing_match
from chorus_project import ChorusProject, Phrase, Recording
from quick_app import create_workspace
from quick_chorus import QuickChorus

APP = Path(__file__).resolve().parents[1]
QML_DIR = APP / 'src' / 'quick_ui' if (APP / 'src' / 'quick_ui').is_dir() else APP / 'quick_ui'


def _relative_luminance(color: QColor) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = channel(color.redF()), channel(color.greenF()), channel(color.blueF())
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg: QColor, bg: QColor) -> float:
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _token_color(tokens: QObject, name: str) -> QColor:
    value = tokens.property(name)
    color = QColor(value) if not isinstance(value, QColor) else value
    assert color.isValid(), f'Invalid theme color token {name!r}: {value!r}'
    return color


_QT_WARNINGS: list[str] = []


def _capture_qt_warning(kind, _ctx, msg):
    if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        _QT_WARNINGS.append(msg)


class UserExperienceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        # Install once per process (repeated qInstallMessageHandler(prev) toggles can crash PySide6).
        qInstallMessageHandler(_capture_qt_warning)

    def test_01_no_qt6_on_accent_signal_collision_in_qml(self):
        """Qt 6 parses `onAccent` as a signal handler for `accent`, silently turning button text black."""
        offenders = []
        pattern = re.compile(r'\bonAccent\b')
        for qml_file in sorted(QML_DIR.glob('*.qml')):
            text = qml_file.read_text(encoding='utf-8-sig')
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith('//'):
                    continue
                if pattern.search(stripped):
                    offenders.append(f'{qml_file.name}:{lineno}: {stripped}')
        self.assertEqual(offenders, [], f'Found forbidden Qt6 `onAccent` identifier: {offenders}')

    def test_02_wcag_contrast_and_light_mode_eye_comfort_live(self):
        """Verify live resolved Dark & Light theme tokens meet WCAG readability & matte Light ceiling."""
        save_appearance({'ui_language': 'zh-CN'})
        _QT_WARNINGS.clear()
        engine, window, owner, modes = create_workspace(self.app, viewport=(1440, 900), scale=100)
        try:
            tokens = window.property('tokens')
            self.assertIsNotNone(tokens)
            for mode in ('dark', 'light'):
                owner.support.setSetting('theme', mode)
                self.app.processEvents()
                self.assertEqual(bool(tokens.property('dark')), mode == 'dark')

                base = _token_color(tokens, 'base')
                surface = _token_color(tokens, 'surface')
                raised = _token_color(tokens, 'raised')
                sidebar = _token_color(tokens, 'sidebar')
                text = _token_color(tokens, 'text')
                muted = _token_color(tokens, 'muted')
                accent = _token_color(tokens, 'accent')
                accent_hover = _token_color(tokens, 'accentHover')
                accent_pressed = _token_color(tokens, 'accentPressed')
                accent_ink = _token_color(tokens, 'accentInk')

                # Primary body text must meet WCAG AAA (>= 7.0) on every surface.
                for bg_name, bg in (('base', base), ('surface', surface), ('raised', raised), ('sidebar', sidebar)):
                    ratio = _contrast_ratio(text, bg)
                    self.assertGreaterEqual(ratio, 7.0, f'{mode}: text on {bg_name} contrast {ratio:.2f} < 7.0')

                # Muted/secondary text must meet WCAG AA (>= 4.5) on every reading surface.
                for bg_name, bg in (('base', base), ('surface', surface), ('raised', raised)):
                    ratio = _contrast_ratio(muted, bg)
                    self.assertGreaterEqual(ratio, 4.5, f'{mode}: muted on {bg_name} contrast {ratio:.2f} < 4.5')

                # Primary accent button ink must meet WCAG AA (>= 4.5) across rest/hover/pressed states.
                for bg_name, bg in (('accent', accent), ('accentHover', accent_hover), ('accentPressed', accent_pressed)):
                    ratio = _contrast_ratio(accent_ink, bg)
                    self.assertGreaterEqual(ratio, 4.5, f'{mode}: accentInk on {bg_name} contrast {ratio:.2f} < 4.5')

                # Semantic status tones must be readable on cards (>= 3.0).
                for tone_name in ('success', 'error', 'attention'):
                    ratio = _contrast_ratio(_token_color(tokens, tone_name), surface)
                    self.assertGreaterEqual(ratio, 3.0, f'{mode}: {tone_name} on surface contrast {ratio:.2f} < 3.0')

                # Light mode should stay matte-paper comfortable (never blinding pure #ffffff page background).
                if mode == 'light':
                    base_lum = _relative_luminance(base)
                    self.assertLessEqual(base_lum, 0.88, f'Light mode base luminance {base_lum:.3f} is too glaring')
                    self.assertGreaterEqual(base_lum, 0.55, f'Light mode base luminance {base_lum:.3f} is too dark')

            self.assertEqual(_QT_WARNINGS, [], f'Theme switch produced QML warnings: {_QT_WARNINGS}')
        finally:
            window.close()
            modes.close()
            owner.close()
            engine.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_03_all_pages_render_without_qml_warnings_or_horizontal_overflow(self):
        """Navigate every route at 1280x720 and 1440x900 and catch clipped/overflowing top-level buttons."""
        save_appearance({'theme': 'dark', 'ui_language': 'zh-CN'})
        for viewport in ((1280, 720), (1440, 900)):
            _QT_WARNINGS.clear()
            engine, window, owner, modes = create_workspace(self.app, viewport=viewport, scale=100)
            try:
                loader = window.findChild(QObject, 'pageLoader')
                self.assertIsNotNone(loader)
                for route in owner.state.routes:
                    owner.state.navigate(route.page_id)
                    owner.navigationChanged.emit()
                    self.app.processEvents()
                    page = loader.property('item')
                    self.assertIsNotNone(page, f'Route {route.page_id} failed to instantiate')
                    self.assertGreater(page.width(), 0, route.page_id)
                    self.assertGreater(page.height(), 0, route.page_id)
                    # Check visible named buttons do not poke outside the window horizontally.
                    overflows = []
                    stack = [page]
                    while stack:
                        item = stack.pop()
                        if not item.isVisible():
                            continue
                        if item.objectName() and item.width() > 0 and item.height() > 0:
                            # Skip items inside horizontally scrollable flickables.
                            parent = item.parentItem()
                            in_hscroll = False
                            while parent is not None:
                                cw = parent.property('contentWidth')
                                if isinstance(cw, (int, float)) and cw > parent.width() + 2:
                                    in_hscroll = True
                                    break
                                parent = parent.parentItem()
                            if not in_hscroll:
                                right = item.mapToScene(item.boundingRect().bottomRight()).x()
                                if right > viewport[0] + 4.0:
                                    overflows.append((route.page_id, item.objectName(), round(right, 1), viewport[0]))
                        stack.extend(item.childItems())
                    self.assertEqual(overflows, [], f'Horizontal overflow at {viewport}: {overflows}')
                self.assertEqual(_QT_WARNINGS, [], f'QML warnings at {viewport}: {_QT_WARNINGS}')
            finally:
                window.close()
                modes.close()
                owner.close()
                engine.deleteLater()
                self.app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_04_chorus_review_cards_and_focus_issue_user_journey(self):
        """Silent rests must not flood review cards, and focusIssue must enter Piano Roll & scroll there."""
        with tempfile.TemporaryDirectory(prefix='ux-chorus-') as tmp:
            root = Path(tmp)
            sr = 44100
            t = np.linspace(0.0, 12.0, sr * 12, endpoint=False, dtype=np.float32)
            # 3s silent intro + 6s voiced phrase (440 Hz) + 3s silence
            wave = np.zeros_like(t)
            wave[sr * 3:sr * 9] = 0.25 * np.sin(2 * np.pi * 440.0 * t[sr * 3:sr * 9])
            p1 = root / '梦灯笼 - 01.wav'
            p2 = root / '梦灯笼 - 02.wav'
            sf.write(p1, wave, sr)
            sf.write(p2, wave, sr)

            r1 = Recording.read(p1, 'vocal')
            r2 = Recording.read(p2, 'vocal')
            # 1) Silent phrase must NOT generate a rhythm review error.
            silent_feat = Features(
                energy=np.zeros(1200, dtype=np.float32),
                shape=np.zeros((1200, 4), dtype=np.float32),
                active=np.zeros(1200, dtype=bool),
                peaks=[0.0] * 100,
            )
            val, detail, review = _timing_match(r1, r2, silent_feat, silent_feat, Phrase(0.0, 2.5), 0.6)
            self.assertFalse(review, f'Silent rest should not emit a review card: {(val, detail, review)}')
            self.assertEqual(detail, '')

            # 2) QuickChorus view & focusIssue journey.
            chorus = QuickChorus(None)
            try:
                project = ChorusProject(title='梦灯笼', id='ux-test-proj', tracks=(r1, r2), guide=r1.id)
                chorus.project = project
                chorus._selected = r1.id
                chorus._mode = 'phrase'
                chorus._view_first = 0.0
                chorus._view_span = 5.0
                chorus._position = 0.0
                chorus._suggestions = (
                    Suggestion(r2.id, 'timing', 7.2, 8.6, 0.04, '起音候选接近，建议试听', review=True,
                               category='timing_review', severity='medium'),
                )
                chorus._resolved = set()
                view = chorus.view
                self.assertEqual(len(view['issues']), 1)
                card = view['issues'][0]
                self.assertEqual(card['shortTrack'], '02')
                self.assertGreaterEqual(card['time'], 7.0)
                self.assertTrue(card['reason'])
                self.assertTrue(card['actionHint'])

                rev_before = view['focusRevision']
                chorus.focusIssue(card['id'])
                after = chorus.view
                self.assertEqual(after['mode'], 'pitch', 'focusIssue must switch directly into Piano Roll')
                self.assertEqual(after['selected'], r2.id, 'focusIssue must select the issue track')
                self.assertGreater(after['viewFirst'], 3.0, 'focusIssue must scroll viewport to the issue time')
                self.assertLessEqual(after['viewFirst'], card['time'], 'issue start must be inside scrolled viewport')
                self.assertGreater(chorus.position, 6.5, 'playhead must move near the issue start')
                self.assertGreater(after['focusRevision'], rev_before, 'focusRevision must bump to notify QML')
            finally:
                chorus.close()
                chorus.deleteLater()
                self.app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main(verbosity=2)
