"""Subtitles (SRT, ASS/SSA, WebVTT) → each other via ffmpeg, always written as UTF-8."""
from __future__ import annotations

import tempfile
from pathlib import Path

from shiftdial.engines import JobContext, ffmpeg
from shiftdial.engines.text import read_text
from shiftdial.utils.paths import Output


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        with tempfile.TemporaryDirectory() as td:
            # ffmpeg assumes UTF-8; old subtitle files are often Windows-1252, so re-save first.
            clean = Path(td) / f"in{src.suffix.lower()}"
            clean.write_text(read_text(src), encoding="utf-8")
            with Output.next_to(src, fmt) as out:
                ffmpeg.run(["-i", str(clean), str(out.tmp)], None, ctx, f"{src.name} → {fmt.upper()}")
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs
