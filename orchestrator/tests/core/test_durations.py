import math

import pytest

from orchestrator.app.core.durations import format_duration


def test_none_is_not_available() -> None:
    assert format_duration(None) == "n/a"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0.0s"),
        (0.0, "0.0s"),
        (0.04, "0.0s"),
        (5.25, "5.2s"),  # exact binary half rounds to even
        (5.35, "5.3s"),
        (1, "1.0s"),
        (42, "42.0s"),
        (59.9, "59.9s"),
        (59.94, "59.9s"),
    ],
)
def test_under_a_minute_uses_one_decimal_place(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (60, "1m 0s"),
        (60.0, "1m 0s"),
        (61, "1m 1s"),
        (125, "2m 5s"),
        (3599, "59m 59s"),
        (3599.9, "59m 59s"),  # fractional seconds are truncated
    ],
)
def test_minutes_and_seconds_from_one_minute_to_one_hour(
    seconds: float, expected: str
) -> None:
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (3600, "1h 0m"),
        (3720, "1h 2m"),
        (3659, "1h 0m"),  # trailing seconds are dropped
        (7325, "2h 2m"),
        (86400, "24h 0m"),
        (3600.5, "1h 0m"),
    ],
)
def test_hours_and_minutes_from_one_hour_upwards(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_boundary_just_below_hour_stays_in_minutes() -> None:
    assert format_duration(3599.99) == "59m 59s"


@pytest.mark.parametrize("seconds", [-1, -0.5, -3600])
def test_negative_durations_are_rejected(seconds: float) -> None:
    with pytest.raises(ValueError):
        format_duration(seconds)


@pytest.mark.parametrize("seconds", [math.nan, math.inf, -math.inf])
def test_non_finite_durations_are_rejected(seconds: float) -> None:
    with pytest.raises(ValueError):
        format_duration(seconds)
