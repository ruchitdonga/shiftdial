"""Detect "dragging files from Explorer + Shift" and tell the GUI."""
from __future__ import annotations

import ctypes
import logging
import math
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202

VK_SHIFT, VK_CONTROL, VK_ESCAPE, VK_F8, VK_F9 = 0x10, 0x11, 0x1B, 0x77, 0x78
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL = 0xA0, 0xA1, 0xA2, 0xA3
VK_LBUTTON, VK_RBUTTON = 0x01, 0x02
SHIFTS = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT}
CTRLS = {VK_CONTROL, VK_LCONTROL, VK_RCONTROL}
TRIGGERS = SHIFTS | CTRLS | {VK_F8, VK_F9}
WATCHED = (VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_F8, VK_F9, VK_ESCAPE)

DRAG_THRESHOLD_PX = 8
POLL_S = 0.008


class DragDetector:
    """Tracks "left button held and moved > 8 px" plus keys, and emits:"""

    def __init__(self, emit: Callable[[str], None], is_file_source: Callable[[int, int], bool]) -> None:
        self._emit = emit
        self._is_file_source = is_file_source
        self._down_at: tuple[int, int] | None = None
        self._dragging = False
        self._source_ok = False
        self._keys: set[int] = set()

    def mouse(self, msg: int, x: int, y: int) -> None:
        if msg == WM_LBUTTONDOWN:
            self._down_at, self._dragging = (x, y), False
        elif msg == WM_MOUSEMOVE and self._down_at and not self._dragging:
            if math.dist(self._down_at, (x, y)) > DRAG_THRESHOLD_PX:
                self._dragging = True
                self._source_ok = self._is_file_source(*self._down_at)
                self._trigger()
        elif msg == WM_LBUTTONUP:
            if self._dragging and self._source_ok:
                self._emit("end")
            self._down_at, self._dragging = None, False

    def key(self, msg: int, vk: int) -> None:
        if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
            fresh = vk not in self._keys  # held keys auto-repeat KEYDOWN; act only on the first
            self._keys.add(vk)
            if fresh and self._dragging and self._source_ok:
                if vk == VK_ESCAPE:
                    self._emit("dismiss")
                elif vk in TRIGGERS:
                    self._trigger()
        elif msg in (WM_KEYUP, WM_SYSKEYUP):
            self._keys.discard(vk)  # releasing Shift leaves the wheel up

    def _trigger(self) -> None:
        if not (self._dragging and self._source_ok):
            return
        shift, ctrl = bool(self._keys & SHIFTS), bool(self._keys & CTRLS)
        if VK_F9 in self._keys or (shift and ctrl):
            self._emit("tools")
        elif VK_F8 in self._keys or shift:
            self._emit("formats")


class InputPoller:
    """Turns polled input *state* into the detector's down/move/up and key events (unit-tested)."""

    def __init__(self, detector: DragDetector) -> None:
        self._detector = detector
        self._pressed = False
        self._keys: set[int] = set()

    @property
    def pressed(self) -> bool:
        return self._pressed

    def feed(self, button_down: bool, x: int, y: int, keys_down: set[int]) -> None:
        if button_down != self._pressed:
            self._pressed = button_down
            self._detector.mouse(WM_LBUTTONDOWN if button_down else WM_LBUTTONUP, x, y)
        elif button_down:
            self._detector.mouse(WM_MOUSEMOVE, x, y)
        for vk in keys_down - self._keys:
            self._detector.key(WM_KEYDOWN, vk)
        for vk in self._keys - keys_down:
            self._detector.key(WM_KEYUP, vk)
        self._keys = set(keys_down)


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SM_SWAPBUTTON = 23

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def is_explorer_at(x: int, y: int) -> bool:
    """True if the window under (x, y) belongs to explorer.exe (folders or the desktop)."""
    hwnd = user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(x, y)), GA_ROOT)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return False
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return False
        return Path(buf.value).name.lower() == "explorer.exe"
    finally:
        kernel32.CloseHandle(handle)


def _is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# Polled rather than hooked: a low-level hook puts every mouse move on the PC behind this process
# (and Python's GIL), which made the cursor lag whenever a conversion was busy.
class DragHooks(QObject):
    """Polls input on its own thread. ``event`` is queued to the GUI thread by Qt."""

    event = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._poller = InputPoller(DragDetector(self.event.emit, is_explorer_at))
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="drag-watch", daemon=True).start()
        log.info("drag watcher started")

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        pt = wintypes.POINT()
        while not self._stop.wait(POLL_S):
            try:
                # The "left" button is the primary one; with swapped mouse buttons that's the right.
                primary = VK_RBUTTON if user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
                down = _is_down(primary)
                if not down and not self._poller.pressed:
                    continue
                user32.GetCursorPos(ctypes.byref(pt))
                keys = {vk for vk in WATCHED if _is_down(vk)}
                self._poller.feed(down, pt.x, pt.y, keys)
            except Exception:  # noqa: BLE001 - keep watching; one bad tick mustn't stop detection
                log.exception("drag watcher")
