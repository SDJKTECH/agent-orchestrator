"""Command-line interface for the CSV summariser."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

from orchestrator.examples.csv_summariser.stats import (
    ColumnSummary,
    read_rows,
    summarise_columns,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.examples.csv_summariser",
        description="Print per-column statistics for a CSV file with a header row.",
    )
    parser.add_argument("input", type=Path, help="path to the CSV file")
    return parser


def _format_number(value: float | str) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def format_summary(summary: ColumnSummary) -> str:
    lines = [
        f"{summary.name or '(unnamed)'} [{summary.type}]",
        f"  count: {summary.count}",
        f"  missing: {summary.missing}",
    ]
    for label in ("min", "max", "mean", "median"):
        value = getattr(summary, label)
        if value is not None:
            lines.append(f"  {label}: {_format_number(value)}")
    if summary.distinct is not None:
        lines.append(f"  distinct: {summary.distinct}")
    if summary.top_values:
        top = ", ".join(f"{value!r} ({n})" for value, n in summary.top_values)
        lines.append(f"  top: {top}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summaries = summarise_columns(read_rows(args.input))
    except (csv.Error, UnicodeDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not summaries:
        print("No columns found (empty file).")
    else:
        print("\n\n".join(format_summary(summary) for summary in summaries))
    return 0
