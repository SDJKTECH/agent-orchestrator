from datetime import date

import pytest

from orchestrator.examples.csv_summariser.stats import (
    DATE,
    FLOAT,
    INTEGER,
    TEXT,
    ColumnSummary,
    detect_type,
    read_rows,
    summarise_columns,
)

# --- detect_type -------------------------------------------------------------


@pytest.mark.parametrize("values", [["1", "2", "3"], ["+3", "-4", "0"], [" 42 "]])
def test_detect_type_integer(values):
    assert detect_type(values) == INTEGER


@pytest.mark.parametrize(
    "values",
    [["1.5", "2.25"], ["1e3", "2E-2"], [".5", "2."], ["-0.0", "7"]],
)
def test_detect_type_float(values):
    assert detect_type(values) == FLOAT


def test_detect_type_mixed_integer_and_float_is_float():
    assert detect_type(["1", "2.5", "3"]) == FLOAT


@pytest.mark.parametrize(
    "values",
    [
        ["2024-01-31"],
        ["2024-01-01", "1999-12-31"],
        ["2024-01-31T10:30:00"],
        ["2024-01-31 10:30:00+02:00"],
    ],
)
def test_detect_type_iso_date(values):
    assert detect_type(values) == DATE


@pytest.mark.parametrize(
    "values",
    [
        ["hello"],
        ["2024-02-30"],  # not a real calendar day
        ["31/01/2024"],  # not ISO 8601
        ["nan"],
        ["inf"],
        ["1_000"],
        ["1,000"],
        ["12abc"],
    ],
)
def test_detect_type_falls_back_to_text(values):
    assert detect_type(values) == TEXT


@pytest.mark.parametrize(
    "values",
    [
        ["1", "two"],  # integer + text
        ["1.5", "x"],  # float + text
        ["2024-01-01", "not a date"],  # date + text
        ["2024-01-01", "3"],  # date + integer
        ["2024-01-01", "1.5"],  # date + float
    ],
)
def test_detect_type_mixed_columns_are_text(values):
    assert detect_type(values) == TEXT


def test_detect_type_with_no_values_is_text():
    assert detect_type([]) == TEXT


# --- numeric statistics ------------------------------------------------------


def test_integer_column_statistics_odd_count():
    [summary] = summarise_columns([["n"], ["3"], ["1"], ["2"], ["10"], ["4"]])

    assert summary == ColumnSummary(
        name="n",
        type=INTEGER,
        count=5,
        missing=0,
        min=1,
        max=10,
        mean=4.0,
        median=3.0,
    )


def test_integer_median_of_even_count_is_average_of_middle_pair():
    [summary] = summarise_columns([["n"], ["1"], ["2"], ["3"], ["4"]])

    assert summary.median == 2.5
    assert summary.mean == 2.5


def test_integer_min_and_max_are_ints():
    [summary] = summarise_columns([["n"], ["-7"], ["12"]])

    assert summary.min == -7 and isinstance(summary.min, int)
    assert summary.max == 12 and isinstance(summary.max, int)


def test_float_column_statistics():
    [summary] = summarise_columns([["x"], ["1.5"], ["2.5"], ["-1"], ["4"]])

    assert summary.type == FLOAT
    assert summary.count == 4
    assert summary.missing == 0
    assert summary.min == -1.0
    assert summary.max == 4.0
    assert summary.mean == pytest.approx(1.75)
    assert summary.median == pytest.approx(2.0)


def test_numeric_column_ignores_surrounding_whitespace():
    [summary] = summarise_columns([["n"], [" 2 "], ["4"]])

    assert summary.type == INTEGER
    assert summary.min == 2
    assert summary.max == 4
    assert summary.mean == 3.0


def test_numeric_statistics_leave_text_fields_unset():
    [summary] = summarise_columns([["n"], ["1"]])

    assert summary.distinct is None
    assert summary.top_values is None


# --- date statistics ---------------------------------------------------------


def test_date_column_reports_earliest_and_latest_as_iso_strings():
    [summary] = summarise_columns(
        [["when"], ["2024-03-01"], ["2023-12-31"], ["2024-01-15"], [""]]
    )

    assert summary.type == DATE
    assert summary.count == 3
    assert summary.missing == 1
    assert summary.min == "2023-12-31"
    assert summary.max == "2024-03-01"
    assert summary.mean is None
    assert summary.median is None


def test_datetime_values_are_reduced_to_their_date():
    [summary] = summarise_columns([["when"], ["2024-05-02T23:59:00"], ["2024-05-01"]])

    assert summary.type == DATE
    assert summary.min == "2024-05-01"
    assert summary.max == "2024-05-02"
    assert date.fromisoformat(summary.max) == date(2024, 5, 2)


# --- text statistics ---------------------------------------------------------


def test_text_column_count_missing_and_distinct():
    [summary] = summarise_columns(
        [["colour"], ["red"], ["blue"], [""], ["red"], ["green"]]
    )

    assert summary.type == TEXT
    assert summary.count == 4
    assert summary.missing == 1
    assert summary.distinct == 3
    assert summary.min is None
    assert summary.max is None
    assert summary.mean is None
    assert summary.median is None


