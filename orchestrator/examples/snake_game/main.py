"""Entry point: ``python -m orchestrator.examples.snake_game [--size WxH]``."""

import argparse

from orchestrator.examples.snake_game import app

MIN_BOARD_SIZE = 5
MAX_BOARD_SIZE = 50


def parse_size(text: str) -> tuple[int, int]:
    """Parse ``WIDTHxHEIGHT`` (``x`` or ``X``) into ``(width, height)``."""
    parts = text.lower().split("x")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise argparse.ArgumentTypeError(
            f"invalid size {text!r}: expected WIDTHxHEIGHT, e.g. 30x15"
        )
    values: list[int] = []
    for part in parts:
        if not (part.isascii() and part.isdigit()):
            raise argparse.ArgumentTypeError(
                f"invalid size {text!r}: {part!r} is not a whole number"
            )
        values.append(int(part))
    for value in values:
        if not MIN_BOARD_SIZE <= value <= MAX_BOARD_SIZE:
            raise argparse.ArgumentTypeError(
                f"invalid size {text!r}: each side must be between "
                f"{MIN_BOARD_SIZE} and {MAX_BOARD_SIZE}"
            )
    return values[0], values[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="snake_game", description="Play Snake.")
    default = (app.GRID_WIDTH, app.GRID_HEIGHT)
    parser.add_argument(
        "--size",
        type=parse_size,
        default=default,
        metavar="WxH",
        help=(
            f"board size as WIDTHxHEIGHT, each {MIN_BOARD_SIZE}-{MAX_BOARD_SIZE} "
            f"(default: {default[0]}x{default[1]})"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    width, height = args.size
    app.run(width, height)


if __name__ == "__main__":
    main()
