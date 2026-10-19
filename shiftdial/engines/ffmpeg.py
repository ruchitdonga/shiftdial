"""Locate and run ffmpeg with progress parsing and cancel support."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Callable

from shiftdial.engines import JobContext
from shiftdial.utils.paths import Output

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash from the GUI app


@cache
def ffmpeg_exe() -> str:
    """Bundled ffmpeg.exe first, then PATH, then imageio-ffmpeg's binary (dev fallback)."""
    if env := os.environ.get("SHIFTDIAL_FFMPEG"):
        return env
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    exe_dir = Path(sys.executable).parent  # built app: ffmpeg.exe sits next to Shiftdial.exe
    for p in (exe_dir / "ffmpeg.exe", base / "ffmpeg.exe", base / "bin" / "ffmpeg.exe"):
        if p.is_file():
            return str(p)
    if found := shutil.which("ffmpeg"):
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise FileNotFoundError("ffmpeg.exe not found; put it in bin/ or on PATH") from None


@dataclass(frozen=True)
class Probe:
    duration: float | None
    has_audio: bool
    width: int | None
    height: int | None
    banner: str


def probe(src: Path) -> Probe:
    """Parsed from ffmpeg's banner (no ffprobe needed)."""
    r = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(src)],
        capture_output=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    d = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    v = re.search(r"Stream #.*?Video: .*?(\d{2,5})x(\d{2,5})", r.stderr)
    return Probe(
        duration=int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3]) if d else None,
        has_audio=bool(re.search(r"Stream #.*?Audio:", r.stderr)),
        width=int(v[1]) if v else None,
        height=int(v[2]) if v else None,
        banner=r.stderr,
    )


def run(args: list[str], duration: float | None, ctx: JobContext, stage: str) -> None:
    """Run ffmpeg with ``args`` (inputs ... output). Kills it if the job is cancelled."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-progress", "pipe:1", "-nostats", *args]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, creationflags=_NO_WINDOW)

        def watch_cancel() -> None:
            while proc.poll() is None:
                if ctx.cancel_event.wait(0.2):
                    proc.kill()
                    return

        threading.Thread(target=watch_cancel, daemon=True).start()
        assert proc.stdout is not None
        for raw in proc.stdout:
            key, _, value = raw.decode("ascii", "replace").strip().partition("=")
            if key == "out_time_us" and duration and value.isdigit():
                ctx.progress(int(value) / 1e6 / duration, stage)
        proc.wait()
        ctx.check()
        if proc.returncode != 0:
            err.seek(0)
            lines = err.read().decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(f"ffmpeg failed: {lines[-1] if lines else proc.returncode}")
    ctx.progress(1.0, stage)


def frame_png(src: Path, t: float) -> bytes:
    """One frame at ``t`` seconds as PNG bytes (for previews; nothing is written to disk)."""
    r = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src),
         "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"],
        capture_output=True, creationflags=_NO_WINDOW,
    )
    return r.stdout


# A pass is (ffmpeg args, expected output seconds for the progress bar).
Pass = tuple[list[str], float | None]


def each(paths: list[Path], ctx: JobContext, label: str, out_ext: Callable[[Path], str],
         passes: Callable[[Path, Probe, Path], list[Pass]]) -> list[Path]:
    """For each file: probe it, build its ffmpeg pass(es) writing to the tmp output, run them."""
    outputs: list[Path] = []
    n = len(paths)
    for i, src in enumerate(paths):
        ctx.check()
        info = probe(src)
        with Output.next_to(src, out_ext(src)) as out:
            runs = passes(src, info, out.tmp)
            for k, (args, seconds) in enumerate(runs):
                frac = lambda f, i=i, k=k: (i + (k + f) / len(runs)) / n  # noqa: E731
                sub = JobContext(lambda f, s, frac=frac: ctx.progress(frac(f), s), ctx.cancel_event)
                run(args, seconds, sub, f"{src.name}: {label}")
            if not out.tmp.exists():
                raise RuntimeError(f"ffmpeg produced no output for {src.name}")
        outputs.append(out.final)
    return outputs


def convert_each(paths: list[Path], fmt: str, out_args: list[str], ctx: JobContext,
                 ext: str | None = None) -> list[Path]:
    """One ffmpeg run per file: ``-i src <out_args> out``. ``ext`` when it differs from ``fmt`` (hevc → mp4)."""
    return each(paths, ctx, f"→ {fmt.upper()}", lambda _: ext or fmt,
                lambda src, info, out: [(["-i", str(src), *out_args, str(out)], info.duration)])
