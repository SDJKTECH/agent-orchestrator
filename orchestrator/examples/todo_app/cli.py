"""Command-line interface for the to-do app."""

import argparse
import sys
from collections.abc import Sequence

from orchestrator.examples.todo_app.store import TodoStore

DEFAULT_FILE = "todo.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="todo_app", description="A small command-line to-do app."
    )
    parser.add_argument(
        "--file",
        default=DEFAULT_FILE,
        help=f"storage file (default: {DEFAULT_FILE})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="add a new item")
    add.add_argument("title", help="title of the item")

    sub.add_parser("list", help="list all items")

    done = sub.add_parser("done", help="mark an item as done")
    done.add_argument("id", type=int, help="id of the item")

    remove = sub.add_parser("remove", help="remove an item")
    remove.add_argument("id", type=int, help="id of the item")
    return parser


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = build_parser().parse_args(argv)
    store = TodoStore(args.file)
    try:
        if args.command == "add":
            item = store.add(args.title)
            print(f"Added {item.id}: {item.title}")
        elif args.command == "list":
            items = store.list_items()
            if not items:
                print("No items.")
            for item in items:
                mark = "x" if item.done else " "
                print(f"{item.id}. [{mark}] {item.title}")
        elif args.command == "done":
            item = store.mark_done(args.id)
            print(f"Marked {item.id} as done: {item.title}")
        elif args.command == "remove":
            item = store.remove(args.id)
            print(f"Removed {item.id}: {item.title}")
    except KeyError:
        return _error(f"no item with id {args.id}")
    except ValueError as exc:
        return _error(str(exc))
    return 0
