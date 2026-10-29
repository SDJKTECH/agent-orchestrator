import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from orchestrator.examples.snake_game import app  # noqa: E402

_REAL_FONT = pygame.font.Font


@pytest.fixture
def screen():
    pygame.init()
    surf = pygame.display.set_mode((560, 600))
    yield surf
    pygame.quit()


@pytest.fixture
def texts(monkeypatch):
    """Record every string rendered through a pygame Font."""
    captured: list[str] = []

    class SpyFont:
        def __init__(self, *args, **kwargs):
            self._font = _REAL_FONT(*args, **kwargs)

        def render(self, text, *args, **kwargs):
            captured.append(text)
            return self._font.render(text, *args, **kwargs)

    monkeypatch.setattr(pygame.font, "Font", SpyFont)
    return captured


@pytest.mark.parametrize("key", [pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE])
def test_start_keys_start(key):
    assert app.handle_start_key(key) == "start"


@pytest.mark.parametrize("key", [pygame.K_q, pygame.K_ESCAPE])
def test_quit_keys_quit(key):
    assert app.handle_start_key(key) == "quit"


@pytest.mark.parametrize(
    "key", [pygame.K_UP, pygame.K_w, pygame.K_p, pygame.K_r, pygame.K_a, pygame.K_x]
)
def test_other_keys_ignored(key):
    assert app.handle_start_key(key) is None


def test_start_screen_shows_title_controls_and_best(screen, texts):
    app.draw_start_screen(screen, pygame.font.Font(None, 28), 42)
    joined = "\n".join(texts)
    assert "SNAKE" in joined
    assert "Best score: 42" in joined
    assert "Arrow" in joined and "WASD" in joined
    assert "Pause" in joined
    assert "Enter" in joined and "Space" in joined
    assert "Q" in joined


def test_start_screen_default_best_zero(screen, texts):
    app.draw_start_screen(screen, pygame.font.Font(None, 28))
    assert "Best score: 0" in texts


def test_start_screen_clears_previous_content(screen):
    screen.fill((255, 0, 0))
    app.draw_start_screen(screen, _REAL_FONT(None, 28), 0)
    assert screen.get_at((0, 0))[:3] == app.BACKGROUND


def test_enter_does_not_affect_gameplay_state():
    state = app.new_game()
    state, quit_req = app.handle_key(state, pygame.K_RETURN)
    assert not quit_req and not state.game_over
