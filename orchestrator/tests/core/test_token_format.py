import pytest

from orchestrator.app.core.token_format import format_tokens


def test_none_is_not_available() -> None:
    assert format_tokens(None) == "n/a"


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "0"),
        (1, "1"),
        (999, "999"),
    ],
)
def test_counts_below_one_thousand_are_shown_as_is(count: int, expected: str) -> None:
    assert format_tokens(count) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1000, "1.0K"),
        (1200, "1.2K"),
        (1250, "1.2K"),  # decimal is truncated, not rounded
        (15000, "15.0K"),
        (999_000, "999.0K"),
        (999_999, "999.9K"),  # must not roll over to "1000.0K"
    ],
)
def test_thousands_use_one_decimal_and_k_suffix(count: int, expected: str) -> None:
    assert format_tokens(count) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1_000_000, "1.0M"),
        (1_050_000, "1.0M"),
        (2_500_000, "2.5M"),
        (12_345_678, "12.3M"),
        (1_000_000_000, "1000.0M"),
    ],
)
def test_millions_use_one_decimal_and_m_suffix(count: int, expected: str) -> None:
    assert format_tokens(count) == expected


def test_boundary_just_below_one_million_stays_in_thousands() -> None:
    assert format_tokens(999_999) == "999.9K"
    assert format_tokens(1_000_000) == "1.0M"


def test_boundary_just_below_one_thousand_stays_as_is() -> None:
    assert format_tokens(999) == "999"
    assert format_tokens(1000) == "1.0K"


@pytest.mark.parametrize("count", [-1, -999, -1_000, -2_500_000])
def test_negative_counts_are_rejected(count: int) -> None:
    with pytest.raises(ValueError):
        format_tokens(count)
