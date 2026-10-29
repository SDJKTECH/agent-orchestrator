"""Tests for the 3-2-1 start countdown (no window is opened)."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

pygame = pytest.importorskip("pygame")

from orchestrator.examples.snake_game import app  # noqa: E402


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0, 3),
        (999, 3),
        (1000, 2),
        (1999, 2),
        (2000, 1),
        (2999, 1),
        (3000, None),
        (10_000, None),
        (-500, 3),
    ],
)
def test_countdown_value(elapsed: int, expected: int | None) -> None:
    assert app.countdown_value(elapsed) == expected


def test_countdown_constants() -> None:
    assert app.COUNTDOWN_SECONDS == 3
    assert app.COUNTDOWN_STEP_MS == 1000


def test_draw_with_countdown_renders_number_and_keeps_state() -> None:
    pygame.font.init()
    screen = pygame.Surface((app.GRID_WIDTH * app.CELL_SIZE, 600))
    font = pygame.font.Font(None, 36)
    state = app.new_game()
    head = state.snake[0]
    app.draw(screen, font, state, 0, None)
    plain = pygame.image.tobytes(screen, "RGB")
    app.draw(screen, font, state, 0, 3)
    with_count = pygame.image.tobytes(screen, "RGB")
    assert plain != with_count
    assert state.score == 0
    assert state.snake[0] == head
