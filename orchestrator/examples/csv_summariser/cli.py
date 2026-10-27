"""Command-line interface for the CSV summariser."""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    formatter = get_formatter(args.format)
    try:
        summaries = summarise_columns(read_rows(args.input))
    except (csv.Error, UnicodeDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(formatter(summaries))
    return 0
