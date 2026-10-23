"""Cached, application-internal moonlight background; no OS transparency hooks."""
from PyQt5.QtCore import QByteArray, QEvent, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QWidget


def _asset(name):
    from common import ROOT
    return ROOT / 'assets' / name


class MaterialBackdrop(QWidget):
    def __init__(self, window, texture_path=None, character_path=None):
        super().__init__(window)
        self.setObjectName('material-backdrop')
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.mode, self.material = 'light', 'glass'
        self._key, self._pixmap = None, None
        self._texture = QPixmap(str(texture_path or _asset('moonlight-background.png')))
        self._character = QPixmap(str(character_path or _asset('headphone-character.png')))
        window.installEventFilter(self)
        self.setGeometry(window.rect())
        self.lower()
        self.show()

    def configure(self, mode, preferences):
        self.mode = mode
        self.material = preferences.get('material', 'glass')
        self._key = None
        self.update()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
            self.lower()
        return False

    def paintEvent(self, event):
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, self.mode, self.material,
               self._texture.cacheKey(), self._character.cacheKey())
        if self._key != key:
            dark = self.mode == 'dark'
            pixmap = QPixmap(max(1, int(self.width()*dpr)), max(1, int(self.height()*dpr)))
            pixmap.setDevicePixelRatio(dpr)
            pixmap.fill(QColor('#171b2e' if dark else '#eef1ff'))
            painter = QPainter(pixmap)
            if self.material == 'glass':
                base = QLinearGradient(0, 0, self.width(), self.height())
                base.setColorAt(0, QColor('#182736' if dark else '#e8f6fc'))
                base.setColorAt(1, QColor('#28223e' if dark else '#ede7ff'))
                painter.fillRect(self.rect(), base)
                if not self._texture.isNull():
                    # One static raster layer, cached at the window's actual DPR.
                    # Crop to fill without stretching; solid mode skips artwork.
                    ratio = max(1, self.width()) / max(1, self.height())
                    tw, th = self._texture.width(), self._texture.height()
                    sw, sh = min(tw, th * ratio), min(th, tw / ratio)
                    source = QRectF((tw-sw)/2, (th-sh)/2, sw, sh)
                    painter.setRenderHint(QPainter.SmoothPixmapTransform)
                    painter.setOpacity(.13 if dark else .80)
                    painter.drawPixmap(QRectF(self.rect()), self._texture, source)
                    painter.setOpacity(1.)
                glow = QRadialGradient(self.width()*.7, self.height()*.2, max(self.width(), self.height())*.6)
                glow.setColorAt(0, QColor(133, 113, 245, 26 if dark else 34))
                glow.setColorAt(1, QColor(133, 113, 245, 0))
                painter.fillRect(self.rect(), glow)
                if not self._character.isNull():
                    # The illustration is part of the cached background, not a
                    # widget or preference. Navigation remains above this layer.
                    painter.setRenderHint(QPainter.SmoothPixmapTransform)
                    painter.setOpacity(.82 if self.height() >= 820 else .46)
                    painter.drawPixmap(self._character_rect(), self._character,
                                       QRectF(self._character.rect()))
                    painter.setOpacity(1.)
            painter.end()
            self._pixmap, self._key = pixmap, key
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.end()

    def _character_rect(self):
        size = min(560., max(350., self.height()*.40))
        return QRectF(-size*.22, self.height()-size, size, size)


def apply_material(window, mode, preferences):
    backdrop = getattr(window, '_vwa_backdrop', None)
    if backdrop is None:
        backdrop = MaterialBackdrop(window)
        window._vwa_backdrop = backdrop
    backdrop.configure(mode, preferences)


def navigation_icon(index, color):
    paths = {
        0: '<path d="M6 3h8l4 4v14H6zM14 3v5h5M9 12h6M9 16h6"/>',
        9: '<path d="M3 10v4M7 5v14M12 2v20M17 7v10M21 10v4"/>',
        8: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m10 8 6 4-6 4z"/>',
        3: '<path d="M4 14v6h16v-6M12 16V3m-5 5 5-5 5 5"/>',
        1: '<path d="m12 2 9 5v10l-9 5-9-5V7zm-9 5 9 5 9-5M12 12v10M7 5l9 5"/>',
        2: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h1m3 0h5M8 12h1m3 0h5M8 16h1m3 0h5"/>',
        4: '<circle cx="12" cy="12" r="4"/><path d="m10 2-1 3-3 1-3-1-1 4 2 2v2l-2 2 2 4 3-1 2 1 1 3h4l1-3 2-1 3 1 2-4-2-2v-2l2-2-2-4-3 1-3-1-1-3z"/>',
    }
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{paths[index]}</g></svg>'
    pixmap=QPixmap(48,48);pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pixmap)
