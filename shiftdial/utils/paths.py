"""Safe output naming: never overwrite, write to name.tmp.ext, rename on success."""
from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType


def data_dir() -> Path:
    """%LOCALAPPDATA%\\Shiftdial (settings, logs, lock file)."""
    d = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Shiftdial"
    d.mkdir(parents=True, exist_ok=True)
    return d


def split_ext(name: str) -> tuple[str, str]:
    """'a.tar.gz' -> ('a', 'tar.gz'); 'photo.png' -> ('photo', 'png')."""
    if name.lower().endswith(".tar.gz"):
        return name[:-7], "tar.gz"
    stem, dot, ext = name.rpartition(".")
    return (stem, ext) if dot and stem else (name, "")


def tmp_for(final: Path) -> Path:
    stem, ext = split_ext(final.name)
    return final.with_name(f"{stem}.tmp.{ext}")


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    """First of 'stem.ext', 'stem (1).ext', ... where neither it nor its tmp file exists."""
    n = 0
    while True:
        p = folder / f"{stem}{f' ({n})' if n else ''}.{ext}"
        if not p.exists() and not tmp_for(p).exists():
            return p
        n += 1


def commit_dir(src_dir: Path, folder: Path, name: str) -> Path:
    """Move a finished temp folder to 'folder/name', or 'name (1)', ... never merging into an existing one."""
    n = 0
    while True:
        dest = folder / f"{name}{f' ({n})' if n else ''}"
        n += 1
        if dest.exists():
            continue
        try:
            os.rename(src_dir, dest)  # fails rather than merging if dest appeared meanwhile
            return dest
        except FileExistsError:
            continue


class Output:
    """Context manager for one output file."""

    def __init__(self, folder: Path, stem: str, ext: str) -> None:
        self._folder, self._stem, self._ext = folder, stem, ext
        self.final = unique_path(folder, stem, ext)
        self.tmp = tmp_for(self.final)

    @classmethod
    def next_to(cls, src: Path, ext: str) -> "Output":
        return cls(src.parent, split_ext(src.name)[0], ext)

    def __enter__(self) -> "Output":
        return self

    def __exit__(
        self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None
    ) -> bool:
        if et is not None:
            self.tmp.unlink(missing_ok=True)
            return False
        while True:
            try:
                os.rename(self.tmp, self.final)  # on Windows this refuses to replace an existing file
                return False
            except FileExistsError:
                self.final = unique_path(self._folder, self._stem, self._ext)
