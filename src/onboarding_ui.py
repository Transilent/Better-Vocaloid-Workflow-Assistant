"""First-run guided tour that overlays the real workspace window."""

from collections.abc import Mapping
from dataclasses import dataclass
from PyQt5 import sip

from PyQt5.QtCore import QEvent, QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop_theme import theme_colors
from i18n import tr as _tr
from support_info import QQ_GROUP


@dataclass(frozen=True)
class _TourStep:
    """One page destination and the copy shown for it."""

    page: str
    title: str
    body: str
    anchor: object = 'navigation'


def _default_steps():
    return (
        _TourStep(
            'processing',
            _tr('欢迎与准备'),
            _tr('从一首歌开始：选择哔哩哔哩链接或本地音频，按需填写歌词和准备选项。VWA 会把准备、调音、混音与发布串成可复查的本地工作流。'),
        ),
        _TourStep(
            'components',
            _tr('模型与组件'),
            _tr('在这里查看语音分离模型和本地运行组件的状态。缺少必要组件时，页面会给出安装入口；内置功能不需要额外下载。'),
        ),
        _TourStep(
            'tuning',
            _tr('调音'),
            _tr('打开 SVP 工程，选择演唱轨与参数来源，预览风格与参数变化后另存新工程。歌声合成和完整听感确认在对应编辑器中完成。'),
        ),
        _TourStep(
            'audio_mixdown',
            _tr('简易混音'),
            _tr('简易混音用于平衡人声、和声与伴奏。调整电平和声像后先试听，确认无误再导出音频。'),
        ),
        _TourStep(
            'video_mixdown',
            _tr('合成视频'),
            _tr('将音频、画面或字幕合成为视频。导出前可在预览区检查时间、画面和成片参数。'),
        ),
        _TourStep(
            'publishing',
            _tr('发布'),
            _tr('发布页整理成片、封面和说明，并在提交平台前显示待核对内容。平台操作始终由你确认。'),
        ),
        _TourStep(
            'updates',
            _tr('设置与支持'),
            _tr('设置页提供界面外观、组件、更新与运行状态。需要帮助时，可从本页的社区信息找到交流渠道。'),
        ),
    )


def _coerce_step(raw):
    if isinstance(raw, _TourStep):
        return raw
    if not isinstance(raw, Mapping):
        raise TypeError('Onboarding steps must be mappings or _TourStep values')
    page = raw.get('page', 'processing')
    page = page if isinstance(page, str) and page else 'processing'
    return _TourStep(
        page=page,
        title=_tr(str(raw.get('title', _tr('新手教程')))),
        body=_tr(str(raw.get('body', ''))),
        anchor=raw.get('anchor', 'navigation'),
    )


class OnboardingTour(QWidget):
    """Blocking spotlight guide for the main window.

    The overlay is a child of ``window``. It never persists completion state:
    callers decide whether a finished reason should be recorded.
    """

    finished = pyqtSignal(str)

    def __init__(self, window, steps=None):
        super().__init__(window)
        self.window = window
        self._steps = tuple(_coerce_step(step) for step in (_default_steps() if steps is None else steps))
        self._active = False
        self._index = 0
        self._original_page = None
        self._spotlight = QRect()
        self._app_filter_installed = False
        self._applying_style = False

        self.setObjectName('onboardingTour')
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setAccessibleName(_tr('新手教程'))

        self._build_ui()
        self._apply_style()
        self.window.installEventFilter(self)
        self._sync_geometry()
        self.hide()

    @property
    def active(self):
        return self._active

    @property
    def spotlight_rect(self):
        """Return a copy of the current highlighted window rectangle."""
        return QRect(self._spotlight)

    def _build_ui(self):
        self._card = QFrame(self)
        self._card.setObjectName('onboardingCard')
        self._card.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self._card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._eyebrow = QLabel(_tr('新手教程'), self._card)
        self._eyebrow.setObjectName('onboardingEyebrow')
        header.addWidget(self._eyebrow, 1)
        self._skip = QPushButton(_tr('跳过教程'), self._card)
        self._skip.setObjectName('onboardingSkip')
        self._skip.setAccessibleName(_tr('跳过教程'))
        self._skip.setToolTip(_tr('按 Esc 跳过教程'))
        self._skip.clicked.connect(self.skip)
        header.addWidget(self._skip)
        layout.addLayout(header)

        self._title = QLabel('', self._card)
        self._title.setObjectName('onboardingTitle')
        self._title.setWordWrap(True)
        self._title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(self._title)

        self._progress = QLabel('', self._card)
        self._progress.setObjectName('onboardingProgress')
        layout.addWidget(self._progress)

        self._body = QTextBrowser(self._card)
        self._body.setObjectName('onboardingBody')
        self._body.setFrameShape(QFrame.NoFrame)
        self._body.setReadOnly(True)
        self._body.setOpenExternalLinks(False)
        self._body.setTextInteractionFlags(Qt.NoTextInteraction)
        self._body.setFocusPolicy(Qt.NoFocus)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._body.document().setDocumentMargin(0)
        self._body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._body, 1)

        self._support = QFrame(self._card)
        self._support.setObjectName('onboardingSupport')
        self._support.setAttribute(Qt.WA_StyledBackground, True)
        support_layout = QVBoxLayout(self._support)
        support_layout.setContentsMargins(12, 9, 12, 9)
        support_layout.setSpacing(3)
        support_title = QLabel(_tr('社区支持'), self._support)
        support_title.setObjectName('onboardingSupportTitle')
        support_layout.addWidget(support_title)
        self._support_text = QLabel(_tr('QQ 群：{0}', QQ_GROUP), self._support)
        self._support_text.setObjectName('onboardingQQGroup')
        self._support_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        support_layout.addWidget(self._support_text)
        layout.addWidget(self._support)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self._previous = QPushButton(_tr('上一步'), self._card)
        self._previous.setObjectName('onboardingPrevious')
        self._previous.clicked.connect(self.previous_step)
        actions.addWidget(self._previous)
        actions.addStretch(1)
        self._next = QPushButton(_tr('下一步'), self._card)
        self._next.setObjectName('onboardingNext')
        self._next.setDefault(True)
        self._next.clicked.connect(self.next_step)
        actions.addWidget(self._next)
        layout.addLayout(actions)

    def _ui_scale(self):
        appearance = getattr(self.window, '_vwa_appearance', None) or {}
        try:
            value = float(appearance.get('ui_scale', 100)) / 100.0
        except (TypeError, ValueError):
            value = 1.0
        return max(0.8, min(1.6, value))

    def _theme(self):
        app = QApplication.instance()
        appearance = getattr(self.window, '_vwa_appearance', None) or {}
        picker = getattr(self.window, 'theme_picker', None)
        picker_mode = picker.currentData() if picker is not None else None
        mode = None
        if app is not None:
            mode = app.property('vwa-theme')
        mode = mode or appearance.get('theme') or picker_mode or 'light'
        material = 'solid'
        if app is not None:
            material = app.property('vwa-material') or material
        material = appearance.get('material') or material
        return ('glass' if material == 'glass' else 'solid'), (mode if mode in ('light', 'dark') else 'light')

    def _apply_style(self):
        if self._applying_style:
            return
        self._applying_style = True
        try:
            _, mode = self._theme()
            # Text must not blend with the page under a spotlight card.
            colors = theme_colors(mode, 'solid')
            scale = self._ui_scale()
            small = max(12, round(14 * scale))
            body_size = max(14, round(16 * scale))
            title_size = max(20, round(24 * scale))
            pad_x = max(14, round(22 * scale))
            pad_y = max(12, round(18 * scale))
            radius = max(12, round(16 * scale))
            button_pad_y = max(5, round(7 * scale))
            button_pad_x = max(9, round(13 * scale))
            self.setStyleSheet(f'''
QFrame#onboardingCard {{
    background-color: {colors['surface']};
    border: 1px solid {colors['border']};
    border-radius: {radius}px;
}}
QLabel#onboardingEyebrow {{
    color: {colors['accent']};
    font-size: {small}px;
    font-weight: 600;
}}
QLabel#onboardingTitle {{
    color: {colors['text']};
    font-size: {title_size}px;
    font-weight: 600;
}}
QLabel#onboardingProgress {{
    color: {colors['muted']};
    font-size: {small}px;
}}
QTextBrowser#onboardingBody {{
    background: transparent;
    border: 0;
    color: {colors['text']};
    font-size: {body_size}px;
    selection-background-color: {colors['selected']};
    selection-color: {colors['selected_text']};
}}
QFrame#onboardingSupport {{
    background-color: {colors['selected']};
    border: 1px solid {colors['border']};
    border-radius: {max(7, round(10 * scale))}px;
}}
QLabel#onboardingSupportTitle {{
    color: {colors['selected_text']};
    font-size: {small}px;
    font-weight: 600;
}}
QLabel#onboardingQQGroup {{
    color: {colors['selected_text']};
    font-size: {body_size}px;
}}
QPushButton#onboardingSkip {{
    background: transparent;
    color: {colors['muted']};
    border: 1px solid transparent;
    border-radius: {max(7, round(9 * scale))}px;
    padding: {button_pad_y}px {button_pad_x}px;
    font-size: {small}px;
}}
QPushButton#onboardingSkip:hover,
QPushButton#onboardingSkip:focus {{
    color: {colors['text']};
    background-color: {colors['button_hover']};
    border-color: {colors['border_hover']};
}}
QPushButton#onboardingPrevious,
QPushButton#onboardingNext {{
    background-color: {colors['button']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    border-radius: {max(7, round(9 * scale))}px;
    padding: {button_pad_y}px {button_pad_x}px;
    font-size: {body_size}px;
}}
QPushButton#onboardingPrevious:hover,
QPushButton#onboardingNext:hover {{
    background-color: {colors['button_hover']};
    border-color: {colors['border_hover']};
}}
QPushButton#onboardingPrevious:focus,
QPushButton#onboardingNext:focus {{
    border-color: {colors['accent']};
}}
QPushButton#onboardingNext {{
    background-color: {colors['accent']};
    color: {colors['accent_text']};
    border-color: {colors['accent']};
    font-weight: 600;
}}
QPushButton#onboardingNext:hover {{
    background-color: {colors['accent_hover']};
    border-color: {colors['accent_hover']};
}}
QPushButton#onboardingPrevious:disabled {{
    background-color: {colors['disabled']};
    color: {colors['disabled_text']};
    border-color: {colors['border']};
}}
''')
            self._card.layout().setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        finally:
            self._applying_style = False
        self.update()

    def _current_page_id(self):
        navigation = getattr(self.window, 'navigation', None)
        if navigation is None:
            return None
        value = getattr(navigation, 'current_page_id', None)
        if callable(value):
            try:
                value = value()
            except Exception:
                return None
        return value if isinstance(value, str) and value else None

    def _navigate(self, page_id):
        navigate = getattr(self.window, 'navigate', None)
        if not callable(navigate) or not isinstance(page_id, str) or not page_id:
            return
        try:
            navigate(page_id)
        except Exception:
            return

    def _navigation_button(self, page_id):
        navigation = getattr(self.window, 'navigation', None)
        if navigation is None:
            return None
        getter = getattr(navigation, 'button', None)
        if not callable(getter):
            return None
        try:
            return getter(page_id)
        except Exception:
            return None

    def _navigation_page(self, page_id):
        navigation = getattr(self.window, 'navigation', None)
        if navigation is None:
            return None
        getter = getattr(navigation, 'widget', None)
        if not callable(getter):
            return None
        try:
            return getter(page_id)
        except Exception:
            return None

    def _page_attribute(self, page_id):
        if not isinstance(page_id, str) or not page_id.isidentifier():
            return None
        return getattr(self.window, page_id, None)

    def _rect_for_widget(self, widget):
        if not isinstance(widget, QWidget):
            return QRect()
        try:
            origin = widget.mapTo(self.window, QPoint(0, 0))
        except RuntimeError:
            return QRect()
        rect = QRect(origin, widget.size()).intersected(self.rect())
        if rect.width() < 8 or rect.height() < 8:
            return QRect()
        return rect

    def _anchor_rect(self, step):
        anchor = step.anchor
        candidates = []
        if callable(anchor):
            try:
                anchor = anchor()
            except Exception:
                anchor = None
        if isinstance(anchor, QWidget):
            candidates.append(anchor)
        elif anchor == 'page':
            candidates.extend((self._page_attribute(step.page), self._navigation_page(step.page)))
        elif anchor == 'whole':
            return self.rect().adjusted(8, 8, -8, -8)
        else:
            candidates.extend((self._navigation_button(step.page), self._navigation_page(step.page), self._page_attribute(step.page)))
        for widget in candidates:
            rect = self._rect_for_widget(widget)
            if rect.isValid() and not rect.isEmpty():
                return rect
        return self.rect().adjusted(8, 8, -8, -8)

    def _sync_geometry(self):
        if self.parentWidget() is self.window:
            self.setGeometry(self.window.rect())
        if not self._steps:
            self._spotlight = QRect()
            return
        step = self._steps[min(self._index, len(self._steps) - 1)]
        self._spotlight = self._anchor_rect(step)
        self._place_card()
        self.update()

    def _place_card(self):
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return
        scale = self._ui_scale()
        margin = max(12, round(20 * min(scale, 1.25)))
        available = self.rect().adjusted(margin, margin, -margin, -margin)
        if available.width() <= 0 or available.height() <= 0:
            return
        card_width = min(
            max(round(340 * scale), round(width * 0.34)),
            round(540 * scale),
            available.width(),
        )
        card_width = max(1, card_width)
        self._card.setFixedWidth(card_width)
        self._card.layout().activate()
        preferred_height = max(round(310 * scale), self._card.sizeHint().height())
        card_height = min(preferred_height, available.height())
        card_height = max(1, card_height)
        self._card.resize(card_width, card_height)

        gap = max(10, round(16 * scale))
        spot = self._spotlight
        x = None
        y = None
        if spot.isValid() and not spot.isEmpty():
            right_x = spot.right() + 1 + gap
            if right_x + card_width <= available.right() + 1:
                x = right_x
                y = spot.center().y() - card_height // 2
            left_x = spot.left() - gap - card_width
            if x is None and left_x >= available.left():
                x = left_x
                y = spot.center().y() - card_height // 2
            below_y = spot.bottom() + 1 + gap
            if x is None and below_y + card_height <= available.bottom() + 1:
                x = spot.center().x() - card_width // 2
                y = below_y
            above_y = spot.top() - gap - card_height
            if x is None and above_y >= available.top():
                x = spot.center().x() - card_width // 2
                y = above_y
        if x is None:
            x = available.right() - card_width + 1
            y = available.bottom() - card_height + 1
        x = max(available.left(), min(int(x), available.right() - card_width + 1))
        y = max(available.top(), min(int(y), available.bottom() - card_height + 1))
        self._card.setGeometry(QRect(x, y, card_width, card_height))
        self._card.raise_()

    def _show_step(self):
        if not self._steps:
            return
        self._index = max(0, min(self._index, len(self._steps) - 1))
        step = self._steps[self._index]
        self._title.setText(step.title)
        self._body.setPlainText(step.body)
        self._body.verticalScrollBar().setValue(0)
        self._progress.setText(_tr('第 {0} / {1} 步', self._index + 1, len(self._steps)))
        self._previous.setEnabled(self._index > 0)
        last = self._index == len(self._steps) - 1
        self._next.setText(_tr('完成') if last else _tr('下一步'))
        self._support.setVisible(last)
        self._navigate(step.page)
        self._apply_style()
        self._sync_geometry()

    def start(self):
        """Start a new tour session, or refocus the active one."""
        if not self._steps:
            return
        if self._active:
            self._sync_geometry()
            self.raise_()
            self.setFocus(Qt.ActiveWindowFocusReason)
            return
        self._original_page = self._current_page_id()
        self._index = 0
        self._active = True
        app = QApplication.instance()
        if app is not None and not self._app_filter_installed:
            app.installEventFilter(self)
            self._app_filter_installed = True
        self._show_step()
        self.show()
        self.raise_()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def next_step(self):
        if not self._active:
            return
        if self._index + 1 >= len(self._steps):
            self._finish('completed', restore_page=True)
            return
        self._index += 1
        self._show_step()

    def previous_step(self):
        if not self._active or self._index <= 0:
            return
        self._index -= 1
        self._show_step()

    def skip(self):
        if self._active:
            self._finish('skipped', restore_page=True)

    def dismiss(self):
        if self._active:
            self._finish('closed', restore_page=False)

    def _finish(self, reason, restore_page):
        if reason not in ('completed', 'skipped', 'closed') or not self._active:
            return
        self._active = False
        original_page = self._original_page
        self._original_page = None
        if restore_page and original_page is not None:
            self._navigate(original_page)
        app = QApplication.instance()
        if app is not None and self._app_filter_installed:
            app.removeEventFilter(self)
        self._app_filter_installed = False
        self.hide()
        self.finished.emit(reason)

    def _scope_object(self, obj):
        return obj is self.window or (isinstance(obj, QWidget) and self.window.isAncestorOf(obj))

    def _handle_key(self, event):
        if not self._active or event.type() not in (QEvent.KeyPress, QEvent.ShortcutOverride):
            return False
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key_Escape:
            if event.type() == QEvent.KeyPress:
                self.skip()
            return True
        if modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        if key == Qt.Key_Left:
            if event.type() == QEvent.KeyPress:
                self.previous_step()
            return True
        if key == Qt.Key_Right:
            if event.type() == QEvent.KeyPress:
                self.next_step()
            return True
        return False

    def _focus_inside_tour(self):
        focus = QApplication.focusWidget()
        return focus is None or focus is self or self.isAncestorOf(focus)

    def focusNextPrevChild(self, forward):
        if not self._active:
            return super().focusNextPrevChild(forward)
        controls = [control for control in (self._skip, self._previous, self._next)
                    if control.isVisible() and control.isEnabled()]
        current = QApplication.focusWidget()
        index = controls.index(current) if current in controls else (-1 if forward else 0)
        controls[(index + (1 if forward else -1)) % len(controls)].setFocus(Qt.TabFocusReason)
        return True

    def eventFilter(self, watched, event):
        if sip.isdeleted(self.window):
            self._active = False
            if self._app_filter_installed and QApplication.instance() is not None:
                QApplication.instance().removeEventFilter(self)
            self._app_filter_installed = False
            return False
        if self._active and self._scope_object(watched):
            if self._handle_key(event):
                return True
            inside = watched is self or self.isAncestorOf(watched)
            if not inside and event.type() in (QEvent.KeyPress, QEvent.ShortcutOverride):
                return True
            if not inside and event.type() == QEvent.FocusIn:
                self.setFocus(Qt.OtherFocusReason)
        if watched is self.window:
            event_type = event.type()
            if event_type == QEvent.Resize and self._active:
                self._sync_geometry()
            elif event_type in (QEvent.StyleChange, QEvent.PaletteChange,
                                QEvent.FontChange, QEvent.ApplicationFontChange):
                self._apply_style()
                if self._active:
                    self._sync_geometry()
        elif watched is QApplication.instance() and self._active:
            focus = QApplication.focusWidget()
            if focus is None or self._scope_object(focus):
                if self._handle_key(event):
                    return True
                if not self._focus_inside_tour() and event.type() in (QEvent.KeyPress, QEvent.ShortcutOverride):
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if self._handle_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.setFocus(Qt.MouseFocusReason)
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def contextMenuEvent(self, event):
        event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        if self._active:
            self._sync_geometry()
            self.setFocus(Qt.ActiveWindowFocusReason)

    def closeEvent(self, event):
        self.dismiss()
        event.accept()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        outer = QPainterPath()
        outer.addRect(QRectF(self.rect()))
        if self._spotlight.isValid() and not self._spotlight.isEmpty():
            hole = QPainterPath()
            radius = max(8, round(12 * self._ui_scale()))
            hole.addRoundedRect(QRectF(self._spotlight), radius, radius)
            dim = outer.subtracted(hole)
        else:
            dim = outer
        _, mode = self._theme()
        shade = QColor(3, 6, 18, 185) if mode == 'dark' else QColor(14, 18, 36, 125)
        painter.fillPath(dim, shade)
        if self._spotlight.isValid() and not self._spotlight.isEmpty():
            colors = theme_colors(mode, 'solid')
            painter.setPen(QPen(QColor(colors['accent']), max(2, round(2 * self._ui_scale()))))
            radius = max(8, round(12 * self._ui_scale()))
            painter.drawRoundedRect(QRectF(self._spotlight).adjusted(1, 1, -1, -1), radius, radius)
        painter.end()
