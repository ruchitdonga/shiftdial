"""Plain text, Markdown and HTML → each other, PDF (Qt's text engine), or DOCX (via Word/LibreOffice)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QFont, QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument

from shiftdial.engines import JobContext
from shiftdial.utils.paths import Output

_app: QGuiApplication | None = None


def _ensure_qt() -> None:
    global _app
    if QGuiApplication.instance() is None:  # no windows are shown; it's needed for fonts
        from PySide6.QtWidgets import QApplication  # full app type, so widgets can still be made later

        _app = QApplication(["shiftdial"])


def read_text(src: Path) -> str:
    raw = src.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def document(src: Path) -> QTextDocument:
    _ensure_qt()
    doc = QTextDocument()
    doc.setDefaultFont(QFont("Segoe UI", 10))
    text, ext = read_text(src), src.suffix.lower()
    if ext in (".md", ".markdown"):
        doc.setMarkdown(text)
    elif ext in (".htm", ".html"):
        doc.setHtml(text)
    else:
        doc.setPlainText(text)
    return doc


def to_pdf(doc: QTextDocument, path: Path) -> None:
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter)
    doc.print_(writer)


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    from shiftdial.engines import office

    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        doc = document(src)
        with Output.next_to(src, fmt) as out:
            if fmt == "pdf":
                to_pdf(doc, out.tmp)
            elif fmt == "html":
                out.tmp.write_text(doc.toHtml(), encoding="utf-8")
            elif fmt == "md":
                out.tmp.write_text(doc.toMarkdown(), encoding="utf-8")
            elif fmt == "txt":
                out.tmp.write_text(doc.toPlainText(), encoding="utf-8")
            elif fmt == "docx":
                with tempfile.TemporaryDirectory() as td:
                    html = Path(td) / f"{src.stem}.html"
                    html.write_text(doc.toHtml(), encoding="utf-8")
                    if office.has_app("Word.Application"):
                        office.office_save("word", html, out.tmp, office.WORD["docx"], ctx)
                    else:
                        office.libreoffice_save(html, out.tmp, "docx", ctx)
            else:
                raise ValueError(fmt)
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs
