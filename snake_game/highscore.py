"""Persistent high score stored in a JSON file (standard library only)."""

from __future__ import annotations

import json
from pathlib import Path

HIGH_SCORE_PATH = Path(__file__).with_name("highscore.json")


def load_high_score(path: Path | None = None) -> int:
    """Return the saved high score; a missing or corrupt file counts as 0."""
    target = path if path is not None else HIGH_SCORE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        value = data["high_score"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def save_high_score(score: int, path: Path | None = None) -> None:
    """Write the high score to the JSON file, creating it if needed."""
    target = path if path is not None else HIGH_SCORE_PATH
    target.write_text(json.dumps({"high_score": int(score)}), encoding="utf-8")
