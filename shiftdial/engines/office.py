"""Documents, spreadsheets and slides through the office suite already installed."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from shiftdial.engines import JobContext
from shiftdial.utils.paths import Output, commit_dir, split_ext

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_SCRIPT = r"""
param([string]$app, [string]$src, [string]$out, [int]$fmt)
$ErrorActionPreference = "Stop"
if ($app -eq "word") {
    # Opening a PDF makes Word ask "convert to an editable document?" even when hidden, which would
    # hang. Switch that one prompt off for this run, then put your setting back exactly as it was.
    $key = "HKCU:\Software\Microsoft\Office\16.0\Word\Options"
    $isPdf = $src.ToLower().EndsWith(".pdf")
    if ($isPdf) {
        $old = (Get-ItemProperty $key -Name DisableConvertPdfWarning -ErrorAction SilentlyContinue).DisableConvertPdfWarning
        New-Item $key -Force -ErrorAction SilentlyContinue | Out-Null
        Set-ItemProperty $key -Name DisableConvertPdfWarning -Value 1 -Type DWord
    }
    try {
        $a = New-Object -ComObject Word.Application
        $a.DisplayAlerts = 0
        $d = $a.Documents.Open($src, $false, $true, $false)  # no convert prompt, read-only, not in recent files
        try { $d.SaveAs2($out, $fmt) } finally { $d.Close(0) }
        if ($a.Documents.Count -eq 0) { $a.Quit() }  # leave Word alone if you have it open
    } finally {
        if ($isPdf) {
            if ($null -eq $old) { Remove-ItemProperty $key -Name DisableConvertPdfWarning -ErrorAction SilentlyContinue }
            else { Set-ItemProperty $key -Name DisableConvertPdfWarning -Value $old -Type DWord }
        }
    }
} elseif ($app -eq "excel") {
    $a = New-Object -ComObject Excel.Application
    $a.DisplayAlerts = $false
    $w = $a.Workbooks.Open($src, 0, $true)  # don't update links, read-only
    try { if ($fmt -lt 0) { $w.ExportAsFixedFormat(0, $out) } else { $w.SaveAs($out, $fmt) } }
    finally { $w.Close($false) }
    if ($a.Workbooks.Count -eq 0) { $a.Quit() }
} else {
    $a = New-Object -ComObject PowerPoint.Application
    $p = $a.Presentations.Open($src, -1, 0, 0)  # read-only, not untitled, no window
    try { $p.SaveAs($out, $fmt) } finally { $p.Close() }
    if ($a.Presentations.Count -eq 0) { $a.Quit() }
}
"""

# Office SaveAs format codes. Excel PDF uses ExportAsFixedFormat (marked -1).
WORD = {"pdf": 17, "docx": 16, "odt": 23, "rtf": 6, "txt": 7, "html": 10}  # 7 = UTF-16 text, re-encoded below
EXCEL = {"pdf": -1, "xlsx": 51, "ods": 60, "csv": 62, "tsv": 42, "html": 44}  # 62 = CSV UTF-8, 42 = UTF-16 tab
SLIDES = {"pdf": 32, "pptx": 24, "ppt": 1, "odp": 35, "png": 18, "jpg": 17}
APP_FOR_KIND = {"document": ("word", "Word.Application"), "spreadsheet": ("excel", "Excel.Application"),
                "slides": ("powerpoint", "PowerPoint.Application")}


def has_app(progid: str) -> bool:
    import winreg

    try:
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\CLSID").Close()
        return True
    except OSError:
        return False


def _has_powerpoint() -> bool:
    return has_app("PowerPoint.Application")


def _soffice() -> str | None:
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and (exe := Path(base) / "LibreOffice" / "program" / "soffice.exe").is_file():
            return str(exe)
    return shutil.which("soffice")


TIMEOUT_S = 600  # a hidden Office app stuck on some dialog must not hang the job forever


def _run(cmd: list[str], ctx: JobContext) -> None:
    """Run, kill on cancel or timeout, raise with the tool's last error line on failure."""
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=_NO_WINDOW)
        waited = 0.0
        while proc.poll() is None:
            if ctx.cancel_event.wait(0.2):
                proc.kill()
                proc.wait()
            waited += 0.2
            if waited > TIMEOUT_S:
                proc.kill()
                proc.wait()
                raise RuntimeError("The office app stopped responding (a dialog may be open); try again")
        ctx.check()
        if proc.returncode != 0:
            log.seek(0)
            lines = [ln for ln in log.read().decode("utf-8", "replace").splitlines() if ln.strip()]
            raise RuntimeError(f"Conversion failed: {lines[-1].strip() if lines else proc.returncode}")


