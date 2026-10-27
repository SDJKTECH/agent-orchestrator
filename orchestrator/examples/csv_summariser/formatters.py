"""Report formatters for the CSV summariser, selected by name via a registry."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Sequence
from html import escape

from orchestrator.examples.csv_summariser.stats import ColumnSummary

Formatter = Callable[[Sequence[ColumnSummary]], str]

_STAT_LABELS = ("min", "max", "mean", "median")
_EMPTY_MESSAGE = "No columns found (empty file)."


def _format_number(value: float | str) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def format_summary(summary: ColumnSummary) -> str:
    lines = [
        f"{summary.name or '(unnamed)'} [{summary.type}]",
        f"  count: {summary.count}",
        f"  missing: {summary.missing}",
    ]
    for label in _STAT_LABELS:
        value = getattr(summary, label)
        if value is not None:
            lines.append(f"  {label}: {_format_number(value)}")
    if summary.distinct is not None:
        lines.append(f"  distinct: {summary.distinct}")
    if summary.top_values:
        top = ", ".join(f"{value!r} ({n})" for value, n in summary.top_values)
        lines.append(f"  top: {top}")
    return "\n".join(lines)


def format_text(summaries: Sequence[ColumnSummary]) -> str:
    if not summaries:
        return _EMPTY_MESSAGE
    return "\n\n".join(format_summary(summary) for summary in summaries)


def format_json(summaries: Sequence[ColumnSummary]) -> str:
    return json.dumps([dataclasses.asdict(s) for s in summaries], indent=2)


def _details(summary: ColumnSummary) -> str:
    parts = []
    for label in _STAT_LABELS:
        value = getattr(summary, label)
        if value is not None:
            parts.append(f"{label}: {_format_number(value)}")
    if summary.distinct is not None:
        parts.append(f"distinct: {summary.distinct}")
    if summary.top_values:
        parts.append("top: " + ", ".join(f"{v!r} ({n})" for v, n in summary.top_values))
    return "; ".join(parts)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def format_markdown(summaries: Sequence[ColumnSummary]) -> str:
    if not summaries:
        return _EMPTY_MESSAGE
    lines = [
        "| Column | Type | Count | Missing | Details |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        name = _md_cell(s.name or "(unnamed)")
        lines.append(
            f"| {name} | {s.type} | {s.count} | {s.missing} | {_md_cell(_details(s))} |"
        )
    return "\n".join(lines)


def format_html(summaries: Sequence[ColumnSummary]) -> str:
    """Return a self-contained HTML page with one table row per column."""
    if summaries:
        rows = "\n".join(
            "      <tr>"
            f"<td>{escape(s.name or '(unnamed)')}</td>"
            f"<td>{escape(s.type)}</td>"
            f"<td>{s.count}</td>"
            f"<td>{s.missing}</td>"
            f"<td>{escape(_details(s))}</td>"
            "</tr>"
            for s in summaries
        )
        body = (
            "  <table>\n    <thead>\n      <tr><th>Column</th><th>Type</th>"
            "<th>Count</th><th>Missing</th><th>Details</th></tr>\n"
            f"    </thead>\n    <tbody>\n{rows}\n    </tbody>\n  </table>"
        )
    else:
        body = f"  <p>{_EMPTY_MESSAGE}</p>"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n  <meta charset="utf-8">\n'
        "  <title>CSV summary</title>\n  <style>\n"
        "    body { font-family: sans-serif; margin: 2rem; }\n"
        "    table { border-collapse: collapse; }\n"
        "    th, td { border: 1px solid #ccc; padding: 0.4rem 0.8rem;"
        " text-align: left; }\n"
        "    th { background: #f0f0f0; }\n"
        "  </style>\n</head>\n<body>\n  <h1>CSV summary</h1>\n"
        f"{body}\n</body>\n</html>\n"
    )


FORMATTERS: dict[str, Formatter] = {
    "text": format_text,
    "json": format_json,
    "markdown": format_markdown,
    "html": format_html,
}

EXTENSION_FORMATS: dict[str, str] = {
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
}


def get_formatter(name: str) -> Formatter:
    try:
        return FORMATTERS[name]
    except KeyError:
        raise ValueError(f"unknown format: {name!r}") from None
