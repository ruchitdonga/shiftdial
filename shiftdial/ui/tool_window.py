"""Settings window for tools that need input: a preview, a few controls, and Apply."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pymupdf
from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent, QFont, QImage, QMouseEvent, QPainter, QPaintEvent, QPixmap, QShowEvent, QTransform,
)
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from shiftdial.engines import image as image_engine
from shiftdial.engines.ffmpeg import frame_png, probe
from shiftdial.engines.image import POSITIONS
from shiftdial.engines.pdf import parse_pages
from shiftdial.registry import TOOL_BY_ID
from shiftdial.ui import theme
from shiftdial.ui.crop_frame import CropFrame
from shiftdial.ui.widgets import Panel

PREVIEW_MAX = 1200  # px; previews are downscaled, results are not
PREVIEW_BOX = (640, 400)
ASPECTS: list[tuple[str, float | None]] = [
    ("Free", None), ("1:1", 1.0), ("4:3", 4 / 3), ("3:2", 1.5), ("16:9", 16 / 9), ("9:16", 9 / 16)]
Getter = Callable[[dict[str, Any]], None]


def fmt_time(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:04.1f}"


def pil_to_pixmap(im: Any) -> QPixmap:
    im = im.convert("RGBA")
    return QPixmap.fromImage(
        QImage(im.tobytes(), im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888).copy())


class RangeSlider(QWidget):
    """Two handles (start, end) on one track, values in ms."""

    changed = Signal(int, int)  # handle (0 = start, 1 = end), value

    MARGIN = 10

    def __init__(self, maximum: int) -> None:
        super().__init__()
        self.maximum = max(1, maximum)
        self.values = [0, self.maximum]
        self._active = -1
        self.setFixedHeight(28)
        self.setMinimumWidth(300)
        self.setAccessibleName("Trim range")

    def set_value(self, handle: int, value: int) -> None:
        lo, hi = (0, self.values[1] - 1) if handle == 0 else (self.values[0] + 1, self.maximum)
        self.values[handle] = int(min(max(value, lo), hi))
        self.update()

    def _x(self, v: int) -> float:
        return self.MARGIN + v / self.maximum * (self.width() - 2 * self.MARGIN)

    def _v(self, x: float) -> int:
        return round((x - self.MARGIN) / max(1, self.width() - 2 * self.MARGIN) * self.maximum)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        x = e.position().x()
        self._active = min((0, 1), key=lambda h: abs(self._x(self.values[h]) - x))
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._active >= 0:
            self.set_value(self._active, self._v(e.position().x()))
            self.changed.emit(self._active, self.values[self._active])

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._active = -1

    def paintEvent(self, _: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = theme.palette()
        y = self.height() / 2
        p.fillRect(QRectF(self.MARGIN, y - 1, self.width() - 2 * self.MARGIN, 2), pal.line)
        x0, x1 = self._x(self.values[0]), self._x(self.values[1])
        p.fillRect(QRectF(x0, y - 1, x1 - x0, 2), pal.signal)
        p.setPen(pal.text)
        p.setBrush(pal.raised)
        for x in (x0, x1):
            p.drawRoundedRect(QRectF(x - 5, y - 9, 10, 18), 2, 2)
            p.drawLine(QPointF(x - 2.5, y), QPointF(x + 2.5, y))
        p.end()


class MediaPreview(QWidget):
    """QMediaPlayer with an optional video surface and a play/pause button."""

    def __init__(self, src: Path, video: bool) -> None:
        super().__init__()
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(QAudioOutput(self))
        self.stop_at: int | None = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        if video:
            surface = QVideoWidget()
            surface.setMinimumSize(*PREVIEW_BOX)
            self.player.setVideoOutput(surface)
            box.addWidget(surface)
        self.button = QPushButton("▶ Play")
        self.button.clicked.connect(self.toggle)
        box.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.on_play: Callable[[], None] = self.player.play
        self.player.positionChanged.connect(self._check_stop)
        self.player.playbackStateChanged.connect(
            lambda s: self.button.setText("⏸ Pause" if s == QMediaPlayer.PlaybackState.PlayingState else "▶ Play"))
        # Pausing a freshly loaded file renders its first frame instead of a black box.
        self.player.mediaStatusChanged.connect(
            lambda s: self.player.pause() if s == QMediaPlayer.MediaStatus.LoadedMedia else None)
        self.player.setSource(QUrl.fromLocalFile(str(src)))

    def toggle(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.on_play()

    def seek(self, ms: int) -> None:
        self.player.setPosition(ms)

    def _check_stop(self, pos: int) -> None:
        if self.stop_at is not None and pos >= self.stop_at:
            self.player.pause()

    def release(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())


class ToolWindow(Panel):
    applied = Signal(list, str, dict)

    def __init__(self, paths: list[Path], tool_id: str) -> None:
        tool = TOOL_BY_ID[tool_id]
        super().__init__(f"Shiftdial · {tool.kind} · {tool.label}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.paths, self.tool_id = paths, tool_id
        more = f"  +{len(paths) - 1} more" if len(paths) > 1 else ""
        self._getters: list[Getter] = []
        self._media: MediaPreview | None = None

        self._box = self.body
        header = QHBoxLayout()
        header.setSpacing(12)
        glyph = QLabel(theme.ICONS.get(tool_id, ""))
        glyph.setFont(theme.icon_font(24))
        glyph.setStyleSheet(f"color: {theme.palette().signal.name()}")
        heading = QVBoxLayout()
        heading.setSpacing(0)
        name = QLabel(tool.label)
        name.setFont(theme.text_font(17, QFont.Weight.DemiBold))
        files = QLabel(f"{paths[0].name}{more}")
        files.setProperty("role", "sub")
        heading.addWidget(name)
        heading.addWidget(files)
        header.addWidget(glyph)
        header.addLayout(heading, 1)
        self._box.addLayout(header)

        self.form = QFormLayout()
        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(10)
        getattr(self, "_build_" + tool_id.replace(":", "_"))()
        self._box.addLayout(self.form)
        if len(paths) > 1:
            note = QLabel(f"Settings apply to all {len(paths)} files; preview shows the first.")
            note.setProperty("role", "sub")
            self._box.addWidget(note)
        self.error = QLabel()
        self.error.setProperty("role", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        self._box.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel, apply = QPushButton("Cancel"), QPushButton("Apply")
        apply.setProperty("role", "primary")
        apply.setDefault(True)
        cancel.clicked.connect(self.close)
        apply.clicked.connect(self._apply)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        self._box.addLayout(buttons)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self, duration=theme.ms(140),
                                        startValue=0.0, endValue=1.0, easingCurve=QEasingCurve.Type.OutQuad)

    def showEvent(self, e: QShowEvent) -> None:
        if self._fade.state() != QPropertyAnimation.State.Running and self.windowOpacity() == 1.0:
            self.setWindowOpacity(0.0)
            self._fade.start()
        super().showEvent(e)

    def params(self) -> dict[str, Any]:
        """Collect params; raises ValueError with a user-facing message if something is off."""
        p: dict[str, Any] = {}
        for get in self._getters:
            get(p)
        return p

    def _apply(self) -> None:
        try:
            params = self.params()
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return
        self.applied.emit(self.paths, self.tool_id, params)
        self.close()

    def closeEvent(self, e: QCloseEvent) -> None:
        if self._media:
            self._media.release()
        super().closeEvent(e)

    def _combo(self, label: str, key: str | None, options: list[tuple[str, Any]], index: int = 0) -> QComboBox:
        cb = QComboBox()
        for text, value in options:
            cb.addItem(text, value)
        cb.setCurrentIndex(index)
        self.form.addRow(label, cb)
        if key:
            self._getters.append(lambda p: p.__setitem__(key, cb.currentData()))
        return cb

    def _slider(self, label: str, key: str, lo: int, hi: int, value: int, suffix: str = "") -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(value)
        s.setAccessibleName(label)
        shown = QLabel(f"{value}{suffix}")
        shown.setMinimumWidth(48)
        s.valueChanged.connect(lambda v: shown.setText(f"{v}{suffix}"))
        row = QHBoxLayout()
        row.addWidget(s)
        row.addWidget(shown)
        self.form.addRow(label, row)
        self._getters.append(lambda p: p.__setitem__(key, s.value()))
        return s

    def _spin(self, label: str, key: str | None, lo: float, hi: float, value: float,
              decimals: int = 0, step: float = 1, suffix: str = "") -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(decimals)
        sp.setSingleStep(step)
        sp.setValue(value)
        sp.setSuffix(suffix)
        self.form.addRow(label, sp)
        if key:
            self._getters.append(lambda p: p.__setitem__(key, sp.value()))
        return sp

    def _text(self, label: str, key: str, value: str = "", placeholder: str = "",
              validate: Callable[[str], None] | None = None) -> QLineEdit:
        ed = QLineEdit(value)
        ed.setPlaceholderText(placeholder)
        self.form.addRow(label, ed)

        def get(p: dict[str, Any]) -> None:
            if validate:
                validate(ed.text())
            p[key] = ed.text()

        self._getters.append(get)
        return ed

    def _preview_label(self, pm: QPixmap) -> QLabel:
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(320, 200)
        self._show_pixmap(label, pm)
        self._box.addWidget(label)
        return label

    @staticmethod
    def _show_pixmap(label: QLabel, pm: QPixmap) -> None:
        label.setPixmap(pm.scaled(*PREVIEW_BOX, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))

    def _first_image(self) -> tuple[Any, tuple[int, int]]:
        """First file as a PIL thumbnail plus its real size."""
        im = image_engine.load(self.paths[0])
        size = im.size
        im.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
        return im, size

    def _crop(self, pm: QPixmap, size: tuple[int, int]) -> None:
        frame = CropFrame(pm, size)
        self._box.addWidget(frame, stretch=1)
        aspect = self._combo("Aspect", None, ASPECTS)
        aspect.currentIndexChanged.connect(lambda _: frame.set_aspect(aspect.currentData()))
        self._getters.append(lambda p: p.__setitem__("box", frame.box()))

    def _build_image_resize(self) -> None:
        im, (w, h) = self._first_image()
        self._preview_label(pil_to_pixmap(im))
        mode = self._combo("Resize by", "mode", [("Percent", "percent"), ("Longest side", "max_side")])
        value = self._spin("Size", "value", 1, 50000, 50, suffix=" %")
        result = QLabel()
        self.form.addRow("Result", result)

        def refresh() -> None:
            pct = mode.currentData() == "percent"
            scale = value.value() / 100 if pct else value.value() / max(w, h)
            result.setText(f"{w} × {h} → {max(1, round(w * scale))} × {max(1, round(h * scale))}")

        def mode_changed() -> None:
            pct = mode.currentData() == "percent"
            value.setSuffix(" %" if pct else " px")
            value.setValue(50 if pct else min(1920, max(w, h)))
            refresh()

        mode.currentIndexChanged.connect(mode_changed)
        value.valueChanged.connect(refresh)
        refresh()

    def _build_image_compress(self) -> None:
        im, _ = self._first_image()
        self._preview_label(pil_to_pixmap(im))
        self._slider("Quality", "quality", 1, 100, 75)
        self.form.addRow(QLabel("JPG/WEBP/AVIF: lower = smaller. PNG: reduced to 256 colours."))

    def _build_image_rotate(self) -> None:
        im, _ = self._first_image()
        pm = pil_to_pixmap(im)
        label = self._preview_label(pm)
        op = self._combo("Turn", "op", [("Right 90°", "right"), ("Left 90°", "left"), ("180°", "180"),
                                        ("Flip horizontal", "flip_h"), ("Flip vertical", "flip_v")])
        transforms = {"right": QTransform().rotate(90), "left": QTransform().rotate(-90),
                      "180": QTransform().rotate(180), "flip_h": QTransform().scale(-1, 1),
                      "flip_v": QTransform().scale(1, -1)}
        op.currentIndexChanged.connect(
            lambda _: self._show_pixmap(label, pm.transformed(transforms[op.currentData()])))
        self._show_pixmap(label, pm.transformed(transforms["right"]))

    def _build_image_crop(self) -> None:
        im, size = self._first_image()
        self._crop(pil_to_pixmap(im), size)

    def _build_image_watermark(self) -> None:
        im, _ = self._first_image()
        label = self._preview_label(pil_to_pixmap(im))

        def require(text: str) -> None:
            if not text.strip():
                raise ValueError("Enter the watermark text")

        text = self._text("Text", "text", "", "e.g. © Your Name", validate=require)
        size = self._slider("Size", "size", 1, 30, 5, " %")
        pos = self._combo("Position", "position", [(p.replace("-", " ").capitalize(), p) for p in POSITIONS])
        opacity = self._slider("Opacity", "opacity", 10, 100, 60, " %")

        def refresh() -> None:
            shown = image_engine.watermark(im, text.text() or "Sample", size.value(),
                                           pos.currentData(), opacity.value())
            self._show_pixmap(label, pil_to_pixmap(shown))

        for signal in (text.textChanged, size.valueChanged, pos.currentIndexChanged, opacity.valueChanged):
            signal.connect(lambda *_: refresh())
        refresh()

    def _duration(self) -> float:
        d = probe(self.paths[0]).duration
        if not d:
            raise ValueError(f"Can't read the length of {self.paths[0].name}")
        return d

    def _build_trim(self, video: bool) -> None:
        dur = self._duration()
        media = self._media = MediaPreview(self.paths[0], video)
        self._box.addWidget(media)
        slider = RangeSlider(int(dur * 1000))
        self._box.addWidget(slider)
        start = self._spin("Start", "start", 0, dur, 0, decimals=2, step=0.1, suffix=" s")
        end = self._spin("End", "end", 0, dur, dur, decimals=2, step=0.1, suffix=" s")
        length = QLabel()
        self.form.addRow("Length", length)

        def sync(handle: int, ms: int) -> None:  # slider → spin boxes + preview
            (start if handle == 0 else end).setValue(ms / 1000)
            media.seek(ms)

        def spin_changed() -> None:  # spin boxes → slider (they can't cross)
            if start.value() >= end.value():
                start.setValue(max(0.0, end.value() - 0.1))
            slider.set_value(0, int(start.value() * 1000))
            slider.set_value(1, int(end.value() * 1000))
            length.setText(fmt_time(end.value() - start.value()))

        def play_selection() -> None:
            media.stop_at = int(end.value() * 1000)
            media.seek(int(start.value() * 1000))
            media.player.play()

        slider.changed.connect(sync)
        start.valueChanged.connect(spin_changed)
        end.valueChanged.connect(spin_changed)
        media.on_play = play_selection
        spin_changed()

    def _build_video_trim(self) -> None:
        self._build_trim(video=True)

    def _build_audio_trim(self) -> None:
        self._build_trim(video=False)

    def _build_video_frame(self) -> None:
        dur = self._duration()
        media = self._media = MediaPreview(self.paths[0], video=True)
        self._box.addWidget(media)
        pos = QSlider(Qt.Orientation.Horizontal)
        pos.setRange(0, int(dur * 1000))
        pos.setAccessibleName("Frame time")
        shown = QLabel(fmt_time(0))
        row = QHBoxLayout()
        row.addWidget(pos)
        row.addWidget(shown)
        self.form.addRow("Time", row)
        pos.valueChanged.connect(lambda ms: (shown.setText(fmt_time(ms / 1000)), media.seek(ms)))
        media.player.positionChanged.connect(  # follow playback; blocked so it doesn't seek back
            lambda ms: (pos.blockSignals(True), pos.setValue(ms), pos.blockSignals(False),
                        shown.setText(fmt_time(ms / 1000))))
        self._getters.append(lambda p: p.__setitem__("time", pos.value() / 1000))

    def _build_video_crop(self) -> None:
        info = probe(self.paths[0])
        pm = QPixmap()
        pm.loadFromData(frame_png(self.paths[0], min(1.0, (info.duration or 0) / 2)))
        if pm.isNull():
            raise ValueError(f"Can't read a frame from {self.paths[0].name}")
        self._crop(pm, (info.width or pm.width(), info.height or pm.height()))

    def _build_video_compress(self) -> None:
        dur = self._duration()
        mb = self.paths[0].stat().st_size / 1024 / 1024
        self.form.addRow("Now", QLabel(f"{mb:.1f} MB, {fmt_time(dur)}"))
        mode = self._combo("Mode", "mode", [("Target size", "size"), ("Quality (CRF)", "crf")])
        target = self._spin("Target", "target_mb", 1, 100000, max(1, round(mb / 2)), decimals=1, suffix=" MB")
        crf = self._slider("CRF", "crf", 18, 40, 28)
        self.form.addRow(QLabel("CRF: lower = better quality, bigger file. 23 is near-original."))

        def mode_changed() -> None:
            by_size = mode.currentData() == "size"
            target.setEnabled(by_size)
            crf.setEnabled(not by_size)

        mode.currentIndexChanged.connect(mode_changed)
        mode_changed()

    def _build_video_scale(self) -> None:
        info = probe(self.paths[0])
        if info.height:
            self.form.addRow("Now", QLabel(f"{info.width} × {info.height}"))
        self._combo("Height", "height", [("2160p · 4K", 2160), ("1440p", 1440), ("1080p", 1080), ("720p", 720),
                                         ("480p", 480), ("360p", 360)], index=3)
        self.form.addRow(QLabel("Width follows the original shape."))

    def _build_video_speed(self) -> None:
        dur = self._duration()
        factor = self._spin("Speed", "factor", 0.25, 4, 2, decimals=2, step=0.25, suffix=" ×")
        result = QLabel()
        self.form.addRow("New length", result)
        factor.valueChanged.connect(lambda f: result.setText(fmt_time(dur / f)))
        result.setText(fmt_time(dur / factor.value()))

    def _page_count(self) -> int:
        with pymupdf.open(self.paths[0]) as doc:
            return doc.page_count

    def _build_pdf_rotate(self) -> None:
        count = self._page_count()
        self.form.addRow("Document", QLabel(f"{count} page{'s' * (count != 1)}"))
        self._combo("Turn", "angle", [("Right 90°", 90), ("180°", 180), ("Left 90°", 270)])

        def check(spec: str) -> None:
            parse_pages(spec, count)  # raises ValueError with a readable message

        self._text("Pages", "pages", "", "All pages, or e.g. 1-3, 5", validate=check)

    def _build_pdf_delete(self) -> None:
        count = self._page_count()
        self.form.addRow("Document", QLabel(f"{count} page{'s' * (count != 1)}"))

        def check(spec: str) -> None:
            if not spec.strip():
                raise ValueError("Enter the pages to delete, e.g. 2, 5-7")
            if len(parse_pages(spec, count)) == count:
                raise ValueError("That would delete every page")

        self._text("Delete pages", "pages", "", "e.g. 2, 5-7", validate=check)
