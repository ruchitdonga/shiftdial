"""Every tool runs on a generated fixture and produces what it claims."""
from __future__ import annotations

import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import py7zr
import pymupdf
import pytest
from PIL import Image

from shiftdial.engines import Cancelled, JobContext, PasswordRequired, apply_tool
from shiftdial.engines import archive as archive_engine
from shiftdial.engines.ffmpeg import probe
from shiftdial.engines.pdf import parse_pages
from shiftdial.engines.video import atempo_chain
from shiftdial.registry import TOOLS, formats_for, tools_for


def size(p: Path) -> tuple[int, int]:
    with Image.open(p) as im:
        return im.size


# ---- registry ------------------------------------------------------------------------

def test_every_tool_has_an_engine_function() -> None:
    from shiftdial.engines import archive, audio, image, pdf, video

    mods = {"image": image, "video": video, "audio": audio, "pdf": pdf, "archive": archive, "folder": archive}
    for t in TOOLS:
        assert callable(getattr(mods[t.kind], f"tool_{t.id.split(':')[1]}")), t.id


def test_tools_for() -> None:
    assert "image:crop" in tools_for([Path("a.png")])
    assert "pdf:merge" not in tools_for([Path("a.pdf")])
    assert "pdf:merge" in tools_for([Path("a.pdf"), Path("b.pdf")])
    assert tools_for([Path("a.png"), Path("b.mp4")]) == []


# ---- image ---------------------------------------------------------------------------

@pytest.mark.parametrize(("params", "expect"), [
    ({"mode": "percent", "value": 50}, (32, 20)),
    ({"mode": "max_side", "value": 32}, (32, 20)),
])
def test_image_resize(image: Path, params: dict, expect: tuple[int, int]) -> None:
    [out] = apply_tool([image], "image:resize", params)
    assert out.suffix == ".png" and size(out) == expect and image.exists()


def test_image_compress(image: Path) -> None:
    jpg = image.with_name("photo.jpg")
    Image.effect_noise((400, 300), 60).convert("RGB").save(jpg, quality=98)
    out_jpg, out_png = apply_tool([jpg, image], "image:compress", {"quality": 40})
    assert out_jpg.stat().st_size < jpg.stat().st_size
    with Image.open(out_png) as im:
        assert im.mode == "P"  # palette PNG


@pytest.mark.parametrize(("op", "expect"), [("right", (40, 64)), ("180", (64, 40)), ("flip_h", (64, 40))])
def test_image_rotate(image: Path, op: str, expect: tuple[int, int]) -> None:
    [out] = apply_tool([image], "image:rotate", {"op": op})
    assert size(out) == expect


def test_image_rotate_right_is_clockwise(tmp_path: Path) -> None:
    src = tmp_path / "dot.png"
    im = Image.new("RGB", (20, 10), "white")
    im.putpixel((0, 0), (255, 0, 0))  # top-left
    im.save(src)
    [out] = apply_tool([src], "image:rotate", {"op": "right"})
    with Image.open(out) as r:
        assert r.getpixel((9, 0)) == (255, 0, 0)  # now top-right


def test_image_crop(image: Path) -> None:
    [out] = apply_tool([image], "image:crop", {"box": [0.25, 0, 0.75, 0.5]})
    assert size(out) == (32, 20)
    with pytest.raises(ValueError):
        apply_tool([image], "image:crop", {"box": [0.5, 0, 0.4, 1]})


def test_image_watermark(image: Path) -> None:
    [out] = apply_tool([image], "image:watermark", {"text": "© test", "position": "center"})
    assert size(out) == (64, 40)
    with pytest.raises(ValueError):
        apply_tool([image], "image:watermark", {"text": "  "})


def _jpeg_with_gps(path: Path, orientation: int = 1) -> None:
    im = Image.new("RGB", (40, 20), "red")
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x8825] = {1: "N", 2: (52.0, 22.0, 0.0)}  # GPSInfo
    im.save(path, exif=exif)


def test_strip_exif(tmp_path: Path) -> None:
    upright, rotated = tmp_path / "a.jpg", tmp_path / "b.jpg"
    _jpeg_with_gps(upright)
    _jpeg_with_gps(rotated, orientation=6)  # 90° CW
    out_a, out_b = apply_tool([upright, rotated], "image:strip_exif")
    for out in (out_a, out_b):
        with Image.open(out) as im:
            assert not im.getexif()
    assert size(out_a) == (40, 20) and size(out_b) == (20, 40)  # rotation baked in


