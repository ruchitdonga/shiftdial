"""Look: a hardware instrument. Warm charcoal (or paper) surfaces, hairlines, one signal orange."""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import QApplication

ICON_FONT = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
TEXT_FONT = "Bahnschrift"
LABEL_FONT = "Bahnschrift SemiBold SemiCondensed"
MONO_FONT = "Cascadia Mono"

ICONS: dict[str, str] = {  # Segoe Fluent Icons code points (thin line glyphs)
    "image:resize": "", "image:compress": "", "image:rotate": "",
    "image:crop": "", "image:strip_exif": "", "image:watermark": "",
    "video:compress": "", "video:trim": "", "video:speed": "",
    "video:mute": "", "video:frame": "", "video:crop": "",
    "audio:trim": "", "audio:normalize": "", "audio:mono": "", "audio:stereo": "",
    "pdf:merge": "", "pdf:split": "", "pdf:rotate": "", "pdf:delete": "",
    "pdf:compress": "", "archive:extract": "",
    "folder:zip": "", "folder:7z": "", "folder:targz": "",
    "image:grayscale": "", "video:scale": "", "video:subs": "",
}
CLOSE_ICON = ""


@dataclass(frozen=True)
class Palette:
    dark: bool
    surface: QColor
    raised: QColor
    line: QColor
    text: QColor
    muted: QColor
    signal: QColor
    on_signal: QColor
    danger: QColor
    ok: QColor


def is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def set_mode(mode: str) -> None:
    """'system' follows Windows; 'light'/'dark' override it. Repaints via colorSchemeChanged."""
    QGuiApplication.styleHints().setColorScheme(
        {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(mode, Qt.ColorScheme.Unknown))


def palette() -> Palette:
    if is_dark():
        return Palette(True, QColor("#1c1b19"), QColor("#262522"), QColor("#3b3a36"), QColor("#ecebe5"),
                       QColor("#8e8b83"), QColor("#ff5b1f"), QColor("#1c1b19"), QColor("#ff4d4d"),
                       QColor("#b8e05a"))
    return Palette(False, QColor("#eeece6"), QColor("#f8f7f3"), QColor("#d4d1c8"), QColor("#1d1c1a"),
                   QColor("#76736b"), QColor("#f04e12"), QColor("#ffffff"), QColor("#d92d2d"),
                   QColor("#4f7a12"))


def alpha(c: QColor, a: float) -> QColor:
    out = QColor(c)
    out.setAlphaF(a)
    return out


def motion() -> bool:
    """Windows 'Animation effects' (Settings › Accessibility › Visual effects)."""
    if sys.platform != "win32":
        return True
    on = ctypes.c_bool(True)
    ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
    return bool(on.value)


def ms(duration: int) -> int:
    """Animation length, or 0 when the user has turned animations off."""
    return duration if motion() else 0


def icon_font(px: float) -> QFont:
    f = QFont()
    f.setFamilies(ICON_FONT)
    f.setPixelSize(round(px))
    return f


def text_font(pt: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(TEXT_FONT)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    return f


def label_font(pt: float, spacing: float = 6) -> QFont:
    """Caps labels: condensed, tracked out, like printing on a panel."""
    f = QFont(LABEL_FONT)
    f.setPointSizeF(pt)
    f.setCapitalization(QFont.Capitalization.AllUppercase)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + spacing)
    return f


def mono_font(pt: float) -> QFont:
    f = QFont(MONO_FONT)
    f.setPointSizeF(pt)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def hexa(c: QColor) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.3f})"


def stylesheet() -> str:
    p = palette()
    return f"""
    * {{ font-family: "{TEXT_FONT}"; font-size: 10pt; color: {p.text.name()}; }}
    QLabel {{ background: transparent; }}
    QLabel[role="sub"] {{ color: {p.muted.name()}; }}
    QLabel[role="error"] {{ color: {p.danger.name()}; }}
    QLabel[role="caps"] {{ color: {p.muted.name()}; font-family: "{LABEL_FONT}"; font-size: 8pt;
                          letter-spacing: 1px; }}
    QPushButton {{
        background: {p.raised.name()}; border: 1px solid {p.line.name()}; border-radius: 4px;
        padding: 6px 14px; font-family: "{LABEL_FONT}"; letter-spacing: 1px;
    }}
    QPushButton:hover {{ border-color: {p.text.name()}; }}
    QPushButton:pressed {{ background: {p.surface.name()}; }}
    QPushButton:disabled {{ color: {p.muted.name()}; border-color: {p.line.name()}; }}
    QPushButton[role="primary"] {{ background: {p.signal.name()}; border-color: {p.signal.name()};
                                  color: {p.on_signal.name()}; }}
    QPushButton[role="primary"]:hover {{ background: {p.signal.lighter(110).name()}; }}
    QPushButton[role="ghost"] {{ background: transparent; border: none; padding: 2px; }}
    QPushButton[role="ghost"]:hover {{ color: {p.signal.name()}; }}
    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
        background: {p.raised.name()}; border: 1px solid {p.line.name()}; border-radius: 4px;
        padding: 5px 8px; selection-background-color: {p.signal.name()}; selection-color: {p.on_signal.name()};
    }}
    QDoubleSpinBox, QSpinBox {{ font-family: "{MONO_FONT}"; }}
    QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {p.signal.name()}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView, QMenu {{
        background: {p.raised.name()}; border: 1px solid {p.line.name()}; padding: 4px;
        selection-background-color: {p.signal.name()}; selection-color: {p.on_signal.name()};
    }}
    QMenu::item {{ padding: 6px 20px; }}
    QMenu::item:selected {{ background: {p.signal.name()}; color: {p.on_signal.name()}; }}
    QMenu::separator {{ height: 1px; background: {p.line.name()}; margin: 4px 6px; }}
    QMenu::indicator:checked {{ width: 6px; height: 6px; margin-left: 8px; background: {p.signal.name()}; }}
    QSlider::groove:horizontal {{ height: 2px; background: {p.line.name()}; }}
    QSlider::sub-page:horizontal {{ height: 2px; background: {p.signal.name()}; }}
    QSlider::handle:horizontal {{
        width: 10px; height: 18px; margin: -8px 0; border-radius: 2px;
        background: {p.raised.name()}; border: 1px solid {p.text.name()};
    }}
    QSlider::handle:horizontal:hover {{ border-color: {p.signal.name()}; }}
    QToolTip {{ background: {p.raised.name()}; border: 1px solid {p.line.name()}; padding: 4px; }}
    """


def apply(app: QApplication) -> None:
    """Install fonts/stylesheet now and again whenever light/dark changes."""
    app.setFont(text_font(10))
    app.setStyleSheet(stylesheet())
    QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _: app.setStyleSheet(stylesheet()))
