"""Wheel geometry and drop handling with synthetic Qt drag events."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shiftdial.wheel import R_IN, R_OUT, Wheel, wedge_at  # noqa: E402

MOVE_OR_COPY = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
NO_BUTTONS, SHIFT = Qt.MouseButton.NoButton, Qt.KeyboardModifier.ShiftModifier


def test_wedge_at() -> None:
    r = (R_IN + R_OUT) / 2
    assert wedge_at(0, -r, 4) == 0  # top
    assert wedge_at(r, 0, 4) == 1  # right (clockwise)
    assert wedge_at(0, r, 4) == 2
    assert wedge_at(-r, 0, 4) == 3
    assert wedge_at(0, 0, 4) == -1  # hub
    assert wedge_at(0, -(R_OUT + 5), 4) == -1  # outside
    assert wedge_at(0, -r, 0) == -1


def _mime(paths: list[Path]) -> QMimeData:
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return m


def test_shift_drop_is_forced_to_copy(qtbot, tmp_path: Path) -> None:
    """Shift proposes MOVE in Explorer; accepting it would delete the originals."""
    files = [tmp_path / "a b.png", tmp_path / "ç.jpg"]
    wheel = Wheel()
    qtbot.addWidget(wheel)
    top = QPoint(wheel.width() // 2, wheel.height() // 2 - (R_IN + R_OUT) // 2)
    mime = _mime(files)

    enter = QDragEnterEvent(top, MOVE_OR_COPY, mime, NO_BUTTONS, SHIFT)
    QApplication.sendEvent(wheel, enter)
    assert enter.isAccepted() and enter.dropAction() == Qt.DropAction.CopyAction
    assert wheel.items[0] == "png"

    move = QDragMoveEvent(top, MOVE_OR_COPY, mime, NO_BUTTONS, SHIFT)
    QApplication.sendEvent(wheel, move)
    assert move.dropAction() == Qt.DropAction.CopyAction and wheel.hover == 0

    drop = QDropEvent(QPointF(top), MOVE_OR_COPY, mime, NO_BUTTONS, SHIFT)
    with qtbot.waitSignal(wheel.chosen) as sig:
        QApplication.sendEvent(wheel, drop)
    assert drop.dropAction() == Qt.DropAction.CopyAction
    assert sig.args == [files, "png"]


def test_move_only_source_is_refused(qtbot, tmp_path: Path) -> None:
    wheel = Wheel()
    qtbot.addWidget(wheel)
    mime = _mime([tmp_path / "a.png"])  # keep a reference: the event doesn't own it
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.MoveAction, mime, NO_BUTTONS, SHIFT)
    QApplication.sendEvent(wheel, enter)
    assert not enter.isAccepted()


def _tracked(qtbot, tmp_path: Path) -> tuple[Wheel, "Job", list]:
    from shiftdial.jobs import Job

    wheel = Wheel()
    qtbot.addWidget(wheel)
    wheel.popup(activate=False)
    job = Job([tmp_path / "a.mp4"], "gif")
    released: list = []
    wheel.released.connect(released.append)
    wheel.track(job)
    job.state = "running"
    job.state_changed.emit("running")
    job.progress.emit(0.5, "encoding")
    return wheel, job, released


def test_progress_readout_hands_running_job_to_a_card(qtbot, tmp_path: Path) -> None:
    wheel, job, released = _tracked(qtbot, tmp_path)
    qtbot.waitUntil(lambda: wheel._val(wheel._progress, 0) > 0.45, timeout=2000)
    wheel.end_drag()  # mouse released: the readout stays
    assert wheel.isVisible() and released == []
    wheel.dismiss()  # clicked away mid-job
    assert not wheel.isVisible() and released == [job]


def test_working_sweep_until_a_real_reading(qtbot, tmp_path: Path) -> None:
    from shiftdial.jobs import Job

    wheel = Wheel()
    qtbot.addWidget(wheel)
    wheel.popup(activate=False)
    job = Job([tmp_path / "a.png"], "webp")
    wheel.track(job)
    job.progress.emit(0.0, "starting")  # engines announce 0 first: not a reading
    assert not wheel._measured
    job.progress.emit(0.35, "loaded")
    assert wheel._measured and not wheel._sweep_timer.isActive()


def test_new_drag_hands_running_job_to_a_card(qtbot, tmp_path: Path) -> None:
    wheel, job, released = _tracked(qtbot, tmp_path)
    wheel.popup("formats", activate=False)
    assert released == [job] and wheel.isVisible() and wheel._job is None


def test_finished_job_closes_by_itself_without_card(qtbot, tmp_path: Path) -> None:
    wheel, job, released = _tracked(qtbot, tmp_path)
    job.state = "done"
    job.state_changed.emit("done")
    qtbot.waitUntil(lambda: not wheel.isVisible(), timeout=4000)
    assert released == []


def test_drop_on_hub_does_nothing(qtbot, tmp_path: Path) -> None:
    wheel = Wheel()
    qtbot.addWidget(wheel)
    mime = _mime([tmp_path / "a.png"])
    hub = QPoint(wheel.width() // 2, wheel.height() // 2)
    QApplication.sendEvent(wheel, QDragEnterEvent(hub, MOVE_OR_COPY, mime, NO_BUTTONS, SHIFT))
    drop = QDropEvent(QPointF(hub), MOVE_OR_COPY, mime, NO_BUTTONS, SHIFT)
    with qtbot.assertNotEmitted(wheel.chosen):
        QApplication.sendEvent(wheel, drop)
