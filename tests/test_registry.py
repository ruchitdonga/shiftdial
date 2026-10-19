from pathlib import Path

from shiftdial.registry import ARCHIVES, formats_for


def test_formats_by_kind() -> None:
    assert formats_for([Path("a.PNG"), Path("b.jpg")])[:2] == ["png", "jpg"]
    assert "mp4" in formats_for([Path("a.mov")])
    assert "flac" in formats_for([Path("a.mp3")])


def test_mixed_and_unknown_only_archive() -> None:
    assert formats_for([Path("a.png"), Path("b.mp4")]) == ARCHIVES
    assert formats_for([Path("notes.xyz")]) == ARCHIVES
