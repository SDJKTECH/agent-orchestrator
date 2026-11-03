"""Tests for the Snake entry point's --size command-line option."""

from __future__ import annotations

import argparse
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

pytest.importorskip("pygame")

from orchestrator.examples.snake_game import app  # noqa: E402
from orchestrator.examples.snake_game import main as snake_main  # noqa: E402


def test_limits_constants():
    assert snake_main.MIN_BOARD_SIZE == 5
    assert snake_main.MAX_BOARD_SIZE == 50


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20x20", (20, 20)),
        ("30x15", (30, 15)),
        ("20X20", (20, 20)),
        ("5x5", (5, 5)),
        ("50x50", (50, 50)),
        ("5x50", (5, 50)),
    ],
)
def test_parse_size_valid(text, expected):
    assert snake_main.parse_size(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "abc",
        "20",
        "20x",
        "x20",
        "",
        "x",
        "0x10",
        "4x20",
        "20x4",
        "51x20",
        "20x51",
        "-5x10",
        "10x-5",
        "axb",
        "1.5x10",
        "20x20x20",
        " 20x20",
        "20x20 ",
        "20 x 20",
        "+10x10",
    ],
)
def test_parse_size_invalid(text):
    with pytest.raises(argparse.ArgumentTypeError):
        snake_main.parse_size(text)


def test_parse_size_error_message_is_helpful():
    with pytest.raises(argparse.ArgumentTypeError, match="WIDTHxHEIGHT"):
        snake_main.parse_size("abc")
    with pytest.raises(argparse.ArgumentTypeError, match="5.*50"):
        snake_main.parse_size("51x20")


def test_parse_args_default():
    assert snake_main.parse_args([]).size == (20, 20)
    assert snake_main.parse_args([]).size == (app.GRID_WIDTH, app.GRID_HEIGHT)


def test_parse_args_size():
    assert snake_main.parse_args(["--size", "30x15"]).size == (30, 15)
    assert snake_main.parse_args(["--size", "20X20"]).size == (20, 20)


@pytest.mark.parametrize("text", ["abc", "20", "20x", "0x10", "4x20", "51x20", "-5x10"])
def test_parse_args_invalid_exits(text, capsys):
    with pytest.raises(SystemExit) as exc:
        snake_main.parse_args(["--size", text])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    # argparse reads a separate "-5x10" token as an option, so the message differs.
    assert "invalid size" in err or "expected one argument" in err


def test_parse_args_negative_equals_form_reports_invalid_size(capsys):
    with pytest.raises(SystemExit):
        snake_main.parse_args(["--size=-5x10"])
    assert "invalid size" in capsys.readouterr().err


def test_parse_args_missing_value_exits():
    with pytest.raises(SystemExit):
        snake_main.parse_args(["--size"])


def test_help_shows_format_and_default(capsys):
    with pytest.raises(SystemExit) as exc:
        snake_main.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--size" in out
    assert "20x20" in out


def test_main_passes_size_to_run(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "run", lambda *a, **k: calls.append((a, k)))
    snake_main.main(["--size", "30x15"])
    assert calls == [((30, 15), {})]


def test_main_default_size(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "run", lambda *a, **k: calls.append(a))
    snake_main.main([])
    assert calls == [(app.GRID_WIDTH, app.GRID_HEIGHT)]


def test_main_uses_sys_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr("sys.argv", ["snake_game", "--size", "7x9"])
    snake_main.main()
    assert calls == [(7, 9)]


def test_main_invalid_size_does_not_run(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(SystemExit):
        snake_main.main(["--size", "4x20"])
    assert calls == []