# ---- video ---------------------------------------------------------------------------

def test_video_trim(video: Path) -> None:
    [out] = apply_tool([video], "video:trim", {"start": 0.2, "end": 0.8})
    assert out.suffix == ".mp4" and 0.4 < (probe(out).duration or 0) < 0.8


def test_video_speed(video: Path) -> None:
    [out] = apply_tool([video], "video:speed", {"factor": 2})
    info = probe(out)
    assert 0.3 < (info.duration or 0) < 0.7 and info.has_audio


def test_video_mute(video: Path) -> None:
    [out] = apply_tool([video], "video:mute")
    assert not probe(out).has_audio and probe(out).width == 160


def test_video_frame(video: Path) -> None:
    [out] = apply_tool([video], "video:frame", {"time": 0.5})
    assert out.suffix == ".png" and size(out) == (160, 120)


def test_video_frame_past_end(video: Path) -> None:
    [out] = apply_tool([video], "video:frame", {"time": 99})
    assert size(out) == (160, 120)


def test_video_crop(video: Path) -> None:
    [out] = apply_tool([video], "video:crop", {"box": [0, 0, 0.5, 0.5]})
    assert (probe(out).width, probe(out).height) == (80, 60)


def test_video_compress_crf(video: Path) -> None:
    [out] = apply_tool([video], "video:compress", {"mode": "crf", "crf": 35})
    assert probe(out).duration


def test_video_compress_target_size(video: Path) -> None:
    [out] = apply_tool([video], "video:compress", {"mode": "size", "target_mb": 0.1})
    assert out.suffix == ".mp4" and out.stat().st_size < 0.15 * 1024 * 1024
    assert not list(video.parent.glob("*.tmp.*")) and not list(video.parent.glob("*.log"))


def test_video_compress_target_too_small(video: Path) -> None:
    with pytest.raises(ValueError, match="too small"):
        apply_tool([video], "video:compress", {"mode": "size", "target_mb": 0.001})


def test_atempo_chain() -> None:
    assert atempo_chain(2) == "atempo=2"
    assert atempo_chain(0.25) == "atempo=0.5,atempo=0.5"


# ---- audio ---------------------------------------------------------------------------

def test_audio_trim(audio: Path) -> None:
    [out] = apply_tool([audio], "audio:trim", {"start": 0.25, "end": 0.75})
    assert out.suffix == ".wav" and 0.4 < (probe(out).duration or 0) < 0.6


def test_audio_normalize(audio: Path) -> None:
    [out] = apply_tool([audio], "audio:normalize")
    assert "48000 Hz" in probe(out).banner


def test_audio_channels(audio: Path) -> None:
    [stereo] = apply_tool([audio], "audio:stereo")
    assert "stereo" in probe(stereo).banner
    [mono] = apply_tool([stereo], "audio:mono")
    assert "mono" in probe(mono).banner


# ---- pdf -----------------------------------------------------------------------------

def pages(p: Path) -> int:
    with pymupdf.open(p) as doc:
        return doc.page_count


def test_parse_pages() -> None:
    assert parse_pages("", 4) == [0, 1, 2, 3]
    assert parse_pages("1-2, 4", 5) == [0, 1, 3]
    assert parse_pages("3-", 5) == [2, 3, 4]
    assert parse_pages("-2", 5) == [0, 1]
    for bad in ("0", "6", "a", "3-2"):
        with pytest.raises(ValueError):
            parse_pages(bad, 5)


def test_pdf_merge(pdf: Path) -> None:
    other = pdf.with_name("b.pdf")
    other.write_bytes(pdf.read_bytes())
    [out] = apply_tool([pdf, other], "pdf:merge")
    assert pages(out) == 6


def test_pdf_split(pdf: Path) -> None:
    outs = apply_tool([pdf], "pdf:split")
    assert [o.name for o in outs] == ["rapport final p1.pdf", "rapport final p2.pdf", "rapport final p3.pdf"]
    assert all(pages(o) == 1 for o in outs)


def test_pdf_rotate(pdf: Path) -> None:
    [out] = apply_tool([pdf], "pdf:rotate", {"angle": 90, "pages": "2"})
    with pymupdf.open(out) as doc:
        assert [pg.rotation for pg in doc] == [0, 90, 0]


def test_pdf_delete(pdf: Path) -> None:
    [out] = apply_tool([pdf], "pdf:delete", {"pages": "1, 3"})
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1 and "Page 2" in doc[0].get_text()
    with pytest.raises(ValueError):
        apply_tool([pdf], "pdf:delete", {"pages": "1-3"})


