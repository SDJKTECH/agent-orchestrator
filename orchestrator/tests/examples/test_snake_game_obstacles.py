import pytest

from orchestrator.examples.snake_game.engine import Direction, GameState


def test_default_has_no_obstacles():
    state = GameState(seed=1)
    assert state.obstacle_count == 0
    assert state.obstacles == set()


def test_default_behaviour_unchanged_by_obstacle_param():
    assert GameState(seed=5).food == GameState(seed=5, obstacle_count=0).food


def test_places_requested_count_in_bounds():
    state = GameState(width=10, height=10, seed=3, obstacle_count=12)
    assert len(state.obstacles) == 12
    assert all(0 <= x < 10 and 0 <= y < 10 for x, y in state.obstacles)


def test_deterministic_for_seed():
    a = GameState(seed=42, obstacle_count=15)
    b = GameState(seed=42, obstacle_count=15)
    assert a.obstacles == b.obstacles
    assert a.food == b.food


def test_different_seeds_differ():
    layouts = {
        frozenset(GameState(seed=s, obstacle_count=15).obstacles) for s in range(5)
    }
    assert len(layouts) > 1


@pytest.mark.parametrize("seed", range(30))
def test_no_overlap_with_snake_or_food(seed):
    state = GameState(width=8, height=8, seed=seed, obstacle_count=20)
    assert not state.obstacles & set(state.snake)
    assert state.food is not None
    assert state.food not in state.obstacles


def test_food_never_respawns_on_obstacle():
    state = GameState(width=6, height=3, seed=7, obstacle_count=5)
    for _ in range(200):
        state.food = state._place_food()
        assert state.food not in state.obstacles
        assert state.food not in state.snake


def test_hitting_obstacle_ends_game():
    state = GameState(width=10, height=10, seed=1)
    head = state.snake[0]
    state.obstacles = {(head[0] + 1, head[1])}
    state.food = (0, 0)
    state.step()
    assert state.game_over


def test_obstacle_hit_after_turn():
    state = GameState(width=10, height=10, seed=1)
    head = state.snake[0]
    state.obstacles = {(head[0], head[1] - 1)}
    state.food = (0, 0)
    state.change_direction(Direction.UP)
    state.step()
    assert state.game_over


def test_free_step_does_not_end_game():
    state = GameState(width=10, height=10, seed=1)
    head = state.snake[0]
    state.obstacles = {(head[0] + 2, head[1])}
    state.food = (0, 0)
    state.step()
    assert not state.game_over
    assert state.snake[0] == (head[0] + 1, head[1])


def test_negative_count_rejected():
    with pytest.raises(ValueError):
        GameState(obstacle_count=-1)


def test_too_many_obstacles_rejected():
    with pytest.raises(ValueError):
        GameState(width=5, height=5, obstacle_count=25)


def test_max_obstacles_leaves_room_for_food():
    state = GameState(width=5, height=5, seed=2, obstacle_count=25 - 3 - 1)
    assert state.food is not None
    assert state.food not in state.obstacles
