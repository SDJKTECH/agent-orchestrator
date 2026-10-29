import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

from orchestrator.examples.snake_game.engine import Direction, GameState  # noqa: E402


def test_not_paused_by_default():
    assert GameState(seed=1).paused is False


def test_toggle_pause_twice_restores_play():
    g = GameState(seed=1)
    g.toggle_pause()
    assert g.paused
    g.toggle_pause()
    assert not g.paused
    before = list(g.snake)
    g.step()
    assert g.snake[0] == (before[0][0] + 1, before[0][1])


def test_step_does_nothing_while_paused():
    g = GameState(seed=1)
    snake, score, food = list(g.snake), g.score, g.food
    g.toggle_pause()
    for _ in range(50):
        g.step()
    assert g.snake == snake and g.score == score and g.food == food
    assert not g.game_over


def test_paused_game_never_ends_at_wall():
    g = GameState(width=5, height=5, seed=1)
    g.snake = [(4, 2), (3, 2), (2, 2)]
    g.toggle_pause()
    for _ in range(10):
        g.step()
    assert not g.game_over
    g.toggle_pause()
    g.step()
    assert g.game_over


def test_paused_does_not_eat():
    g = GameState(seed=1)
    head = g.snake[0]
    g.food = (head[0] + 1, head[1])
    g.toggle_pause()
    g.step()
    assert g.score == 0 and g.food == (head[0] + 1, head[1])


def test_direction_ignored_while_paused():
    g = GameState(seed=1)
    g.toggle_pause()
    g.change_direction(Direction.UP)
    g.toggle_pause()
    g.step()
    assert g.direction == Direction.RIGHT


def test_direction_before_pause_is_kept():
    g = GameState(seed=1)
    g.change_direction(Direction.UP)
    g.toggle_pause()
    g.toggle_pause()
    g.step()
    assert g.direction == Direction.UP


def test_toggle_pause_noop_after_game_over():
    g = GameState(seed=1)
    g.game_over = True
    g.toggle_pause()
    assert not g.paused


pygame = pytest.importorskip("pygame")
from orchestrator.examples.snake_game import app  # noqa: E402


def test_p_key_toggles_pause():
    g = GameState(seed=1)
    state, quit_ = app.handle_key(g, pygame.K_p)
    assert state is g and not quit_ and g.paused
    app.handle_key(g, pygame.K_p)
    assert not g.paused


def test_direction_keys_ignored_when_paused_via_app():
    g = GameState(seed=1)
    app.handle_key(g, pygame.K_p)
    app.handle_key(g, pygame.K_UP)
    app.handle_key(g, pygame.K_p)
    g.step()
    assert g.direction == Direction.RIGHT


def test_p_ignored_when_game_over():
    g = GameState(seed=1)
    g.game_over = True
    app.handle_key(g, pygame.K_p)
    assert not g.paused


def test_draw_paused_shows_overlay():
    pygame.font.init()
    font = pygame.font.Font(None, 24)
    g = GameState(seed=1)
    screen = pygame.Surface((400, 440))
    app.draw(screen, font, g)
    before = pygame.image.tostring(screen, "RGB")
    g.toggle_pause()
    app.draw(screen, font, g)
    assert pygame.image.tostring(screen, "RGB") != before