def office_save(app: str, src: Path, out: Path, code: int, ctx: JobContext) -> None:
    """Open ``src`` in Word/Excel/PowerPoint (``app``) and save it as ``out`` with format ``code``."""
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "office.ps1"
        script.write_text(_SCRIPT, encoding="utf-8")
        _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
              app, str(src.resolve()), str(out), str(code)], ctx)


def libreoffice_save(src: Path, out: Path, fmt: str, ctx: JobContext) -> None:
    soffice = _soffice()
    if not soffice:
        raise RuntimeError("This conversion needs Microsoft Office or LibreOffice installed")
    with tempfile.TemporaryDirectory() as td:
        _run([soffice, "--headless", "--convert-to", fmt, "--outdir", td, str(src.resolve())], ctx)
        made = next(Path(td).glob(f"{src.stem}.*"), None)
        if made is None:
            raise RuntimeError(f"LibreOffice produced no {fmt.upper()} for {src.name}")
        shutil.move(str(made), out)


def _utf16_to_utf8(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-16"), encoding="utf-8")


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    from shiftdial.registry import kind_of

    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        kind = kind_of(src)
        if kind == "spreadsheet" and fmt == "json":
            outputs.append(_sheet_to_json(src, ctx))
            continue
        app, progid = APP_FOR_KIND[kind]
        code = {"word": WORD, "excel": EXCEL, "powerpoint": SLIDES}[app][fmt]
        use_office = has_app(progid)
        if kind == "slides" and fmt in ("png", "jpg"):
            with tempfile.TemporaryDirectory(dir=src.parent, prefix=".shiftdial-") as td:
                folder = Path(td) / "slides"
                if use_office:
                    office_save(app, src, folder, code, ctx)
                else:  # shortcut: LibreOffice exports only the first slide; Office exports all
                    folder.mkdir()
                    libreoffice_save(src, folder / f"Slide1.{fmt}", fmt, ctx)
                outputs.append(commit_dir(folder, src.parent, f"{split_ext(src.name)[0]} slides"))
            continue
        with Output.next_to(src, fmt) as out:
            if use_office:
                office_save(app, src, out.tmp, code, ctx)
                if fmt in ("txt", "tsv"):
                    _utf16_to_utf8(out.tmp)
            else:
                libreoffice_save(src, out.tmp, fmt, ctx)
            if not out.tmp.exists():
                raise RuntimeError(f"No {fmt.upper()} was produced for {src.name}")
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs


def _sheet_to_json(src: Path, ctx: JobContext) -> Path:
    """First sheet → JSON array of row objects (header row = keys)."""
    from shiftdial.engines import data

    if src.suffix.lower() in (".csv", ".tsv"):
        records = data.read_table(src)
    else:
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "sheet.csv"
            if has_app("Excel.Application"):
                office_save("excel", src, csv_path, EXCEL["csv"], ctx)
            else:
                libreoffice_save(src, csv_path, "csv", ctx)
            records = data.read_table(csv_path)
    with Output.next_to(src, "json") as out:
        data.write(records, out.tmp, "json")
    return out.final
