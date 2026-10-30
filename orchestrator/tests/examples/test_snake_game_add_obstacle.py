"""Tests for GameState.add_obstacle and obstacles appearing as the level rises."""

from __future__ import annotations

import os

import pytest

from orchestrator.examples.snake_game.engine import (
    MAX_OBSTACLES,
    Direction,
    GameState,
    level_for_score,
)


def _front(state: GameState) -> set[tuple[int, int]]:
    hx, hy = state.snake[0]
    dx, dy = state.direction.value
    return {(hx + dx, hy + dy), (hx + 2 * dx, hy + 2 * dy)}


def test_max_obstacles_is_eight():
    assert MAX_OBSTACLES == 8


def test_fresh_game_has_no_obstacles():
    state = GameState(seed=1)
    assert state.obstacles == set()
    assert state.obstacle_count == 0


def test_new_game_has_no_obstacles():
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pytest.importorskip("pygame")
    from orchestrator.examples.snake_game import app

    assert app.new_game().obstacles == set()


def test_add_obstacle_returns_cell_and_records_it():
    state = GameState(seed=3)
    cell = state.add_obstacle()
    assert cell is not None
    assert state.obstacles == {cell}
    assert state.obstacle_count == 1
    assert 0 <= cell[0] < state.width and 0 <= cell[1] < state.height


@pytest.mark.parametrize("seed", range(30))
def test_added_obstacle_avoids_forbidden_cells(seed):
    state = GameState(seed=seed)
    for _ in range(MAX_OBSTACLES):
        before = set(state.obstacles)
        cell = state.add_obstacle()
        assert cell is not None
        assert cell not in set(state.snake)
        assert cell != state.food
        assert cell not in before
        assert cell not in _front(state)


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN, Direction.LEFT])
def test_front_cells_avoided_after_turn(direction):
    dx, dy = direction.value
    for seed in range(40):
        s = GameState(seed=seed)
        s.direction = direction
        s._pending_direction = direction
        hx, hy = s.snake[0]
        front = {(hx + dx, hy + dy), (hx + 2 * dx, hy + 2 * dy)}
        cell = s.add_obstacle()
        assert cell is not None
        assert cell not in front


def test_limit_reached_returns_none():
    state = GameState(seed=2)
    for _ in range(MAX_OBSTACLES):
        assert state.add_obstacle() is not None
    assert len(state.obstacles) == MAX_OBSTACLES
    snapshot = set(state.obstacles)
    assert state.add_obstacle() is None
    assert state.obstacles == snapshot


def test_limit_counts_constructor_obstacles():
    state = GameState(seed=2, obstacle_count=MAX_OBSTACLES)
    assert state.add_obstacle() is None
    assert len(state.obstacles) == MAX_OBSTACLES


def test_no_free_cell_returns_none_on_full_grid():
    state = GameState(width=3, height=1, seed=1)
    assert state.add_obstacle() is None
    assert state.obstacles == set()


def test_no_free_cell_when_only_front_cells_remain():
    # 5x1: snake at x=0..2 heading right; free cells 3 and 4 are the front cells.
    state = GameState(width=5, height=1, seed=1)
    assert state.add_obstacle() is None
    assert state.obstacles == set()


def test_no_free_cell_when_food_takes_last_one():
    state = GameState(width=6, height=1, seed=1)
    # head x=3, snake x=1..3; front cells x=4,5; the only other free cell is x=0.
    state.food = (0, 0)
    assert state.add_obstacle() is None
    state.food = (4, 0)
    assert state.add_obstacle() == (0, 0)


def test_same_seed_same_obstacle_positions():
    def run(seed):
        s = GameState(seed=seed)
        return [s.add_obstacle() for _ in range(MAX_OBSTACLES)]

    assert run(42) == run(42)
    assert run(42) != run(43)


def test_constructor_obstacle_behaviour_unchanged():
    a = GameState(seed=9, obstacle_count=4)
    b = GameState(seed=9, obstacle_count=4)
    assert len(a.obstacles) == 4
    assert a.obstacles == b.obstacles
    assert a.food == b.food
    assert a.food not in a.obstacles


def test_add_obstacle_on_top_of_constructor_obstacles():
    state = GameState(seed=9, obstacle_count=3)
    existing = set(state.obstacles)
    cell = state.add_obstacle()
    assert cell is not None and cell not in existing
    assert len(state.obstacles) == 4


def test_pause_does_not_add_obstacles():
    state = GameState(seed=1)
    state.toggle_pause()
    for _ in range(10):
        state.step()
    state.toggle_pause()
    assert state.obstacles == set()


def test_level_up_loop_adds_one_obstacle_per_level_up_to_limit():
    """Mimic app.run(): call add_obstacle only when the level increases."""
    state = GameState(width=40, height=40, seed=7)
    level = level_for_score(state.score)
    for score in range(1, 80):
        state.score = score
        new_level = level_for_score(score)
        if new_level > level:
            state.add_obstacle()
        level = new_level
        assert len(state.obstacles) == min(level - 1, MAX_OBSTACLES)
    assert len(state.obstacles) == MAX_OBSTACLES


def test_obstacle_never_covers_the_bonus_food():
    state = GameState(width=6, height=1, seed=1)
    # Same layout as above: x=0 is the only cell an obstacle could take.
    state.food = (4, 0)
    state.bonus_food = (0, 0)
    assert state.add_obstacle() is None
    assert state.obstacles == set()
    state.bonus_food = None
    assert state.add_obstacle() == (0, 0)
