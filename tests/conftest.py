"""Generated fixtures. Names contain spaces and non-ASCII on purpose."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from shiftdial.engines.ffmpeg import ffmpeg_exe


@pytest.fixture
def image(tmp_path: Path) -> Path:
    p = tmp_path / "tést fïle 画像.png"
    im = Image.new("RGBA", (64, 40), (200, 30, 30, 255))
    im.paste((0, 0, 0, 0), (0, 0, 16, 16))  # some transparency
    im.save(p)
    return p


def _ff(*args: str) -> None:
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    p = tmp_path / "clip vidéo.mp4"
    _ff("-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p))
    return p


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    import pymupdf

    p = tmp_path / "rapport final.pdf"
    with pymupdf.open() as doc:
        for i in range(3):
            doc.new_page().insert_text((72, 72), f"Page {i + 1}")
        doc.save(p)
    return p


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    p = tmp_path / "sön g.wav"
    _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(p))
    return p
