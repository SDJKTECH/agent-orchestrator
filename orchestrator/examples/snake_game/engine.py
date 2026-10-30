"""Display-free Snake Game engine (standard library only)."""

from __future__ import annotations

import random
from enum import Enum

Cell = tuple[int, int]

POINTS_PER_LEVEL = 5
BASE_STEPS_PER_SECOND = 8
SPEED_INCREMENT = 1
MAX_STEPS_PER_SECOND = 20
MAX_OBSTACLES = 8
BONUS_EVERY = 5  # a bonus food appears after every 5th normal food eaten
BONUS_POINTS = 3
BONUS_LIFETIME_STEPS = 20


def level_for_score(score: int) -> int:
    """Return the level (starting at 1) for a score; +1 every 5 points."""
    return max(score, 0) // POINTS_PER_LEVEL + 1


def speed_for_level(level: int) -> int:
    """Return steps per second for a level, capped at the maximum speed."""
    speed = BASE_STEPS_PER_SECOND + (max(level, 1) - 1) * SPEED_INCREMENT
    return min(speed, MAX_STEPS_PER_SECOND)


class Direction(Enum):
    UP = (0, -1)
    DOWN = (0, 1)
    LEFT = (-1, 0)
    RIGHT = (1, 0)

    @property
    def opposite(self) -> Direction:
        dx, dy = self.value
        return Direction((-dx, -dy))


class GameState:
    """Snake game state. The snake is a list of cells, head first."""

    def __init__(
        self,
        width: int = 20,
        height: int = 20,
        seed: int | None = None,
        wrap: bool = False,
        obstacle_count: int = 0,
    ) -> None:
        if width < 3 or height < 1:
            raise ValueError("grid is too small")
        if obstacle_count < 0:
            raise ValueError("obstacle_count must not be negative")
        self.wrap = wrap
        self.width = width
        self.height = height
        self._rng = random.Random(seed)
        head = (width // 2, height // 2)
        self.snake: list[Cell] = [head, (head[0] - 1, head[1]), (head[0] - 2, head[1])]
        self.direction = Direction.RIGHT
        self._pending_direction = Direction.RIGHT
        self.score = 0
        self.game_over = False
        self.paused = False
        self.obstacle_count = obstacle_count
        self.obstacles: set[Cell] = self._place_obstacles(obstacle_count)
        self.bonus_food: Cell | None = None
        self.bonus_steps_left = 0
        self.foods_eaten = 0
        self.food: Cell | None = self._place_food()

    def _place_obstacles(self, count: int) -> set[Cell]:
        """Pick ``count`` cells (seeded) that avoid the snake's starting cells.

        At least one free cell is always left over so food can be placed.
        """
        if count == 0:
            return set()
        occupied = set(self.snake)
        free = [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if (x, y) not in occupied
        ]
        if count > len(free) - 1:
            raise ValueError("too many obstacles for the grid")
        return set(self._rng.sample(free, count))

    def _cells_in_front_of_head(self) -> set[Cell]:
        """Return the two cells directly ahead of the head (current/pending heading)."""
        hx, hy = self.snake[0]
        cells: set[Cell] = set()
        for direction in {self.direction, self._pending_direction}:
            dx, dy = direction.value
            for distance in (1, 2):
                x, y = hx + dx * distance, hy + dy * distance
                if self.wrap:
                    x, y = x % self.width, y % self.height
                cells.add((x, y))
        return cells

    def add_obstacle(self) -> Cell | None:
        """Place one extra obstacle on a free cell (seeded) and return it.

        The cell avoids the snake, the food, the bonus food, existing obstacles
        and the two cells in front of the head. Returns None when ``MAX_OBSTACLES`` is
        reached or no suitable cell exists.
        """
        if len(self.obstacles) >= MAX_OBSTACLES:
            return None
        blocked = set(self.snake) | self.obstacles | self._cells_in_front_of_head()
        if self.food is not None:
            blocked.add(self.food)
        if self.bonus_food is not None:
            blocked.add(self.bonus_food)
        free = [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if (x, y) not in blocked
        ]
        if not free:
            return None
        cell = self._rng.choice(free)
        self.obstacles.add(cell)
        self.obstacle_count = len(self.obstacles)
        return cell

    def _place_food(self) -> Cell | None:
        occupied = set(self.snake) | self.obstacles
        if self.bonus_food is not None:
            occupied.add(self.bonus_food)
        free = [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if (x, y) not in occupied
        ]
        return self._rng.choice(free) if free else None

    def _spawn_bonus(self) -> None:
        """Place a bonus food on a free cell (not snake, obstacle or food)."""
        occupied = set(self.snake) | self.obstacles
        if self.food is not None:
            occupied.add(self.food)
        free = [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if (x, y) not in occupied
        ]
        if free:
            self.bonus_food = self._rng.choice(free)
            self.bonus_steps_left = BONUS_LIFETIME_STEPS

    def change_direction(self, direction: Direction) -> None:
        """Turn the snake; a direct reversal is ignored."""
        if self.paused or direction == self.direction.opposite:
            return
        self._pending_direction = direction

    def toggle_pause(self) -> None:
        """Pause or resume the game; has no effect once the game is over."""
        if self.game_over:
            return
        self.paused = not self.paused

    def step(self) -> None:
        """Advance the snake one cell; does nothing while paused."""
        if self.game_over or self.paused:
            return
        self.direction = self._pending_direction
        dx, dy = self.direction.value
        hx, hy = self.snake[0]
        new_head = (hx + dx, hy + dy)

        if self.wrap:
            new_head = (new_head[0] % self.width, new_head[1] % self.height)
        elif not (0 <= new_head[0] < self.width and 0 <= new_head[1] < self.height):
            self.game_over = True
            return

        if new_head in self.obstacles:
            self.game_over = True
            return

        eating_normal = new_head == self.food
        eating_bonus = self.bonus_food is not None and new_head == self.bonus_food
        eating = eating_normal or eating_bonus
        # The tail cell is vacated this step unless the snake grows.
        body = self.snake if eating else self.snake[:-1]
        if new_head in body:
            self.game_over = True
            return

        self.snake.insert(0, new_head)
        if not eating:
            self.snake.pop()
        if eating_bonus:
            self.score += BONUS_POINTS
            self.bonus_food = None
            self.bonus_steps_left = 0
        elif self.bonus_food is not None:
            self.bonus_steps_left -= 1
            if self.bonus_steps_left <= 0:
                self.bonus_food = None
                self.bonus_steps_left = 0
        if eating_normal:
            self.score += 1
            self.foods_eaten += 1
            self.food = self._place_food()
            if self.food is None:
                self.game_over = True  # board full: the player has won
            elif self.foods_eaten % BONUS_EVERY == 0 and self.bonus_food is None:
                self._spawn_bonus()
