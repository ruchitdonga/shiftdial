"""Start with Windows: one value under HKCU\\...\\Run (per user, no admin rights needed), or in the Store build
the manifest's startup task, since a packaged app's Run key would land in its private registry."""
from __future__ import annotations

import asyncio
import ctypes
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Shiftdial"
TASK_ID = "ShiftdialStartup"  # packaging/AppxManifest.xml


class NeedsSettings(Exception):
    """The user switched the startup task off in Windows; only they can switch it back on, in Settings."""


def command() -> str | None:
    """What Windows should run at sign-in: the built Shiftdial.exe."""
    return f'"{sys.executable}"' if getattr(sys, "frozen", False) else None


def packaged() -> bool:
    """True inside the Store (MSIX) build."""
    n = ctypes.c_uint32(0)
    return ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(n), None) != 15700  # APPMODEL_ERROR_NO_PACKAGE


def _task():
    from winrt.windows.applicationmodel import StartupTask

    return asyncio.run(StartupTask.get_async(TASK_ID))


def _on(state) -> bool:
    from winrt.windows.applicationmodel import StartupTaskState

    return state in (StartupTaskState.ENABLED, StartupTaskState.ENABLED_BY_POLICY)


def is_enabled(key: str = RUN_KEY) -> bool:
    if packaged():
        return _on(_task().state)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, VALUE)[0] == command()
    except OSError:
        return False


def set_enabled(on: bool, key: str = RUN_KEY) -> None:
    if packaged():
        task = _task()
        if not on:
            task.disable()
        elif not _on(asyncio.run(task.request_enable_async())):
            raise NeedsSettings
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        if on:
            cmd = command()
            if cmd is None:
                raise RuntimeError("Start with Windows needs the built Shiftdial.exe")
            winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, VALUE)
            except FileNotFoundError:
                pass
