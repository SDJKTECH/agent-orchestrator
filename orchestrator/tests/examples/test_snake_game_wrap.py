import pytest

from orchestrator.examples.snake_game.engine import Direction, GameState


def make(wrap, snake, direction, food=(3, 3), w=6, h=5) -> GameState:
    g = GameState(width=w, height=h, seed=1, wrap=wrap)
    g.snake = list(snake)
    g.direction = g._pending_direction = direction
    g.food = food
    return g


EDGES = [
    (Direction.RIGHT, [(5, 2), (4, 2), (3, 2)], (0, 2)),
    (Direction.LEFT, [(0, 2), (1, 2), (2, 2)], (5, 2)),
    (Direction.UP, [(2, 0), (2, 1), (2, 2)], (2, 4)),
    (Direction.DOWN, [(2, 4), (2, 3), (2, 2)], (2, 0)),
]


def test_wrap_default_off():
    assert GameState(seed=1).wrap is False
    assert GameState(seed=1, wrap=True).wrap is True


@pytest.mark.parametrize("direction,snake,expected", EDGES)
def test_wrap_on_each_edge(direction, snake, expected):
    g = make(True, snake, direction, food=(0, 0))
    g.step()
    assert not g.game_over
    assert g.snake[0] == expected
    assert len(g.snake) == 3


@pytest.mark.parametrize("direction,snake,expected", EDGES)
def test_wrap_off_dies_on_each_edge(direction, snake, expected):
    g = make(False, snake, direction)
    g.step()
    assert g.game_over
    assert g.snake == snake


def test_wrap_onto_vacated_tail_is_allowed():
    snake = [(5, 0), (4, 0), (3, 0), (2, 0), (1, 0), (0, 0)]
    g = make(True, snake, Direction.RIGHT)
    g.step()
    assert not g.game_over
    assert g.snake[0] == (0, 0)


def test_wrap_onto_tail_while_eating_is_collision():
    snake = [(5, 0), (4, 0), (3, 0), (2, 0), (1, 0), (0, 0)]
    g = make(True, snake, Direction.RIGHT, food=(0, 0))
    g.step()
    assert g.game_over


def test_wrap_self_collision_with_body_ends_game():
    g = make(True, [(5, 2), (4, 2), (0, 2), (1, 2), (2, 2)], Direction.RIGHT)
    g.step()
    assert g.game_over


def test_wrap_food_and_scoring():
    g = make(True, [(5, 2), (4, 2), (3, 2)], Direction.RIGHT, food=(0, 2))
    g.step()
    assert g.score == 1 and len(g.snake) == 4
    assert g.snake[0] == (0, 2)
    assert g.food is not None and g.food not in g.snake


def test_wrap_food_placement_in_bounds_and_free():
    g = GameState(width=5, height=4, seed=3, wrap=True)
    assert g.food not in g.snake
    assert 0 <= g.food[0] < 5 and 0 <= g.food[1] < 4


def test_wrap_full_loop_returns_to_start():
    g = GameState(width=6, height=5, seed=1, wrap=True)
    g.food = None
    start = list(g.snake)
    for _ in range(6):
        g.step()
    assert g.snake == start and not g.game_over
