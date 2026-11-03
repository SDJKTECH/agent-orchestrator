import json
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.examples.todo_app import store as store_module
from orchestrator.examples.todo_app.cli import main
from orchestrator.examples.todo_app.store import TodoItem, TodoStore

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# store.py
# ---------------------------------------------------------------------------


def test_store_list_returns_empty_when_file_missing(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")

    assert store.list_items() == []
    assert not (tmp_path / "todo.json").exists()


def test_store_add_assigns_first_id_one_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"
    store = TodoStore(path)

    item = store.add("Buy milk")

    assert item == TodoItem(id=1, title="Buy milk", done=False)
    assert store.list_items() == [item]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "next_id": 2,
        "items": [{"id": 1, "title": "Buy milk", "done": False}],
    }


def test_store_add_strips_surrounding_whitespace_from_title(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")

    item = store.add("  Walk the dog \n")

    assert item.title == "Walk the dog"


@pytest.mark.parametrize("title", ["", "   ", "\t\n"])
def test_store_add_rejects_empty_or_blank_title_without_writing(
    tmp_path: Path, title: str
) -> None:
    path = tmp_path / "todo.json"
    store = TodoStore(path)

    with pytest.raises(ValueError, match="must not be empty"):
        store.add(title)

    assert not path.exists()


def test_store_add_preserves_unicode_titles(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")

    store.add("Café ☕ 買い物")

    assert TodoStore(tmp_path / "todo.json").list_items()[0].title == "Café ☕ 買い物"


def test_store_ids_increase_sequentially(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")

    ids = [store.add(f"task {n}").id for n in range(3)]

    assert ids == [1, 2, 3]


def test_store_ids_are_not_reused_after_removing_last_item(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")
    store.add("first")
    store.add("second")

    store.remove(2)
    new_item = store.add("third")

    assert new_item.id == 3


def test_store_ids_are_not_reused_after_removing_all_items(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")
    store.add("first")
    store.remove(1)

    assert store.add("second").id == 2
    assert store.list_items() == [TodoItem(id=2, title="second")]


def test_store_next_id_persists_across_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"
    TodoStore(path).add("first")
    TodoStore(path).add("second")
    TodoStore(path).remove(2)

    assert TodoStore(path).add("third").id == 3


def test_store_mark_done_sets_flag_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"
    store = TodoStore(path)
    store.add("first")
    store.add("second")

    marked = store.mark_done(2)

    assert marked == TodoItem(id=2, title="second", done=True)
    reloaded = TodoStore(path).list_items()
    assert [(item.id, item.done) for item in reloaded] == [(1, False), (2, True)]


def test_store_mark_done_is_idempotent(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")
    store.add("first")
    store.mark_done(1)

    assert store.mark_done(1).done is True


def test_store_mark_done_unknown_id_raises_key_error(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")
    store.add("first")

    with pytest.raises(KeyError):
        store.mark_done(99)


def test_store_mark_done_on_missing_file_raises_key_error(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")

    with pytest.raises(KeyError):
        store.mark_done(1)


def test_store_remove_returns_item_and_persists_removal(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"
    store = TodoStore(path)
    store.add("first")
    store.add("second")
    store.add("third")

    removed = store.remove(2)

    assert removed == TodoItem(id=2, title="second")
    assert [item.id for item in TodoStore(path).list_items()] == [1, 3]


def test_store_remove_unknown_id_raises_key_error_and_keeps_items(
    tmp_path: Path,
) -> None:
    path = tmp_path / "todo.json"
    store = TodoStore(path)
    store.add("first")

    with pytest.raises(KeyError):
        store.remove(42)

    assert [item.title for item in TodoStore(path).list_items()] == ["first"]


def test_store_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "todo.json"

    TodoStore(path).add("first")

    assert path.is_file()


def test_store_accepts_string_and_pathlike_paths(tmp_path: Path) -> None:
    str_path = tmp_path / "a.json"

    TodoStore(str(str_path)).add("from str")

    assert TodoStore(str_path).list_items()[0].title == "from str"


def test_store_save_leaves_no_temp_files_behind(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todo.json")
    store.add("first")
    store.add("second")
    store.mark_done(1)
    store.remove(2)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["todo.json"]


def test_store_failed_replace_keeps_original_file_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "todo.json"
    store = TodoStore(path)
    store.add("original")
    original_bytes = path.read_bytes()

    def fail_replace(src: object, dst: object) -> None:
        raise OSError("disk exploded")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="disk exploded"):
        store.add("never saved")

    assert path.read_bytes() == original_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == ["todo.json"]


def test_store_uses_temp_file_in_same_directory_then_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "todo.json"
    seen: list[tuple[Path, Path]] = []
    real_replace = store_module.os.replace

    def spy_replace(src: str, dst: Path) -> None:
        seen.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(store_module.os, "replace", spy_replace)

    TodoStore(path).add("first")

    assert len(seen) == 1
    src, dst = seen[0]
    assert src.parent == tmp_path
    assert src != path
    assert dst == path


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "",
        "[]",
        '"just a string"',
        "42",
        '{"items": []}',
        '{"next_id": 1}',
        '{"next_id": "1", "items": []}',
        '{"next_id": true, "items": []}',
        '{"next_id": 1, "items": {"a": 1}}',
        '{"next_id": 1, "items": ["oops"]}',
        '{"next_id": 1, "items": [{"id": 1, "title": "x"}]}',
        '{"next_id": 1, "items": [{"id": "1", "title": "x", "done": false}]}',
        '{"next_id": 1, "items": [{"id": true, "title": "x", "done": false}]}',
        '{"next_id": 1, "items": [{"id": 1, "title": 5, "done": false}]}',
        '{"next_id": 1, "items": [{"id": 1, "title": "x", "done": "no"}]}',
    ],
)
def test_store_corrupt_file_raises_value_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "todo.json"
    path.write_text(content, encoding="utf-8")
    store = TodoStore(path)

    with pytest.raises(ValueError, match="corrupt"):
        store.list_items()

    with pytest.raises(ValueError, match="corrupt"):
        store.add("new")

    assert path.read_text(encoding="utf-8") == content


def test_store_corrupt_file_error_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"
    path.write_text("{oops", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        TodoStore(path).list_items()

    assert str(path) in str(excinfo.value)


def test_store_undecodable_bytes_raise_value_error(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"
    path.write_bytes(b"\xff\xfe\x00garbage")

    with pytest.raises(ValueError):
        TodoStore(path).list_items()


def test_store_next_id_is_raised_above_existing_ids(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"
    _write_json(
        path,
        {
            "next_id": 1,
            "items": [{"id": 5, "title": "hand-edited", "done": False}],
        },
    )

    item = TodoStore(path).add("next")

    assert item.id == 6


def test_store_empty_items_list_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"
    _write_json(path, {"next_id": 7, "items": []})

    assert TodoStore(path).list_items() == []
    assert TodoStore(path).add("first").id == 7


# ---------------------------------------------------------------------------
# cli.py
# ---------------------------------------------------------------------------


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_add_prints_confirmation_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "todo.json"

    code, out, err = _run(capsys, "--file", str(path), "add", "Buy milk")

    assert code == 0
    assert out == "Added 1: Buy milk\n"
    assert err == ""
    assert TodoStore(path).list_items() == [TodoItem(id=1, title="Buy milk")]


def test_cli_list_empty_store_prints_no_items(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(capsys, "--file", str(tmp_path / "todo.json"), "list")

    assert code == 0
    assert out == "No items.\n"
    assert err == ""


def test_cli_list_shows_ids_done_marks_and_titles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(tmp_path / "todo.json")
    _run(capsys, "--file", path, "add", "first")
    _run(capsys, "--file", path, "add", "second")
    _run(capsys, "--file", path, "done", "2")

    code, out, _ = _run(capsys, "--file", path, "list")

    assert code == 0
    assert out == "1. [ ] first\n2. [x] second\n"


def test_cli_done_marks_item_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(tmp_path / "todo.json")
    _run(capsys, "--file", path, "add", "Buy milk")

    code, out, err = _run(capsys, "--file", path, "done", "1")

    assert code == 0
    assert out == "Marked 1 as done: Buy milk\n"
    assert err == ""
    assert TodoStore(path).list_items()[0].done is True


def test_cli_done_unknown_id_prints_error_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(tmp_path / "todo.json")
    _run(capsys, "--file", path, "add", "first")

    code, out, err = _run(capsys, "--file", path, "done", "99")

    assert code == 1
    assert out == ""
    assert err == "error: no item with id 99\n"


def test_cli_remove_deletes_item_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(tmp_path / "todo.json")
    _run(capsys, "--file", path, "add", "first")
    _run(capsys, "--file", path, "add", "second")

    code, out, err = _run(capsys, "--file", path, "remove", "1")

    assert code == 0
    assert out == "Removed 1: first\n"
    assert err == ""
    assert [item.title for item in TodoStore(path).list_items()] == ["second"]


def test_cli_remove_unknown_id_prints_error_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(capsys, "--file", str(tmp_path / "todo.json"), "remove", "3")

    assert code == 1
    assert out == ""
    assert err == "error: no item with id 3\n"


@pytest.mark.parametrize("title", ["", "   "])
def test_cli_add_empty_title_prints_error_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], title: str
) -> None:
    path = tmp_path / "todo.json"

    code, out, err = _run(capsys, "--file", str(path), "add", title)

    assert code == 1
    assert out == ""
    assert err.startswith("error: ")
    assert "must not be empty" in err
    assert not path.exists()


def test_cli_corrupt_file_prints_error_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "todo.json"
    path.write_text("{broken", encoding="utf-8")

    for command in (["list"], ["add", "x"], ["done", "1"], ["remove", "1"]):
        code, out, err = _run(capsys, "--file", str(path), *command)

        assert code == 1, command
        assert out == "", command
        assert err.startswith("error: ") and "corrupt" in err, command
    assert path.read_text(encoding="utf-8") == "{broken"


def test_cli_ids_are_not_reused_across_invocations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(tmp_path / "todo.json")
    _run(capsys, "--file", path, "add", "first")
    _run(capsys, "--file", path, "add", "second")
    _run(capsys, "--file", path, "remove", "2")

    code, out, _ = _run(capsys, "--file", path, "add", "third")

    assert code == 0
    assert out == "Added 3: third\n"


def test_cli_default_file_is_todo_json_in_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    code, _, _ = _run(capsys, "add", "Default location")

    assert code == 0
    assert json.loads((tmp_path / "todo.json").read_text(encoding="utf-8"))[
        "items"
    ] == [{"id": 1, "title": "Default location", "done": False}]


def test_cli_file_option_selects_storage_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "custom.json"

    _run(capsys, "--file", str(custom), "add", "custom item")

    assert custom.is_file()
    assert not (tmp_path / "todo.json").exists()


def test_cli_list_on_missing_default_file_does_not_create_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    code, out, _ = _run(capsys, "list")

    assert code == 0
    assert out == "No items.\n"
    assert not (tmp_path / "todo.json").exists()


def test_cli_missing_subcommand_exits_with_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--file", str(tmp_path / "todo.json")])

    assert excinfo.value.code == 2


def test_cli_unknown_subcommand_exits_with_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--file", str(tmp_path / "todo.json"), "frobnicate"])

    assert excinfo.value.code == 2


@pytest.mark.parametrize("command", ["done", "remove"])
def test_cli_non_integer_id_exits_with_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--file", str(tmp_path / "todo.json"), command, "abc"])

    assert excinfo.value.code == 2


def test_cli_add_requires_title_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--file", str(tmp_path / "todo.json"), "add"])

    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# __main__.py
# ---------------------------------------------------------------------------


def test_python_m_entry_point_adds_item(tmp_path: Path) -> None:
    path = tmp_path / "todo.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.examples.todo_app",
            "--file",
            str(path),
            "add",
            "Buy milk",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Added 1: Buy milk\n"
    assert TodoStore(path).list_items() == [TodoItem(id=1, title="Buy milk")]


def test_python_m_entry_point_propagates_error_exit_code(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.examples.todo_app",
            "--file",
            str(tmp_path / "todo.json"),
            "done",
            "7",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == "error: no item with id 7\n"
