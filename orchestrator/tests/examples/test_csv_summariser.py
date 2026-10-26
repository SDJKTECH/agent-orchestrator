import json

import pytest

from orchestrator.examples.csv_summariser import (
    column_stats,
    detect_type,
    summarise,
)
from orchestrator.examples.csv_summariser.cli import format_table, main
from orchestrator.examples.csv_summariser.stats import (
    BOOLEAN,
    DATE,
    FLOAT,
    INTEGER,
    TEXT,
)


def _write_csv(tmp_path, content, name="data.csv"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# --- detect_type: each column type -------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        ["1", "2", "3"],
        ["-5", "0", "+7"],
        ["007", "42"],
        ["9999999999999999999999"],
    ],
)
def test_detect_type_integer(values):
    assert detect_type(values) == INTEGER


@pytest.mark.parametrize(
    "values",
    [
        ["1.5", "2.25"],
        ["1e3", "2.5"],
        ["-0.5"],
        ["1", "2.5"],  # a mix of int and float literals is float
    ],
)
def test_detect_type_float(values):
    assert detect_type(values) == FLOAT


@pytest.mark.parametrize(
    "values",
    [
        ["true", "false", "TRUE"],
        ["True"],
        ["FALSE", "false"],
    ],
)
def test_detect_type_boolean_is_case_insensitive(values):
    assert detect_type(values) == BOOLEAN


@pytest.mark.parametrize(
    "values",
    [
        ["2024-01-05", "1999-12-31"],
        ["2024-02-29"],  # leap day
    ],
)
def test_detect_type_date(values):
    assert detect_type(values) == DATE


@pytest.mark.parametrize(
    "values",
    [
        ["hello", "world"],
        ["2024-02-30"],  # not a real calendar date
        ["2024-1-5"],  # not zero-padded YYYY-MM-DD
        ["05/01/2024"],  # not ISO
        ["2024-01-05T10:00:00"],  # datetime, not a plain date
        ["yes", "no"],  # boolean-like words other than true/false are text
    ],
)
def test_detect_type_text(values):
    assert detect_type(values) == TEXT


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-Infinity", "1e400"])
def test_non_finite_float_words_are_text(value):
    assert detect_type([value]) == TEXT


# --- detect_type: missing values and mixed columns ---------------------------


def test_empty_cells_are_ignored_when_choosing_type():
    assert detect_type(["", "1", "", "2", ""]) == INTEGER
    assert detect_type(["", "2024-01-05", "  "]) == DATE
    assert detect_type(["", "true"]) == BOOLEAN


def test_whitespace_only_cells_count_as_missing():
    assert detect_type(["  ", "\t", "3"]) == INTEGER


def test_all_empty_column_is_text():
    assert detect_type(["", "", ""]) == TEXT


def test_no_cells_is_text():
    assert detect_type([]) == TEXT


def test_column_mixing_numbers_and_text_is_text():
    assert detect_type(["1", "two", "3"]) == TEXT
    assert detect_type(["1.5", "n/a"]) == TEXT


def test_column_mixing_dates_and_numbers_is_text():
    assert detect_type(["2024-01-05", "7"]) == TEXT


def test_column_mixing_booleans_and_numbers_is_text():
    assert detect_type(["true", "1"]) == TEXT


# --- column_stats: numeric columns -------------------------------------------


def test_integer_column_stats():
    stats = column_stats("n", ["3", "1", "", "2", "4"])

    assert stats["name"] == "n"
    assert stats["type"] == INTEGER
    assert stats["count"] == 4
    assert stats["missing"] == 1
    assert stats["unique"] == 4
    assert stats["min"] == 1
    assert stats["max"] == 4
    assert stats["mean"] == pytest.approx(2.5)
    assert stats["median"] == pytest.approx(2.5)
    assert "top" not in stats


def test_integer_median_for_odd_count():
    stats = column_stats("n", ["5", "1", "3"])

    assert stats["median"] == 3
    assert stats["mean"] == pytest.approx(3.0)