def test_pdf_compress(pdf: Path) -> None:
    [out] = apply_tool([pdf], "pdf:compress")
    assert pages(out) == 3


# ---- archive extract -----------------------------------------------------------------

def _make_archive(tmp_path: Path, fmt: str, members: dict[str, str]) -> Path:
    src = tmp_path / "src"
    for name, text in members.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(text, encoding="utf-8")
    out = tmp_path / f"pack ü.{fmt}"
    if fmt == "zip":
        with zipfile.ZipFile(out, "w") as z:
            for name in members:
                z.write(src / name, name)
    elif fmt == "7z":
        with py7zr.SevenZipFile(out, "w") as z:
            for name in members:
                z.write(src / name, name)
    else:
        with tarfile.open(out, "w:gz") as t:
            for name in members:
                t.add(src / name, name)
    return out


@pytest.mark.parametrize("fmt", ["zip", "7z", "tar.gz"])
def test_extract(tmp_path: Path, fmt: str) -> None:
    arc = _make_archive(tmp_path, fmt, {"a.txt": "A", "sub/b é.txt": "B"})
    assert tools_for([arc]) == ["archive:extract"]
    [out] = apply_tool([arc], "archive:extract")
    assert out == tmp_path / "pack ü"
    assert (out / "a.txt").read_text(encoding="utf-8") == "A"
    assert (out / "sub" / "b é.txt").read_text(encoding="utf-8") == "B"
    assert not [p for p in tmp_path.iterdir() if ".extracting-" in p.name]


def test_extract_is_first_on_the_formats_wheel(tmp_path: Path) -> None:
    from shiftdial.engines import run

    arc = _make_archive(tmp_path, "zip", {"a.txt": "A"})
    assert formats_for([arc]) == ["archive:extract", "zip", "7z", "tar.gz"]
    [out] = run([arc], formats_for([arc])[0])  # what a drop on the first wedge does
    assert (out / "a.txt").read_text(encoding="utf-8") == "A"


def test_extract_single_top_folder_is_not_nested(tmp_path: Path) -> None:
    arc = _make_archive(tmp_path, "zip", {"photos/1.txt": "1", "photos/2.txt": "2"})
    [out] = apply_tool([arc], "archive:extract")
    assert out == tmp_path / "photos" and (out / "1.txt").exists()


def test_extract_never_merges_into_existing_folder(tmp_path: Path) -> None:
    arc = _make_archive(tmp_path, "zip", {"a.txt": "new", "b.txt": "x"})
    (tmp_path / "pack ü").mkdir()
    (tmp_path / "pack ü" / "a.txt").write_text("old", encoding="utf-8")
    [out] = apply_tool([arc], "archive:extract")
    assert out.name == "pack ü (1)"
    assert (tmp_path / "pack ü" / "a.txt").read_text(encoding="utf-8") == "old"


def test_extract_refuses_path_traversal(tmp_path: Path) -> None:
    (tmp_path / "inner").mkdir()
    evil = tmp_path / "inner" / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../escaped.txt", "gotcha")
    with pytest.raises(ValueError, match="unsafe path"):
        apply_tool([evil], "archive:extract")
    assert not (tmp_path / "escaped.txt").exists()
    assert sorted(p.name for p in (tmp_path / "inner").iterdir()) == ["evil.zip"]


def test_extract_cancel_leaves_nothing(tmp_path: Path) -> None:
    arc = _make_archive(tmp_path, "zip", {f"f{i}.txt": "x" * 1000 for i in range(50)})
    ctx = JobContext()
    ctx.cancel_event.set()
    with pytest.raises(Cancelled):
        apply_tool([arc], "archive:extract", ctx=ctx)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pack ü.zip", "src"]


def test_extract_reports_progress(tmp_path: Path) -> None:
    arc = _make_archive(tmp_path, "zip", {"a.txt": "A", "b.txt": "B"})
    seen: list[float] = []
    apply_tool([arc], "archive:extract", ctx=JobContext(lambda f, s: seen.append(f)))
    assert 0 < seen[0] < 1 and seen[-1] == 1.0


