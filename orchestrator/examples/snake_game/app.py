"""Pygame front end for the Snake Game.

All game rules live in ``snake_game.engine``; this module only handles input
and drawing.
"""

from __future__ import annotations

import pygame

from orchestrator.examples.snake_game.engine import (
    Direction,
    GameState,
    level_for_score,
    speed_for_level,
)
from orchestrator.examples.snake_game.highscore import load_high_score, save_high_score

GRID_WIDTH = 20
GRID_HEIGHT = 20
CELL_SIZE = 28
HUD_HEIGHT = 40

BACKGROUND = (20, 20, 20)
GRID_COLOR = (40, 40, 40)
SNAKE_COLOR = (80, 200, 120)
HEAD_COLOR = (140, 240, 170)
FOOD_COLOR = (230, 70, 70)
OBSTACLE_COLOR = (130, 130, 150)
TEXT_COLOR = (240, 240, 240)

KEY_TO_DIRECTION: dict[int, Direction] = {
    pygame.K_UP: Direction.UP,
    pygame.K_w: Direction.UP,
    pygame.K_DOWN: Direction.DOWN,
    pygame.K_s: Direction.DOWN,
    pygame.K_LEFT: Direction.LEFT,
    pygame.K_a: Direction.LEFT,
    pygame.K_RIGHT: Direction.RIGHT,
    pygame.K_d: Direction.RIGHT,
}


def key_to_direction(key: int) -> Direction | None:
    """Map a pygame key code to a Direction, or None if it is not a move key."""
    return KEY_TO_DIRECTION.get(key)


def new_game() -> GameState:
    """Create a fresh game state."""
    return GameState(GRID_WIDTH, GRID_HEIGHT)


def handle_key(state: GameState, key: int) -> tuple[GameState, bool]:
    """Apply a key press to the game.

    Returns ``(state, quit_requested)``. While the game is over, R returns a
    new game state and Q requests quitting; otherwise direction keys call
    ``change_direction``.
    """
    if state.game_over:
        if key == pygame.K_r:
            return new_game(), False
        if key == pygame.K_q:
            return state, True
        return state, False
    if key == pygame.K_p:
        state.toggle_pause()
        return state, False
    direction = key_to_direction(key)
    if direction is not None:
        state.change_direction(direction)
    return state, False


def _draw_centered(
    screen: pygame.Surface, font: pygame.font.Font, text: str, y: int
) -> None:
    surface = font.render(text, True, TEXT_COLOR)
    screen.blit(surface, surface.get_rect(center=(screen.get_width() // 2, y)))


def draw(
    screen: pygame.Surface,
    font: pygame.font.Font,
    state: GameState,
    high_score: int = 0,
) -> None:
    """Draw the grid, snake, food, HUD and the game-over overlay."""
    screen.fill(BACKGROUND)
    for x in range(state.width + 1):
        px = x * CELL_SIZE
        pygame.draw.line(
            screen, GRID_COLOR, (px, HUD_HEIGHT), (px, screen.get_height())
        )
    for y in range(state.height + 1):
        py = HUD_HEIGHT + y * CELL_SIZE
        pygame.draw.line(screen, GRID_COLOR, (0, py), (screen.get_width(), py))

    def cell_rect(cell: tuple[int, int]) -> pygame.Rect:
        return pygame.Rect(
            cell[0] * CELL_SIZE + 1,
            HUD_HEIGHT + cell[1] * CELL_SIZE + 1,
            CELL_SIZE - 1,
            CELL_SIZE - 1,
        )

    for cell in state.obstacles:
        pygame.draw.rect(screen, OBSTACLE_COLOR, cell_rect(cell))
    if state.food is not None:
        pygame.draw.rect(screen, FOOD_COLOR, cell_rect(state.food))
    for i, cell in enumerate(state.snake):
        pygame.draw.rect(screen, HEAD_COLOR if i == 0 else SNAKE_COLOR, cell_rect(cell))

    hud = (
        f"Score: {state.score}   Level: {level_for_score(state.score)}"
        f"   Best: {max(high_score, state.score)}"
    )
    screen.blit(font.render(hud, True, TEXT_COLOR), (10, 8))

    if state.paused and not state.game_over:
        _draw_centered(screen, font, "Paused", screen.get_height() // 2)

    if state.game_over:
        mid = screen.get_height() // 2
        _draw_centered(screen, font, "Game Over", mid - 30)
        _draw_centered(screen, font, f"Final score: {state.score}", mid)
        _draw_centered(screen, font, "Press R to restart or Q to quit", mid + 30)


START_KEYS = (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE)
QUIT_KEYS = (pygame.K_q, pygame.K_ESCAPE)


def handle_start_key(key: int) -> str | None:
    """Interpret a key press on the start screen.

    Returns ``"start"`` for Enter/Space, ``"quit"`` for Q/Esc, else ``None``.
    """
    if key in START_KEYS:
        return "start"
    if key in QUIT_KEYS:
        return "quit"
    return None


def draw_start_screen(
    screen: pygame.Surface, font: pygame.font.Font, high_score: int = 0
) -> None:
    """Draw the title, controls and best score shown before the first move."""
    screen.fill(BACKGROUND)
    mid = screen.get_height() // 2
    title_font = pygame.font.Font(None, 72)
    title = title_font.render("SNAKE", True, HEAD_COLOR)
    screen.blit(title, title.get_rect(center=(screen.get_width() // 2, mid - 120)))
    lines = [
        "Eat the food to grow and score points",
        "Move: Arrow keys or WASD",
        "Pause: P",
        f"Best score: {high_score}",
        "Press Enter or Space to start",
        "Press Q or Esc to quit",
    ]
    for i, line in enumerate(lines):
        _draw_centered(screen, font, line, mid - 50 + i * 32)


def run() -> None:
    """Open the window and run the game loop until the player quits."""
    pygame.init()
    try:
        screen = pygame.display.set_mode(
            (GRID_WIDTH * CELL_SIZE, HUD_HEIGHT + GRID_HEIGHT * CELL_SIZE)
        )
        pygame.display.set_caption("Snake")
        font = pygame.font.Font(None, 28)
        clock = pygame.time.Clock()
        step_event = pygame.USEREVENT + 1
        high_score = load_high_score()
        state = new_game()
        level = level_for_score(state.score)
        pygame.time.set_timer(step_event, 1000 // speed_for_level(level))
        running = True
        on_start_screen = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif on_start_screen:
                    if event.type == pygame.KEYDOWN:
                        action = handle_start_key(event.key)
                        if action == "quit":
                            running = False
                        elif action == "start":
                            on_start_screen = False
                            state = new_game()
                elif event.type == pygame.KEYDOWN:
                    state, quit_requested = handle_key(state, event.key)
                    running = running and not quit_requested
                elif event.type == step_event:
                    state.step()
            if state.game_over and state.score > high_score:
                high_score = state.score
                save_high_score(high_score)
            new_level = level_for_score(state.score)
            if new_level != level:
                level = new_level
                pygame.time.set_timer(step_event, 1000 // speed_for_level(level))
            if on_start_screen:
                draw_start_screen(screen, font, high_score)
            else:
                draw(screen, font, state, high_score)
            pygame.display.flip()
            clock.tick(60)
    finally:
        pygame.quit()