def test_float_column_stats():
    stats = column_stats("x", ["1.5", "2.5", "", "4.0"])

    assert stats["type"] == FLOAT
    assert stats["count"] == 3
    assert stats["missing"] == 1
    assert stats["min"] == pytest.approx(1.5)
    assert stats["max"] == pytest.approx(4.0)
    assert stats["mean"] == pytest.approx(8.0 / 3)
    assert stats["median"] == pytest.approx(2.5)


def test_mixed_int_and_float_column_uses_float_stats():
    stats = column_stats("x", ["1", "2.5"])

    assert stats["type"] == FLOAT
    assert stats["min"] == pytest.approx(1.0)
    assert stats["max"] == pytest.approx(2.5)
    assert stats["mean"] == pytest.approx(1.75)


def test_negative_numbers_in_stats():
    stats = column_stats("x", ["-3", "-1", "2"])

    assert stats["min"] == -3
    assert stats["max"] == 2
    assert stats["median"] == -1


def test_numeric_column_with_whitespace_is_stripped():
    stats = column_stats("x", [" 10 ", "20\t"])

    assert stats["type"] == INTEGER
    assert stats["min"] == 10
    assert stats["max"] == 20
    assert stats["unique"] == 2


# --- column_stats: text columns ----------------------------------------------


def test_text_column_stats_counts_and_top_values():
    cells = ["apple", "pear", "apple", "", "fig", "apple", "pear", "kiwi"]

    stats = column_stats("fruit", cells)

    assert stats["type"] == TEXT
    assert stats["count"] == 7
    assert stats["missing"] == 1
    assert stats["unique"] == 4
    assert stats["top"] == [
        {"value": "apple", "count": 3},
        {"value": "pear", "count": 2},
        {"value": "fig", "count": 1},
    ]
    assert "min" not in stats
    assert "mean" not in stats


def test_text_top_values_limited_to_three():
    cells = ["a", "b", "b", "c", "c", "c", "d", "d", "d", "d"]

    top = column_stats("t", cells)["top"]

    assert [entry["value"] for entry in top] == ["d", "c", "b"]
    assert [entry["count"] for entry in top] == [4, 3, 2]


def test_text_top_values_ties_keep_first_appearance_order():
    stats = column_stats("t", ["zeta", "alpha", "mu", "alpha", "mu", "zeta"])

    assert [entry["value"] for entry in stats["top"]] == ["zeta", "alpha", "mu"]


def test_text_unique_and_top_use_stripped_values():
    stats = column_stats("t", [" x ", "x", "x  "])

    assert stats["unique"] == 1
    assert stats["top"] == [{"value": "x", "count": 3}]


def test_text_column_with_fewer_than_three_values():
    stats = column_stats("t", ["only"])

    assert stats["top"] == [{"value": "only", "count": 1}]


def test_mixed_number_and_text_column_is_reported_as_text():
    stats = column_stats("m", ["1", "two", "", "1", "3"])

    assert stats["type"] == TEXT
    assert stats["count"] == 4
    assert stats["missing"] == 1
    assert stats["unique"] == 3
    assert stats["top"][0] == {"value": "1", "count": 2}
    assert "min" not in stats


def test_boolean_column_is_reported_as_text_stats():
    stats = column_stats("b", ["true", "false", "true", ""])

    assert stats["type"] == BOOLEAN
    assert stats["count"] == 3
    assert stats["missing"] == 1
    assert stats["unique"] == 2
    assert "top" not in stats
    assert "min" not in stats


def test_date_column_has_no_numeric_or_top_stats():
    stats = column_stats("d", ["2024-01-05", "2024-01-06", ""])

    assert stats["type"] == DATE
    assert stats["count"] == 2
    assert stats["missing"] == 1
    assert stats["unique"] == 2
    assert "min" not in stats
    assert "top" not in stats


# --- column_stats: edge cases ------------------------------------------------


def test_entirely_empty_column():
    stats = column_stats("blank", ["", "  ", ""])

    assert stats["type"] == TEXT
    assert stats["count"] == 0
    assert stats["missing"] == 3
    assert stats["unique"] == 0
    assert stats["top"] == []


