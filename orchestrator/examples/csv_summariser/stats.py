"""Column type detection and per-column statistics for CSV data.

The first row of the input is treated as the header. Conventions:

* An empty string is a missing value. Missing values are excluded from
  ``count`` and from every other statistic.
* A row with too few fields is padded with missing values; fields beyond the
  header width are ignored.
* A column's type is the narrowest of integer, float, date (ISO 8601) that fits
  *all* of its non-missing values; mixed columns, and columns without any
  values, fall back to text.
* Date columns report ``count``, ``missing``, ``min`` and ``max`` (ISO strings).
"""

from __future__ import annotations

import csv
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

INTEGER = "integer"
FLOAT = "float"
DATE = "date"
TEXT = "text"

TOP_VALUES: Final = 3

_INTEGER_RE = re.compile(r"[+-]?\d+")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


@dataclass(frozen=True)
class ColumnSummary:
    """Statistics for one CSV column.

    Fields that do not apply to the column's type are ``None``.
    """

    name: str
    type: str
    count: int
    missing: int
    min: int | float | str | None = None
    max: int | float | str | None = None
    mean: float | None = None
    median: float | None = None
    distinct: int | None = None
    top_values: tuple[tuple[str, int], ...] | None = None


def read_rows(path: Path) -> list[list[str]]:
    """Read every row (header included) of the CSV file at ``path``."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _is_integer(value: str) -> bool:
    return _INTEGER_RE.fullmatch(value) is not None


def _is_float(value: str) -> bool:
    return _FLOAT_RE.fullmatch(value) is not None


def detect_type(values: Iterable[str]) -> str:
    """Return the column type for non-missing ``values``."""
    stripped = [value.strip() for value in values]
    if not stripped:
        return TEXT
    if all(_is_integer(value) for value in stripped):
        return INTEGER
    if all(_is_float(value) for value in stripped):
        return FLOAT
    if all(_parse_date(value) is not None for value in stripped):
        return DATE
    return TEXT


def _summarise_column(name: str, cells: Sequence[str]) -> ColumnSummary:
    values = [cell for cell in cells if cell != ""]
    missing = len(cells) - len(values)
    column_type = detect_type(values)
    count = len(values)

    if column_type in (INTEGER, FLOAT):
        convert = int if column_type == INTEGER else float
        numbers = [convert(value.strip()) for value in values]
        return ColumnSummary(
            name=name,
            type=column_type,
            count=count,
            missing=missing,
            min=min(numbers),
            max=max(numbers),
            mean=statistics.fmean(numbers),
            median=float(statistics.median(numbers)),
        )

    if column_type == DATE:
        dates = [d for v in values if (d := _parse_date(v.strip())) is not None]
        return ColumnSummary(
            name=name,
            type=column_type,
            count=count,
            missing=missing,
            min=min(dates).isoformat(),
            max=max(dates).isoformat(),
        )

    counts = Counter(values)
    return ColumnSummary(
        name=name,
        type=TEXT,
        count=count,
        missing=missing,
        distinct=len(counts),
        top_values=tuple(counts.most_common(TOP_VALUES)),
    )


def summarise_columns(rows: Sequence[Sequence[str]]) -> list[ColumnSummary]:
    """Return one :class:`ColumnSummary` per column of ``rows``.

    ``rows[0]`` is the header. An empty input yields no columns; a header-only
    input yields columns with no values.
    """
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    return [
        _summarise_column(
            name, [row[index] if index < len(row) else "" for row in body]
        )
        for index, name in enumerate(header)
    ]
