"""Small pieces shared by cards and tool windows: a caps tag, a hairline progress bar, a frameless panel."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QLineF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from shiftdial.ui import theme


class Tag(QWidget):
    """Caps text in a hairline box, e.g. 'TRIM'; ``filled`` paints it in the signal colour."""

    def __init__(self, text: str, filled: bool = False) -> None:
        super().__init__()
        self._text, self._filled = text, filled
        self._font = theme.label_font(7.5, spacing=12)
        self.setFixedHeight(18)
        self._fit()

    def _fit(self) -> None:
        self.setFixedWidth(QFontMetrics(self._font).horizontalAdvance(self._text.upper()) + 14)

    def set(self, text: str, filled: bool = False) -> None:
        self._text, self._filled = text, filled
        self._fit()
        self.update()

    def paintEvent(self, _: QPaintEvent) -> None:
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(pal.signal if self._filled else pal.line, 1))
        p.setBrush(pal.signal if self._filled else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 2, 2)
        p.setPen(pal.on_signal if self._filled else pal.text)
        p.setFont(self._font)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


class LineBar(QWidget):
    """A 2px progress line with a small cursor block at its head; eases to new values."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(8)
        self._value = 0.0
        self.state = "running"
        self._anim = QVariantAnimation(self, duration=theme.ms(300), easingCurve=QEasingCurve.Type.OutQuart)
        self._anim.valueChanged.connect(self._set_now)

    def set_value(self, v: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(max(0.0, min(1.0, v)))
        self._anim.start()

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def _set_now(self, v: float) -> None:
        self._value = float(v)
        self.update()

    def paintEvent(self, _: QPaintEvent) -> None:
        pal = theme.palette()
        colour = {"done": pal.text, "failed": pal.danger}.get(self.state, pal.signal)
        p = QPainter(self)
        y = self.height() / 2
        w = self.width()
        p.fillRect(QRectF(0, y - 0.5, w, 1), pal.line)
        head = w * self._value
        p.fillRect(QRectF(0, y - 1, head, 2), colour)
        if 0 < self._value < 1:
            p.fillRect(QRectF(max(0.0, head - 3), y - 4, 3, 8), colour)
        p.end()


class Panel(QWidget):
    """Frameless window drawn in the app's own style: caps title row, ✕, drag the title to move, Esc closes."""

    RADIUS = 6

    def __init__(self, title: str) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(title)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self._title_row = QWidget()
        self._title_row.setFixedHeight(38)
        row = QHBoxLayout(self._title_row)
        row.setContentsMargins(16, 0, 8, 0)
        caption = QLabel(title)
        caption.setProperty("role", "caps")
        close = QPushButton(theme.CLOSE_ICON)
        close.setProperty("role", "ghost")
        close.setFont(theme.icon_font(11))
        close.setFixedSize(30, 30)
        close.setAccessibleName("Close")
        close.clicked.connect(self.close)
        row.addWidget(caption, 1)
        row.addWidget(close)
        outer.addWidget(self._title_row)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(18, 6, 18, 18)
        self.body.setSpacing(12)
        outer.addLayout(self.body)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < self._title_row.height():
            self.windowHandle().startSystemMove()  # native move: snaps and multi-monitor work
        super().mousePressEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _: QPaintEvent) -> None:
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(pal.line, 1))
        p.setBrush(pal.surface)
        p.drawRoundedRect(r, self.RADIUS, self.RADIUS)
        y = self._title_row.height() + 0.5
        p.drawLine(QLineF(r.left() + 1, y, r.right() - 1, y))
        p.end()