def test_column_with_no_cells():
    stats = column_stats("none", [])

    assert stats["type"] == TEXT
    assert stats["count"] == 0
    assert stats["missing"] == 0
    assert stats["unique"] == 0
    assert stats["top"] == []


def test_stats_result_is_json_serialisable():
    stats = column_stats("x", ["1", "", "3"])

    assert json.loads(json.dumps(stats)) == stats


# --- summarise: files --------------------------------------------------------


def test_summarise_reports_rows_and_each_column(tmp_path):
    path = _write_csv(
        tmp_path,
        "id,name,score,joined,active\n"
        "1,ann,2.5,2024-01-05,true\n"
        "2,bob,,2024-02-01,false\n"
        "3,ann,4.0,,true\n",
    )

    summary = summarise(path)

    assert summary["rows"] == 3
    columns = {column["name"]: column for column in summary["columns"]}
    assert list(columns) == ["id", "name", "score", "joined", "active"]
    assert columns["id"]["type"] == INTEGER
    assert columns["id"]["max"] == 3
    assert columns["name"]["type"] == TEXT
    assert columns["name"]["top"][0] == {"value": "ann", "count": 2}
    assert columns["score"]["type"] == FLOAT
    assert columns["score"]["missing"] == 1
    assert columns["score"]["mean"] == pytest.approx(3.25)
    assert columns["joined"]["type"] == DATE
    assert columns["joined"]["missing"] == 1
    assert columns["active"]["type"] == BOOLEAN


def test_summarise_empty_file_raises(tmp_path):
    path = _write_csv(tmp_path, "")

    with pytest.raises(ValueError, match="empty or has no header"):
        summarise(path)


def test_summarise_blank_lines_only_raises(tmp_path):
    path = _write_csv(tmp_path, "\n\n   \n")

    with pytest.raises(ValueError, match="empty or has no header"):
        summarise(path)


def test_summarise_header_only_file_has_zero_rows(tmp_path):
    path = _write_csv(tmp_path, "a,b,c\n")

    summary = summarise(path)

    assert summary["rows"] == 0
    assert [column["name"] for column in summary["columns"]] == ["a", "b", "c"]
    for column in summary["columns"]:
        assert column["type"] == TEXT
        assert column["count"] == 0
        assert column["missing"] == 0
        assert column["unique"] == 0
        assert column["top"] == []


def test_summarise_column_entirely_empty(tmp_path):
    path = _write_csv(tmp_path, "value,blank\n1,\n2,\n3,\n")

    summary = summarise(path)

    blank = summary["columns"][1]
    assert blank["name"] == "blank"
    assert blank["type"] == TEXT
    assert blank["count"] == 0
    assert blank["missing"] == 3
    assert blank["unique"] == 0


def test_summarise_mixed_column_is_text(tmp_path):
    path = _write_csv(tmp_path, "code\n12\nABC\n12\n")

    column = summarise(path)["columns"][0]

    assert column["type"] == TEXT
    assert column["top"][0] == {"value": "12", "count": 2}


def test_summarise_skips_blank_lines_between_rows(tmp_path):
    path = _write_csv(tmp_path, "n\n1\n\n2\n\n")

    summary = summarise(path)

    assert summary["rows"] == 2
    assert summary["columns"][0]["count"] == 2


def test_summarise_pads_short_rows_with_missing_cells(tmp_path):
    path = _write_csv(tmp_path, "a,b\n1,x\n2\n")

    summary = summarise(path)

    assert summary["rows"] == 2
    b = summary["columns"][1]
    assert b["missing"] == 1
    assert b["count"] == 1


