"""Fonts: TTF/OTF desktop fonts ↔ WOFF/WOFF2 web fonts (fontTools; WOFF2 uses Brotli)."""
from __future__ import annotations

from pathlib import Path

from fontTools.ttLib import TTFont

from shiftdial.engines import JobContext
from shiftdial.utils.paths import Output, split_ext


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        font = TTFont(src)
        if fmt == "ttf":  # back to a desktop font; PostScript-outline fonts are .otf
            font.flavor = None
            ext = "otf" if ("CFF " in font or "CFF2" in font) else "ttf"
        else:
            font.flavor, ext = fmt, fmt
        with Output(src.parent, split_ext(src.name)[0], ext) as out:
            font.save(out.tmp)
        font.close()
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs
