"""Start with Windows: one value under HKCU\\...\\Run (per user, no admin rights needed)."""
from __future__ import annotations

import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Shiftdial"


def command() -> str | None:
    """What Windows should run at sign-in: the built Shiftdial.exe."""
    return f'"{sys.executable}"' if getattr(sys, "frozen", False) else None


def is_enabled(key: str = RUN_KEY) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, VALUE)[0] == command()
    except OSError:
        return False


def set_enabled(on: bool, key: str = RUN_KEY) -> None:
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