def test_summarise_accepts_utf8_bom_in_header(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes("﻿name,age\nann,30\n".encode())

    summary = summarise(path)

    assert [column["name"] for column in summary["columns"]] == ["name", "age"]


def test_summarise_quoted_fields_with_commas(tmp_path):
    path = _write_csv(tmp_path, 'city,n\n"Paris, France",1\n"Oslo",2\n')

    summary = summarise(path)

    assert summary["columns"][0]["top"][0] == {"value": "Paris, France", "count": 1}
    assert summary["columns"][1]["type"] == INTEGER


def test_summarise_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        summarise(tmp_path / "does-not-exist.csv")


# --- format_table ------------------------------------------------------------


def test_format_table_renders_rows_and_headers(tmp_path):
    path = _write_csv(tmp_path, "n,t\n1,a\n3,a\n")

    text = format_table(summarise(path))

    lines = text.splitlines()
    assert lines[0] == "Rows: 2"
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
        "median",
        "top",
    ]
    assert set(lines[3].replace(" ", "")) == {"-"}
    assert "integer" in text
    assert "a (2)" in text


def test_format_table_shows_dash_for_absent_stats(tmp_path):
    path = _write_csv(tmp_path, "t\nx\n")

    text = format_table(summarise(path))
    row = text.splitlines()[-1]

    assert "text" in row
    assert "-" in row


def test_format_table_header_only_file(tmp_path):
    path = _write_csv(tmp_path, "a,b\n")

    text = format_table(summarise(path))

    assert text.splitlines()[0] == "Rows: 0"


# --- CLI ---------------------------------------------------------------------