def test_text_top_values_are_three_most_common_with_counts():
    rows = [["v"]] + [[v] for v in ["a", "b", "a", "c", "a", "b", "d", "b", "b", "e"]]
    [summary] = summarise_columns(rows)

    assert summary.top_values == (("b", 4), ("a", 3), ("c", 1))


def test_text_top_values_break_ties_by_first_appearance():
    rows = [["v"]] + [[v] for v in ["z", "y", "x", "y", "x", "z"]]
    [summary] = summarise_columns(rows)

    assert summary.top_values == (("z", 2), ("y", 2), ("x", 2))


def test_text_top_values_with_fewer_than_three_distinct_values():
    [summary] = summarise_columns([["v"], ["only"], ["only"]])

    assert summary.top_values == (("only", 2),)
    assert summary.distinct == 1


def test_text_top_values_exclude_missing_values():
    [summary] = summarise_columns([["v"], [""], [""], [""], ["x"]])

    assert summary.top_values == (("x", 1),)
    assert summary.missing == 3
    assert summary.count == 1


def test_text_values_are_compared_without_stripping():
    [summary] = summarise_columns([["v"], ["a"], [" a"], ["a"]])

    assert summary.distinct == 2
    assert summary.top_values == (("a", 2), (" a", 1))


# --- missing values ----------------------------------------------------------


def test_empty_strings_are_missing_for_numeric_columns():
    [summary] = summarise_columns([["n"], ["1"], [""], ["3"], [""]])

    assert summary.type == INTEGER
    assert summary.count == 2
    assert summary.missing == 2
    assert summary.mean == 2.0
    assert summary.median == 2.0


def test_column_of_only_empty_strings_has_no_values():
    [summary] = summarise_columns([["n"], [""], [""]])

    assert summary == ColumnSummary(
        name="n",
        type=TEXT,
        count=0,
        missing=2,
        distinct=0,
        top_values=(),
    )


# --- edge cases --------------------------------------------------------------


def test_empty_input_yields_no_columns():
    assert summarise_columns([]) == []


def test_header_only_yields_columns_without_values():
    summaries = summarise_columns([["id", "name"]])

    assert [(s.name, s.type, s.count, s.missing) for s in summaries] == [
        ("id", TEXT, 0, 0),
        ("name", TEXT, 0, 0),
    ]
    assert summaries[0].top_values == ()
    assert summaries[0].distinct == 0


def test_short_rows_are_padded_with_missing_values():
    summaries = summarise_columns(
        [["a", "b", "c"], ["1", "x", "2024-01-01"], ["2"], ["3", "y"]]
    )

    a, b, c = summaries
    assert (a.type, a.count, a.missing) == (INTEGER, 3, 0)
    assert (b.type, b.count, b.missing) == (TEXT, 2, 1)
    assert (c.type, c.count, c.missing) == (DATE, 1, 2)
    assert c.min == c.max == "2024-01-01"


def test_long_rows_ignore_fields_beyond_header_width():
    summaries = summarise_columns([["a"], ["1", "999"], ["2", "888", "777"]])

    assert len(summaries) == 1
    assert summaries[0].type == INTEGER
    assert summaries[0].count == 2
    assert summaries[0].max == 2


def test_mixed_type_column_falls_back_to_text():
    [summary] = summarise_columns([["v"], ["1"], ["2"], ["three"], ["1"], [""]])

    assert summary.type == TEXT
    assert summary.count == 4
    assert summary.missing == 1
    assert summary.min is None
    assert summary.mean is None
    assert summary.distinct == 3
    assert summary.top_values == (("1", 2), ("2", 1), ("three", 1))


def test_one_summary_per_header_column_in_order():
    summaries = summarise_columns(
        [["id", "score", "when", "label"], ["1", "0.5", "2024-01-01", "a"]]
    )

    assert [s.name for s in summaries] == ["id", "score", "when", "label"]
    assert [s.type for s in summaries] == [INTEGER, FLOAT, DATE, TEXT]


def test_accepts_tuple_rows():
    [summary] = summarise_columns((("n",), ("4",), ("6",)))

    assert summary.mean == 5.0


def test_blank_header_name_keeps_column_position():
    summaries = summarise_columns([["", "x"], ["1", ""]])

    assert [s.name for s in summaries] == ["", "x"]
    assert summaries[0].type == INTEGER
    assert summaries[1].missing == 1


# --- read_rows ---------------------------------------------------------------


def test_read_rows_parses_quoted_fields_and_strips_bom(tmp_path):
    path = tmp_path / "data.csv"
    path.write_bytes(
        b'\xef\xbb\xbfname,note\r\nann,"hello, world"\r\nbob,"multi\nline"\r\n'
    )

    assert read_rows(path) == [
        ["name", "note"],
        ["ann", "hello, world"],
        ["bob", "multi\nline"],
    ]


def test_read_rows_of_empty_file_is_empty_list(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")

    assert read_rows(path) == []
    assert summarise_columns(read_rows(path)) == []


def test_read_rows_of_header_only_file(tmp_path):
    path = tmp_path / "header.csv"
    path.write_text("a,b\n", encoding="utf-8")

    summaries = summarise_columns(read_rows(path))

    assert [s.name for s in summaries] == ["a", "b"]
    assert all(s.count == 0 for s in summaries)
