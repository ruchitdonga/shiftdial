"""PDF tools via PyMuPDF: merge, split, rotate pages, delete pages, compress."""
from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

import pymupdf

from shiftdial.engines import JobContext
from shiftdial.utils.paths import Output, commit_dir, split_ext


def parse_pages(spec: str, count: int) -> list[int]:
    """'1-3, 5, 8-' → 0-based sorted page indices. Empty spec means every page."""
    if not spec.strip():
        return list(range(count))
    pages: set[int] = set()
    for part in filter(None, (s.strip() for s in spec.split(","))):
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)|(\d+)", part)
        if not m:
            raise ValueError(f"Bad page range '{part}' (use e.g. 1-3, 5, 8-)")
        if m[3]:
            first = last = int(m[3])
        else:
            first, last = int(m[1] or 1), int(m[2] or count)
        if not 1 <= first <= last <= count:
            raise ValueError(f"Pages '{part}' are outside 1-{count}")
        pages.update(range(first - 1, last))
    return sorted(pages)


def _open(src: Path) -> pymupdf.Document:
    doc = pymupdf.open(src)
    if doc.needs_pass:
        doc.close()
        raise ValueError(f"{src.name} is password protected")
    return doc


def _save(doc: pymupdf.Document, path: Path) -> None:
    doc.save(path, garbage=4, deflate=True)


def _each(paths: list[Path], ctx: JobContext, label: str,
          edit: Callable[[pymupdf.Document], None]) -> list[Path]:
    """Open each PDF, ``edit(doc)`` it in memory, save next to the source."""
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name}: {label}")
        with _open(src) as doc:
            edit(doc)
            with Output.next_to(src, "pdf") as out:
                _save(doc, out.tmp)
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs


def _pages_out(src: Path, ext: str, render: Callable[[pymupdf.Page], bytes], ctx: JobContext) -> Path:
    """One file per page: a single file for a 1-page PDF, else a '<name> pages' folder."""
    stem = split_ext(src.name)[0]
    with _open(src) as doc:
        n = doc.page_count
        if n == 1:
            with Output.next_to(src, ext) as out:
                out.tmp.write_bytes(render(doc[0]))
            return out.final
        with tempfile.TemporaryDirectory(dir=src.parent, prefix=".shiftdial-") as td:
            folder = Path(td) / "pages"
            folder.mkdir()
            width = len(str(n))
            for i, page in enumerate(doc):
                ctx.check()
                ctx.progress(i / n, f"{src.name}: page {i + 1} of {n}")
                (folder / f"{stem} p{i + 1:0{width}d}.{ext}").write_bytes(render(page))
            return commit_dir(folder, src.parent, f"{stem} pages")


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    from shiftdial.engines import office

    renders: dict[str, Callable[[pymupdf.Page], bytes]] = {
        "png": lambda pg: pg.get_pixmap(dpi=150).tobytes("png"),
        "jpg": lambda pg: pg.get_pixmap(dpi=150).tobytes("jpg", jpg_quality=90),
        "svg": lambda pg: pg.get_svg_image().encode("utf-8"),
    }
    outputs: list[Path] = []
    for src in paths:
        ctx.check()
        if fmt in renders:
            outputs.append(_pages_out(src, fmt, renders[fmt], ctx))
        elif fmt == "txt":
            with _open(src) as doc, Output.next_to(src, "txt") as out:
                out.tmp.write_text("\n\f".join(page.get_text() for page in doc), encoding="utf-8")
            outputs.append(out.final)
        elif fmt == "cbz":
            with _open(src) as doc, Output.next_to(src, "cbz") as out, \
                    zipfile.ZipFile(out.tmp, "w", zipfile.ZIP_STORED) as z:
                for i, page in enumerate(doc):
                    ctx.check()
                    ctx.progress(i / doc.page_count, f"{src.name}: page {i + 1}")
                    z.writestr(f"{i + 1:04d}.jpg", renders["jpg"](page))
            outputs.append(out.final)
        elif fmt == "docx":
            if not office.has_app("Word.Application"):
                raise RuntimeError("PDF → DOCX needs Microsoft Word installed")
            with Output.next_to(src, "docx") as out:
                ctx.progress(0.1, f"{src.name} → DOCX (Word)")
                office.office_save("word", src, out.tmp, office.WORD["docx"], ctx)
            outputs.append(out.final)
        else:
            raise ValueError(fmt)
    ctx.progress(1.0, "Done")
    return outputs


def tool_merge(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """All PDFs → one, in drop order, named after the first."""
    with pymupdf.open() as merged:
        for i, src in enumerate(paths):
            ctx.check()
            ctx.progress(i / len(paths), f"Adding {src.name}")
            with _open(src) as doc:
                merged.insert_pdf(doc)
        with Output.next_to(paths[0], "pdf") as out:
            _save(merged, out.tmp)
    ctx.progress(1.0, "Done")
    return [out.final]


def tool_split(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """One file per page: 'report p01.pdf', 'report p02.pdf', ..."""
    outputs: list[Path] = []
    for src in paths:
        stem = split_ext(src.name)[0]
        with _open(src) as doc:
            width = len(str(doc.page_count))
            for i in range(doc.page_count):
                ctx.check()
                ctx.progress(i / doc.page_count, f"{src.name}: page {i + 1}")
                with pymupdf.open() as page_doc:
                    page_doc.insert_pdf(doc, from_page=i, to_page=i)
                    with Output(src.parent, f"{stem} p{i + 1:0{width}d}", "pdf") as out:
                        _save(page_doc, out.tmp)
                outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs


def tool_rotate(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """angle 90/180/270 clockwise; pages like '1-3, 5' (empty = all)."""
    angle, spec = int(p.get("angle", 90)), str(p.get("pages", ""))
    if angle not in (90, 180, 270):
        raise ValueError("Angle must be 90, 180 or 270")

    def edit(doc: pymupdf.Document) -> None:
        for i in parse_pages(spec, doc.page_count):
            page = doc[i]
            page.set_rotation((page.rotation + angle) % 360)

    return _each(paths, ctx, "Rotate pages", edit)


def tool_delete(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    spec = str(p.get("pages", ""))
    if not spec.strip():
        raise ValueError("Say which pages to delete, e.g. 2, 5-7")

    def edit(doc: pymupdf.Document) -> None:
        doomed = parse_pages(spec, doc.page_count)
        if len(doomed) == doc.page_count:
            raise ValueError("That would delete every page")
        doc.delete_pages(doomed)

    return _each(paths, ctx, "Delete pages", edit)


def tool_compress(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """Downsample images above 150 dpi to JPEG q75, drop unused objects, deflate streams."""
    return _each(paths, ctx, "Compress",
                 lambda doc: doc.rewrite_images(dpi_threshold=160, dpi_target=150, quality=75))
