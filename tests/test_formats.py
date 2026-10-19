"""Every new format family converts a generated fixture for real. Office-backed tests skip without Office."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from shiftdial.engines import apply_tool, convert
from shiftdial.engines.ffmpeg import ffmpeg_exe, probe
from shiftdial.engines.office import has_app
from shiftdial.registry import FORMATS, KIND_EXTS, formats_for

needs_word = pytest.mark.skipif(not has_app("Word.Application"), reason="Word not installed")
needs_excel = pytest.mark.skipif(not has_app("Excel.Application"), reason="Excel not installed")
needs_ppt = pytest.mark.skipif(not has_app("PowerPoint.Application"), reason="PowerPoint not installed")


def pages(p: Path) -> int:
    with pymupdf.open(p) as doc:
        return doc.page_count


def test_every_dial_fits() -> None:
    for kind in FORMATS:
        ext = sorted(KIND_EXTS[kind])[0]
        assert len(formats_for([Path(f"x.{ext}")])) <= 11, kind


# ---- images ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext", ["tga", "ppm", "pcx", "qoi", "jp2", "sgi"])
def test_more_image_inputs(tmp_path: Path, ext: str) -> None:
    src = tmp_path / f"in.{ext}"
    Image.new("RGB", (40, 30), "teal").save(src)
    [out] = convert([src], "png")
    with Image.open(out) as im:
        assert im.size == (40, 30)


@pytest.mark.parametrize("fmt", ["tiff", "gif", "svg"])
def test_new_image_outputs(image: Path, fmt: str) -> None:
    [out] = convert([image], fmt)
    assert out.suffix == f".{fmt}" and out.stat().st_size > 0
    if fmt == "svg":
        assert "<svg" in out.read_text(encoding="utf-8")


def test_grayscale_keeps_transparency(image: Path) -> None:
    [out] = apply_tool([image], "image:grayscale")
    with Image.open(out) as im:
        rgba = im.convert("RGBA")
        r, g, b, _ = rgba.getpixel((40, 30))
        assert r == g == b and rgba.getpixel((0, 0))[3] == 0


# ---- vector ---------------------------------------------------------------------------

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="red"/></svg>'


@pytest.mark.parametrize("fmt", ["pdf", "png", "jpg"])
def test_svg(tmp_path: Path, fmt: str) -> None:
    src = tmp_path / "logo.svg"
    src.write_text(SVG, encoding="utf-8")
    [out] = convert([src], fmt)
    if fmt == "pdf":
        assert pages(out) == 1
    else:
        with Image.open(out) as im:
            assert im.size == (160, 80)  # rendered at 2×


# ---- video / audio / subtitles --------------------------------------------------------

@pytest.mark.parametrize("fmt", ["hevc", "av1", "mkv", "avi", "apng"])
def test_new_video_outputs(video: Path, fmt: str) -> None:
    [out] = convert([video], fmt)
    assert out.suffix == {"hevc": ".mp4", "av1": ".mp4", "apng": ".png"}.get(fmt, f".{fmt}")
    assert (probe(out).duration or 0) > 0.5 or fmt == "apng"


def test_video_scale(video: Path) -> None:
    [out] = apply_tool([video], "video:scale", {"height": 240})
    assert (probe(out).width, probe(out).height) == (320, 240)


def test_video_subs(video: Path, tmp_path: Path) -> None:
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nHello\n", encoding="utf-8")
    with_subs = tmp_path / "with subs.mkv"
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(video), "-i", str(srt),
                    "-map", "0", "-map", "1", "-c", "copy", str(with_subs)], check=True)
    [out] = apply_tool([with_subs], "video:subs")
    assert "Hello" in out.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="no subtitle"):
        apply_tool([video], "video:subs")


@pytest.mark.parametrize("fmt", ["aac", "opus", "aiff", "alac", "wma"])
def test_new_audio_outputs(audio: Path, fmt: str) -> None:
    [out] = convert([audio], fmt)
    assert out.suffix == (".m4a" if fmt == "alac" else f".{fmt}")
    assert (probe(out).duration or 0) > 0.5


@pytest.mark.parametrize("fmt", ["vtt", "ass", "srt"])
def test_subtitles(tmp_path: Path, fmt: str) -> None:
    src = tmp_path / "film.srt"
    src.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nCafé\n".encode("cp1252"))  # old Windows encoding
    [out] = convert([src], fmt)
    assert "Café" in out.read_text(encoding="utf-8")


# ---- pdf, e-books, text ---------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["png", "jpg", "svg"])
def test_pdf_pages_to_images(pdf: Path, fmt: str) -> None:
    [folder] = convert([pdf], fmt)
    assert folder.is_dir() and len(list(folder.glob(f"*.{fmt}"))) == 3


def test_pdf_text_and_cbz(pdf: Path) -> None:
    [txt] = convert([pdf], "txt")
    assert "Page 2" in txt.read_text(encoding="utf-8")
    [cbz] = convert([pdf], "cbz")
    assert len(zipfile.ZipFile(cbz).namelist()) == 3


@needs_word
def test_pdf_to_docx(pdf: Path) -> None:
    [out] = convert([pdf], "docx")
    assert zipfile.is_zipfile(out)


def _epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                   'full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("book.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
                   'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T'
                   '</dc:title><dc:identifier id="id">x</dc:identifier><dc:language>en</dc:language></metadata>'
                   '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
                   '<spine><itemref idref="c1"/></spine></package>')
        z.writestr("c1.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
                   "<h1>Chapter one</h1><p>It was a dark night.</p></body></html>")


@pytest.mark.parametrize("fmt", ["pdf", "txt"])
def test_ebook(tmp_path: Path, fmt: str) -> None:
    src = tmp_path / "novel.epub"
    _epub(src)
    [out] = convert([src], fmt)
    text = out.read_text(encoding="utf-8") if fmt == "txt" else pymupdf.open(out)[0].get_text()
    assert "dark night" in text


@pytest.mark.parametrize("fmt", ["pdf", "html", "txt"])
def test_markdown(tmp_path: Path, fmt: str) -> None:
    src = tmp_path / "notes.md"
    src.write_text("# Title\n\nSome **bold** text.\n", encoding="utf-8")
    [out] = convert([src], fmt)
    if fmt == "pdf":  # text isn't checked: tests run Qt "offscreen", which has no fonts on Windows
        assert pages(out) == 1
        return
    text = out.read_text(encoding="utf-8")
    assert "bold" in text and "**" not in text


@needs_word
def test_markdown_to_docx(tmp_path: Path) -> None:
    src = tmp_path / "notes.md"
    src.write_text("# Title\n\nHello\n", encoding="utf-8")
    [out] = convert([src], "docx")
    assert zipfile.is_zipfile(out)


# ---- office ---------------------------------------------------------------------------

@needs_word
@pytest.mark.parametrize("fmt", ["pdf", "docx", "txt"])
def test_document(tmp_path: Path, fmt: str) -> None:
    src = tmp_path / "letter.rtf"
    src.write_text(r"{\rtf1\ansi Dear {\b reader}, hello.}", encoding="ascii")
    [out] = convert([src], fmt)
    if fmt == "pdf":
        assert "reader" in pymupdf.open(out)[0].get_text()
    elif fmt == "txt":
        assert "Dear reader" in out.read_text(encoding="utf-8")
    else:
        assert zipfile.is_zipfile(out)


def test_csv_to_json_without_office(tmp_path: Path) -> None:
    src = tmp_path / "people.csv"
    src.write_text("name,age\nAna,31\nBo,27\n", encoding="utf-8")
    [out] = convert([src], "json")
    assert json.loads(out.read_text(encoding="utf-8")) == [{"name": "Ana", "age": "31"}, {"name": "Bo", "age": "27"}]


@needs_excel
def test_csv_to_xlsx_with_excel(tmp_path: Path) -> None:
    src = tmp_path / "people.csv"
    src.write_text("name,age\nAna,31\n", encoding="utf-8")
    [out] = convert([src], "xlsx")
    from openpyxl import load_workbook

    assert load_workbook(out).active["A2"].value == "Ana"


@needs_ppt
def test_slides_to_png_folder(tmp_path: Path) -> None:
    deck = tmp_path / "deck.pptx"
    make = tmp_path / "make.ps1"
    make.write_text('param([string]$out)\n$app = New-Object -ComObject PowerPoint.Application\n'
                    "$p = $app.Presentations.Add(0)\n$null = $p.Slides.Add(1, 12)\n$null = $p.Slides.Add(2, 12)\n"
                    "$p.SaveAs($out)\n$p.Close()\nif ($app.Presentations.Count -eq 0) { $app.Quit() }\n",
                    encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(make), str(deck)],
                   check=True, timeout=120)
    [folder] = convert([deck], "png")
    assert folder.name == "deck slides" and len(list(folder.glob("*.*"))) == 2


# ---- data, fonts, 3D, CAD, archives ---------------------------------------------------

DATA = {"store": "Corner shop", "items": [{"name": "tea", "qty": 2}, {"name": "milk", "qty": 1}]}


@pytest.mark.parametrize("fmt", ["yaml", "toml", "xml", "jsonl", "csv", "xlsx"])
def test_data(tmp_path: Path, fmt: str) -> None:
    src = tmp_path / "order.json"
    src.write_text(json.dumps(DATA), encoding="utf-8")
    [out] = convert([src], fmt)
    if fmt in ("yaml", "toml"):  # round-trip back to JSON
        [back] = convert([out], "json")
        assert json.loads(back.read_text(encoding="utf-8")) == DATA
    elif fmt == "csv":
        assert "Corner shop" in out.read_text(encoding="utf-8-sig")
    else:
        assert out.stat().st_size > 0


def test_yaml_is_parsed_safely(tmp_path: Path) -> None:
    src = tmp_path / "evil.yaml"
    src.write_text("!!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8")
    with pytest.raises(Exception, match="python/object"):
        convert([src], "json")


@pytest.mark.parametrize("fmt", ["woff", "woff2"])
def test_fonts(tmp_path: Path, fmt: str) -> None:
    arial = Path(os.environ["SystemRoot"]) / "Fonts" / "arial.ttf"
    if not arial.exists():
        pytest.skip("no Arial to test with")
    src = tmp_path / "Arial.ttf"
    shutil.copy(arial, src)
    [web] = convert([src], fmt)
    assert web.suffix == f".{fmt}"
    [back] = convert([web], "ttf")
    assert back.suffix == ".ttf" and back.name == "Arial (1).ttf"


@pytest.mark.parametrize("fmt", ["glb", "obj", "ply", "off"])
def test_models(tmp_path: Path, fmt: str) -> None:
    import trimesh

    src = tmp_path / "cube.stl"
    trimesh.creation.box().export(src)
    [out] = convert([src], fmt)
    assert len(trimesh.load(str(out), force="mesh").vertices) >= 8


@pytest.mark.parametrize("fmt", ["pdf", "svg", "png"])
def test_cad(tmp_path: Path, fmt: str) -> None:
    import ezdxf

    src = tmp_path / "plan.dxf"
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (100, 50))
    doc.modelspace().add_circle((50, 25), 20)
    doc.saveas(src)
    [out] = convert([src], fmt)
    assert out.stat().st_size > 200


def test_extract_iso(tmp_path: Path) -> None:
    (tmp_path / "disc").mkdir()
    (tmp_path / "disc" / "readme.txt").write_text("hi", encoding="utf-8")
    iso = tmp_path / "disc image.iso"
    subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "tar.exe"), "--format", "iso9660", "-cf",
                    str(iso), "-C", str(tmp_path / "disc"), "readme.txt"], check=True)
    [out] = apply_tool([iso], "archive:extract")
    assert (out / "readme.txt").read_text(encoding="utf-8") == "hi"
