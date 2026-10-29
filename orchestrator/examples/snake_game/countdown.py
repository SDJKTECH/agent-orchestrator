"""Start-of-game 3-2-1 countdown logic (no pygame dependency)."""

from __future__ import annotations

COUNTDOWN_SECONDS = 3
COUNTDOWN_STEP_MS = 1000


def countdown_value(
    elapsed_ms: int,
    seconds: int = COUNTDOWN_SECONDS,
    step_ms: int = COUNTDOWN_STEP_MS,
) -> int | None:
    """Return the number to display after ``elapsed_ms``, or None when finished.

    With the defaults: 0-999 ms -> 3, 1000-1999 -> 2, 2000-2999 -> 1,
    3000+ -> None. Negative elapsed time is treated as 0.
    """
    elapsed = max(0, elapsed_ms)
    value = seconds - elapsed // step_ms
    return value if value > 0 else None
