"""Tests for the food streak tracking in ``GameState``."""

from __future__ import annotations

from orchestrator.examples.snake_game.engine import Direction, GameState


def _eat_ahead(state: GameState, bonus: bool = False) -> None:
    dx, dy = state.direction.value
    hx, hy = state.snake[0]
    cell = (hx + dx, hy + dy)
    if bonus:
        state.bonus_food = cell
        state.bonus_steps_left = 10
    else:
        state.food = cell
    state.step()


def test_streak_starts_at_zero() -> None:
    state = GameState(seed=1)
    assert state.current_streak == 0
    assert state.longest_streak == 0


def test_constructor_accepts_longest_streak() -> None:
    state = GameState(seed=1, longest_streak=7)
    assert state.longest_streak == 7
    assert state.current_streak == 0


def test_eating_n_foods_gives_streak_n() -> None:
    state = GameState(seed=1)
    for n in range(1, 4):
        _eat_ahead(state)
        assert state.current_streak == n
        assert state.longest_streak == n


def test_bonus_food_counts() -> None:
    state = GameState(seed=1)
    _eat_ahead(state)
    _eat_ahead(state, bonus=True)
    assert state.current_streak == 2
    assert state.longest_streak == 2


def test_moving_without_eating_keeps_streak() -> None:
    state = GameState(seed=1)
    _eat_ahead(state)
    state.food = (0, 0)
    state.step()
    assert state.current_streak == 1


def test_wall_hit_resets_current_keeps_longest() -> None:
    state = GameState(width=10, height=10, seed=1)
    _eat_ahead(state)
    _eat_ahead(state)
    state.food = (0, 0)
    for _ in range(20):
        state.step()
    assert state.game_over
    assert state.current_streak == 0
    assert state.longest_streak == 2


def test_longest_streak_not_lowered_by_smaller_streak() -> None:
    state = GameState(seed=1, longest_streak=5)
    _eat_ahead(state)
    assert state.current_streak == 1
    assert state.longest_streak == 5


def test_longest_streak_grows_past_carried_value() -> None:
    state = GameState(seed=1, longest_streak=1)
    _eat_ahead(state)
    _eat_ahead(state)
    assert state.longest_streak == 2


def test_wrap_mode_never_resets_streak() -> None:
    state = GameState(width=10, height=10, seed=1, wrap=True)
    _eat_ahead(state)
    state.food = (0, 0)
    for _ in range(25):
        state.step()
    assert not state.game_over
    assert state.current_streak == 1


def test_self_collision_does_not_reset_streak() -> None:
    state = GameState(width=10, height=10, seed=1)
    for _ in range(4):
        _eat_ahead(state)
    state.food = (0, 0)
    for d in (Direction.DOWN, Direction.LEFT, Direction.UP):
        state.change_direction(d)
        state.step()
    assert state.game_over
    assert state.current_streak == 4


def test_obstacle_hit_does_not_reset_streak() -> None:
    state = GameState(width=10, height=10, seed=1)
    _eat_ahead(state)
    hx, hy = state.snake[0]
    state.obstacles.add((hx + 1, hy))
    state.food = (0, 0)
    state.step()
    assert state.game_over
    assert state.current_streak == 1