def test_cli_prints_table_by_default(tmp_path, capsys):
    path = _write_csv(tmp_path, "x\n1\n2\n")

    exit_code = main([str(path)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith("Rows: 2")
    assert "integer" in out


def test_cli_json_output(tmp_path, capsys):
    path = _write_csv(tmp_path, "x,y\n1,a\n2,a\n")

    exit_code = main([str(path), "--json"])

    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["rows"] == 2
    assert data["columns"][0]["type"] == INTEGER
    assert data["columns"][0]["median"] == pytest.approx(1.5)
    assert data["columns"][1]["top"] == [{"value": "a", "count": 2}]


def test_cli_header_only_file_exits_zero(tmp_path, capsys):
    path = _write_csv(tmp_path, "a,b\n")

    assert main([str(path)]) == 0
    assert "Rows: 0" in capsys.readouterr().out


def test_cli_empty_file_reports_error(tmp_path, capsys):
    path = _write_csv(tmp_path, "")

    exit_code = main([str(path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_cli_missing_file_reports_error(tmp_path, capsys):
    exit_code = main([str(tmp_path / "missing.csv")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.startswith("error: ")


def test_cli_header_only_file_json_output(tmp_path, capsys):
    path = _write_csv(tmp_path, "a,b\n")

    exit_code = main([str(path), "--json"])

    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["rows"] == 0
    assert [column["type"] for column in data["columns"]] == [TEXT, TEXT]
    assert all(column["top"] == [] for column in data["columns"])


def test_cli_empty_file_json_reports_error(tmp_path, capsys):
    path = _write_csv(tmp_path, "")

    exit_code = main([str(path), "--json"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_cli_directory_path_reports_error(tmp_path, capsys):
    exit_code = main([str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_cli_non_utf8_file_reports_error(tmp_path, capsys):
    path = tmp_path / "latin1.csv"
    path.write_bytes("name\ncaf\xe9\n".encode("latin-1"))

    exit_code = main([str(path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith("error: ")


# --- additional coverage: type detection edge cases --------------------------


def test_integer_column_with_float_looking_zero_is_float():
    assert detect_type(["1", "2.0"]) == FLOAT


def test_date_rejects_non_leap_february_29():
    assert detect_type(["2023-02-29"]) == TEXT
    assert detect_type(["2024-02-29", "2023-02-29"]) == TEXT


def test_date_rejects_out_of_range_month_and_day():
    assert detect_type(["2024-13-01"]) == TEXT
    assert detect_type(["2024-00-10"]) == TEXT
    assert detect_type(["2024-01-32"]) == TEXT


def test_date_with_surrounding_whitespace_is_date():
    assert detect_type([" 2024-01-05 ", ""]) == DATE


def test_date_column_mixed_with_text_is_text():
    assert detect_type(["2024-01-05", "2024-01-06", "soon"]) == TEXT


def test_boolean_column_with_empty_cells_stats_ignore_missing():
    stats = column_stats("b", ["", "", "FALSE"])

    assert stats["type"] == BOOLEAN
    assert stats["count"] == 1
    assert stats["missing"] == 2
    assert stats["unique"] == 1


# --- additional coverage: column_stats edge cases ----------------------------


def test_integer_stats_when_all_values_equal():
    stats = column_stats("n", ["7", "7", "7"])

    assert stats["min"] == 7
    assert stats["max"] == 7
    assert stats["mean"] == pytest.approx(7.0)
    assert stats["median"] == 7
    assert stats["unique"] == 1


def test_float_stats_single_value():
    stats = column_stats("x", ["2.5", ""])

    assert stats["type"] == FLOAT
    assert stats["count"] == 1
    assert stats["missing"] == 1
    assert stats["min"] == pytest.approx(2.5)
    assert stats["max"] == pytest.approx(2.5)
    assert stats["median"] == pytest.approx(2.5)


def test_numeric_column_with_only_whitespace_cells_is_text():
    stats = column_stats("x", ["  ", "", "\t"])

    assert stats["type"] == TEXT
    assert stats["count"] == 0
    assert stats["missing"] == 3


def test_text_stats_top_values_when_all_unique():
    stats = column_stats("t", ["c", "a", "b"])

    assert stats["unique"] == 3
    assert [entry["value"] for entry in stats["top"]] == ["c", "a", "b"]
    assert all(entry["count"] == 1 for entry in stats["top"])


# --- additional coverage: summarise file edge cases --------------------------


def test_summarise_trims_header_names(tmp_path):
    path = _write_csv(tmp_path, "  name , age \nann,30\n")

    summary = summarise(path)

    assert [column["name"] for column in summary["columns"]] == ["name", "age"]
    assert summary["columns"][1]["type"] == INTEGER


def test_summarise_ignores_cells_beyond_header(tmp_path):
    path = _write_csv(tmp_path, "a,b\n1,x,extra\n2,y,more,stuff\n")

    summary = summarise(path)

    assert summary["rows"] == 2
    assert [column["name"] for column in summary["columns"]] == ["a", "b"]
    assert summary["columns"][0]["max"] == 2


def test_summarise_bom_only_file_raises(tmp_path):
    path = tmp_path / "bom_only.csv"
    path.write_bytes("﻿".encode())

    with pytest.raises(ValueError, match="empty or has no header"):
        summarise(path)


def test_summarise_rows_of_only_empty_cells_are_skipped_as_blank(tmp_path):
    # A line of only commas has no non-empty cell, so the reader treats it as a
    # blank line and skips it; the row is not counted in "rows".
    path = _write_csv(tmp_path, "a,b\n,\n,\n")

    summary = summarise(path)

    assert summary["rows"] == 0
    for column in summary["columns"]:
        assert column["type"] == TEXT
        assert column["count"] == 0
        assert column["missing"] == 0
        assert column["top"] == []


def test_summarise_partly_empty_rows_count_missing_cells(tmp_path):
    path = _write_csv(tmp_path, "a,b\n1,\n,\n2,\n")

    summary = summarise(path)

    assert summary["rows"] == 2
    a, b = summary["columns"]
    assert a["type"] == INTEGER
    assert a["count"] == 2
    assert a["missing"] == 0
    assert b["type"] == TEXT
    assert b["count"] == 0
    assert b["missing"] == 2


# --- additional coverage: format_table rendering -----------------------------


def test_format_table_renders_float_stats_compactly(tmp_path):
    path = _write_csv(tmp_path, "x\n1.0\n2.0\n5.0\n")

    text = format_table(summarise(path))

    # mean is 8/3 -> formatted with 4 significant digits
    assert "2.667" in text
    assert "float" in text


def test_format_table_all_empty_column_does_not_crash(tmp_path):
    path = _write_csv(tmp_path, "a,b\n1,\n2,\n")

    text = format_table(summarise(path))
    lines = text.splitlines()

    # title, blank line, header, separator, then one line per column
    assert lines[0] == "Rows: 2"
    assert len(lines) == 6
    assert "integer" in text
    assert "text" in lines[-1]
