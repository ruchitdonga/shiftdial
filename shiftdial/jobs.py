"""Job queue: at most 2 conversions at once, Qt signals for progress, cancel support."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from shiftdial.engines import Cancelled, JobContext, PasswordRequired, run
from shiftdial.registry import label_for

log = logging.getLogger(__name__)
MAX_RUNNING = 2


class Job(QObject):
    """One drop = one job. Signals are emitted from a worker thread; Qt queues them to the GUI."""

    progress = Signal(float, str)
    state_changed = Signal(str)  # queued | running | done | failed | cancelled

    def __init__(self, paths: list[Path], action: str, params: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.paths, self.action, self.params = paths, action, params or {}
        self.ctx = JobContext(self.progress.emit)
        self.state = "queued"
        self.outputs: list[Path] = []
        self.error = ""
        self.needs_password = False
        self.started_at: float | None = None

    @property
    def title(self) -> str:
        more = f" +{len(self.paths) - 1} more" if len(self.paths) > 1 else ""
        verb = label_for(self.action) if ":" in self.action else f"→ {label_for(self.action)}"
        return f"{self.paths[0].name}{more}: {verb}"

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def cancel(self) -> None:
        self.ctx.cancel_event.set()

    def _set(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def run(self) -> None:
        """Runs on a worker thread."""
        self.started_at = time.monotonic()
        self._set("running")
        try:
            self.ctx.check()
            self.outputs = run(self.paths, self.action, self.params, self.ctx)
            log.info("done: %s -> %s", self.title, [str(p) for p in self.outputs])
            self._set("done")
        except Cancelled:
            log.info("cancelled: %s", self.title)
            self._set("cancelled")
        except PasswordRequired as e:
            log.info("password needed: %s", self.title)  # never log the password itself
            self.error, self.needs_password = str(e), True
            self._set("failed")
        except Exception as e:  # noqa: BLE001 - any engine failure ends the job, not the app
            log.exception("failed: %s", self.title)
            self.error = str(e) or type(e).__name__
            self._set("failed")


class JobQueue(QObject):
    job_added = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._pool = ThreadPoolExecutor(MAX_RUNNING, thread_name_prefix="job")
        self.jobs: list[Job] = []
        self.last_output_dir: Path | None = None

    def submit(self, paths: list[Path], action: str, params: dict[str, Any] | None = None,
               announce: bool = True) -> Job:
        """``action`` is a format ('webp') or a tool id ('video:trim')."""
        job = Job(paths, action, params)
        job.state_changed.connect(lambda s: self._on_state(job, s))
        self.jobs.append(job)
        log.info("queued: %s", job.title)
        if announce:
            self.job_added.emit(job)
        self._pool.submit(job.run)
        return job

    def _on_state(self, job: Job, state: str) -> None:
        if state == "done" and job.outputs:
            self.last_output_dir = job.outputs[0].parent
        if job.finished and job in self.jobs:
            self.jobs.remove(job)

    def retry(self, job: Job, extra: dict[str, Any]) -> Job:
        """Run ``job`` again with extra params, e.g. a password."""
        return self.submit(job.paths, job.action, {**job.params, **extra})

    def cancel_all(self) -> None:
        for job in list(self.jobs):
            job.cancel()

    def shutdown(self) -> None:
        """Cancel everything and wait, so engines delete their partial outputs before exit."""
        self.cancel_all()
        self._pool.shutdown(wait=True)
