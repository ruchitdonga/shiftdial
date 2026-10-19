from pathlib import Path

import pytest

from shiftdial.cli import main


def test_cli_convert(image: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["convert", str(image), "--to", "webp"]) == 0
    assert Path(capsys.readouterr().out.strip()).name == f"{image.stem}.webp"


def test_cli_bad_format(image: Path) -> None:
    assert main(["convert", str(image), "--to", "mp4"]) == 1
