"""Tests for the pure ``hud_text`` helper (no window is opened)."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

pytest.importorskip("pygame")

import pygame  # noqa: E402

from orchestrator.examples.snake_game.app import (  # noqa: E402
    handle_key,
    hud_text,
    new_game,
)
from orchestrator.examples.snake_game.engine import GameState  # noqa: E402


def _eat_food_ahead(state: GameState) -> None:
    """Put the food directly in front of the head and step once."""
    dx, dy = state.direction.value
    hx, hy = state.snake[0]
    state.food = (hx + dx, hy + dy)
    state.step()


def test_fresh_game_shows_length_three() -> None:
    state = GameState(seed=1)
    assert hud_text(state) == "Score: 0   Length: 3   Streak: 0   Level: 1   Best: 0"


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
    assert text.split("   ")[:3] == ["Score: 0", "Length: 3", "Streak: 0"]


def test_best_uses_high_score_and_score() -> None:
    state = GameState(seed=1)
    assert hud_text(state, high_score=5).endswith("Best: 5")
    state.score = 7
    text = hud_text(state, high_score=5)
    assert text.startswith("Score: 7   Length: 3")
    assert text.endswith("Best: 7")


def test_hud_shows_longest_streak() -> None:
    state = GameState(seed=1)
    state.longest_streak = 4
    text = hud_text(state, high_score=5)
    assert "Streak: 4" in text
    assert text.index("Length:") < text.index("Streak:") < text.index("Level:")


def test_hud_streak_updates_after_eating() -> None:
    state = GameState(seed=1)
    _eat_food_ahead(state)
    _eat_food_ahead(state)
    assert "Streak: 2" in hud_text(state)


def test_restart_carries_longest_streak() -> None:
    state = GameState(seed=1)
    state.longest_streak = 6
    state.game_over = True
    new_state, quit_requested = handle_key(state, pygame.K_r)
    assert not quit_requested
    assert new_state is not state
    assert new_state.longest_streak == 6
    assert new_state.current_streak == 0
    assert "Streak: 6" in hud_text(new_state)


def test_new_game_defaults_to_zero_streak() -> None:
    state = new_game()
    assert (state.current_streak, state.longest_streak) == (0, 0)
    assert new_game(3).longest_streak == 3
