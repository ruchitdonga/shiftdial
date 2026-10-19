"""Progress cards, bottom-right, one per job — laid out like a device readout."""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from shiftdial.jobs import Job
from shiftdial.registry import label_for
from shiftdial.ui import theme
from shiftdial.ui.widgets import LineBar, Tag

CARD_WIDTH = 360
RADIUS = 6
DONE_LINGER_MS = 8000


def _elide(label: QLabel, text: str, width: int) -> None:
    label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, width))
    label.setToolTip(text)


def _set_role(w: QWidget, role: str) -> None:
    w.setProperty("role", role)
    w.style().unpolish(w)
    w.style().polish(w)


def _action_tag(action: str) -> str:
    return label_for(action) if ":" in action else f"→ {label_for(action)}"


class Card(QFrame):
    def __init__(self, job: Job, stack: "CardStack") -> None:
        super().__init__()
        self.job, self._stack = job, stack
        self.setFixedWidth(CARD_WIDTH)

        self.tag = Tag(_action_tag(job.action))
        more = f"  +{len(job.paths) - 1}" if len(job.paths) > 1 else ""
        self.name = QLabel()
        self.name.setFont(theme.text_font(10))
        _elide(self.name, job.paths[0].name + more, CARD_WIDTH - 150)
        close = QPushButton(theme.CLOSE_ICON)
        close.setProperty("role", "ghost")
        close.setFont(theme.icon_font(10))
        close.setFixedSize(24, 24)
        close.setToolTip("Hide (the job keeps running)")
        close.setAccessibleName("Hide")
        close.clicked.connect(self.remove)

        self.stage = QLabel("queued")
        _set_role(self.stage, "sub")
        self.stage.setFont(theme.text_font(8.5))
        self.percent = QLabel("")
        self.percent.setFont(theme.mono_font(9))
        self.bar = LineBar()
        self.elapsed = QLabel("00:00")
        self.elapsed.setFont(theme.mono_font(9))
        _set_role(self.elapsed, "sub")
        self.action = QPushButton("Cancel")
        self.action.clicked.connect(self._on_action)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self.tag)
        head.addWidget(self.name, 1)
        head.addWidget(close)
        mid = QHBoxLayout()
        mid.addWidget(self.stage, 1)
        mid.addWidget(self.percent)
        foot = QHBoxLayout()
        foot.addWidget(self.elapsed)
        foot.addStretch()
        foot.addWidget(self.action)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 12, 12)
        box.setSpacing(7)
        box.addLayout(head)
        box.addLayout(mid)
        box.addWidget(self.bar)
        box.addLayout(foot)

        self._fx = QGraphicsOpacityEffect(self, opacity=0.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QPropertyAnimation(self._fx, b"opacity", self, duration=theme.ms(180),
                                        startValue=0.0, endValue=1.0, easingCurve=QEasingCurve.Type.OutQuad)
        self._fade.finished.connect(lambda: self.setGraphicsEffect(None))
        self._fade.start()

        self._timer = QTimer(self, interval=1000, timeout=self._tick)
        job.progress.connect(self._on_progress)
        job.state_changed.connect(self._on_state)
        self._on_state(job.state)

    def paintEvent(self, _: QPaintEvent) -> None:
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(pal.line, 1))
        p.setBrush(pal.surface)
        p.drawRoundedRect(r, RADIUS, RADIUS)
        strip = {"done": pal.text, "failed": pal.danger, "cancelled": pal.muted}.get(self.job.state, pal.signal)
        if self.job.needs_password:
            strip = pal.signal
        p.fillRect(QRectF(r.left() + 6, r.top() + 12, 2, r.height() - 24), strip)
        p.end()

    def _tick(self) -> None:
        if self.job.started_at is not None:
            s = int(time.monotonic() - self.job.started_at)
            self.elapsed.setText(f"{s // 60:02d}:{s % 60:02d}")

    def _on_progress(self, fraction: float, stage: str) -> None:
        if not self.job.finished:
            self.bar.set_value(fraction)
            self.percent.setText(f"{fraction * 100:3.0f}%")
            _elide(self.stage, stage, CARD_WIDTH - 90)

    def _primary(self, text: str) -> None:
        self.action.setText(text)
        self.action.setEnabled(True)
        _set_role(self.action, "primary")

    def _on_state(self, state: str) -> None:
        self.update()
        if state == "running":
            self.stage.setText("starting")
            self._timer.start()
            return
        if not self.job.finished:
            return
        self._timer.stop()
        self._tick()
        if state == "done":
            self.bar.set_state("done")
            self.bar.set_value(1.0)
            self.percent.setText("100%")
            self.tag.set("saved", filled=True)
            _elide(self.stage, ", ".join(p.name for p in self.job.outputs), CARD_WIDTH - 90)
            self._primary("Open folder")
            QTimer.singleShot(DONE_LINGER_MS, self, self.remove)
        elif state == "failed" and self.job.needs_password:
            self.tag.set("locked")
            self.stage.setText(self.job.error)
            self.percent.setText("")
            self.bar.hide()
            self._primary("Password…")
            self._stack.relayout()
        elif state == "failed":
            self.bar.set_state("failed")
            self.tag.set("failed")
            _set_role(self.stage, "error")
            self.stage.setWordWrap(True)
            self.stage.setText(self.job.error[:300])
            self.percent.setText("")
            self.action.hide()
            self._stack.relayout()
        else:
            self.tag.set("cancelled")
            self.stage.setText("stopped, nothing written")
            self.action.hide()
            QTimer.singleShot(1500, self, self.remove)

    def _on_action(self) -> None:
        if self.job.state == "done" and self.job.outputs:
            os.startfile(self.job.outputs[0].parent)  # noqa: S606 - opens a folder we wrote to
        elif self.job.needs_password:
            self._ask_password()
        else:
            self.action.setEnabled(False)
            self.stage.setText("cancelling")
            self.job.cancel()

    def _ask_password(self) -> None:
        # Own top-level window: the card stack never takes focus, so a child dialog couldn't be typed in.
        dlg = QInputDialog()
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        dlg.setWindowTitle("Archive password")
        dlg.setLabelText(f"Password for {self.job.paths[0].name}:")
        dlg.setTextEchoMode(QLineEdit.EchoMode.Password)
        dlg.show()
        dlg.activateWindow()
        if dlg.exec() and dlg.textValue():
            self._stack.retry(self.job, {"password": dlg.textValue()})
            self.remove()

    def remove(self) -> None:
        """Hide only removes the card; the job keeps running."""
        self._stack.remove(self)


class CardStack(QWidget):
    """Frameless always-on-top column of cards that never takes focus."""

    def __init__(self, retry: Callable[[Job, dict[str, Any]], object]) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.retry = retry
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(8)
        self._box.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        self._slide = QPropertyAnimation(self, b"pos", self, duration=theme.ms(220),
                                         easingCurve=QEasingCurve.Type.OutQuart)

    def add(self, job: Job) -> None:
        self._box.addWidget(Card(job, self))
        self.relayout()

    def remove(self, card: Card) -> None:
        if self._box.indexOf(card) < 0:
            return
        self._box.removeWidget(card)
        card.hide()
        card.deleteLater()
        self.relayout()

    def relayout(self) -> None:
        if self._box.count() == 0:
            self.hide()
            return
        old_top = self.y() if self.isVisible() else None
        self.adjustSize()
        g = QGuiApplication.primaryScreen().availableGeometry()
        target = QPoint(g.right() - self.width() - 16, g.bottom() - self.height() - 16)
        start = QPoint(target.x(), old_top) if old_top is not None else target + QPoint(0, 32)
        self._slide.stop()
        self.move(start)
        self.show()
        self._slide.setStartValue(start)
        self._slide.setEndValue(target)
        self._slide.start()
