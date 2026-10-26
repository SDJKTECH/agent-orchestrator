"""Compute per-column summary statistics for a CSV file."""

import csv
import math
from pathlib import Path
from typing import Any

INTEGER = "integer"
FLOAT = "float"
TEXT = "text"


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _parse_float(value: str) -> float | None:
    try:
        number = float(value)
    except ValueError:
        return None
    # "nan" and "inf" parse as floats but are treated as text.
    return number if math.isfinite(number) else None


def _detect_type(values: list[str]) -> str:
    """Return the narrowest type that fits every value (text if no values)."""
    if not values:
        return TEXT
    if all(_parse_int(v) is not None for v in values):
        return INTEGER
    if all(_parse_float(v) is not None for v in values):
        return FLOAT
    return TEXT


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

    Returns ``{"rows": int, "columns": [...]}`` where each column entry has
    ``name``, ``type`` (integer, float or text), ``count`` (non-empty values),
    ``missing``, ``unique`` and, for numeric columns, ``min``, ``max`` and
    ``mean``.

    Raises ``ValueError`` for an empty file or a file without a header, and
    lets ``OSError`` (e.g. ``FileNotFoundError``) propagate.
    """
    header, rows = _read_rows(path)
    columns: list[dict[str, Any]] = []
    for index, name in enumerate(header):
        cells = [row[index].strip() if index < len(row) else "" for row in rows]
        values = [cell for cell in cells if cell]
        column_type = _detect_type(values)
        column: dict[str, Any] = {
            "name": name,
            "type": column_type,
            "count": len(values),
            "missing": len(cells) - len(values),
            "unique": len(set(values)),
        }
        if column_type != TEXT:
            numbers: list[int] | list[float]
            if column_type == INTEGER:
                numbers = [int(v) for v in values]
            else:
                numbers = [float(v) for v in values]
            column["min"] = min(numbers)
            column["max"] = max(numbers)
            column["mean"] = sum(numbers) / len(numbers)
        columns.append(column)
    return {"rows": len(rows), "columns": columns}
