import json

import pytest

from snake_game import highscore
from snake_game.highscore import load_high_score, save_high_score


def test_missing_file_is_zero(tmp_path):
    assert load_high_score(tmp_path / "nope.json") == 0


def test_save_creates_file_and_roundtrips(tmp_path):
    p = tmp_path / "hs.json"
    save_high_score(12, p)
    assert p.exists()
    assert load_high_score(p) == 12


def test_save_overwrites(tmp_path):
    p = tmp_path / "hs.json"
    save_high_score(12, p)
    save_high_score(3, p)
    assert load_high_score(p) == 3


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "{",
        "[]",
        "null",
        "{}",
        '{"high_score": "abc"}',
        '{"high_score": -4}',
        '{"high_score": 1.5}',
        '{"high_score": true}',
    ],
)
def test_corrupt_content_is_zero(tmp_path, content):
    p = tmp_path / "hs.json"
    p.write_text(content, encoding="utf-8")
    assert load_high_score(p) == 0


def test_invalid_utf8_is_zero(tmp_path):
    p = tmp_path / "hs.json"
    p.write_bytes(b"\xff\xfe\x00bad")
    assert load_high_score(p) == 0


def test_directory_path_is_zero(tmp_path):
    assert load_high_score(tmp_path) == 0


def test_saved_file_is_json(tmp_path):
    p = tmp_path / "hs.json"
    save_high_score(7, p)
    assert json.loads(p.read_text(encoding="utf-8")) == {"high_score": 7}


def test_default_path_used(tmp_path, monkeypatch):
    p = tmp_path / "default.json"
    monkeypatch.setattr(highscore, "HIGH_SCORE_PATH", p)
    assert load_high_score() == 0
    save_high_score(9)
    assert load_high_score() == 9
