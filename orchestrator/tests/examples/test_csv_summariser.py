import json
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.examples.csv_summariser import summarise
from orchestrator.examples.csv_summariser.cli import format_table, main

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(tmp_path: Path, text: str, name: str = "data.csv") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _column(summary: dict, name: str) -> dict:
    matches = [column for column in summary["columns"] if column["name"] == name]
    assert len(matches) == 1, f"expected one column named {name!r}"
    return matches[0]


# --- summarise: types and statistics -------------------------------------


def test_mixed_integer_float_and_text_columns(tmp_path):
    path = _write(
        tmp_path,
        "id,price,label,qty\n1,1.5,apple,10\n2,2.5,banana,20\n3,4,apple,30\n",
    )

    summary = summarise(path)

    assert summary["rows"] == 3
    assert [column["name"] for column in summary["columns"]] == [
        "id",
        "price",
        "label",
        "qty",
    ]

    assert _column(summary, "id") == {
        "name": "id",
        "type": "integer",
        "count": 3,
        "missing": 0,
        "unique": 3,
        "min": 1,
        "max": 3,
        "mean": 2.0,
    }

    price = _column(summary, "price")
    assert price["type"] == "float"
    assert price["min"] == 1.5
    assert price["max"] == 4.0
    assert price["mean"] == pytest.approx(8 / 3)

    label = _column(summary, "label")
    assert label == {
        "name": "label",
        "type": "text",
        "count": 3,
        "missing": 0,
        "unique": 2,
    }
    assert "min" not in label and "max" not in label and "mean" not in label

    qty = _column(summary, "qty")
    assert qty["type"] == "integer"
    assert (qty["min"], qty["max"], qty["mean"]) == (10, 30, 20.0)


def test_missing_values_are_excluded_from_type_and_statistics(tmp_path):
    path = _write(tmp_path, "a,b,c\n1,,x\n,2.5,\n3,,y\n")

    summary = summarise(path)

    assert summary["rows"] == 3

    a = _column(summary, "a")
    assert (a["type"], a["count"], a["missing"], a["unique"]) == (
        "integer",
        2,
        1,
        2,
    )
    assert (a["min"], a["max"], a["mean"]) == (1, 3, 2.0)

    b = _column(summary, "b")
    assert (b["type"], b["count"], b["missing"], b["unique"]) == ("float", 1, 2, 1)
    assert (b["min"], b["max"], b["mean"]) == (2.5, 2.5, 2.5)

    c = _column(summary, "c")
    assert (c["type"], c["count"], c["missing"], c["unique"]) == ("text", 2, 1, 2)


def test_whitespace_only_cells_count_as_missing(tmp_path):
    path = _write(tmp_path, "a,b\n 1 ,   \n2,x\n")

    summary = summarise(path)

    a = _column(summary, "a")
    assert (a["type"], a["count"], a["missing"]) == ("integer", 2, 0)
    b = _column(summary, "b")
    assert (b["type"], b["count"], b["missing"], b["unique"]) == ("text", 1, 1, 1)


def test_column_with_only_empty_cells_is_text_with_no_statistics(tmp_path):
    path = _write(tmp_path, "a,b\n1,\n2,\n")

    summary = summarise(path)

    b = _column(summary, "b")
    assert b == {
        "name": "b",
        "type": "text",
        "count": 0,
        "missing": 2,
        "unique": 0,
    }


def test_short_rows_count_absent_cells_as_missing(tmp_path):
    path = _write(tmp_path, "a,b,c\n1,2\n3,4,5\n")

    summary = summarise(path)

    assert summary["rows"] == 2
    c = _column(summary, "c")
    assert (c["type"], c["count"], c["missing"]) == ("integer", 1, 1)
    assert (c["min"], c["max"], c["mean"]) == (5, 5, 5.0)


def test_integer_column_with_one_decimal_value_becomes_float(tmp_path):
    path = _write(tmp_path, "n\n1\n2\n3.0\n")

    column = _column(summarise(path), "n")

    assert column["type"] == "float"
    assert (column["min"], column["max"], column["mean"]) == (1.0, 3.0, 2.0)


def test_column_mixing_numbers_and_words_is_text(tmp_path):
    path = _write(tmp_path, "v\n1\ntwo\n3\n")

    column = _column(summarise(path), "v")

    assert column["type"] == "text"
    assert column["count"] == 3
    assert "min" not in column


@pytest.mark.parametrize("token", ["nan", "NaN", "inf", "-Infinity"])
def test_non_finite_float_tokens_are_treated_as_text(tmp_path, token):
    path = _write(tmp_path, f"v\n1.5\n{token}\n")

    column = _column(summarise(path), "v")

    assert column["type"] == "text"


