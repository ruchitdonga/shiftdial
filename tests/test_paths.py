from pathlib import Path

import pytest

from shiftdial.utils.paths import Output, split_ext, unique_path


def test_split_ext() -> None:
    assert split_ext("a.tar.gz") == ("a", "tar.gz")
    assert split_ext("photo.final.png") == ("photo.final", "png")
    assert split_ext("README") == ("README", "")


def test_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "photo.png").write_bytes(b"orig")
    (tmp_path / "photo (1).png").write_bytes(b"orig1")
    assert unique_path(tmp_path, "photo", "png").name == "photo (2).png"
    with Output(tmp_path, "photo", "png") as out:
        assert out.tmp.name == "photo (2).tmp.png"
        out.tmp.write_bytes(b"new")
    assert out.final.read_bytes() == b"new"
    assert (tmp_path / "photo.png").read_bytes() == b"orig"


def test_tmp_removed_on_failure(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        with Output(tmp_path, "x", "mp4") as out:
            out.tmp.write_bytes(b"partial")
            raise RuntimeError("boom")
    assert list(tmp_path.iterdir()) == []


def test_rename_race_picks_next_name(tmp_path: Path) -> None:
    with Output(tmp_path, "a", "png") as out:
        out.tmp.write_bytes(b"mine")
        (tmp_path / "a.png").write_bytes(b"someone else")  # claimed meanwhile
    assert out.final.name == "a (1).png"
    assert (tmp_path / "a.png").read_bytes() == b"someone else"
