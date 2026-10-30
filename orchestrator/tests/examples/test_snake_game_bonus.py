"""Tests for the bonus food rule in the Snake Game engine."""

from orchestrator.examples.snake_game.engine import (
    BONUS_EVERY,
    BONUS_LIFETIME_STEPS,
    BONUS_POINTS,
    Direction,
    GameState,
)


def _state(**kwargs) -> GameState:
    return GameState(60, 5, seed=1, **kwargs)


def _eat_normal(state: GameState) -> None:
    hx, hy = state.snake[0]
    state.food = (hx + 1, hy)
    state.step()


def _state_with_bonus() -> GameState:
    state = _state()
    for _ in range(BONUS_EVERY):
        _eat_normal(state)
    assert state.bonus_food is not None
    return state


def test_no_bonus_initially():
    state = _state()
    assert state.bonus_food is None


def test_no_bonus_before_fifth_food():
    state = _state()
    for _ in range(BONUS_EVERY - 1):
        _eat_normal(state)
        assert state.bonus_food is None
    assert state.score == BONUS_EVERY - 1


def test_bonus_appears_after_fifth_food():
    state = _state_with_bonus()
    assert state.score == 5
    assert state.bonus_food not in state.snake
    assert state.bonus_food != state.food
    assert state.bonus_food not in state.obstacles


def test_bonus_appears_again_after_tenth_food():
    state = _state_with_bonus()
    state.bonus_food = None
    for _ in range(BONUS_EVERY):
        _eat_normal(state)
    assert state.bonus_food is not None


def test_eating_bonus_gives_three_points_and_clears_it():
    state = _state_with_bonus()
    hx, hy = state.snake[0]
    state.bonus_food = (hx + 1, hy)
    length = len(state.snake)
    state.step()
    assert state.score == 5 + BONUS_POINTS == 8
    assert state.bonus_food is None
    assert len(state.snake) == length + 1
    assert state.food is not None


def test_eating_bonus_does_not_count_toward_next_bonus():
    state = _state_with_bonus()
    hx, hy = state.snake[0]
    state.bonus_food = (hx + 1, hy)
    state.step()
    for _ in range(BONUS_EVERY - 1):
        _eat_normal(state)
    assert state.bonus_food is None
    _eat_normal(state)
    assert state.bonus_food is not None


def test_normal_food_still_one_point():
    state = _state()
    _eat_normal(state)
    assert state.score == 1


def test_bonus_expires_after_lifetime_steps():
    state = _state_with_bonus()
    # park the bonus far away in a row the snake never visits
    state.bonus_food = (0, 0)
    state.snake = [(30, 4), (29, 4), (28, 4)]
    state.direction = state._pending_direction = Direction.UP
    state.food = (59, 0)
    # use wrap-free safe path: go up/left alternately is complex, so use wrap
    state.wrap = True
    state.bonus_steps_left = BONUS_LIFETIME_STEPS
    for i in range(BONUS_LIFETIME_STEPS - 1):
        state.step()
        assert state.bonus_food == (0, 0), i
    state.step()
    assert state.bonus_food is None
    assert not state.game_over


def test_bonus_lifetime_set_on_spawn():
    state = _state_with_bonus()
    assert state.bonus_steps_left == BONUS_LIFETIME_STEPS


def test_bonus_not_stepped_while_paused():
    state = _state_with_bonus()
    left = state.bonus_steps_left
    state.toggle_pause()
    state.step()
    assert state.bonus_steps_left == left


def test_bonus_never_on_snake_or_obstacles():
    for seed in range(40):
        state = GameState(8, 8, seed=seed, obstacle_count=20)
        state.foods_eaten = BONUS_EVERY - 1
        state._spawn_bonus()
        assert state.bonus_food is not None
        assert state.bonus_food not in state.snake
        assert state.bonus_food not in state.obstacles
        assert state.bonus_food != state.food


def test_normal_food_never_on_bonus():
    for seed in range(40):
        state = GameState(4, 4, seed=seed)
        state._spawn_bonus()
        bonus = state.bonus_food
        for _ in range(20):
            state.food = state._place_food()
            assert state.food != bonus


def test_bonus_not_spawned_when_no_free_cell():
    state = GameState(3, 1, seed=0)
    state.food = (2, 0)  # the snake fills the other cells
    state._spawn_bonus()
    assert state.bonus_food is None


def test_bonus_is_deterministic_for_seed():
    a = _state_with_bonus()
    b = _state_with_bonus()
    assert a.bonus_food == b.bonus_food
