"""UTF-8 exports. CSV names are escaped to prevent spreadsheet formula injection."""

import csv
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from cs2demo_parser.models import Analysis


class JSONReport(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def json_text(analysis: JSONReport) -> str:
    return json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def write_json(analysis: JSONReport, path: Path, *, force: bool = False) -> None:
    content = json_text(analysis)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if force else "x", encoding="utf-8") as handle:
        handle.write(content)


def safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(
    analysis: Analysis, path: Path, *, per_round: bool = False, force: bool = False
) -> None:
    source = [asdict(row) for row in analysis.player_rounds] if per_round else analysis.players
    write_rows_csv(source, path, force=force)


def write_rows_csv(
    source: Sequence[dict[str, Any]], path: Path, *, force: bool = False
) -> None:
    rows = [
        {
            key: safe_cell(
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list, tuple)) else value
            )
            for key, value in row.items()
        }
        for row in source
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if force else "x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
