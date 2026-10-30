"""Display-free start-of-game countdown logic."""

from __future__ import annotations

COUNTDOWN_SECONDS = 3


def countdown_value(
    elapsed_seconds: float, total_seconds: int = COUNTDOWN_SECONDS
) -> int | None:
    """Return the number to show (3, 2, 1), or None once the countdown is over.

    Each number is shown for one second. Negative elapsed time is treated as 0.
    """
    elapsed = max(elapsed_seconds, 0.0)
    if elapsed >= total_seconds:
        return None
    return total_seconds - int(elapsed)
