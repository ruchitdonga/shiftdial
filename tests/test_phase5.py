"""Settings file and Start-with-Windows toggle (against a throwaway registry key)."""
from __future__ import annotations

import sys
import winreg
from collections.abc import Iterator
from pathlib import Path

import pytest

from shiftdial.utils import settings, startup

TEST_KEY = r"Software\ShiftdialTest"


def test_settings_roundtrip_and_bad_files(tmp_path: Path) -> None:
    f = tmp_path / "settings.json"
    assert settings.load(f) == {"theme": "system"}  # missing
    settings.save({"theme": "dark"}, f)
    assert settings.load(f) == {"theme": "dark"}
    for bad in ("{not json", '{"theme": "neon"}', '["dark"]', '{"theme": 5}'):
        f.write_text(bad, encoding="utf-8")
        assert settings.load(f) == {"theme": "system"}


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Drop Wheel\Shiftdial.exe")
    yield '"C:\\Apps\\Drop Wheel\\Shiftdial.exe"'
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_KEY)
    except FileNotFoundError:
        pass


def test_startup_toggle(frozen: str) -> None:
    assert startup.command() == frozen  # quoted: the path has a space
    assert not startup.is_enabled(TEST_KEY)
    startup.set_enabled(True, TEST_KEY)
    assert startup.is_enabled(TEST_KEY)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as k:
        assert winreg.QueryValueEx(k, "Shiftdial")[0] == frozen
    startup.set_enabled(False, TEST_KEY)
    startup.set_enabled(False, TEST_KEY)  # twice is fine
    assert not startup.is_enabled(TEST_KEY)


def test_startup_unavailable_from_source() -> None:
    assert startup.command() is None
    with pytest.raises(RuntimeError):
        startup.set_enabled(True, TEST_KEY)


def test_not_packaged_from_source() -> None:
    assert startup.packaged() is False


def test_store_startup_task(monkeypatch) -> None:
    from winrt.windows.applicationmodel import StartupTaskState as S

    class Task:
        state, disabled = S.DISABLED, False

        async def request_enable_async(self):
            if self.state != S.DISABLED_BY_USER:
                self.state = S.ENABLED
            return self.state

        def disable(self):
            self.state, self.disabled = S.DISABLED, True

    task = Task()
    monkeypatch.setattr(startup, "packaged", lambda: True)
    monkeypatch.setattr(startup, "_task", lambda: task)
    startup.set_enabled(True)
    assert startup.is_enabled()
    startup.set_enabled(False)
    assert task.disabled and not startup.is_enabled()
    task.state = S.DISABLED_BY_USER  # switched off in Windows: only Settings can turn it back on
    with pytest.raises(startup.NeedsSettings):
        startup.set_enabled(True)
