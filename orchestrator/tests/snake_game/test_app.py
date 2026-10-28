"""Tests for the pygame front end's pure helpers (no window is opened)."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from snake_game import app  # noqa: E402
from snake_game.engine import Direction, GameState  # noqa: E402


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (pygame.K_UP, Direction.UP),
        (pygame.K_w, Direction.UP),
        (pygame.K_DOWN, Direction.DOWN),
        (pygame.K_s, Direction.DOWN),
        (pygame.K_LEFT, Direction.LEFT),
        (pygame.K_a, Direction.LEFT),
        (pygame.K_RIGHT, Direction.RIGHT),
        (pygame.K_d, Direction.RIGHT),
    ],
)
def test_key_to_direction_maps_arrows_and_wasd(key, expected):
    assert app.key_to_direction(key) is expected


@pytest.mark.parametrize("key", [pygame.K_SPACE, pygame.K_r, pygame.K_q, 0])
def test_key_to_direction_unmapped_returns_none(key):
    assert app.key_to_direction(key) is None


def test_handle_key_changes_direction_while_playing():
    state = GameState(10, 10, seed=1)
    result, quit_requested = app.handle_key(state, pygame.K_UP)
    assert result is state
    assert quit_requested is False
    state.step()
    assert state.direction is Direction.UP


def test_handle_key_ignores_reversal():
    state = GameState(10, 10, seed=1)
    app.handle_key(state, pygame.K_LEFT)
    state.step()
    assert state.direction is Direction.RIGHT


def test_handle_key_r_and_q_ignored_while_playing():
    state = GameState(10, 10, seed=1)
    for key in (pygame.K_r, pygame.K_q):
        result, quit_requested = app.handle_key(state, key)
        assert result is state
        assert quit_requested is False


def _game_over_state() -> GameState:
    state = GameState(5, 5, seed=1)
    state.score = 3
    state.game_over = True
    return state


def test_restart_with_r_returns_fresh_game():
    old = _game_over_state()
    new, quit_requested = app.handle_key(old, pygame.K_r)
    assert quit_requested is False
    assert new is not old
    assert new.game_over is False
    assert new.score == 0
    assert (new.width, new.height) == (app.GRID_WIDTH, app.GRID_HEIGHT)
    assert len(new.snake) == 3


def test_q_requests_quit_when_game_over():
    old = _game_over_state()
    result, quit_requested = app.handle_key(old, pygame.K_q)
    assert quit_requested is True
    assert result is old


def test_direction_keys_ignored_when_game_over():
    old = _game_over_state()
    snake_before = list(old.snake)
    result, quit_requested = app.handle_key(old, pygame.K_UP)
    assert result is old
    assert quit_requested is False
    assert old.game_over is True
    assert old.snake == snake_before


def test_new_game_uses_configured_grid():
    state = app.new_game()
    assert (state.width, state.height) == (app.GRID_WIDTH, app.GRID_HEIGHT)
    assert state.game_over is False


def test_draw_headless_does_not_raise_for_playing_and_game_over():
    pygame.init()
    try:
        screen = pygame.Surface(
            (
                app.GRID_WIDTH * app.CELL_SIZE,
                app.HUD_HEIGHT + app.GRID_HEIGHT * app.CELL_SIZE,
            )
        )
        font = pygame.font.Font(None, 28)
        state = app.new_game()
        app.draw(screen, font, state)
        state.game_over = True
        app.draw(screen, font, state)
    finally:
        pygame.quit()
