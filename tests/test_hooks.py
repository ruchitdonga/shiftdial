"""DragDetector state machine, fed synthetic hook events (no real hooks installed)."""
from __future__ import annotations

from shiftdial.hooks import (
    VK_ESCAPE, VK_F8, VK_F9, VK_LCONTROL, VK_LSHIFT, WM_KEYDOWN, WM_KEYUP, WM_LBUTTONDOWN,
    WM_LBUTTONUP, WM_MOUSEMOVE, DragDetector,
)


def make(source_ok: bool = True) -> tuple[DragDetector, list[str]]:
    events: list[str] = []
    return DragDetector(events.append, lambda x, y: source_ok), events


def drag(d: DragDetector) -> None:
    d.mouse(WM_LBUTTONDOWN, 100, 100)
    d.mouse(WM_MOUSEMOVE, 120, 100)


def press(d: DragDetector, *vks: int) -> None:
    for vk in vks:
        d.key(WM_KEYDOWN, vk)


def test_shift_without_drag_does_nothing() -> None:
    d, ev = make()
    press(d, VK_LSHIFT)
    d.mouse(WM_LBUTTONDOWN, 0, 0)
    d.mouse(WM_MOUSEMOVE, 5, 0)  # under the 8 px threshold
    press(d, VK_F8)
    assert ev == []


def test_shift_during_drag_shows_formats_once() -> None:
    d, ev = make()
    drag(d)
    press(d, VK_LSHIFT, VK_LSHIFT, VK_LSHIFT)  # auto-repeat
    d.mouse(WM_LBUTTONUP, 120, 100)
    assert ev == ["formats", "end"]


def test_shift_held_before_drag_starts() -> None:
    d, ev = make()
    press(d, VK_LSHIFT)
    drag(d)
    assert ev == ["formats"]


def test_shift_then_ctrl_switches_to_tools() -> None:
    d, ev = make()
    drag(d)
    press(d, VK_LSHIFT, VK_LCONTROL)
    assert ev == ["formats", "tools"]


def test_ctrl_alone_does_nothing() -> None:
    d, ev = make()
    drag(d)
    press(d, VK_LCONTROL)
    assert ev == []


def test_f8_f9() -> None:
    d, ev = make()
    drag(d)
    press(d, VK_F8)
    d.key(WM_KEYUP, VK_F8)
    press(d, VK_F9)
    assert ev == ["formats", "tools"]


def test_esc_dismisses() -> None:
    d, ev = make()
    drag(d)
    press(d, VK_LSHIFT, VK_ESCAPE)
    assert ev == ["formats", "dismiss"]


def test_shift_again_after_release_reopens() -> None:
    d, ev = make()
    drag(d)
    press(d, VK_LSHIFT)
    d.key(WM_KEYUP, VK_LSHIFT)
    press(d, VK_LSHIFT)
    assert ev == ["formats", "formats"]


def test_non_explorer_drag_ignored() -> None:
    d, ev = make(source_ok=False)
    drag(d)
    press(d, VK_LSHIFT, VK_ESCAPE)
    d.mouse(WM_LBUTTONUP, 0, 0)
    assert ev == []


def test_poller_turns_state_into_events() -> None:
    from shiftdial.hooks import InputPoller

    d, ev = make()
    poll = InputPoller(d)
    poll.feed(False, 0, 0, set())
    poll.feed(True, 100, 100, set())  # press
    poll.feed(True, 120, 100, set())  # moved past the threshold: a drag
    poll.feed(True, 125, 100, {VK_LSHIFT})  # Shift pressed
    poll.feed(True, 130, 100, {VK_LSHIFT})  # still held: no repeat
    poll.feed(True, 130, 100, {VK_LSHIFT, VK_LCONTROL})  # + Ctrl
    poll.feed(False, 130, 100, set())  # released
    assert ev == ["formats", "tools", "end"]


def test_new_press_resets_drag() -> None:
    d, ev = make()
    drag(d)
    d.mouse(WM_LBUTTONUP, 120, 100)
    d.mouse(WM_LBUTTONDOWN, 0, 0)
    press(d, VK_LSHIFT)
    assert ev == ["end"]
