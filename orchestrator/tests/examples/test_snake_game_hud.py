"""Tests for the pure ``hud_text`` helper (no window is opened)."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

pytest.importorskip("pygame")

from orchestrator.examples.snake_game.app import hud_text  # noqa: E402
from orchestrator.examples.snake_game.engine import GameState  # noqa: E402


def _eat_food_ahead(state: GameState) -> None:
    """Put the food directly in front of the head and step once."""
    dx, dy = state.direction.value
    hx, hy = state.snake[0]
    state.food = (hx + dx, hy + dy)
    state.step()


def test_fresh_game_shows_length_three() -> None:
    state = GameState(seed=1)
    assert hud_text(state) == "Score: 0   Length: 3   Level: 1   Best: 0"


def test_length_follows_growth() -> None:
    state = GameState(seed=1)
    _eat_food_ahead(state)
    assert len(state.snake) == 4
    assert state.score == 1
    assert f"Length: {len(state.snake)}" in hud_text(state)
    _eat_food_ahead(state)
    assert len(state.snake) == 5
    assert "Length: 5" in hud_text(state)


def test_length_unchanged_when_not_eating() -> None:
    state = GameState(seed=1)
    state.food = (0, 0)
    state.step()
    assert "Length: 3" in hud_text(state)


def test_length_directly_follows_score() -> None:
    text = hud_text(GameState(seed=1))
    assert text.index("Score:") < text.index("Length:") < text.index("Level:")
    assert text.split("   ")[:2] == ["Score: 0", "Length: 3"]


def test_best_uses_high_score_and_score() -> None:
    state = GameState(seed=1)
    assert hud_text(state, high_score=5).endswith("Best: 5")
    state.score = 7
    text = hud_text(state, high_score=5)
    assert text.startswith("Score: 7   Length: 3")
    assert text.endswith("Best: 7")
