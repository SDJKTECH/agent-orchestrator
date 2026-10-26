"""Command-line interface for the CSV summariser."""

import argparse
import csv
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from orchestrator.examples.csv_summariser.summary import summarise

_HEADERS = ["name", "type", "count", "missing", "unique", "min", "max", "mean"]
_MARKDOWN_TITLES = ["Name", "Type", "Count", "Missing", "Unique", "Min", "Max", "Mean"]


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


def format_json(summary: dict[str, Any]) -> str:
    """Render the full summary dict as JSON."""
    return json.dumps(summary, indent=2)


def _escape_cell(text: str) -> str:
    """Escape text so it stays inside a single Markdown table cell."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def format_markdown(summary: dict[str, Any]) -> str:
    """Render a summary dict as a Markdown table, one row per column."""
    lines = [
        "| " + " | ".join(_MARKDOWN_TITLES) + " |",
        "| " + " | ".join("---" for _ in _MARKDOWN_TITLES) + " |",
    ]
    for column in summary["columns"]:
        cells = [_escape_cell(_format_value(column.get(key))) for key in _HEADERS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


FORMATTERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "text": format_table,
    "json": format_json,
    "markdown": format_markdown,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.examples.csv_summariser",
        description="Summarise the columns of a CSV file.",
    )
    parser.add_argument("path", help="path to a CSV file with a header row")
    parser.add_argument(
        "--format",
        default="text",
        metavar="{" + ",".join(FORMATTERS) + "}",
        help="output format: text (default), json or markdown",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="shorthand for --format json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="PATH",
        help="write the result to this file instead of printing it",
    )
    return parser


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_format = "json" if args.json and args.format == "text" else args.format
    formatter = FORMATTERS.get(output_format)
    if formatter is None:
        choices = ", ".join(FORMATTERS)
        return _fail(f"unknown format '{output_format}' (choose from {choices})")

    try:
        summary = summarise(args.path)
    except FileNotFoundError:
        return _fail(f"file not found: {args.path}")
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return _fail(f"cannot read {args.path}: {exc}")
    except ValueError as exc:
        return _fail(str(exc))

    result = formatter(summary)
    if args.output is None:
        print(result)
        return 0
    try:
        args.output.write_text(result + "\n", encoding="utf-8")
    except OSError as exc:
        return _fail(f"cannot write {args.output}: {exc}")
    return 0
