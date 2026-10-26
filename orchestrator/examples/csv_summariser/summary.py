"""Compute per-column summary statistics for a CSV file."""

import csv
from pathlib import Path
from typing import Any

from orchestrator.examples.csv_summariser.stats import column_stats


def _read_rows(path: str | Path) -> tuple[list[str], list[list[str]]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        header: list[str] | None = None
        rows: list[list[str]] = []
        for record in csv.reader(handle):
            if not any(cell.strip() for cell in record):
                continue  # skip blank lines
            if header is None:
                header = [cell.strip() for cell in record]
            else:
                rows.append(record)
    if header is None:
        raise ValueError(f"CSV file is empty or has no header row: {path}")
    return header, rows


def summarise(path: str | Path) -> dict[str, Any]:
    """Summarise the CSV file at ``path``.

    Returns ``{"rows": int, "columns": [...]}`` where each column entry is
    produced by :func:`~orchestrator.examples.csv_summariser.stats.column_stats`
    (type, count, missing, unique, plus min/max/mean/median for numeric
    columns and the most common values for text columns).

    A header-only file gives zero rows and text columns with no values.
    Raises ``ValueError`` for an empty file or a file without a header, and
    lets ``OSError`` (e.g. ``FileNotFoundError``) propagate.
    """
    header, rows = _read_rows(path)
    columns = [
        column_stats(name, [row[index] if index < len(row) else "" for row in rows])
        for index, name in enumerate(header)
    ]
    return {"rows": len(rows), "columns": columns}
