import pytest

from orchestrator.examples.snake_game.engine import (
    BASE_STEPS_PER_SECOND,
    MAX_STEPS_PER_SECOND,
    POINTS_PER_LEVEL,
    level_for_score,
    speed_for_level,
)


@pytest.mark.parametrize(
    "score,level",
    [(0, 1), (1, 1), (4, 1), (5, 2), (9, 2), (10, 3), (24, 5), (25, 6), (-3, 1)],
)
def test_level_for_score(score, level):
    assert level_for_score(score) == level


def test_points_per_level_is_five():
    assert POINTS_PER_LEVEL == 5


def test_level_one_uses_base_speed():
    assert speed_for_level(1) == BASE_STEPS_PER_SECOND


def test_speed_increases_with_level():
    assert speed_for_level(2) > speed_for_level(1)
    assert speed_for_level(3) > speed_for_level(2)


def test_speed_is_capped():
    assert speed_for_level(1000) == MAX_STEPS_PER_SECOND
    speeds = [speed_for_level(lv) for lv in range(1, 100)]
    assert max(speeds) == MAX_STEPS_PER_SECOND
    assert speeds == sorted(speeds)


def test_speed_below_level_one_clamped():
    assert speed_for_level(0) == BASE_STEPS_PER_SECOND
    assert speed_for_level(-5) == BASE_STEPS_PER_SECOND
