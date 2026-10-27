import json

import pytest

from orchestrator.examples.csv_summariser.cli import build_parser, main
from orchestrator.examples.csv_summariser.formatters import (
    DEFAULT_FORMAT,
    FORMATTERS,
    format_json,
    format_markdown,
    format_text,
    get_formatter,
)
from orchestrator.examples.csv_summariser.stats import ColumnSummary


@pytest.fixture
def summaries():
    return [
        ColumnSummary(
            name="id",
            type="integer",
            count=2,
            missing=1,
            min=1,
            max=2,
            mean=1.5,
            median=1.5,
        ),
        ColumnSummary(
            name="name",
            type="text",
            count=3,
            missing=0,
            distinct=2,
            top_values=(("ann", 2), ("bob", 1)),
        ),
        ColumnSummary(
            name="when",
            type="date",
            count=2,
            missing=0,
            min="2024-01-01",
            max="2024-01-02",
        ),
    ]


# --- registry / get_formatter -------------------------------------------------


def test_registry_contains_expected_formats():
    assert set(FORMATTERS) == {"text", "json", "markdown"}
    assert DEFAULT_FORMAT == "text"


@pytest.mark.parametrize(
    ("name", "func"),
    [("text", format_text), ("json", format_json), ("markdown", format_markdown)],
)
def test_get_formatter_returns_registered_function(name, func):
    assert get_formatter(name) is func


@pytest.mark.parametrize("name", ["xml", "", "JSON", "Text"])
def test_get_formatter_unknown_name_raises_clear_error(name):
    with pytest.raises(ValueError) as excinfo:
        get_formatter(name)
    message = str(excinfo.value)
    assert repr(name) in message
    assert "json, markdown, text" in message


# --- text ---------------------------------------------------------------------


def test_text_formatter_output(summaries):
    assert format_text(summaries[:2]) == (
        "id [integer]\n  count: 2\n  missing: 1\n"
        "  min: 1\n  max: 2\n  mean: 1.5\n  median: 1.5"
        "\n\n"
        "name [text]\n  count: 3\n  missing: 0\n"
        "  distinct: 2\n  top: 'ann' (2), 'bob' (1)"
    )


def test_text_formatter_empty():
    assert format_text([]) == "No columns found (empty file)."


# --- json ---------------------------------------------------------------------


def test_json_formatter_one_object_per_column(summaries):
    data = json.loads(format_json(summaries))
    assert [d["name"] for d in data] == ["id", "name", "when"]
    assert data[0] == {
        "name": "id",
        "type": "integer",
        "count": 2,
        "missing": 1,
        "min": 1,
        "max": 2,
        "mean": 1.5,
        "median": 1.5,
        "distinct": None,
        "top_values": None,
    }
    assert data[1]["top_values"] == [["ann", 2], ["bob", 1]]
    assert data[1]["distinct"] == 2
    assert data[2]["min"] == "2024-01-01"


def test_json_formatter_empty():
    assert json.loads(format_json([])) == []


# --- markdown -----------------------------------------------------------------


def test_markdown_formatter_table(summaries):
    lines = format_markdown(summaries).splitlines()
    assert lines[0] == (
        "| column | type | count | missing | min | max | mean | median "
        "| distinct | top |"
    )
    assert lines[1] == "| " + " | ".join(["---"] * 10) + " |"
    assert len(lines) == 2 + len(summaries)
    assert lines[2] == "| id | integer | 2 | 1 | 1 | 2 | 1.5 | 1.5 |  |  |"
    assert lines[3] == (
        "| name | text | 3 | 0 |  |  |  |  | 2 | 'ann' (2), 'bob' (1) |"
    )
    assert lines[4] == ("| when | date | 2 | 0 | 2024-01-01 | 2024-01-02 |  |  |  |  |")


def test_markdown_formatter_escapes_pipes_and_newlines():
    summary = ColumnSummary(
        name="a|b",
        type="text",
        count=1,
        missing=0,
        distinct=1,
        top_values=(("x\ny", 1),),
    )
    row = format_markdown([summary]).splitlines()[2]
    assert row.startswith("| a\\|b | text |")
    assert "\n" not in row
    assert "'x\\\\ny'" in row or "x y" in row or "x\\ny" in row


def test_markdown_formatter_unnamed_column():
    summary = ColumnSummary(name="", type="text", count=0, missing=0)
    assert "(unnamed)" in format_markdown([summary])


def test_markdown_formatter_empty_has_only_header():
    assert len(format_markdown([]).splitlines()) == 2


# --- CLI ----------------------------------------------------------------------


def _csv(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("id,name\n1,ann\n2,bob\n", encoding="utf-8")
    return str(path)


def test_cli_default_format_is_text(tmp_path, capsys):
    assert main([_csv(tmp_path)]) == 0
    explicit = capsys.readouterr().out
    assert main([_csv(tmp_path), "--format", "text"]) == 0
    assert capsys.readouterr().out == explicit
    assert "id [integer]" in explicit


def test_cli_json_format(tmp_path, capsys):
    assert main([_csv(tmp_path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["name"] for d in data] == ["id", "name"]


def test_cli_markdown_format(tmp_path, capsys):
    assert main([_csv(tmp_path), "--format", "markdown"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("| column | type")
    assert len(lines) == 4


def test_cli_rejects_unknown_format(tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([_csv(tmp_path), "--format", "xml"])
    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_cli_parser_format_default(tmp_path):
    assert build_parser().parse_args([_csv(tmp_path)]).format == "text"
