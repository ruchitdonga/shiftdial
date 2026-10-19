"""Conversion engines. ``convert`` is the single entry point used by the CLI and job queue."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from shiftdial.registry import ARCHIVES, formats_for, kind_of_all, tools_for


class Cancelled(Exception):
    pass


class PasswordRequired(Exception):
    """The archive is encrypted; retry with params['password']."""

    def __init__(self, name: str, wrong: bool = False) -> None:
        super().__init__(f"Wrong password for {name}" if wrong else f"{name} needs a password")
        self.wrong = wrong


@dataclass
class JobContext:
    """Passed to every engine: reports progress and carries the cancel flag."""

    on_progress: Callable[[float, str], None] = lambda fraction, stage: None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def progress(self, fraction: float, stage: str) -> None:
        self.on_progress(max(0.0, min(1.0, fraction)), stage)

    def check(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()


def convert(paths: list[Path], fmt: str, ctx: JobContext | None = None) -> list[Path]:
    """Convert ``paths`` to ``fmt``; returns the output files."""
    from shiftdial.engines import (
        archive, audio, books, cad, data, font, image, model, office, pdf, subs, text, vector, video,
    )

    ctx = ctx or JobContext()
    fmt = fmt.lower().lstrip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in formats_for(paths):
        raise ValueError(f"Can't convert these files to {fmt}. Options: {', '.join(formats_for(paths))}")
    if fmt in ARCHIVES:
        return [archive.make(paths, fmt, ctx)]
    engine = {"image": image, "vector": vector, "video": video, "audio": audio, "subtitle": subs, "pdf": pdf,
              "document": office, "spreadsheet": office, "slides": office, "text": text, "ebook": books,
              "data": data, "font": font, "model": model, "cad": cad}[kind_of_all(paths)]
    return engine.convert(paths, fmt, ctx)


def apply_tool(paths: list[Path], tool_id: str, params: dict[str, Any] | None = None,
               ctx: JobContext | None = None) -> list[Path]:
    """Run tool ``tool_id`` ('video:trim') on ``paths``; returns the output files."""
    from shiftdial.engines import archive, audio, image, pdf, video

    if tool_id not in tools_for(paths):
        raise ValueError(f"{tool_id} doesn't apply to these files. Options: {', '.join(tools_for(paths))}")
    kind, name = tool_id.split(":")
    module = {"image": image, "video": video, "audio": audio, "pdf": pdf, "archive": archive,
              "folder": archive}[kind]
    return getattr(module, f"tool_{name}")(paths, params or {}, ctx or JobContext())


def run(paths: list[Path], action: str, params: dict[str, Any] | None = None,
        ctx: JobContext | None = None) -> list[Path]:
    """A wheel item is either a format ('webp') or a tool id ('image:crop')."""
    return apply_tool(paths, action, params, ctx) if ":" in action else convert(paths, action, ctx)
