"""Vector drawings → PDF / PNG / JPG. SVG(Z) via PyMuPDF; Windows EMF/WMF via Pillow (Windows renders them)."""
from __future__ import annotations

import gzip
from pathlib import Path

import pymupdf
from PIL import Image

from shiftdial.engines import JobContext
from shiftdial.engines.image import flatten
from shiftdial.utils.paths import Output

ZOOM = 2  # SVGs are often small; render at 2× their pixel size so the PNG is crisp


def _svg(src: Path) -> pymupdf.Document:
    raw = src.read_bytes()
    if src.suffix.lower() == ".svgz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return pymupdf.open(stream=raw, filetype="svg")


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        with Output.next_to(src, fmt) as out:
            if src.suffix.lower() in (".svg", ".svgz"):
                with _svg(src) as doc:
                    if fmt == "pdf":
                        out.tmp.write_bytes(doc.convert_to_pdf())
                    else:
                        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), alpha=fmt == "png")
                        out.tmp.write_bytes(pix.tobytes("png" if fmt == "png" else "jpg"))
            else:
                with Image.open(src) as im:
                    im.load()  # EMF/WMF are rasterised by Windows here
                    rgba = im.convert("RGBA")
                if fmt == "png":
                    rgba.save(out.tmp, "PNG")
                else:
                    flatten(rgba).save(out.tmp, "PDF" if fmt == "pdf" else "JPEG", quality=92)
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs
