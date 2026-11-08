"""Mixing workspace: a source pane beside a large editor; transport is docked."""
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import QFrame, QGridLayout, QScrollArea, QSizePolicy, QWidget


class MixerWorkspace(QWidget):
    WIDE_BREAKPOINT = 1200
    MEDIUM_BREAKPOINT = 980
    SIDE_CARD_MIN_WIDTH = 340

    def __init__(self, mixer, processing, preview, export, parent=None, chrome=()):
        super().__init__(parent)
        self.setObjectName('mixer-workspace')
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.cards = (mixer, processing, preview, export)
        self._chrome = tuple(chrome)
        self._columns = 0
        self._mode = 'stacked'
        self._curve_expanded = False
        self.rack_console = None
        self._descriptions = []
        for card in self.cards:
            card.setMinimumWidth(0)
            card.setMaximumWidth(16777215)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            item = card.layout().itemAt(1) if card.layout() else None
            description = item.widget() if item else None
            if description is not None and description.property('role') == 'muted':
                self._descriptions.append(description)
        self._mixer_scroll = QScrollArea(self)
        self._mixer_scroll.setObjectName('mixdownSourcePane')
        self._mixer_scroll.setFrameShape(QFrame.NoFrame)
        self._mixer_scroll.setWidgetResizable(True)
        self._mixer_scroll.setMinimumWidth(0)
        self._mixer_scroll.setWidget(mixer)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.reflow(force=True)

    def column_count(self):
        return self._columns

    def mode(self):
        return self._mode

    @property
    def curve_expanded(self):
        return self._curve_expanded

    def set_curve_expanded(self, enabled):
        enabled = bool(enabled)
        if enabled != self._curve_expanded:
            self._curve_expanded = enabled
            self.reflow(force=True)

    def set_rack_console(self, console):
        self.rack_console = console
        self._sync_rack_console()

    def _sync_rack_console(self):
        if self.rack_console is not None:
            desired = self._mode == 'wide'
            if getattr(self.rack_console, '_wide_rack_hint', None) != desired:
                self.rack_console.set_wide_rack(desired)

    def reflow(self, force=False):
        mode = ('wide' if self.width() >= self.WIDE_BREAKPOINT else
                'medium' if self.width() >= self.MEDIUM_BREAKPOINT else 'stacked')
        columns = 1 if mode == 'stacked' or self._curve_expanded else 2
        compact = mode != 'stacked'
        body = getattr(self, '_body_layout', None)
        if body is not None:
            body.setContentsMargins(12, 10, 12, 10)
            body.setSpacing(10)
        for widget in self._chrome:
            widget.setVisible(not compact and not self._curve_expanded)
        for description in self._descriptions:
            description.setVisible(False)
        for card in self.cards[:2]:
            card.layout().setContentsMargins(14, 10, 14, 10)
            card.layout().setSpacing(8)
        self._sync_rack_console()
        if not force and columns == self._columns and mode == self._mode:
            return
        self._columns, self._mode = columns, mode
        while self.grid.count():
            self.grid.takeAt(0)
        processing, export = self.cards[1], self.cards[3]
        self._mixer_scroll.setVisible(not self._curve_expanded)
        export.setVisible(not self._curve_expanded)
        self._mixer_scroll.setMinimumHeight(360 if mode == 'stacked' else 0)
        self._mixer_scroll.setMaximumHeight(520 if mode == 'stacked' else 16777215)
        for column in range(2): self.grid.setColumnStretch(column, 0)
        for row in range(3): self.grid.setRowStretch(row, 0)
        if self._curve_expanded:
            self.grid.addWidget(processing, 0, 0)
            self.grid.setColumnStretch(0, 1)
            self.grid.setRowStretch(0, 1)
        elif columns == 2:
            self.grid.addWidget(self._mixer_scroll, 0, 0)
            self.grid.addWidget(processing, 0, 1)
            self.grid.addWidget(export, 1, 0, 1, 2)
            self.grid.setColumnStretch(0, 2)
            self.grid.setColumnStretch(1, 3)
            self.grid.setRowStretch(0, 1)
        else:
            self.grid.addWidget(self._mixer_scroll, 0, 0)
            self.grid.addWidget(processing, 1, 0)
            self.grid.addWidget(export, 2, 0)
            self.grid.setColumnStretch(0, 1)
            self.grid.setRowStretch(1, 1)
        self._sync_rack_console()
        self.grid.invalidate()
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())
