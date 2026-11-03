"""JSON file storage for the to-do app.

File format::

    {"next_id": 3, "items": [{"id": 1, "title": "...", "done": false}, ...]}

``next_id`` is persisted so ids are never reused, even after removals.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TodoItem:
    id: int
    title: str
    done: bool = False


class TodoStore:
    """Stores to-do items in a JSON file, writing it atomically."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def _load(self) -> tuple[int, list[TodoItem]]:
        """Return ``(next_id, items)``; raises ValueError if the file is corrupt."""
        if not self.path.exists():
            return 1, []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError("top-level value must be an object")
            next_id = data["next_id"]
            if isinstance(next_id, bool) or not isinstance(next_id, int):
                raise TypeError("next_id must be an integer")
            items: list[TodoItem] = []
            for raw in data["items"]:
                item_id, title, done = raw["id"], raw["title"], raw["done"]
                if (
                    isinstance(item_id, bool)
                    or not isinstance(item_id, int)
                    or not isinstance(title, str)
                    or not isinstance(done, bool)
                ):
                    raise TypeError("invalid item fields")
                items.append(TodoItem(id=item_id, title=title, done=done))
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cannot read to-do file {self.path}: {exc}") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(
                f"To-do file {self.path} is corrupt: {exc!r}. "
                "Fix or delete the file to continue."
            ) from exc
        # Guard against a hand-edited counter that would reuse an existing id.
        next_id = max([next_id, *(item.id + 1 for item in items)])
        return next_id, items

    def _save(self, next_id: int, items: list[TodoItem]) -> None:
        """Write to a temp file in the same directory, then replace atomically."""
        payload = {"next_id": next_id, "items": [asdict(item) for item in items]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def list_items(self) -> list[TodoItem]:
        return self._load()[1]

    def add(self, title: str) -> TodoItem:
        """Add an item; raises ValueError if the title is empty or blank."""
        title = title.strip()
        if not title:
            raise ValueError("Title must not be empty")
        next_id, items = self._load()
        item = TodoItem(id=next_id, title=title)
        items.append(item)
        self._save(next_id + 1, items)
        return item

    def mark_done(self, item_id: int) -> TodoItem:
        """Mark an item done; raises KeyError if the id is unknown."""
        next_id, items = self._load()
        for item in items:
            if item.id == item_id:
                item.done = True
                self._save(next_id, items)
                return item
        raise KeyError(item_id)

    def remove(self, item_id: int) -> TodoItem:
        """Remove an item; raises KeyError if the id is unknown."""
        next_id, items = self._load()
        for index, item in enumerate(items):
            if item.id == item_id:
                del items[index]
                self._save(next_id, items)
                return item
        raise KeyError(item_id)
