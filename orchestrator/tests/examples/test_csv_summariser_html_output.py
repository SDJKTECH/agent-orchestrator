import re

import pytest

from orchestrator.examples.csv_summariser.cli import build_parser, main
from orchestrator.examples.csv_summariser.formatters import (
    EXTENSION_FORMATS,
    FORMATTERS,
    format_html,
    get_formatter,
)
from orchestrator.examples.csv_summariser.stats import ColumnSummary


def _csv(tmp_path, content="id,name\n1,ann\n2,bob\n"):
    path = tmp_path / "data.csv"
    path.write_text(content, encoding="utf-8")
    return path


def _rows(html):
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    return re.findall(r"<tr>.*?</tr>", body)


# --- html formatter ----------------------------------------------------------


def test_html_registered_in_registry():
    assert FORMATTERS["html"] is format_html
    assert get_formatter("html") is format_html


def test_html_is_self_contained_page():
    html = format_html([ColumnSummary(name="a", type="integer", count=1, missing=0)])

    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html and html.rstrip().endswith("</html>")
    assert "<style>" in html
    assert "<table>" in html
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html and "<link" not in html


def test_html_one_row_per_column():
    summaries = [
        ColumnSummary(name="a", type="integer", count=2, missing=1, min=1, max=2),
        ColumnSummary(name="b", type="text", count=3, missing=0, distinct=2),
        ColumnSummary(name="c", type="date", count=1, missing=0),
    ]

    rows = _rows(format_html(summaries))

    assert len(rows) == 3
    assert "<td>a</td><td>integer</td><td>2</td><td>1</td>" in rows[0]
    assert "min: 1" in rows[0]
    assert "distinct: 2" in rows[1]


def test_html_escapes_markup_in_names_and_values():
    summary = ColumnSummary(
        name='<b>&"x"',
        type="text",
        count=1,
        missing=0,
        distinct=1,
        top_values=(("<script>alert(1)</script>", 1),),
    )

    html = format_html([summary])

    assert "<script>alert" not in html
    assert "&lt;b&gt;&amp;&quot;x&quot;" in html
    assert "&lt;script&gt;" in html


def test_html_unnamed_column_label():
    html = format_html([ColumnSummary(name="", type="text", count=0, missing=0)])

    assert "<td>(unnamed)</td>" in html


def test_html_empty_summaries_shows_message_without_table():
    html = format_html([])

    assert "No columns found (empty file)." in html
    assert "<table>" not in html
    assert html.startswith("<!DOCTYPE html>")


def test_get_formatter_unknown_raises():
    with pytest.raises(ValueError):
        get_formatter("pdf")


def test_extension_formats_mapping():
    assert EXTENSION_FORMATS == {
        ".json": "json",
        ".md": "markdown",
        ".html": "html",
    }


# --- CLI: --format html / --output -------------------------------------------


def test_cli_format_html_prints_to_stdout(tmp_path, capsys):
    assert main([str(_csv(tmp_path)), "--format", "html"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("<!DOCTYPE html>")
    assert len(_rows(out)) == 2


def test_cli_parser_accepts_html_choice_and_output(tmp_path):
    args = build_parser().parse_args(
        [str(tmp_path / "f.csv"), "--format", "html", "--output", str(tmp_path / "o")]
    )

    assert args.format == "html"
    assert args.output == tmp_path / "o"


def test_cli_output_writes_file_and_prints_nothing(tmp_path, capsys):
    out_path = tmp_path / "report.txt"

    assert main([str(_csv(tmp_path)), "--output", str(out_path)]) == 0

    assert capsys.readouterr().out == ""
    text = out_path.read_text(encoding="utf-8")
    assert "id [integer]" in text
    assert text.endswith("\n")


def test_cli_output_creates_parent_folders(tmp_path):
    out_path = tmp_path / "a" / "b" / "report.html"

    assert main([str(_csv(tmp_path)), "--output", str(out_path)]) == 0

    assert out_path.is_file()


def test_cli_output_overwrites_existing_file(tmp_path):
    out_path = tmp_path / "report.txt"
    out_path.write_text("OLD CONTENT " * 1000, encoding="utf-8")

    assert main([str(_csv(tmp_path)), "--output", str(out_path)]) == 0

    assert "OLD CONTENT" not in out_path.read_text(encoding="utf-8")


def test_cli_output_unwritable_path_reports_error(tmp_path, capsys):
    # Parent "directory" is actually a file, so it cannot be created.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")

    assert main([str(_csv(tmp_path)), "--output", str(blocker / "r.html")]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: cannot write ")
    assert "r.html" in captured.err


def test_cli_output_is_directory_reports_error(tmp_path, capsys):
    target = tmp_path / "adir"
    target.mkdir()

    assert main([str(_csv(tmp_path)), "--output", str(target)]) == 1

    assert capsys.readouterr().err.startswith("error: cannot write ")


def test_cli_input_error_does_not_create_output(tmp_path, capsys):
    out_path = tmp_path / "out" / "r.json"

    assert main([str(tmp_path / "missing.csv"), "--output", str(out_path)]) == 1

    assert not out_path.exists()
    assert capsys.readouterr().err.startswith("error: ")


# --- CLI: default format from extension --------------------------------------


@pytest.mark.parametrize(
    ("name", "marker"),
    [
        ("r.json", '"name": "id"'),
        ("r.md", "| Column | Type |"),
        ("r.html", "<!DOCTYPE html>"),
        ("r.HTML", "<!DOCTYPE html>"),
        ("r.JSON", '"name": "id"'),
    ],
)
def test_cli_default_format_from_extension(tmp_path, name, marker):
    out_path = tmp_path / name

    assert main([str(_csv(tmp_path)), "--output", str(out_path)]) == 0

    assert marker in out_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["r.txt", "report", "r.csv"])
def test_cli_unknown_extension_defaults_to_text(tmp_path, name):
    out_path = tmp_path / name

    assert main([str(_csv(tmp_path)), "--output", str(out_path)]) == 0

    assert out_path.read_text(encoding="utf-8").startswith("id [integer]")


def test_cli_explicit_format_overrides_extension(tmp_path):
    out_path = tmp_path / "r.html"

    assert (
        main([str(_csv(tmp_path)), "--output", str(out_path), "--format", "json"]) == 0
    )

    text = out_path.read_text(encoding="utf-8")
    assert text.lstrip().startswith("[")
    assert "<html" not in text


def test_cli_no_output_defaults_to_text(tmp_path, capsys):
    assert main([str(_csv(tmp_path))]) == 0

    assert capsys.readouterr().out.startswith("id [integer]")
