import json
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.examples.csv_summariser import cli, summarise
from orchestrator.examples.csv_summariser.cli import (
    format_json,
    format_markdown,
    format_table,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

SAMPLE = "name,age,score,city\nann,30,1.5,Paris\nbob,,2.5,\ncy,25,,Rome\n"

MARKDOWN_HEADER = "| Name | Type | Count | Missing | Unique | Min | Max | Mean |"
MARKDOWN_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- | --- |"


def _write_csv(tmp_path, text=SAMPLE, name="data.csv"):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def _summary_for_sample(tmp_path):
    return summarise(_write_csv(tmp_path))


# --- format_json -------------------------------------------------------------


def test_format_json_is_valid_json_with_full_summary(tmp_path):
    summary = _summary_for_sample(tmp_path)

    parsed = json.loads(format_json(summary))

    assert parsed == summary
    assert parsed["rows"] == 3
    assert [column["name"] for column in parsed["columns"]] == [
        "name",
        "age",
        "score",
        "city",
    ]
    age = parsed["columns"][1]
    assert age["type"] == "integer"
    assert age["min"] == 25
    assert age["max"] == 30
    assert age["mean"] == 27.5


def test_format_json_keeps_text_column_without_numeric_stats(tmp_path):
    parsed = json.loads(format_json(_summary_for_sample(tmp_path)))

    city = parsed["columns"][3]
    assert city == {
        "name": "city",
        "type": "text",
        "count": 2,
        "missing": 1,
        "unique": 2,
    }


def test_format_json_handles_header_only_file(tmp_path):
    summary = summarise(_write_csv(tmp_path, "a,b\n"))

    parsed = json.loads(format_json(summary))

    assert parsed["rows"] == 0
    assert [column["type"] for column in parsed["columns"]] == ["text", "text"]
    assert all(column["count"] == 0 for column in parsed["columns"])


# --- format_markdown ---------------------------------------------------------


def test_format_markdown_has_header_separator_and_one_row_per_column(tmp_path):
    lines = format_markdown(_summary_for_sample(tmp_path)).splitlines()

    assert lines[0] == MARKDOWN_HEADER
    assert lines[1] == MARKDOWN_SEPARATOR
    assert len(lines) == 2 + 4  # header, separator, one row per column


def test_format_markdown_rows_contain_type_and_key_statistics(tmp_path):
    lines = format_markdown(_summary_for_sample(tmp_path)).splitlines()

    assert lines[2] == "| name | text | 3 | 0 | 3 | - | - | - |"
    assert lines[3] == "| age | integer | 2 | 1 | 2 | 25 | 30 | 27.5 |"
    assert lines[4] == "| score | float | 2 | 1 | 2 | 1.5 | 2.5 | 2 |"
    assert lines[5] == "| city | text | 2 | 1 | 2 | - | - | - |"


def test_format_markdown_escapes_pipes_and_newlines_in_column_names(tmp_path):
    path = _write_csv(tmp_path, 'a|b,"x\ny"\n1,2\n')

    markdown = format_markdown(summarise(path))

    lines = markdown.splitlines()
    assert lines[2] == "| a\\|b | integer | 1 | 0 | 1 | 1 | 1 | 1 |"
    assert lines[3] == "| x y | integer | 1 | 0 | 1 | 2 | 2 | 2 |"
    assert len(lines) == 4


def test_format_markdown_escapes_backslashes(tmp_path):
    path = _write_csv(tmp_path, "back\\slash\nx\n")

    row = format_markdown(summarise(path)).splitlines()[2]

    assert row.startswith("| back\\\\slash | text |")


def test_format_markdown_header_only_file_has_no_data_rows(tmp_path):
    lines = format_markdown(summarise(_write_csv(tmp_path, "a,b\n"))).splitlines()

    assert lines == [
        MARKDOWN_HEADER,
        MARKDOWN_SEPARATOR,
        "| a | text | 0 | 0 | 0 | - | - | - |",
        "| b | text | 0 | 0 | 0 | - | - | - |",
    ]


# --- format_table (default text output) --------------------------------------


def test_format_table_starts_with_row_count_and_lists_every_column(tmp_path):
    text = format_table(_summary_for_sample(tmp_path))

    lines = text.splitlines()
    assert lines[0] == "Rows: 3"
    assert lines[1] == ""
    assert lines[2].split() == [
        "name",
        "type",
        "count",
        "missing",
        "unique",
        "min",
        "max",
        "mean",
    ]
    assert set(lines[3].replace(" ", "")) == {"-"}
    assert len(lines) == 4 + 4  # title, blank, header, rule, four columns


# --- main: default text format -----------------------------------------------


def test_cli_default_format_is_text(tmp_path, capsys):
    path = _write_csv(tmp_path)

    assert main([str(path)]) == 0

    out = capsys.readouterr().out
    assert out.startswith("Rows: 3")
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_cli_format_text_explicit_matches_default(tmp_path, capsys):
    path = _write_csv(tmp_path)

    main([str(path)])
    default_out = capsys.readouterr().out
    assert main([str(path), "--format", "text"]) == 0

    assert capsys.readouterr().out == default_out


# --- main: --format json / markdown ------------------------------------------


def test_cli_format_json_prints_valid_full_summary(tmp_path, capsys):
    path = _write_csv(tmp_path)

    assert main([str(path), "--format", "json"]) == 0

    out = capsys.readouterr().out
    assert json.loads(out) == summarise(path)


def test_cli_json_flag_is_shorthand_for_format_json(tmp_path, capsys):
    path = _write_csv(tmp_path)

    assert main([str(path), "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == summarise(path)


def test_cli_format_markdown_prints_table(tmp_path, capsys):
    path = _write_csv(tmp_path)

    assert main([str(path), "--format", "markdown"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0] == MARKDOWN_HEADER
    assert "| age | integer | 2 | 1 | 2 | 25 | 30 | 27.5 |" in out.splitlines()


# --- main: --output ----------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("text", format_table),
        ("json", format_json),
        ("markdown", format_markdown),
    ],
)
def test_cli_output_writes_each_format_to_file_and_not_stdout(
    tmp_path, capsys, fmt, expected
):
    path = _write_csv(tmp_path)
    out_path = tmp_path / "result.txt"

    assert main([str(path), "--format", fmt, "--output", str(out_path)]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert out_path.read_text(encoding="utf-8") == expected(summarise(path)) + "\n"


def test_cli_output_json_file_is_valid_json(tmp_path):
    path = _write_csv(tmp_path)
    out_path = tmp_path / "summary.json"

    assert main([str(path), "--format", "json", "--output", str(out_path)]) == 0

    assert json.loads(out_path.read_text(encoding="utf-8")) == summarise(path)


def test_cli_json_flag_with_output_writes_json_file(tmp_path):
    path = _write_csv(tmp_path)
    out_path = tmp_path / "out.md"

    assert main([str(path), "--json", "--output", str(out_path)]) == 0

    assert json.loads(out_path.read_text(encoding="utf-8"))["rows"] == 3


def test_cli_output_overwrites_existing_file(tmp_path):
    path = _write_csv(tmp_path)
    out_path = tmp_path / "result.txt"
    out_path.write_text("stale content that is much longer" * 5, encoding="utf-8")

    assert main([str(path), "--output", str(out_path)]) == 0

    assert "stale" not in out_path.read_text(encoding="utf-8")


def test_cli_output_unwritable_path_reports_error_without_traceback(tmp_path, capsys):
    path = _write_csv(tmp_path)
    bad_out = tmp_path / "is_a_directory"
    bad_out.mkdir()

    assert main([str(path), "--output", str(bad_out)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: cannot write")
    assert "Traceback" not in captured.err


# --- main: error cases -------------------------------------------------------


def test_cli_missing_file_prints_clear_error_and_returns_1(tmp_path, capsys):
    missing = tmp_path / "nope.csv"

    assert main([str(missing)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: file not found: {missing}\n"
    assert "Traceback" not in captured.err


def test_cli_missing_file_with_output_does_not_create_output(tmp_path, capsys):
    out_path = tmp_path / "result.json"

    assert main([str(tmp_path / "nope.csv"), "--output", str(out_path)]) == 1

    assert not out_path.exists()
    assert "file not found" in capsys.readouterr().err


def test_cli_directory_instead_of_file_reports_unreadable(tmp_path, capsys):
    assert main([str(tmp_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: cannot read")
    assert "Traceback" not in captured.err


def test_cli_permission_error_reports_unreadable(tmp_path, capsys, monkeypatch):
    path = _write_csv(tmp_path)

    def deny(_path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "summarise", deny)

    assert main([str(path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: cannot read {path}")
    assert "Permission denied" in captured.err
    assert "Traceback" not in captured.err


def test_cli_non_utf8_file_reports_unreadable(tmp_path, capsys):
    path = tmp_path / "binary.csv"
    path.write_bytes(b"\xff\xfe\x00\x81bad,\xc3\x28\n")

    assert main([str(path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: cannot read")
    assert "Traceback" not in captured.err


def test_cli_empty_file_reports_no_header_and_returns_1(tmp_path, capsys):
    path = _write_csv(tmp_path, "")

    assert main([str(path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "CSV file is empty or has no header row" in captured.err
    assert "Traceback" not in captured.err


def test_cli_blank_lines_only_reports_no_header(tmp_path, capsys):
    path = _write_csv(tmp_path, "\n   \n,,\n")

    assert main([str(path)]) == 1

    assert "has no header row" in capsys.readouterr().err


@pytest.mark.parametrize("fmt", ["xml", "JSON", "Markdown", "tsv", ""])
def test_cli_unknown_format_reports_error_and_returns_1(tmp_path, capsys, fmt):
    path = _write_csv(tmp_path)

    assert main([str(path), "--format", fmt]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"error: unknown format '{fmt}'")
    assert "text, json, markdown" in captured.err
    assert "Traceback" not in captured.err


def test_cli_unknown_format_does_not_write_output_file(tmp_path, capsys):
    path = _write_csv(tmp_path)
    out_path = tmp_path / "result.xml"

    assert main([str(path), "--format", "xml", "--output", str(out_path)]) == 1

    assert not out_path.exists()


def test_cli_missing_path_argument_exits_with_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])

    assert exc.value.code == 2
    assert "path" in capsys.readouterr().err


# --- end-to-end: python -m invocation ----------------------------------------


def _run_module(*args):
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.examples.csv_summariser", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_module_entry_point_prints_json_and_exits_zero(tmp_path):
    path = _write_csv(tmp_path)

    result = _run_module(str(path), "--format", "json")

    assert result.returncode == 0
    assert json.loads(result.stdout) == summarise(path)
    assert result.stderr == ""


def test_module_entry_point_missing_file_exits_nonzero_without_traceback(tmp_path):
    result = _run_module(str(tmp_path / "missing.csv"))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "error: file not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_module_entry_point_unknown_format_exits_nonzero_without_traceback(
    tmp_path,
):
    path = _write_csv(tmp_path)

    result = _run_module(str(path), "--format", "yaml")

    assert result.returncode == 1
    assert "unknown format 'yaml'" in result.stderr
    assert "Traceback" not in result.stderr


# --- additional edge cases ---------------------------------------------------


def test_cli_output_into_missing_directory_reports_error_without_traceback(
    tmp_path, capsys
):
    path = _write_csv(tmp_path)
    out_path = tmp_path / "no_such_dir" / "result.json"

    assert main([str(path), "--format", "json", "--output", str(out_path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: cannot write")
    assert "Traceback" not in captured.err
    assert not out_path.exists()


@pytest.mark.parametrize("fmt", ["text", "json", "markdown"])
def test_cli_unreadable_input_with_output_does_not_create_output(tmp_path, capsys, fmt):
    path = _write_csv(tmp_path, "")
    out_path = tmp_path / "result.out"

    assert main([str(path), "--format", fmt, "--output", str(out_path)]) == 1

    assert not out_path.exists()
    assert "has no header row" in capsys.readouterr().err


def test_cli_output_replaces_previous_file_when_format_changes(tmp_path):
    path = _write_csv(tmp_path)
    out_path = tmp_path / "result"

    assert main([str(path), "--format", "json", "--output", str(out_path)]) == 0
    assert main([str(path), "--format", "markdown", "--output", str(out_path)]) == 0

    content = out_path.read_text(encoding="utf-8")
    assert content.startswith(MARKDOWN_HEADER)
    assert "{" not in content


def test_non_ascii_column_names_survive_json_and_markdown(tmp_path, capsys):
    path = _write_csv(tmp_path, "nom,città\nérika,Zürich\n", name="unicode.csv")
    summary = summarise(path)

    assert json.loads(format_json(summary))["columns"][1]["name"] == "città"
    assert "| città | text |" in format_markdown(summary)

    out_path = tmp_path / "unicode.md"
    assert main([str(path), "--format", "markdown", "--output", str(out_path)]) == 0
    assert "| città | text |" in out_path.read_text(encoding="utf-8")


def test_module_entry_point_writes_markdown_to_output_file(tmp_path):
    path = _write_csv(tmp_path)
    out_path = tmp_path / "summary.md"

    result = _run_module(str(path), "--format", "markdown", "--output", str(out_path))

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert out_path.read_text(encoding="utf-8").splitlines()[0] == MARKDOWN_HEADER


def test_module_entry_point_unreadable_output_exits_nonzero_without_traceback(
    tmp_path,
):
    path = _write_csv(tmp_path)
    bad_out = tmp_path / "missing_dir" / "out.json"

    result = _run_module(str(path), "--format", "json", "--output", str(bad_out))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "error: cannot write" in result.stderr
    assert "Traceback" not in result.stderr
