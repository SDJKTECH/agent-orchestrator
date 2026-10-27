"""Command-line interface for the CSV summariser."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

from orchestrator.examples.csv_summariser.filters import (
    Condition,
    FilterError,
    apply_filters,
    parse_condition,
)
from orchestrator.examples.csv_summariser.formatters import (
    DEFAULT_FORMAT,
    FORMATTERS,
    format_summary,
    get_formatter,
)
from orchestrator.examples.csv_summariser.stats import read_rows, summarise_columns

__all__ = ["build_parser", "format_summary", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.examples.csv_summariser",
        description="Print per-column statistics for a CSV file with a header row.",
    )
    parser.add_argument("input", type=Path, help="path to the CSV file")
    parser.add_argument(
        "--format",
        choices=sorted(FORMATTERS),
        default=DEFAULT_FORMAT,
        help="output format (default: %(default)s)",
    )
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        type=_condition_arg,
        metavar="CONDITION",
        help=(
            "keep only rows matching <column><op><value>, e.g. 'age>30' "
            "(ops: =, !=, >, >=, <, <=); repeatable, all must match"
        ),
    )
    return parser


def _condition_arg(text: str) -> Condition:
    try:
        return parse_condition(text)
    except FilterError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _filter_rows(rows: list[list[str]], conditions: list[Condition]) -> list[list[str]]:
    """Keep the header plus the body rows that satisfy all ``conditions``."""
    if not rows:
        return rows
    header, body = rows[0], rows[1:]
    # Pad short rows so every header column is addressable by the filters.
    padded = [[*row, *([""] * (len(header) - len(row)))] for row in body]
    records = [dict(zip(header, row, strict=False)) for row in padded]
    kept = apply_filters(records, conditions)
    kept_ids = {id(record) for record in kept}
    return [header] + [
        row for row, record in zip(body, records, strict=True) if id(record) in kept_ids
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    formatter = get_formatter(args.format)
    try:
        rows = read_rows(args.input)
    except (csv.Error, UnicodeDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.where:
        total = max(len(rows) - 1, 0)
        try:
            rows = _filter_rows(rows, args.where)
        except FilterError as exc:
            parser.error(str(exc))
        # stderr keeps stdout machine-readable for the json/csv formats.
        print(f"Kept {max(len(rows) - 1, 0)} of {total} rows", file=sys.stderr)
    print(formatter(summarise_columns(rows)))
    return 0
