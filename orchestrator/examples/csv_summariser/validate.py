"""Validate a CSV file, reporting problems instead of summarising it.

The first row is the header. Row numbers in messages are 1-based file rows
(the header is row 1). Problems reported:

* rows with too few or too many fields compared with the header;
* duplicate column names in the header;
* duplicate data rows (identical to an earlier row).

Completely blank lines are skipped by the CSV reader and not reported.
"""

from __future__ import annotations

from pathlib import Path

from .stats import read_rows


def validate_csv(path: str | Path) -> list[str]:
    """Return a list of human-readable problems found in the CSV at ``path``.

    An empty list means the file is valid.
    """
    rows = read_rows(Path(path))
    if not rows:
        return []

    problems: list[str] = []
    header = rows[0]
    width = len(header)

    first_seen_column: dict[str, int] = {}
    for index, name in enumerate(header, start=1):
        if name in first_seen_column:
            problems.append(
                f"Header: duplicate column name {name!r} "
                f"(columns {first_seen_column[name]} and {index})"
            )
        else:
            first_seen_column[name] = index

    first_seen_row: dict[tuple[str, ...], int] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        if not row:
            continue

        if len(row) < width:
            problems.append(
                f"Row {row_number}: too few fields (expected {width}, got {len(row)})"
            )
        elif len(row) > width:
            problems.append(
                f"Row {row_number}: too many fields (expected {width}, got {len(row)})"
            )

        key = tuple(row)
        if key in first_seen_row:
            problems.append(f"Row {row_number}: duplicate of row {first_seen_row[key]}")
        else:
            first_seen_row[key] = row_number

    return problems
