"""Radial overlay: a dial of wedges under the cursor that accepts file drops."""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QLineF, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QCursor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QGuiApplication, QKeyEvent, QMouseEvent, QPainter,
    QPainterPath, QPaintEvent, QPen,
)
from PySide6.QtWidgets import QWidget

from shiftdial.jobs import Job
from shiftdial.registry import formats_for, kind_of_all, label_for, tools_for
from shiftdial.ui import theme

R_OUT = 150
R_IN = 52
MARGIN = 6
TICKS = 72
KIND_NAMES = {"image": "images", "video": "video", "audio": "audio", "pdf": "pdf", "archive": "archives",
              "folder": "folders", "slides": "slides"}


def wedge_at(dx: float, dy: float, n: int) -> int:
    """Index of the wedge at offset (dx, dy) from the center, or -1."""
    if n == 0 or not R_IN <= math.hypot(dx, dy) <= R_OUT:
        return -1
    span = 360 / n
    clockwise_from_top = (90 - math.degrees(math.atan2(-dy, dx))) % 360
    return int(((clockwise_from_top + span / 2) % 360) // span)


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _polar(c: QPointF, r: float, deg: float) -> QPointF:
    """Point at radius r and angle deg (Qt convention: 0 = 3 o'clock, counter-clockwise)."""
    a = math.radians(deg)
    return QPointF(c.x() + r * math.cos(a), c.y() - r * math.sin(a))


class Wheel(QWidget):
    chosen = Signal(list, str)
    released = Signal(object)  # Job the dial stopped showing before you'd seen how it ended → a card

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        side = 2 * (R_OUT + MARGIN)
        self.setFixedSize(side, side)
        self.paths: list[Path] = []
        self.items: list[str] = []
        self.hover = -1
        self.mode = "formats"
        self._closing = False

        def anim(ms: int, curve: QEasingCurve.Type = QEasingCurve.Type.OutCubic) -> QVariantAnimation:
            a = QVariantAnimation(self, duration=theme.ms(ms), easingCurve=curve)
            a.valueChanged.connect(lambda _: self.update())
            return a

        self._open = anim(200, QEasingCurve.Type.Linear)
        self._glide = anim(120)
        self._glow = anim(80)
        self._flash = anim(200, QEasingCurve.Type.InQuad)
        self._flash.finished.connect(self._finish_close)
        self._job: Job | None = None
        self._progress = anim(400)
        self._morph = anim(320, QEasingCurve.Type.OutBack)
        self._clock = QTimer(self, interval=500, timeout=self.update)
        # Until the job reports a real in-between value, a segment sweeps round the scale instead of
        # sitting at 0% (one-step jobs like most images never report one; ffmpeg takes a moment).
        self._measured = False
        self._sweep = 0.0
        self._sweep_timer = QTimer(self, interval=16, timeout=self._sweep_step)

    @staticmethod
    def _val(a: QVariantAnimation, default: float) -> float:
        v = a.currentValue()
        return default if v is None else float(v)

    @staticmethod
    def _replay(a: QVariantAnimation, start: float, end: float) -> None:
        a.stop()
        a.setStartValue(start)
        a.setEndValue(end)
        a.start()

    def _items_for(self, paths: list[Path]) -> list[str]:
        return (tools_for if self.mode == "tools" else formats_for)(paths) if paths else []

    def popup(self, mode: str = "formats", pos: QPoint | None = None, activate: bool = True) -> None:
        """Show centered on ``pos`` (default: cursor), kept inside the screen."""
        if self._job:
            self.dismiss()
        self.mode = mode
        if self.isVisible() and not self._closing:
            self.items = self._items_for(self.paths)
            self._set_hover(-1)
            self._replay(self._open, 0.4, 1.0)
            return
        pos = pos or QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        x = min(max(pos.x() - self.width() // 2, g.left()), g.right() - self.width())
        y = min(max(pos.y() - self.height() // 2, g.top()), g.bottom() - self.height())
        self.paths, self.items, self.hover, self._closing = [], [], -1, False
        self._flash.stop()
        self._glow.stop()
        self.move(x, y)
        self._replay(self._open, 0.0, 1.0)
        self.show()
        self.raise_()
        if activate:
            self.activateWindow()  # so Esc reaches us; during a drag the hooks handle Esc

    def dismiss(self) -> None:
        job, self._job = self._job, None
        if job:
            job.progress.disconnect(self._on_job_progress)
            job.state_changed.disconnect(self._on_job_state)
            self._clock.stop()
            self._sweep_timer.stop()
            if not job.finished or job.state == "failed":
                self.released.emit(job)
        self.hide()
        self.paths, self.items, self.hover, self._closing = [], [], -1, False

    def end_drag(self) -> None:
        """Mouse released somewhere: close, unless the dial is showing a job's progress."""
        if self._job is None:
            self.dismiss()

    def _finish_close(self) -> None:
        if self._closing:
            self.dismiss()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.dismiss()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        job = self._job
        if job and job.state == "done" and job.outputs:
            os.startfile(job.outputs[0].parent)  # noqa: S606 - opens a folder we wrote to
        self.dismiss()

    def track(self, job: Job) -> None:
        """Turn the dial into a progress readout for the job just dropped on it."""
        self._job = job
        self._closing = False
        self._flash.stop()
        self._replay(self._progress, 0.0, 0.0)
        self._measured = False
        if theme.motion():
            self._sweep_timer.start()
        self._replay(self._morph, 0.0, 1.0)
        job.progress.connect(self._on_job_progress)
        job.state_changed.connect(self._on_job_state)
        self._clock.start()
        self._on_job_state(job.state)

    def _on_job_progress(self, fraction: float, _stage: str) -> None:
        if 0 < fraction < 1:
            self._measured = True
            self._sweep_timer.stop()
        self._replay(self._progress, self._val(self._progress, 0.0), fraction)

    def _sweep_step(self) -> None:
        self._sweep = (self._sweep + 4) % 360
        self.update()

    def _on_job_state(self, state: str) -> None:
        job = self._job
        if job is None:
            return
        self.update()

        def close_if(still: Job, ms: int) -> None:
            QTimer.singleShot(ms, self, lambda: self._job is still and self._fade_out())

        if job.finished:
            self._sweep_timer.stop()
        if state == "done":
            self._replay(self._progress, self._val(self._progress, 0.0), 1.0)
            close_if(job, 1600)
        elif state == "failed" and job.needs_password:
            self.dismiss()
        elif state == "failed":
            close_if(job, 2500)
        elif state == "cancelled":
            close_if(job, 700)

    def _fade_out(self) -> None:
        if theme.motion():
            self._closing = True
            self._replay(self._flash, 0.0, 1.0)
        else:
            self.dismiss()

    def _set_hover(self, i: int) -> None:
        if i == self.hover:
            return
        n = len(self.items)
        if i >= 0:
            shown = self._val(self._glow, 0.0)
            if (self.hover < 0 and shown < 0.05) or self._glide.currentValue() is None:
                self._replay(self._glide, float(i), float(i))
            else:
                cur = self._val(self._glide, float(i))
                diff = ((i - cur + n / 2) % n) - n / 2
                self._replay(self._glide, cur, cur + diff)
            self._replay(self._glow, shown, 1.0)
        else:
            self._replay(self._glow, self._val(self._glow, 1.0), 0.0)
        self.hover = i

    def _index(self, p: QPointF) -> int:
        return wedge_at(p.x() - self.width() / 2, p.y() - self.height() / 2, len(self.items))

    @staticmethod
    def _accept_as_copy(e: QDragMoveEvent) -> bool:
        # SAFETY: Shift during an Explorer drag proposes MOVE. If we accepted a move,
        # Explorer would delete the original files after the drop. Only ever accept COPY.
        if not e.possibleActions() & Qt.DropAction.CopyAction:
            e.ignore()
            return False
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()
        return True

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths or self._closing or self._job:
            e.ignore()
            return
        if self._accept_as_copy(e):
            self.paths, self.items = paths, self._items_for(paths)
            self._set_hover(self._index(e.position()))
            self.update()

    def dragMoveEvent(self, e: QDragMoveEvent) -> None:
        self._set_hover(self._index(e.position()))
        if self.hover >= 0 and not self._closing:
            self._accept_as_copy(e)
        else:
            e.ignore()
        self.update()

    def dragLeaveEvent(self, e: object) -> None:
        if not self._closing:
            self.dismiss()

    def dropEvent(self, e: QDropEvent) -> None:
        idx = self._index(e.position())
        if idx < 0 or self._closing or not self._accept_as_copy(e):
            e.ignore()
            return
        self.chosen.emit(self.paths, self.items[idx])
        if self._job:
            return
        if theme.motion():
            self._closing = True
            self._replay(self._flash, 0.0, 1.0)
        else:
            self.dismiss()

    @staticmethod
    def _sector(c: QPointF, r_in: float, r_out: float, start: float, sweep: float) -> QPainterPath:
        path = QPainterPath()
        outer = QRectF(c.x() - r_out, c.y() - r_out, 2 * r_out, 2 * r_out)
        path.arcMoveTo(outer, start)
        path.arcTo(outer, start, sweep)
        path.arcTo(QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in), start + sweep, -sweep)
        path.closeSubpath()
        return path

    def _paint_progress(self, job: Job) -> None:
        """Readout: the scale fills clockwise, a needle sweeps, the grown knob shows the percent."""
        pal = theme.palette()
        prog = _clamp(self._val(self._progress, 0.0))
        morph = self._val(self._morph, 1.0)
        fade = self._val(self._flash, 0.0) if self._closing else 0.0
        failed, done = job.state == "failed", job.state == "done"
        ink = pal.danger if failed else pal.signal

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1 - fade)
        c = QPointF(self.width() / 2, self.height() / 2)
        disc = QRectF(c.x() - R_OUT + 0.5, c.y() - R_OUT + 0.5, 2 * R_OUT - 1, 2 * R_OUT - 1)
        p.setPen(QPen(pal.line, 1))
        p.setBrush(theme.alpha(pal.surface, 0.97))
        p.drawEllipse(disc)

        working = not self._measured and not job.finished and prog < 0.001
        start, span = (self._sweep, 60.0) if working else (0.0, prog * 360)
        for k in range(TICKS):
            deg = 90 - k * 5
            major = k % 6 == 0
            lit = (k * 5 - start) % 360 < span
            colour = ink if lit else (pal.muted if major else theme.alpha(pal.muted, 0.45))
            p.setPen(QPen(colour, 1.6 if lit else 1))
            p.drawLine(QLineF(_polar(c, R_OUT - 4, deg), _polar(c, R_OUT - (12 if major else 8), deg)))
        r = R_OUT - 15
        p.setPen(QPen(ink, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), round((90 - start) * 16), round(-span * 16))

        knob_r = (R_IN - 6) + 34 * morph
        p.setPen(QPen(pal.line, 1))
        p.setBrush(pal.raised)
        p.drawEllipse(c, knob_r, knob_r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.alpha(pal.line, 0.8), 1, Qt.PenStyle.DotLine))
        p.drawEllipse(c, knob_r - 6, knob_r - 6)
        needle = 90 - ((start + span) if working else prog * 360)
        p.setPen(QPen(ink, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QLineF(_polar(c, knob_r - 14, needle), _polar(c, knob_r - 3, needle)))

        face = QRectF(c.x() - knob_r, c.y() - knob_r, 2 * knob_r, 2 * knob_r)
        p.setOpacity((1 - fade) * _clamp(morph))
        p.setPen(pal.muted)
        p.setFont(theme.label_font(7, spacing=14))
        tag = label_for(job.action) if ":" in job.action else f"→ {label_for(job.action)}"
        p.drawText(face.adjusted(0, 26, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, tag)
        if failed:
            reading, font = "FAILED", theme.label_font(15, spacing=6)
        elif job.state == "cancelled":
            reading, font = "STOPPED", theme.label_font(15, spacing=6)
        elif working and job.started_at is not None:
            s = int(time.monotonic() - job.started_at)
            reading, font = f"{s // 60:02d}:{s % 60:02d}", theme.mono_font(22)
        else:
            reading, font = f"{prog * 100:.0f}%", theme.mono_font(24)
        p.setPen(ink if failed or done else pal.text)
        p.setFont(font)
        p.drawText(face, Qt.AlignmentFlag.AlignCenter, reading)
        if done:
            under = "saved · click to open"
        elif job.state == "queued":
            under = "waiting"
        elif working:
            under = "working"
        elif job.started_at is not None and not job.finished:
            s = int(time.monotonic() - job.started_at)
            under = f"{s // 60:02d}:{s % 60:02d}"
        else:
            under = ""
        p.setPen(pal.muted)
        p.setFont(theme.mono_font(8) if under[:1].isdigit() else theme.label_font(6.5, spacing=12))
        p.drawText(face.adjusted(0, 0, 0, -24), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, under)
        p.end()

    def paintEvent(self, _: QPaintEvent) -> None:
        if self._job:
            self._paint_progress(self._job)
            return
        pal = theme.palette()
        kind = kind_of_all(self.paths) if self.paths else None
        t = self._val(self._open, 1.0)
        flash = self._val(self._flash, 0.0) if self._closing else 0.0
        glow = self._val(self._glow, 0.0)
        n = len(self.items)
        span = 360 / n if n else 0
        sel = self._val(self._glide, float(max(self.hover, 0)))
        sel_deg = 90 - sel * span

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        appear = _clamp(t / 0.3)
        p.setOpacity(appear * (1 - flash))
        scale = 0.96 + 0.04 * (1 - (1 - appear) ** 3)
        p.translate(c)
        p.scale(scale, scale)
        p.translate(-c)

        # Body. Never fully transparent, or drops would fall through to the window below.
        disc = QRectF(c.x() - R_OUT + 0.5, c.y() - R_OUT + 0.5, 2 * R_OUT - 1, 2 * R_OUT - 1)
        p.setPen(QPen(pal.line, 1))
        p.setBrush(theme.alpha(pal.surface, 0.97))
        p.drawEllipse(disc)

        if n and glow > 0.01:
            p.setPen(Qt.PenStyle.NoPen)
            face = theme.alpha(pal.signal, 0.85 * flash) if flash else theme.alpha(pal.raised, glow)
            p.setBrush(face)
            p.drawPath(self._sector(c, R_IN + 2, R_OUT - 16, sel_deg + span / 2, -span))

        sweep = _clamp((t - 0.1) / 0.7)
        boundaries = {round(((i + 0.5) * span) / 5) % TICKS for i in range(n)} if n > 1 else set()
        for k in range(TICKS):
            if k / TICKS > sweep:
                break
            deg = 90 - k * 5
            major = k in boundaries
            off = (deg - sel_deg + 180) % 360 - 180
            lit = n and glow > 0.01 and abs(off) < span / 2 - 1.5
            ink = theme.alpha(pal.signal, glow) if lit else pal.muted if major else theme.alpha(pal.muted, 0.45)
            p.setPen(QPen(ink, 1.6 if lit else 1))
            p.drawLine(QLineF(_polar(c, R_OUT - 4, deg), _polar(c, R_OUT - (12 if major else 8), deg)))

        if n and glow > 0.01:
            r = R_OUT - 15
            arc = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            p.setPen(QPen(theme.alpha(pal.signal, glow), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(arc, round((sel_deg + span / 2 - 1.5) * 16), round(-(span - 3) * 16))

        if n > 1:
            p.setPen(QPen(pal.line, 1))
            for i in range(n):
                deg = 90 - i * span + span / 2
                p.drawLine(QLineF(_polar(c, R_IN + 8, deg), _polar(c, R_OUT - 20, deg)))

        for i, item in enumerate(self.items):
            local = _clamp((t - 0.1 - 0.35 * i / max(n, 1)) / 0.3)
            if local <= 0:
                continue
            mid = 90 - i * span
            hovered = i == self.hover
            colour = pal.signal if hovered else pal.text
            pt = _polar(c, (R_IN + R_OUT - 16) / 2 + 2, mid)
            base = p.opacity()
            p.setOpacity(base * local)
            p.setPen(colour)
            glyph = theme.ICONS.get(item)
            if glyph:
                p.setFont(theme.icon_font(18))
                p.drawText(QRectF(pt.x() - 30, pt.y() - 24, 60, 24), Qt.AlignmentFlag.AlignCenter, glyph)
                p.setFont(theme.label_font(7.5, spacing=10))
                p.drawText(QRectF(pt.x() - 44, pt.y() + 2, 88, 18), Qt.AlignmentFlag.AlignCenter, label_for(item))
            else:
                p.setFont(theme.label_font(11 if n <= 8 else 10, spacing=8))
                p.drawText(QRectF(pt.x() - 44, pt.y() - 12, 88, 24), Qt.AlignmentFlag.AlignCenter, label_for(item))
            p.setOpacity(base)

        knob_r = R_IN - 6
        p.setPen(QPen(pal.line, 1))
        p.setBrush(pal.raised)
        p.drawEllipse(c, knob_r, knob_r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.alpha(pal.line, 0.8), 1, Qt.PenStyle.DotLine))
        p.drawEllipse(c, knob_r - 5, knob_r - 5)
        pointer_deg = sel_deg if n and glow > 0.01 else 90
        p.setPen(QPen(pal.signal if glow > 0.01 else pal.muted, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QLineF(_polar(c, knob_r - 12, pointer_deg), _polar(c, knob_r - 3, pointer_deg)))

        face = QRectF(c.x() - knob_r, c.y() - knob_r, 2 * knob_r, 2 * knob_r)
        p.setPen(pal.muted)
        p.setFont(theme.label_font(6.5, spacing=14))
        p.drawText(face.adjusted(0, 18, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.mode)
        if self.hover >= 0 and n:
            reading, font = label_for(self.items[self.hover]), theme.label_font(10.5, spacing=6)
        elif self.paths:
            reading, font = f"{len(self.paths):02d}", theme.mono_font(17)
        else:
            reading, font = "--", theme.mono_font(17)
        p.setPen(pal.text)
        p.setFont(font)
        p.drawText(face.adjusted(6, 2, -6, 0), Qt.AlignmentFlag.AlignCenter, reading)
        p.setPen(pal.muted)
        p.setFont(theme.label_font(6.5, spacing=14))
        if not self.paths:
            under = "drop files"
        elif kind in ("mixed", "other"):
            under = "mixed"
        else:
            under = KIND_NAMES.get(kind or "", kind or "")
        if self.paths and not self.items:
            under = "no options"
        p.drawText(face.adjusted(0, 0, 0, -16), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, under)
        p.end()
