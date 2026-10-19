from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from shiftdial.jobs import JobQueue  # noqa: E402


def test_job_runs_and_reports(qtbot, audio: Path) -> None:
    q = JobQueue()
    job = q.submit([audio], "mp3")
    # job.state flips on the worker thread; the queue hears about it via a queued signal.
    qtbot.waitUntil(lambda: q.jobs == [], timeout=20000)
    assert job.state == "done" and job.outputs[0].suffix == ".mp3"
    assert q.last_output_dir == audio.parent
    q.shutdown()


def test_cancel_all_cleans_up(qtbot, video: Path) -> None:
    q = JobQueue()
    jobs = [q.submit([video], "webm") for _ in range(3)]  # third waits in the queue
    q.cancel_all()
    qtbot.waitUntil(lambda: all(j.state == "cancelled" for j in jobs), timeout=20000)
    q.shutdown()
    assert sorted(p.name for p in video.parent.iterdir()) == [video.name]


def test_password_retry(qtbot, tmp_path: Path) -> None:
    from tests.test_tools import _encrypted_zip

    arc = _encrypted_zip(tmp_path, "aes256")
    q = JobQueue()
    job = q.submit([arc], "archive:extract")
    qtbot.waitUntil(lambda: job.state == "failed", timeout=10000)
    assert job.needs_password
    again = q.retry(job, {"password": "pw 1"})
    qtbot.waitUntil(lambda: again.state == "done", timeout=10000)
    assert (again.outputs[0] / "secret.txt").exists()
    q.shutdown()


def test_failure_is_reported(qtbot, tmp_path: Path) -> None:
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    q = JobQueue()
    job = q.submit([bad], "jpg")
    qtbot.waitUntil(lambda: job.state == "failed", timeout=10000)
    assert job.error and not list(tmp_path.glob("*.tmp.*"))
    q.shutdown()
