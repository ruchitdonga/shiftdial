"""Video conversions and tools via ffmpeg."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from shiftdial.engines import JobContext, audio, ffmpeg
from shiftdial.engines.ffmpeg import Pass, Probe

_MAP = ["-map", "0:v:0", "-map", "0:a:0?"]
EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"  # H.264 4:2:0 needs even dimensions
AUDIO_KBPS = 128


def codec(ext: str, crf: int | None = None) -> list[str]:
    """Encoder args for an output container."""
    if ext == "webm":
        return ["-c:v", "libvpx-vp9", "-crf", str(crf or 32), "-b:v", "0", "-row-mt", "1",
                "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p",
                "-c:a", "libopus", "-b:a", f"{AUDIO_KBPS}k"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf or 23), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]


ARGS: dict[str, list[str]] = {
    "mp4": [*_MAP, "-vf", EVEN, *codec("mp4")],
    "mov": [*_MAP, "-vf", EVEN, *codec("mov")],
    "webm": [*_MAP, *codec("webm")],
    # Palette generated and applied in one filter graph keeps GIF colors decent.
    "gif": ["-an", "-vf", "fps=12,scale='min(480,iw)':-1:flags=lanczos,"
            "split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0"],
    "mp3": audio.ARGS["mp3"],
    "mkv": [*_MAP, "-vf", EVEN, *codec("mkv")[:-2]],
    "hevc": [*_MAP, "-vf", EVEN, "-c:v", "libx265", "-preset", "medium", "-crf", "26", "-pix_fmt", "yuv420p",
             "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],  # hvc1: plays on Apple
    "av1": [*_MAP, "-vf", EVEN, "-c:v", "libaom-av1", "-crf", "32", "-b:v", "0", "-cpu-used", "6",
            "-row-mt", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "avi": [*_MAP, "-vf", EVEN, "-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-q:a", "3"],
    "apng": ["-an", "-vf", "fps=12,scale='min(480,iw)':-1:flags=lanczos", "-plays", "0", "-f", "apng"],
}
OUT_EXT = {"hevc": "mp4", "av1": "mp4", "apng": "png"}


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    return ffmpeg.convert_each(paths, fmt, ARGS[fmt], ctx, OUT_EXT.get(fmt))


def same_ext(src: Path) -> str:
    """Tools keep MP4/MOV/WEBM; other containers become MP4."""
    ext = src.suffix.lower().lstrip(".")
    return ext if ext in ("mp4", "mov", "webm") else "mp4"


def _reencode(paths: list[Path], ctx: JobContext, label: str, vf: str = "", af: str = "",
              before_input: list[str] | None = None, after_input: list[str] | None = None,
              seconds: float | None = None, crf: int | None = None) -> list[Path]:
    """Re-encode each file with optional filters, keeping its container."""
    def passes(src: Path, info: Probe, out: Path) -> list[Pass]:
        ext = out.suffix.lstrip(".")
        audio_args = ["-af", af] if af and info.has_audio else []
        args = [*(before_input or []), "-i", str(src), *(after_input or []), *_MAP,
                "-vf", ",".join(f for f in (vf, EVEN) if f), *audio_args, *codec(ext, crf), str(out)]
        return [(args, seconds or info.duration)]

    return ffmpeg.each(paths, ctx, label, same_ext, passes)


def tool_trim(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """Keep start..end seconds. Re-encodes, so cuts are frame-accurate."""
    start, end = float(p.get("start", 0)), float(p["end"])
    if not 0 <= start < end:
        raise ValueError(f"Bad trim range {start}..{end}")
    return _reencode(paths, ctx, "Trim", before_input=["-ss", f"{start:.3f}"],
                     after_input=["-t", f"{end - start:.3f}"], seconds=end - start)


def atempo_chain(factor: float) -> str:
    """atempo accepts 0.5..100 per instance; chain halves for slower speeds."""
    parts: list[str] = []
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    parts.append(f"atempo={factor:.6g}")
    return ",".join(parts)


def tool_speed(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    factor = float(p.get("factor", 2))
    if not 0.1 <= factor <= 16:
        raise ValueError("Speed must be between 0.1x and 16x")
    return _reencode(paths, ctx, f"Speed ×{factor:g}", vf=f"setpts=PTS/{factor:.6g}",
                     af=atempo_chain(factor))


def tool_mute(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """Drop audio without re-encoding the video (fast, lossless)."""
    def passes(src: Path, info: Probe, out: Path) -> list[Pass]:
        return [(["-i", str(src), "-map", "0:v", "-c", "copy", "-an", str(out)], info.duration)]

    return ffmpeg.each(paths, ctx, "Mute", lambda src: src.suffix.lower().lstrip(".") or "mp4", passes)


def tool_frame(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """Save the frame at ``time`` seconds as PNG."""
    t = max(0.0, float(p.get("time", 0)))

    def passes(src: Path, info: Probe, out: Path) -> list[Pass]:
        if info.duration and t > info.duration - 0.5:
            # Seeking at/after the last frame yields nothing: read the final seconds and
            # keep overwriting one image, which leaves the last frame.
            return [(["-sseof", "-3", "-i", str(src), "-update", "1", str(out)], None)]
        return [(["-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-update", "1", str(out)], None)]

    return ffmpeg.each(paths, ctx, "Frame", lambda _: "png", passes)


def tool_scale(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """Resize to ``height`` lines (480/720/1080/…), keeping the aspect ratio."""
    height = int(p.get("height", 720))
    if not 120 <= height <= 4320:
        raise ValueError(f"Height {height} is out of range")
    return _reencode(paths, ctx, f"Resize to {height}p", vf=f"scale=-2:{height}:flags=lanczos")


def tool_subs(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """Save the first subtitle track as SRT."""
    def passes(src: Path, info: Probe, out: Path) -> list[Pass]:
        if "Subtitle:" not in info.banner:
            raise ValueError(f"{src.name} has no subtitle track")
        return [(["-i", str(src), "-map", "0:s:0", "-c:s", "srt", str(out)], None)]

    return ffmpeg.each(paths, ctx, "Subtitles", lambda _: "srt", passes)


def tool_crop(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """box = fractions [x0, y0, x1, y1] of the frame."""
    x0, y0, x1, y1 = (float(v) for v in p["box"])
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"Bad crop box {p['box']}")
    vf = f"crop=iw*{x1 - x0:.6f}:ih*{y1 - y0:.6f}:iw*{x0:.6f}:ih*{y0:.6f}"
    return _reencode(paths, ctx, "Crop", vf=vf)


def tool_compress(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """mode 'crf' (quality, 18 best .. 40 smallest) or 'size' (target_mb, two-pass H.264 MP4)."""
    if p.get("mode", "crf") == "crf":
        return _reencode(paths, ctx, "Compress", crf=int(p.get("crf", 28)))

    target_mb = float(p.get("target_mb", 25))
    with tempfile.TemporaryDirectory() as td:
        log = str(Path(td) / "pass")

        def passes(src: Path, info: Probe, out: Path) -> list[Pass]:
            if not info.duration:
                raise ValueError(f"Can't read the length of {src.name}")
            audio_kbps = AUDIO_KBPS if info.has_audio else 0
            video_kbps = int(target_mb * 8 * 1024 / info.duration - audio_kbps)
            if video_kbps < 50:
                raise ValueError(f"{target_mb:g} MB is too small for {info.duration:.0f} s of video")
            common = ["-i", str(src), *_MAP, "-vf", EVEN, "-c:v", "libx264", "-preset", "medium",
                      "-b:v", f"{video_kbps}k", "-pix_fmt", "yuv420p", "-passlogfile", log]
            first = [*common, "-pass", "1", "-an", "-f", "null", os.devnull]
            second = [*common, "-pass", "2", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k",
                      "-movflags", "+faststart", str(out)]
            return [(first, info.duration), (second, info.duration)]

        return ffmpeg.each(paths, ctx, f"Compress to {target_mb:g} MB", lambda _: "mp4", passes)