def test_unique_counts_distinct_non_empty_values_case_sensitively(tmp_path):
    path = _write(tmp_path, "word\napple\nApple\napple\n\n")

    column = _column(summarise(path), "word")

    assert column["count"] == 3
    assert column["missing"] == 0
    assert column["unique"] == 2


def test_header_names_are_stripped_and_utf8_bom_is_ignored(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text(" name , age \nAda,36\n", encoding="utf-8-sig")

    summary = summarise(path)

    assert [column["name"] for column in summary["columns"]] == ["name", "age"]
    assert _column(summary, "age")["type"] == "integer"


def test_blank_lines_are_skipped_and_not_counted_as_rows(tmp_path):
    path = _write(tmp_path, "a\n1\n\n   \n2\n")

    summary = summarise(path)

    assert summary["rows"] == 2
    assert _column(summary, "a")["count"] == 2


def test_header_only_file_has_zero_rows(tmp_path):
    path = _write(tmp_path, "a,b\n")

    summary = summarise(path)

    assert summary["rows"] == 0
    assert _column(summary, "a") == {
        "name": "a",
        "type": "text",
        "count": 0,
        "missing": 0,
        "unique": 0,
    }


def test_accepts_str_path_as_well_as_pathlib_path(tmp_path):
    path = _write(tmp_path, "a\n1\n")

    assert summarise(str(path)) == summarise(path)


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        summarise(tmp_path / "does-not-exist.csv")


# --- summarise: invalid input -------------------------------------------


def test_empty_file_raises_value_error(tmp_path):
    path = _write(tmp_path, "")

    with pytest.raises(ValueError, match="empty or has no header"):
        summarise(path)


def test_file_with_only_blank_lines_raises_value_error(tmp_path):
    path = _write(tmp_path, "\n   \n\n")

    with pytest.raises(ValueError, match="no header row"):
        summarise(path)


# --- format_table --------------------------------------------------------


def test_format_table_renders_none_as_dash_and_floats_to_four_significant_digits():
    summary = {
        "rows": 1,
        "columns": [
            {
                "name": "x",
                "type": "float",
                "count": 1,
                "missing": 0,
                "unique": 1,
                "min": 1.23456,
                "max": 1.23456,
                "mean": 1.23456,
            },
            {"name": "t", "type": "text", "count": 1, "missing": 0, "unique": 1},
        ],
    }

    lines = format_table(summary).splitlines()

    assert lines[0] == "Rows: 1"
    assert lines[2].split()[0] == "name"
    assert lines[4].split() == ["x", "float", "1", "0", "1", "1.235", "1.235", "1.235"]
    assert lines[5].split() == ["t", "text", "1", "0", "1", "-", "-", "-"]


# --- CLI -----------------------------------------------------------------


def test_cli_prints_readable_table_and_returns_zero(tmp_path, capsys):
    path = _write(tmp_path, "price,label\n1.5,apple\n2.5,banana\n")

    exit_code = main([str(path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "Rows: 2"
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
    price_row = next(line for line in lines if line.startswith("price"))
    assert price_row.split() == ["price", "float", "2", "0", "2", "1.5", "2.5", "2"]
    label_row = next(line for line in lines if line.startswith("label"))
    assert label_row.split() == ["label", "text", "2", "0", "2", "-", "-", "-"]
    assert capsys.readouterr().err == ""


def test_cli_json_flag_prints_summary_as_json(tmp_path, capsys):
    path = _write(tmp_path, "a,b\n1,x\n,y\n")

    exit_code = main(["--json", str(path)])

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == summarise(path)


def test_cli_json_flag_may_follow_the_path(tmp_path, capsys):
    path = _write(tmp_path, "a\n1\n")

    assert main([str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 1


def test_cli_missing_file_prints_to_stderr_and_returns_one(tmp_path, capsys):
    missing = tmp_path / "missing.csv"

    exit_code = main([str(missing)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "missing.csv" in captured.err


def test_cli_missing_file_with_json_flag_returns_one(tmp_path, capsys):
    exit_code = main(["--json", str(tmp_path / "nope.csv")])

    assert exit_code == 1
    assert capsys.readouterr().out == ""


def test_cli_empty_file_prints_error_and_returns_one(tmp_path, capsys):
    path = _write(tmp_path, "")

    exit_code = main([str(path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "empty or has no header row" in captured.err


def test_cli_without_path_exits_with_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2
    assert "path" in capsys.readouterr().err


def test_python_m_entry_point_runs_the_summariser(tmp_path):
    path = _write(tmp_path, "n\n1\n2\n3\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.examples.csv_summariser",
            "--json",
            str(path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == summarise(path)


def test_python_m_entry_point_missing_file_exits_one(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.examples.csv_summariser",
            str(tmp_path / "absent.csv"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "error:" in result.stderr
