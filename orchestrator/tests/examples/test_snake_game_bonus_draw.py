"""Tests for drawing the bonus food in the pygame window (headless)."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

pygame = pytest.importorskip("pygame")

from orchestrator.examples.snake_game import app  # noqa: E402


@pytest.fixture
def surface_and_font():
    pygame.init()
    try:
        screen = pygame.Surface(
            (
                app.GRID_WIDTH * app.CELL_SIZE,
                app.HUD_HEIGHT + app.GRID_HEIGHT * app.CELL_SIZE,
            )
        )
        yield screen, pygame.font.Font(None, 28)
    finally:
        pygame.quit()


def _pixel(screen, cell):
    point = (
        cell[0] * app.CELL_SIZE + app.CELL_SIZE // 2,
        app.HUD_HEIGHT + cell[1] * app.CELL_SIZE + app.CELL_SIZE // 2,
    )
    return tuple(screen.get_at(point))[:3]


def _state():
    state = app.new_game()
    state.obstacles = type(state.obstacles)()
    return state


def test_bonus_colour_differs_from_other_colours():
    others = {
        app.FOOD_COLOR,
        app.SNAKE_COLOR,
        app.HEAD_COLOR,
        app.OBSTACLE_COLOR,
        app.BACKGROUND,
        app.GRID_COLOR,
    }
    assert app.BONUS_FOOD_COLOR not in others


def test_bonus_food_drawn_in_bonus_colour(surface_and_font):
    screen, font = surface_and_font
    state = _state()
    state.bonus_food = (0, 0)
    app.draw(screen, font, state)
    assert _pixel(screen, (0, 0)) == app.BONUS_FOOD_COLOR


def test_normal_food_keeps_food_colour_alongside_bonus(surface_and_font):
    screen, font = surface_and_font
    state = _state()
    state.food = (1, 0)
    state.bonus_food = (3, 0)
    app.draw(screen, font, state)
    assert _pixel(screen, (1, 0)) == app.FOOD_COLOR
    assert _pixel(screen, (3, 0)) == app.BONUS_FOOD_COLOR


def test_no_bonus_colour_when_bonus_absent(surface_and_font):
    screen, font = surface_and_font
    state = _state()
    state.bonus_food = None
    app.draw(screen, font, state)
    for x in range(state.width):
        for y in range(state.height):
            assert _pixel(screen, (x, y)) != app.BONUS_FOOD_COLOR


def test_bonus_disappears_after_being_cleared(surface_and_font):
    screen, font = surface_and_font
    state = _state()
    state.bonus_food = (0, 0)
    app.draw(screen, font, state)
    state.bonus_food = None
    app.draw(screen, font, state)
    assert _pixel(screen, (0, 0)) != app.BONUS_FOOD_COLOR
