"""Column type detection and per-column statistics for the CSV summariser."""

import math
import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import date
from typing import Any

INTEGER = "integer"
FLOAT = "float"
BOOLEAN = "boolean"
DATE = "date"
TEXT = "text"

NUMERIC_TYPES = (INTEGER, FLOAT)
TOP_VALUES = 3

_BOOLEAN_WORDS = frozenset({"true", "false"})

ColumnStats = dict[str, Any]


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


def _is_boolean(value: str) -> bool:
    return value.lower() in _BOOLEAN_WORDS


def _is_iso_date(value: str) -> bool:
    # Strictly YYYY-MM-DD (fromisoformat also accepts other forms on 3.11+).
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def present_values(cells: Sequence[str]) -> list[str]:
    """Return the stripped, non-empty cells (empty cells are missing)."""
    stripped = (cell.strip() for cell in cells)
    return [cell for cell in stripped if cell]


def detect_type(values: Sequence[str]) -> str:
    """Return the narrowest type fitting every non-empty value.

    Empty values are ignored; a column with no values at all is text, and a
    column mixing types (e.g. numbers and words) is text.
    """
    present = present_values(values)
    if not present:
        return TEXT
    if all(_parse_int(v) is not None for v in present):
        return INTEGER
    if all(_parse_float(v) is not None for v in present):
        return FLOAT
    if all(_is_boolean(v) for v in present):
        return BOOLEAN
    if all(_is_iso_date(v) for v in present):
        return DATE
    return TEXT


def column_stats(name: str, cells: Sequence[str]) -> ColumnStats:
    """Detect the type of a column and compute its statistics.

    ``cells`` are the raw cells of the column, one per data row. Every result
    has ``name``, ``type``, ``count`` (non-empty cells), ``missing`` and
    ``unique``. Numeric columns (integer, float) add ``min``, ``max``, ``mean``
    and ``median``; text columns add ``top`` (the 3 most common values as
    ``{"value", "count"}`` entries, ties in order of first appearance).
    """
    values = present_values(cells)
    column_type = detect_type(values)
    result: ColumnStats = {
        "name": name,
        "type": column_type,
        "count": len(values),
        "missing": len(cells) - len(values),
        "unique": len(set(values)),
    }
    if column_type in NUMERIC_TYPES:
        numbers: list[int] | list[float]
        if column_type == INTEGER:
            numbers = [int(v) for v in values]
        else:
            numbers = [float(v) for v in values]
        result["min"] = min(numbers)
        result["max"] = max(numbers)
        result["mean"] = sum(numbers) / len(numbers)
        result["median"] = statistics.median(numbers)
    elif column_type == TEXT:
        result["top"] = [
            {"value": value, "count": count}
            for value, count in Counter(values).most_common(TOP_VALUES)
        ]
    return result