@pytest.mark.parametrize(("tool", "ext"), [("folder:zip", "zip"), ("folder:7z", "7z"), ("folder:targz", "tar.gz")])
def test_zip_folder_from_tools_wheel(tmp_path: Path, tool: str, ext: str) -> None:
    folder = tmp_path / "Holiday photos"
    (folder / "day 1").mkdir(parents=True)
    (folder / "day 1" / "a.txt").write_text("A", encoding="utf-8")
    assert tools_for([folder]) == ["folder:zip", "folder:7z", "folder:targz"]
    assert formats_for([folder]) == ["zip", "7z", "tar.gz"]
    [out] = apply_tool([folder], tool)
    assert out == tmp_path / f"Holiday photos.{ext}"
    [back] = apply_tool([out], "archive:extract")  # round trip: single top folder isn't nested
    assert (back / "day 1" / "a.txt").read_text(encoding="utf-8") == "A"


def test_slides_offer_pdf() -> None:
    assert formats_for([Path("deck.pptx"), Path("old.PPT")])[0] == "pdf"


def test_pptx_to_pdf_with_powerpoint(tmp_path: Path) -> None:
    from shiftdial.engines import convert, office

    if not office._has_powerpoint():
        pytest.skip("PowerPoint not installed")
    deck = tmp_path / "démo deck.pptx"
    make = tmp_path / "make.ps1"
    make.write_text('param([string]$out)\n$app = New-Object -ComObject PowerPoint.Application\n'
                    "$p = $app.Presentations.Add(0)\n$null = $p.Slides.Add(1, 12)\n$null = $p.Slides.Add(2, 12)\n"
                    "$p.SaveAs($out)\n$p.Close()\nif ($app.Presentations.Count -eq 0) { $app.Quit() }\n",
                    encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(make), str(deck)],
                   check=True, timeout=120)
    [out] = convert([deck], "pdf")
    assert out.name == "démo deck.pdf" and pages(out) == 2
    assert not list(tmp_path.glob("*.tmp.*"))


def test_not_an_archive(tmp_path: Path) -> None:
    fake = tmp_path / "fake.zip"
    fake.write_bytes(b"this is not a zip")
    with pytest.raises(RuntimeError, match="Can't extract"):
        apply_tool([fake], "archive:extract")


def _encrypted_zip(tmp_path: Path, method: str) -> Path:
    """Python's zipfile can't write encrypted zips; Windows' tar.exe can."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "secret.txt").write_text("top secret", encoding="utf-8")
    out = tmp_path / "locked.zip"
    subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "tar.exe"), "-a", "-c",
                    "--options", f"zip:encryption={method}", "--passphrase", "pw 1",
                    "-f", str(out), "-C", str(src), "secret.txt"], check=True)
    return out


@pytest.mark.parametrize("method", ["aes256", "zipcrypt"])
@pytest.mark.parametrize("use_7zip", [False, True])
def test_extract_password_zip(tmp_path: Path, method: str, use_7zip: bool,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    if use_7zip and not archive_engine._seven_zip():
        pytest.skip("7-Zip not installed")
    if not use_7zip:
        monkeypatch.setattr(archive_engine, "_seven_zip", lambda: None)
    arc = _encrypted_zip(tmp_path, method)
    with pytest.raises(PasswordRequired) as no_pw:
        apply_tool([arc], "archive:extract")
    assert not no_pw.value.wrong
    with pytest.raises(PasswordRequired) as bad_pw:
        apply_tool([arc], "archive:extract", {"password": "nope"})
    assert bad_pw.value.wrong
    [out] = apply_tool([arc], "archive:extract", {"password": "pw 1"})
    assert (out / "secret.txt").read_text(encoding="utf-8") == "top secret"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["locked", "locked.zip", "src"]


def test_extract_password_7z_with_hidden_names(tmp_path: Path) -> None:
    if not archive_engine._seven_zip():
        pytest.skip("7-Zip not installed")
    arc = tmp_path / "vault.7z"
    with py7zr.SevenZipFile(arc, "w", password="s3cret", header_encryption=True) as z:
        z.writestr("hidden", "a.txt")
        z.writestr("hidden2", "b.txt")
    with pytest.raises(PasswordRequired):
        apply_tool([arc], "archive:extract")
    with pytest.raises(PasswordRequired):
        apply_tool([arc], "archive:extract", {"password": "wrong"})
    [out] = apply_tool([arc], "archive:extract", {"password": "s3cret"})
    assert (out / "a.txt").read_text() == "hidden"


def test_password_7z_without_7zip_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive_engine, "_seven_zip", lambda: None)
    arc = _make_archive(tmp_path, "7z", {"a.txt": "A"})
    with pytest.raises(RuntimeError, match="7-Zip"):
        apply_tool([arc], "archive:extract", {"password": "x"})
