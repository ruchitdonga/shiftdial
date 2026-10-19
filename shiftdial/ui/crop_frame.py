"""Image preview with a draggable crop rectangle (corners resize, inside moves, outside redraws)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

HANDLE = 10
MIN_FRAC = 0.02


class CropFrame(QWidget):
    changed = Signal()

    def __init__(self, pixmap: QPixmap, source_size: tuple[int, int]) -> None:
        super().__init__()
        self._pm = pixmap
        self._src_w, self._src_h = source_size
        self._box = [0.1, 0.1, 0.9, 0.9]
        self._aspect: float | None = None  # width / height in source pixels
        self._drag: tuple[str, QPointF, list[float]] | None = None
        self.setMinimumSize(320, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def box(self) -> list[float]:
        return [round(v, 5) for v in self._box]

    def set_aspect(self, aspect: float | None) -> None:
        """Lock to width/height ``aspect`` (None = free); re-fit the largest centred box."""
        self._aspect = aspect
        if aspect:
            fw = min(1.0, aspect * self._src_h / self._src_w)
            fh = fw * self._src_w / aspect / self._src_h
            self._box = [0.5 - fw / 2, 0.5 - fh / 2, 0.5 + fw / 2, 0.5 + fh / 2]
        self.changed.emit()
        self.update()

    def pixel_size(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self._box
        return round((x1 - x0) * self._src_w), round((y1 - y0) * self._src_h)

    def _img_rect(self) -> QRectF:
        s = self._pm.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width() - s.width()) / 2, (self.height() - s.height()) / 2, s.width(), s.height())

    def _to_frac(self, p: QPointF) -> tuple[float, float]:
        r = self._img_rect()
        return (min(1.0, max(0.0, (p.x() - r.x()) / r.width())),
                min(1.0, max(0.0, (p.y() - r.y()) / r.height())))

    def _box_rect(self) -> QRectF:
        r = self._img_rect()
        x0, y0, x1, y1 = self._box
        return QRectF(r.x() + x0 * r.width(), r.y() + y0 * r.height(),
                      (x1 - x0) * r.width(), (y1 - y0) * r.height())

    def _corner_at(self, p: QPointF) -> str | None:
        b = self._box_rect()
        corners = {"tl": b.topLeft(), "tr": b.topRight(), "bl": b.bottomLeft(), "br": b.bottomRight()}
        return next((k for k, c in corners.items()
                     if abs(c.x() - p.x()) <= HANDLE and abs(c.y() - p.y()) <= HANDLE), None)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        p = e.position()
        corner = self._corner_at(p)
        if corner:
            self._drag = (corner, p, list(self._box))
        elif self._box_rect().contains(p):
            self._drag = ("move", p, list(self._box))
        else:
            fx, fy = self._to_frac(p)
            self._box = [fx, fy, min(1.0, fx + MIN_FRAC), min(1.0, fy + MIN_FRAC)]
            self._drag = ("br", p, list(self._box))
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        p = e.position()
        if not self._drag:
            corner = self._corner_at(p)
            cursor = (Qt.CursorShape.SizeFDiagCursor if corner in ("tl", "br") else
                      Qt.CursorShape.SizeBDiagCursor if corner else
                      Qt.CursorShape.SizeAllCursor if self._box_rect().contains(p) else
                      Qt.CursorShape.CrossCursor)
            self.setCursor(cursor)
            return
        mode, start, b = self._drag
        fx, fy = self._to_frac(p)
        if mode == "move":
            sx, sy = self._to_frac(start)
            w, h = b[2] - b[0], b[3] - b[1]
            x0 = min(max(0.0, b[0] + fx - sx), 1 - w)
            y0 = min(max(0.0, b[1] + fy - sy), 1 - h)
            self._box = [x0, y0, x0 + w, y0 + h]
        else:
            self._resize(mode, b, fx, fy)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag = None

    def _resize(self, corner: str, b: list[float], fx: float, fy: float) -> None:
        """Move ``corner`` to (fx, fy); the opposite corner stays put."""
        ax = b[2] if "l" in corner else b[0]
        ay = b[3] if "t" in corner else b[1]
        w = max(MIN_FRAC, abs(fx - ax))
        h = max(MIN_FRAC, abs(fy - ay))
        if self._aspect:
            h = w * self._src_w / self._aspect / self._src_h
        sx = -1 if "l" in corner else 1
        sy = -1 if "t" in corner else 1
        room_x = ax if sx < 0 else 1 - ax
        room_y = ay if sy < 0 else 1 - ay
        scale = min(1.0, room_x / w, room_y / h)
        w, h = w * scale, h * scale
        x0, x1 = sorted((ax, ax + sx * w))
        y0, y1 = sorted((ay, ay + sy * h))
        self._box = [x0, y0, x1, y1]

    def paintEvent(self, _: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        img = self._img_rect()
        p.drawPixmap(img, self._pm, QRectF(self._pm.rect()))
        b = self._box_rect()
        shade = QColor(0, 0, 0, 140)
        for r in (QRectF(img.left(), img.top(), img.width(), b.top() - img.top()),
                  QRectF(img.left(), b.bottom(), img.width(), img.bottom() - b.bottom()),
                  QRectF(img.left(), b.top(), b.left() - img.left(), b.height()),
                  QRectF(b.right(), b.top(), img.right() - b.right(), b.height())):
            p.fillRect(r, shade)
        p.setPen(QPen(QColor("white"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(b)
        p.setBrush(QColor("white"))
        for c in (b.topLeft(), b.topRight(), b.bottomLeft(), b.bottomRight()):
            p.drawEllipse(c, 5, 5)
        w, h = self.pixel_size()
        p.setPen(QColor("white"))
        p.drawText(b.adjusted(6, 4, -6, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, f"{w} × {h}")
        p.end()
