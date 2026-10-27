"""Command-line interface for the CSV summariser."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

from orchestrator.examples.csv_summariser.formatters import (
    EXTENSION_FORMATS,
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
        help="report format (default: inferred from the --output extension, else text)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the report to this file instead of printing it",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summaries = summarise_columns(read_rows(args.input))
    except (csv.Error, UnicodeDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    fmt = args.format
    if fmt is None:
        suffix = args.output.suffix.lower() if args.output else ""
        fmt = EXTENSION_FORMATS.get(suffix, "text")
    report = get_formatter(fmt)(summaries)
    if args.output is None:
        print(report)
        return 0
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write {args.output}: {exc}", file=sys.stderr)
        return 1
    return 0
