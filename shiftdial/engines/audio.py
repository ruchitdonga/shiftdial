"""Audio conversions via ffmpeg. Also used for 'extract audio' from video."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from shiftdial.engines import JobContext, ffmpeg
from shiftdial.engines.ffmpeg import Pass, Probe

_A = ["-vn", "-map", "0:a:0"]  # first audio stream only; drops cover art, fails clearly if no audio

ARGS: dict[str, list[str]] = {
    "mp3": [*_A, "-c:a", "libmp3lame", "-q:a", "2"],
    "wav": [*_A, "-c:a", "pcm_s16le"],
    "flac": [*_A, "-c:a", "flac"],
    "ogg": [*_A, "-c:a", "libvorbis", "-q:a", "5"],
    "m4a": [*_A, "-c:a", "aac", "-b:a", "192k"],
    "aac": [*_A, "-c:a", "aac", "-b:a", "192k"],
    "opus": [*_A, "-c:a", "libopus", "-b:a", "128k"],
    "aiff": [*_A, "-c:a", "pcm_s16be"],
    "alac": [*_A, "-c:a", "alac"],
    "wma": [*_A, "-c:a", "wmav2", "-b:a", "192k"],
}
OUT_EXT = {"alac": "m4a"}


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    return ffmpeg.convert_each(paths, fmt, ARGS[fmt], ctx, OUT_EXT.get(fmt))


def same_ext(src: Path) -> str:
    """Tools keep the source format when we can encode it; otherwise M4A."""
    ext = src.suffix.lower().lstrip(".")
    return ext if ext in ARGS else "m4a"


def _reencode(paths: list[Path], ctx: JobContext, label: str, extra: list[str],
              before_input: list[str] | None = None, seconds: float | None = None) -> list[Path]:
    def passes(src: Path, info: Probe, out: Path) -> list[Pass]:
        ext = out.suffix.lstrip(".")
        args = [*(before_input or []), "-i", str(src), *ARGS[ext], *extra, str(out)]
        return [(args, seconds or info.duration)]

    return ffmpeg.each(paths, ctx, label, same_ext, passes)


def tool_trim(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    start, end = float(p.get("start", 0)), float(p["end"])
    if not 0 <= start < end:
        raise ValueError(f"Bad trim range {start}..{end}")
    return _reencode(paths, ctx, "Trim", ["-t", f"{end - start:.3f}"],
                     before_input=["-ss", f"{start:.3f}"], seconds=end - start)


def tool_normalize(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """EBU R128 loudness to -16 LUFS (single-pass loudnorm). It resamples, so pin 48 kHz."""
    return _reencode(paths, ctx, "Normalize", ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000"])


def tool_mono(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    return _reencode(paths, ctx, "Mono", ["-ac", "1"])


def tool_stereo(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    return _reencode(paths, ctx, "Stereo", ["-ac", "2"])
