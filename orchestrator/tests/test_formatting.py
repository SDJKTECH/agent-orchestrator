import math

import pytest

from orchestrator.app.formatting import format_cost


def test_none_returns_not_available():
    assert format_cost(None) == "n/a"


def test_zero_returns_two_decimal_zero():
    assert format_cost(0) == "$0.00"
    assert format_cost(0.0) == "$0.00"


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (1.5, "$1.50"),
        (12.5, "$12.50"),
        (100, "$100.00"),
    ],
)
def test_amounts_of_at_least_a_cent_use_two_decimals(amount, expected):
    assert format_cost(amount) == expected


def test_exact_cent_boundary_uses_two_decimals():
    assert format_cost(0.01) == "$0.01"


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (0.0042, "$0.0042"),
        (0.005, "$0.0050"),
        (0.0001, "$0.0001"),
    ],
)
def test_positive_amounts_below_a_cent_use_four_decimals(amount, expected):
    assert format_cost(amount) == expected


def test_just_below_cent_boundary_uses_four_decimals():
    assert format_cost(0.0099) == "$0.0099"


@pytest.mark.parametrize("amount", [-0.01, -0.0042, -1.0])
def test_negative_amounts_raise_value_error(amount):
    with pytest.raises(ValueError):
        format_cost(amount)


@pytest.mark.parametrize("amount", [math.nan, math.inf, -math.inf])
def test_non_finite_amounts_raise_value_error(amount):
    with pytest.raises(ValueError):
        format_cost(amount)
