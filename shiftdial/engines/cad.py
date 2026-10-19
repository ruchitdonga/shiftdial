"""CAD drawings: DXF → PDF, SVG, PNG (ezdxf's drawing add-on, model space, on a white page)."""
from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf, svg
from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration

from shiftdial.engines import JobContext
from shiftdial.utils.paths import Output

PAGE = layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(10))  # 0 × 0 = fit the drawing
CONFIG = Configuration(background_policy=BackgroundPolicy.WHITE)


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        try:
            doc = ezdxf.readfile(src)
        except (OSError, ezdxf.DXFStructureError) as e:
            raise ValueError(f"Can't read {src.name}: {e}") from e
        backend = svg.SVGBackend() if fmt == "svg" else pymupdf.PyMuPdfBackend()
        Frontend(RenderContext(doc), backend, config=CONFIG).draw_layout(doc.modelspace())
        with Output.next_to(src, fmt) as out:
            if fmt == "svg":
                out.tmp.write_text(backend.get_string(PAGE), encoding="utf-8")
            elif fmt == "pdf":
                out.tmp.write_bytes(backend.get_pdf_bytes(PAGE))
            else:
                out.tmp.write_bytes(backend.get_pixmap_bytes(PAGE, fmt="png", dpi=150))
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs
