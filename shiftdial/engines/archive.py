"""Pack files/folders into ZIP, 7Z or TAR.GZ; extract ZIP, 7Z, RAR and TAR archives."""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import py7zr

from shiftdial.engines import JobContext, PasswordRequired
from shiftdial.utils.paths import Output, commit_dir, split_ext


def _entries(paths: list[Path]) -> list[tuple[Path, str]]:
    """(file, name inside archive). Folders keep their own name as the top level."""
    out: list[tuple[Path, str]] = []
    for p in paths:
        if p.is_dir():
            out += [(f, f.relative_to(p.parent).as_posix()) for f in sorted(p.rglob("*")) if f.is_file()]
        else:
            out.append((p, p.name))
    return out


def _archive_stem(paths: list[Path]) -> str:
    if len(paths) == 1:
        return paths[0].name if paths[0].is_dir() else split_ext(paths[0].name)[0]
    return paths[0].parent.name or "Archive"


def _open(fmt: str, tmp: Path) -> tuple[Any, Callable[[Path, str], None]]:
    """The archive writer and its add-one-file function."""
    if fmt == "zip":
        z = zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
        return z, z.write
    if fmt == "tar.gz":
        t = tarfile.open(tmp, "w:gz")
        return t, lambda f, name: t.add(f, name, recursive=False)
    if fmt == "7z":
        s = py7zr.SevenZipFile(tmp, "w")
        return s, s.write
    raise ValueError(fmt)


def make(paths: list[Path], fmt: str, ctx: JobContext) -> Path:
    entries = _entries(paths)
    total = sum(f.stat().st_size for f, _ in entries) or 1
    done = 0
    with Output(paths[0].parent, _archive_stem(paths), fmt) as out:
        writer, add = _open(fmt, out.tmp)
        with writer:
            for f, name in entries:
                ctx.check()
                add(f, name)
                done += f.stat().st_size
                ctx.progress(done / total, f"Packing {fmt.upper()}: {f.name}")
    ctx.progress(1.0, "Done")
    return out.final


def tool_zip(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    return [make(paths, "zip", ctx)]


def tool_7z(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    return [make(paths, "7z", ctx)]


def tool_targz(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    return [make(paths, "tar.gz", ctx)]


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _bsdtar() -> str:
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if not exe.is_file():
        raise FileNotFoundError("Extracting needs Windows' built-in tar.exe (Windows 10 1803 or newer)")
    return str(exe)


def _archive_name(src: Path) -> str:
    """'photos.tar.gz' -> 'photos', 'backup.rar' -> 'backup'."""
    lower = src.name.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
        if lower.endswith(ext):
            return src.name[: -len(ext)]
    return src.stem


def _seven_zip() -> str | None:
    """7-Zip's 7z.exe if installed; only needed for password-protected 7Z/RAR."""
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and (exe := Path(base) / "7-Zip" / "7z.exe").is_file():
            return str(exe)
    return shutil.which("7z")


# Without a passphrase, bsdtar tries to prompt on a console and can block forever.
# A dummy one makes an encrypted entry fail fast; unencrypted archives ignore it.
_NO_PASSWORD = "shiftdial-no-password"
_ENCRYPTION_HINTS = ("passphrase", "encrypt", "password")


def _check_names(src: Path, names: list[str]) -> list[str]:
    """Refuse absolute paths and '..' before anything is written (zip-slip)."""
    for n in names:
        parts = PurePosixPath(n.replace("\\", "/")).parts
        if n.startswith(("/", "\\")) or ":" in n or ".." in parts:
            raise ValueError(f"{src.name} contains an unsafe path: {n}")
    return names


def _fail(src: Path, output: str, password: str | None) -> None:
    """Raise PasswordRequired for encryption errors, otherwise a readable RuntimeError."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    low = output.lower()
    if any(h in low for h in _ENCRYPTION_HINTS):
        raise PasswordRequired(src.name, wrong=password is not None)
    # ZipCrypto's password check passes ~1 in 256 wrong passwords; the data then fails to decompress.
    if password is not None and ("decompression failed" in low or "crc" in low):
        raise PasswordRequired(src.name, wrong=True)
    raise RuntimeError(f"Can't extract {src.name}: {lines[-1] if lines else 'unknown error'}")


def _run(cmd: list[str], ctx: JobContext, on_line: Callable[[str], None]) -> tuple[int, str]:
    """Run a command, stream its merged output lines to ``on_line``, kill it on cancel."""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)

    def watch_cancel() -> None:
        while proc.poll() is None:
            if ctx.cancel_event.wait(0.2):
                proc.kill()
                return

    threading.Thread(target=watch_cancel, daemon=True).start()
    assert proc.stdout is not None
    out: list[str] = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        out.append(line)
        on_line(line)
    proc.wait()
    ctx.check()
    return proc.returncode, "\n".join(out)


def _extract_bsdtar(src: Path, dest: Path, password: str | None, ctx: JobContext, stage: str) -> None:
    pw = ["--passphrase", password or _NO_PASSWORD]
    r = subprocess.run([_bsdtar(), "-t", *pw, "-f", str(src)], stdin=subprocess.DEVNULL,
                       capture_output=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if r.returncode != 0:
        _fail(src, r.stderr, password)
    total = len(_check_names(src, [n for n in r.stdout.splitlines() if n.strip()]))
    done = 0

    def on_line(line: str) -> None:  # -v prints 'x <name>' per extracted entry
        nonlocal done
        if line.startswith("x "):
            done += 1
            ctx.progress(done / max(1, total), stage)

    code, output = _run([_bsdtar(), "-x", "-v", *pw, "-f", str(src), "-C", str(dest)], ctx, on_line)
    if code != 0:
        _fail(src, output, password)


def _extract_7zip(exe: str, src: Path, dest: Path, password: str, ctx: JobContext, stage: str) -> None:
    # -p on the command line is visible to other processes of this user while it runs.
    pw = f"-p{password}"
    r = subprocess.run([exe, "l", "-ba", "-slt", pw, str(src)], stdin=subprocess.DEVNULL,
                       capture_output=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if r.returncode != 0:
        _fail(src, r.stdout + r.stderr, password)
    names = [ln[7:] for ln in r.stdout.splitlines() if ln.startswith("Path = ")]
    total = len(_check_names(src, names))
    done = 0

    def on_line(line: str) -> None:  # -bb1 prints '- <name>' per extracted file
        nonlocal done
        if line.startswith("- "):
            done += 1
            ctx.progress(done / max(1, total), stage)

    code, output = _run([exe, "x", pw, f"-o{dest}", "-y", "-bb1", "-bsp0", str(src)], ctx, on_line)
    if code != 0:
        _fail(src, output, password)


def tool_extract(paths: list[Path], p: dict[str, Any], ctx: JobContext) -> list[Path]:
    """Extract each archive next to itself into a folder named after it."""
    password: str | None = p.get("password") or None
    seven = _seven_zip() if password else None
    outputs: list[Path] = []
    n = len(paths)
    for i, src in enumerate(paths):
        ctx.check()
        if password and not seven and not src.name.lower().endswith(".zip"):
            raise RuntimeError(f"Password-protected {src.suffix.upper().lstrip('.')} files need "
                               "7-Zip installed (free, 7-zip.org)")
        tmp = Path(tempfile.mkdtemp(prefix=f".{_archive_name(src)}.extracting-", dir=src.parent))
        try:
            sub = JobContext(lambda f, s, i=i: ctx.progress((i + f) / n, s), ctx.cancel_event)
            stage = f"Extracting {src.name}"
            if seven and password:
                _extract_7zip(seven, src, tmp, password, sub, stage)
            else:
                _extract_bsdtar(src, tmp, password, sub, stage)
            children = list(tmp.iterdir())
            if len(children) == 1 and children[0].is_dir():
                outputs.append(commit_dir(children[0], src.parent, children[0].name))
                tmp.rmdir()
            else:
                outputs.append(commit_dir(tmp, src.parent, _archive_name(src)))
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    ctx.progress(1.0, "Done")
    return outputs
