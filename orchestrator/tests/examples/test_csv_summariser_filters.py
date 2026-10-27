import pytest

from orchestrator.examples.csv_summariser.filters import (
    Condition,
    FilterError,
    apply_filters,
    parse_condition,
)

ROWS = [
    {"name": "Ann", "age": "25", "city": "Paris"},
    {"name": "Bob", "age": "30", "city": "London"},
    {"name": "Cy", "age": "35", "city": "Paris"},
    {"name": "Di", "age": "9", "city": "Rome"},
]


def names(rows):
    return [r["name"] for r in rows]


# --- parse_condition ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("age>30", Condition("age", ">", "30")),
        ("age>=30", Condition("age", ">=", "30")),
        ("age<30", Condition("age", "<", "30")),
        ("age<=30", Condition("age", "<=", "30")),
        ("city=Paris", Condition("city", "=", "Paris")),
        ("city!=Paris", Condition("city", "!=", "Paris")),
        ("  city  =  Paris  ", Condition("city", "=", "Paris")),
        ("note=", Condition("note", "=", "")),
        ("a=b=c", Condition("a", "=", "b=c")),
    ],
)
def test_parse_condition_valid(text, expected):
    assert parse_condition(text) == expected


@pytest.mark.parametrize("text", ["", "age", "=30", ">5", "age~30", "!=x"])
def test_parse_condition_malformed(text):
    with pytest.raises(FilterError, match="Malformed"):
        parse_condition(text)


@pytest.mark.xfail(
    strict=True,
    reason="Known defect: a whitespace-only column name is accepted as a column",
)
def test_parse_condition_whitespace_only_column_is_malformed():
    with pytest.raises(FilterError, match="Malformed"):
        parse_condition("  >=  3")


def test_filter_error_is_value_error():
    assert issubclass(FilterError, ValueError)


# --- operators ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("cond", "expected"),
    [
        ("age=30", ["Bob"]),
        ("age!=30", ["Ann", "Cy", "Di"]),
        ("age>30", ["Cy"]),
        ("age>=30", ["Bob", "Cy"]),
        ("age<30", ["Ann", "Di"]),
        ("age<=30", ["Ann", "Bob", "Di"]),
        ("city=Paris", ["Ann", "Cy"]),
        ("city!=Paris", ["Bob", "Di"]),
    ],
)
def test_each_operator(cond, expected):
    assert names(apply_filters(ROWS, [parse_condition(cond)])) == expected


# --- numeric vs text ---------------------------------------------------------


def test_numeric_comparison_not_lexicographic():
    # As text "9" > "30"; numerically it is not.
    assert names(apply_filters(ROWS, [parse_condition("age>10")])) == [
        "Ann",
        "Bob",
        "Cy",
    ]


def test_numeric_equality_across_formats():
    rows = [{"v": "30.0"}, {"v": "30"}, {"v": "3e1"}, {"v": "31"}]
    assert len(apply_filters(rows, [parse_condition("v=30")])) == 3


def test_text_comparison_when_not_numeric():
    rows = [{"c": "apple"}, {"c": "banana"}, {"c": "cherry"}]
    assert apply_filters(rows, [parse_condition("c>banana")]) == [{"c": "cherry"}]
    assert apply_filters(rows, [parse_condition("c<=banana")]) == rows[:2]


def test_text_comparison_when_cell_not_numeric_but_value_is():
    rows = [{"v": "abc"}, {"v": "5"}]
    # "abc" vs "5" is compared as text: "abc" > "5"
    assert apply_filters(rows, [parse_condition("v>5")]) == [{"v": "abc"}]


def test_text_comparison_is_case_sensitive():
    rows = [{"c": "paris"}, {"c": "Paris"}]
    assert apply_filters(rows, [parse_condition("c=Paris")]) == [{"c": "Paris"}]


@pytest.mark.parametrize("word", ["nan", "inf", "-inf"])
def test_nan_and_inf_treated_as_text(word):
    rows = [{"v": word}, {"v": "1"}]
    assert apply_filters(rows, [parse_condition("v=" + word)]) == [{"v": word}]


def test_empty_cell_compared_as_text():
    rows = [{"v": ""}, {"v": "x"}]
    assert apply_filters(rows, [parse_condition("v=")]) == [{"v": ""}]


def test_none_cell_treated_as_empty():
    rows = [{"v": None}, {"v": "x"}]
    assert apply_filters(rows, [parse_condition("v=")]) == [{"v": None}]


# --- combining / edge cases --------------------------------------------------


def test_all_conditions_must_match():
    conds = [parse_condition("city=Paris"), parse_condition("age>=30")]
    assert names(apply_filters(ROWS, conds)) == ["Cy"]


def test_no_conditions_returns_all_rows():
    assert apply_filters(ROWS, []) == ROWS


def test_no_rows_returns_empty():
    assert apply_filters([], [parse_condition("age>1")]) == []


def test_accepts_generator_and_returns_list():
    result = apply_filters((r for r in ROWS), [parse_condition("age>30")])
    assert isinstance(result, list)
    assert names(result) == ["Cy"]


def test_input_rows_not_mutated():
    snapshot = [dict(r) for r in ROWS]
    apply_filters(ROWS, [parse_condition("age>30")])
    assert ROWS == snapshot


# --- errors ------------------------------------------------------------------


def test_unknown_column_raises():
    with pytest.raises(FilterError, match="Unknown column 'zip'"):
        apply_filters(ROWS, [parse_condition("zip=1")])


def test_unknown_column_raises_even_if_earlier_condition_fails():
    conds = [parse_condition("age>1000"), parse_condition("zip=1")]
    with pytest.raises(FilterError, match="zip"):
        apply_filters(ROWS, conds)


def test_unknown_column_not_raised_without_rows():
    assert apply_filters([], [parse_condition("zip=1")]) == []


def test_column_name_is_case_sensitive():
    with pytest.raises(FilterError):
        apply_filters(ROWS, [parse_condition("Age>1")])
