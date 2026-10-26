"""Command-line interface for the task dashboard generator."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from orchestrator.examples.task_dashboard.model import load_tasks
from orchestrator.examples.task_dashboard.render import render_html


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.examples.task_dashboard",
        description="Render a static HTML dashboard from a JSON task list.",
    )
    parser.add_argument("input", type=Path, help="path to the JSON task list")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("dashboard.html"),
        help="output HTML file (default: dashboard.html)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        tasks = load_tasks(args.input)
        html = render_html(tasks)
        args.output.write_text(html, encoding="utf-8")
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
