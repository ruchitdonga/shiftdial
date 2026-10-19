"""Each engine converts a generated fixture to every format it offers."""
from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pymupdf
import py7zr
import pytest
from PIL import Image

from shiftdial.engines import Cancelled, JobContext, convert
from shiftdial.engines import audio as audio_engine
from shiftdial.engines import video as video_engine
from shiftdial.engines.ffmpeg import probe
from shiftdial.registry import FORMATS


@pytest.mark.parametrize("fmt", [f for f in FORMATS["image"] if f not in ("pdf", "svg")])  # svg: test_formats
def test_image(image: Path, fmt: str) -> None:
    [out] = convert([image], fmt)
    suffix = " (1)" if fmt == "png" else ""  # same format as source: must not overwrite
    assert out.name == f"{image.stem}{suffix}.{fmt}" and not list(image.parent.glob("*.tmp.*"))
    with Image.open(out) as im:
        assert im.size == ((64, 64) if fmt == "ico" else (64, 40))


def test_images_to_one_pdf(image: Path) -> None:
    second = image.with_name("two.png")
    Image.new("RGB", (30, 30), "blue").save(second)
    [out] = convert([image, second], "pdf")
    with pymupdf.open(out) as doc:
        assert doc.page_count == 2


@pytest.mark.parametrize("fmt", FORMATS["video"])
def test_video(video: Path, fmt: str) -> None:
    [out] = convert([video], fmt)
    assert out.suffix == f".{video_engine.OUT_EXT.get(fmt, fmt)}" and out.stat().st_size > 0
    assert (probe(out).duration or 0) > 0.5 or fmt == "apng"


@pytest.mark.parametrize("fmt", FORMATS["audio"])
def test_audio(audio: Path, fmt: str) -> None:
    [out] = convert([audio], fmt)
    assert out.suffix == f".{audio_engine.OUT_EXT.get(fmt, fmt)}"
    assert (probe(out).duration or 0) > 0.5


def test_same_format_does_not_overwrite(audio: Path) -> None:
    before = audio.read_bytes()
    [out] = convert([audio], "wav")
    assert out.name == f"{audio.stem} (1).wav" and audio.read_bytes() == before


@pytest.mark.parametrize("fmt", ["zip", "7z", "tar.gz"])
def test_archive(image: Path, audio: Path, fmt: str) -> None:
    sub = image.parent / "földer"
    sub.mkdir()
    (sub / "inner.txt").write_text("hi", encoding="utf-8")
    [out] = convert([image, audio, sub], fmt)
    assert out.name == f"{image.parent.name}.{fmt}"
    if fmt == "zip":
        names = zipfile.ZipFile(out).namelist()
    elif fmt == "7z":
        with py7zr.SevenZipFile(out) as z:
            names = z.getnames()
    else:
        with tarfile.open(out) as t:
            names = t.getnames()
    assert {image.name, audio.name, "földer/inner.txt"} <= set(names)


def test_progress_reported(audio: Path) -> None:
    seen: list[float] = []
    convert([audio], "mp3", JobContext(lambda f, s: seen.append(f)))
    assert seen and seen[-1] == 1.0


def test_cancel_leaves_no_files(video: Path) -> None:
    ctx = JobContext()
    ctx.on_progress = lambda f, s: ctx.cancel_event.set()  # cancel on first progress line
    with pytest.raises(Cancelled):
        convert([video], "webm", ctx)
    assert sorted(p.name for p in video.parent.iterdir()) == [video.name]


def test_wrong_format_rejected(image: Path) -> None:
    with pytest.raises(ValueError):
        convert([image], "mp4")
