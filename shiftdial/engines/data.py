"""Structured data: JSON, JSON Lines, YAML, TOML, XML ↔ each other, CSV and XLSX tables."""
from __future__ import annotations

import csv
import json
import tomllib
from pathlib import Path
from typing import Any

from shiftdial.engines import JobContext
from shiftdial.engines.text import read_text
from shiftdial.utils.paths import Output


def read_table(path: Path) -> list[dict[str, str]]:
    """CSV/TSV with a header row → list of row dicts."""
    text = read_text(path)
    if path.suffix.lower() == ".tsv":
        delimiter = "\t"
    else:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


def read(src: Path) -> Any:
    ext = src.suffix.lower().lstrip(".")
    if ext in ("csv", "tsv"):
        return read_table(src)
    text = read_text(src)
    if ext == "json":
        return json.loads(text)
    if ext in ("jsonl", "ndjson"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if ext in ("yaml", "yml"):
        import yaml

        return yaml.safe_load(text)  # untrusted input: never let a file construct Python objects
    if ext == "toml":
        return tomllib.loads(text)
    if ext == "xml":
        import xmltodict

        return xmltodict.parse(text, disable_entities=True)  # no entity expansion ("billion laughs")
    raise ValueError(f"Can't read {src.name}")


def records(obj: Any) -> list[dict[str, Any]]:
    """Coerce any data into table rows: a list of objects, the one list inside an object, or one row."""
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if isinstance(v, list)]
        if len(obj) == 1 and lists:
            obj = lists[0]
        else:
            return [obj]
    if isinstance(obj, list):
        return [r if isinstance(r, dict) else {"value": r} for r in obj]
    return [{"value": obj}]


def _cell(v: Any) -> Any:
    return json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v


def write(obj: Any, out: Path, fmt: str) -> None:
    if fmt == "json":
        out.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    elif fmt == "jsonl":
        rows = obj if isinstance(obj, list) else [obj]
        out.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows),
                       encoding="utf-8")
    elif fmt == "yaml":
        import yaml

        out.write_text(yaml.safe_dump(json.loads(json.dumps(obj, default=str)), allow_unicode=True,
                                      sort_keys=False), encoding="utf-8")
    elif fmt == "toml":
        import tomli_w

        table = obj if isinstance(obj, dict) else {"items": obj}  # TOML's top level must be a table
        try:
            out.write_text(tomli_w.dumps(json.loads(json.dumps(table, default=str))), encoding="utf-8")
        except TypeError as e:
            raise ValueError(f"This data can't be written as TOML ({e}); null values aren't allowed") from e
    elif fmt == "xml":
        import xmltodict

        if not (isinstance(obj, dict) and len(obj) == 1):  # XML needs exactly one root element
            obj = {"root": {"item": obj} if isinstance(obj, list) else obj}
        out.write_text(xmltodict.unparse(obj, pretty=True), encoding="utf-8")
    elif fmt in ("csv", "xlsx"):
        rows = records(obj)
        columns = list(dict.fromkeys(k for r in rows for k in r))  # union, first-seen order
        if fmt == "csv":
            with out.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel reads UTF-8
                w = csv.DictWriter(f, fieldnames=columns)
                w.writeheader()
                w.writerows({k: _cell(v) for k, v in r.items()} for r in rows)
        else:
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.append(columns)
            for r in rows:
                ws.append([_cell(r.get(c)) for c in columns])
            wb.save(out)
    else:
        raise ValueError(fmt)


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        obj = read(src)
        with Output.next_to(src, fmt) as out:
            write(obj, out.tmp, fmt)
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs
