"""E-books and fixed-layout documents (EPUB, MOBI, AZW3, FB2, CBZ, XPS) → PDF or TXT via PyMuPDF."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from shiftdial.engines import JobContext
from shiftdial.utils.paths import Output


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        with pymupdf.open(src) as doc:
            if doc.needs_pass:
                raise ValueError(f"{src.name} is DRM/password protected")
            with Output.next_to(src, fmt) as out:
                if fmt == "pdf":
                    out.tmp.write_bytes(doc.convert_to_pdf())
                else:
                    out.tmp.write_text("\n\n".join(page.get_text() for page in doc), encoding="utf-8")
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs
