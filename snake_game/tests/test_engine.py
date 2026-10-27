import pytest

from snake_game.engine import Direction, GameState


def make(food=(0, 0), **kw) -> GameState:
    g = GameState(**kw)
    g.food = food
    return g


def set_snake(g: GameState, cells, direction=Direction.RIGHT) -> None:
    g.snake = list(cells)
    g.direction = g._pending_direction = direction


def test_defaults():
    g = GameState(seed=1)
    assert (g.width, g.height) == (20, 20)
    assert len(g.snake) == 3
    assert g.score == 0 and not g.game_over
    assert g.food not in g.snake


def test_invalid_grid():
    with pytest.raises(ValueError):
        GameState(width=2)


def test_moves_right_one_cell():
    g = make()
    head = g.snake[0]
    old_len = len(g.snake)
    g.step()
    assert g.snake[0] == (head[0] + 1, head[1])
    assert len(g.snake) == old_len


@pytest.mark.parametrize(
    "d,delta",
    [(Direction.UP, (0, -1)), (Direction.DOWN, (0, 1)), (Direction.RIGHT, (1, 0))],
)
def test_turns(d, delta):
    g = make()
    head = g.snake[0]
    g.change_direction(d)
    g.step()
    assert g.snake[0] == (head[0] + delta[0], head[1] + delta[1])


def test_eating_grows_scores_and_replaces_food():
    g = GameState(seed=3)
    head = g.snake[0]
    g.food = (head[0] + 1, head[1])
    g.step()
    assert len(g.snake) == 4
    assert g.score == 1
    assert g.food is not None and g.food not in g.snake
    assert not g.game_over


def test_wall_collision_right():
    g = make(width=5, height=5)
    for _ in range(10):
        g.step()
    assert g.game_over


@pytest.mark.parametrize(
    "cells,direction",
    [
        ([(2, 0), (1, 0), (0, 0)], Direction.UP),
        ([(2, 4), (1, 4), (0, 4)], Direction.DOWN),
        ([(0, 2), (1, 2), (2, 2)], Direction.LEFT),
    ],
)
def test_wall_collision_other_sides(cells, direction):
    g = make(width=5, height=5)
    set_snake(g, cells, direction)
    g.step()
    assert g.game_over
    assert g.snake == cells


def test_step_after_game_over_is_noop():
    g = make(width=5, height=5)
    for _ in range(10):
        g.step()
    snap = list(g.snake)
    g.step()
    assert g.snake == snap


def test_self_collision():
    g = make(width=10, height=10)
    set_snake(g, [(5, 5), (5, 6), (6, 6), (6, 5), (6, 4)], Direction.UP)
    g.change_direction(Direction.RIGHT)
    g.step()  # new head (6,5) is part of the body
    assert g.game_over


def test_moving_into_vacating_tail_is_safe():
    g = make(width=10, height=10, food=(9, 9))
    set_snake(g, [(5, 5), (5, 6), (4, 6), (4, 5)], Direction.UP)
    g.change_direction(Direction.LEFT)
    g.step()  # (4,5) is the tail, which vacates this step
    assert not g.game_over


@pytest.mark.parametrize(
    "first,second",
    [(Direction.RIGHT, Direction.LEFT), (Direction.UP, Direction.DOWN)],
)
def test_reversal_ignored(first, second):
    g = make()
    g.change_direction(first)
    g.step()
    g.change_direction(second)
    head = g.snake[0]
    g.step()
    assert not g.game_over
    assert g.direction == first
    dx, dy = first.value
    assert g.snake[0] == (head[0] + dx, head[1] + dy)


def test_reversal_ignored_initially():
    g = make()
    g.change_direction(Direction.LEFT)
    g.step()
    assert g.direction == Direction.RIGHT and not g.game_over


def test_seed_repeatable():
    assert GameState(seed=42).food == GameState(seed=42).food
    a, b = GameState(seed=5), GameState(seed=5)
    for _ in range(3):
        a.food = b.food = (a.snake[0][0] + 1, a.snake[0][1])
        a.step()
        b.step()
        assert a.food == b.food


def test_food_never_on_snake_over_long_run():
    g = GameState(width=6, height=6, seed=7)
    for _ in range(300):
        if g.game_over:
            break
        head = g.snake[0]
        dx, dy = g.direction.value
        nxt = (head[0] + dx, head[1] + dy)
        if 0 <= nxt[0] < 6 and 0 <= nxt[1] < 6 and nxt not in g.snake:
            g.food = nxt
        g.step()
        if g.food is not None:
            assert g.food not in g.snake
    assert g.score > 0


def test_board_full_ends_game():
    g = GameState(width=4, height=1, seed=0)
    set_snake(g, [(2, 0), (1, 0), (0, 0)])
    g.food = (3, 0)
    g.step()  # eats the last free cell
    assert g.score == 1
    assert g.food is None
    assert g.game_over
