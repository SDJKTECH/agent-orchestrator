"""Command-line interface for the CSV summariser."""

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from orchestrator.examples.csv_summariser.summary import summarise

_HEADERS = ["name", "type", "count", "missing", "unique", "min", "max", "mean"]


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def format_table(summary: dict[str, Any]) -> str:
    """Render a summary dict as a readable plain-text table."""
    table = [_HEADERS] + [
        [_format_value(column.get(key)) for key in _HEADERS]
        for column in summary["columns"]
    ]
    widths = [max(len(row[i]) for row in table) for i in range(len(_HEADERS))]
    lines = [f"Rows: {summary['rows']}", ""]
    for number, row in enumerate(table):
        cells = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        lines.append("  ".join(cells).rstrip())
        if number == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.examples.csv_summariser",
        description="Summarise the columns of a CSV file.",
    )
    parser.add_argument("path", help="path to a CSV file with a header row")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = summarise(args.path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_table(summary))
    return 0
