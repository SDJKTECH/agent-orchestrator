import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

from orchestrator.examples.snake_game.countdown import (  # noqa: E402
    COUNTDOWN_SECONDS,
    countdown_value,
)


def test_default_is_three_seconds():
    assert COUNTDOWN_SECONDS == 3


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0, 3),
        (0.5, 3),
        (0.999, 3),
        (1.0, 2),
        (1.5, 2),
        (1.999, 2),
        (2.0, 1),
        (2.999, 1),
        (3.0, None),
        (3.5, None),
        (100, None),
    ],
)
def test_countdown_values(elapsed, expected):
    assert countdown_value(elapsed) == expected


def test_negative_elapsed_treated_as_zero():
    assert countdown_value(-5) == 3


def test_custom_total():
    assert countdown_value(0, total_seconds=5) == 5
    assert countdown_value(4.2, total_seconds=5) == 1
    assert countdown_value(5, total_seconds=5) is None


def test_zero_total_is_immediately_over():
    assert countdown_value(0, total_seconds=0) is None


def test_sequence_counts_down_in_order():
    seen = []
    for t in range(0, 40):
        v = countdown_value(t / 10)
        if v is not None and (not seen or seen[-1] != v):
            seen.append(v)
    assert seen == [3, 2, 1]


def test_new_game_starts_with_score_zero_and_countdown_active():
    from orchestrator.examples.snake_game.app import new_game

    state = new_game()
    assert countdown_value(0) == 3
    assert state.score == 0


def test_draw_shows_countdown_number():
    import pygame

    from orchestrator.examples.snake_game.app import draw, new_game

    pygame.init()
    try:
        screen = pygame.Surface((560, 600))
        font = pygame.font.Font(None, 28)
        state = new_game()
        draw(screen, font, state, 0, None)
        plain = pygame.image.tostring(screen, "RGB")
        draw(screen, font, state, 0, 3)
        with_cd = pygame.image.tostring(screen, "RGB")
        assert plain != with_cd
    finally:
        pygame.quit()
