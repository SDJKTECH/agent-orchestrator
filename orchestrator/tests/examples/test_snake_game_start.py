"""Tests for the title/start screen (no real window is opened)."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

pygame = pytest.importorskip("pygame")

from orchestrator.examples.snake_game import app  # noqa: E402


@pytest.fixture
def surface():
    pygame.font.init()
    screen = pygame.Surface((400, 400))
    font = pygame.font.Font(None, 24)
    return screen, font


def _non_background_pixels(screen):
    w, h = screen.get_size()
    return sum(
        1
        for x in range(0, w, 2)
        for y in range(0, h, 2)
        if tuple(screen.get_at((x, y)))[:3] != app.BACKGROUND
    )


def test_start_lines_explain_controls_and_goal():
    text = " ".join(app.START_LINES).lower()
    assert "arrow" in text and "wasd" in text
    assert "pause" in text
    assert "food" in text


def test_draw_start_screen_covers_previous_content(surface):
    screen, font = surface
    screen.fill((255, 0, 0))
    app.draw_start_screen(screen, font)
    assert tuple(screen.get_at((0, 0)))[:3] == app.BACKGROUND
    assert _non_background_pixels(screen) > 0  # text was drawn


def test_draw_start_screen_shows_best_only_when_nonzero(surface):
    screen, font = surface
    app.draw_start_screen(screen, font, 0)
    without = _non_background_pixels(screen)
    app.draw_start_screen(screen, font, 7)
    assert _non_background_pixels(screen) > without


def test_draw_start_screen_default_high_score_matches_zero(surface):
    screen, font = surface
    app.draw_start_screen(screen, font)
    first = pygame.image.tostring(screen, "RGB")
    app.draw_start_screen(screen, font, 0)
    assert pygame.image.tostring(screen, "RGB") == first
