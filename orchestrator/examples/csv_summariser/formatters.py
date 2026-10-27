"""Output formats for CSV column statistics.

Each formatter takes the list of :class:`ColumnSummary` produced by
``summarise_columns`` and returns a string. Formatters are looked up by name
through :func:`get_formatter`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict

from orchestrator.examples.csv_summariser.stats import ColumnSummary

Formatter = Callable[[Sequence[ColumnSummary]], str]

_STAT_LABELS = ("min", "max", "mean", "median")
_MARKDOWN_HEADER = (
    "column",
    "type",
    "count",
    "missing",
    "min",
    "max",
    "mean",
    "median",
    "distinct",
    "top",
)


def _format_number(value: float | str) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def format_summary(summary: ColumnSummary) -> str:
    """Render one column as the plain-text block."""
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
        return "No columns found (empty file)."
    return "\n\n".join(format_summary(summary) for summary in summaries)


def format_json(summaries: Sequence[ColumnSummary]) -> str:
    """Render a JSON array with one object per column."""
    return json.dumps([asdict(summary) for summary in summaries], indent=2)


def _escape_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def format_markdown(summaries: Sequence[ColumnSummary]) -> str:
    """Render a Markdown table with one row per column."""
    rows = [
        _MARKDOWN_HEADER,
        tuple("---" for _ in _MARKDOWN_HEADER),
    ]
    for summary in summaries:
        top = (
            ", ".join(f"{value!r} ({n})" for value, n in summary.top_values)
            if summary.top_values
            else ""
        )
        cells = [summary.name or "(unnamed)", summary.type]
        cells += [str(summary.count), str(summary.missing)]
        for label in _STAT_LABELS:
            value = getattr(summary, label)
            cells.append("" if value is None else _format_number(value))
        cells.append("" if summary.distinct is None else str(summary.distinct))
        cells.append(top)
        rows.append(tuple(_escape_cell(cell) for cell in cells))
    return "\n".join("| " + " | ".join(row) + " |" for row in rows)


FORMATTERS: dict[str, Formatter] = {
    "text": format_text,
    "json": format_json,
    "markdown": format_markdown,
}

DEFAULT_FORMAT = "text"


def get_formatter(name: str) -> Formatter:
    """Return the formatter registered as ``name``.

    Raises:
        ValueError: if ``name`` is not a known format.
    """
    try:
        return FORMATTERS[name]
    except KeyError:
        known = ", ".join(sorted(FORMATTERS))
        raise ValueError(
            f"unknown output format {name!r}; expected one of: {known}"
        ) from None
