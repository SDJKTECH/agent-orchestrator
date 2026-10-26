import json

import pytest

from orchestrator.examples.task_dashboard import (
    Task,
    count_by_status,
    load_tasks,
    percent_done,
    render_html,
)
from orchestrator.examples.task_dashboard.cli import main


def _write_json(tmp_path, data, name="tasks.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- load_tasks: valid input -------------------------------------------------


def test_load_tasks_reads_all_fields(tmp_path):
    path = _write_json(
        tmp_path,
        [
            {"title": "Write docs", "status": "todo", "assignee": "ann"},
            {"title": "Build", "status": "in_progress"},
        ],
    )

    tasks = load_tasks(path)

    assert tasks == [
        Task(title="Write docs", status="todo", assignee="ann"),
        Task(title="Build", status="in_progress", assignee=None),
    ]


def test_load_tasks_accepts_empty_list(tmp_path):
    assert load_tasks(_write_json(tmp_path, [])) == []


def test_load_tasks_treats_empty_assignee_as_none(tmp_path):
    path = _write_json(tmp_path, [{"title": "x", "status": "done", "assignee": ""}])

    assert load_tasks(path)[0].assignee is None


def test_load_tasks_accepts_each_known_status(tmp_path):
    path = _write_json(
        tmp_path,
        [
            {"title": t, "status": s}
            for t, s in [("a", "todo"), ("b", "in_progress"), ("c", "done")]
        ],
    )

    assert [task.status for task in load_tasks(path)] == [
        "todo",
        "in_progress",
        "done",
    ]


# --- load_tasks: validation errors -------------------------------------------


def test_invalid_json_raises_value_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[{'title': ", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON"):
        load_tasks(path)


def test_missing_file_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        load_tasks(tmp_path / "does-not-exist.json")


def test_top_level_object_raises_value_error(tmp_path):
    path = _write_json(tmp_path, {"title": "x", "status": "done"})

    with pytest.raises(ValueError, match="list of tasks"):
        load_tasks(path)


def test_non_object_item_raises_value_error(tmp_path):
    path = _write_json(tmp_path, [{"title": "ok", "status": "done"}, "oops"])

    with pytest.raises(ValueError, match="task #2: expected an object"):
        load_tasks(path)


def test_missing_title_raises_value_error(tmp_path):
    path = _write_json(tmp_path, [{"status": "todo"}])

    with pytest.raises(ValueError, match="task #1: missing or empty 'title'"):
        load_tasks(path)


@pytest.mark.parametrize("title", ["", "   ", 42, None])
def test_blank_or_non_string_title_raises_value_error(tmp_path, title):
    path = _write_json(tmp_path, [{"title": title, "status": "todo"}])

    with pytest.raises(ValueError, match="missing or empty 'title'"):
        load_tasks(path)


def test_unknown_status_raises_value_error_naming_the_task(tmp_path):
    path = _write_json(tmp_path, [{"title": "Ship", "status": "blocked"}])

    with pytest.raises(ValueError, match="unknown status 'blocked'") as info:
        load_tasks(path)

    assert "'Ship'" in str(info.value)
    assert "todo, in_progress, done" in str(info.value)


def test_missing_status_raises_value_error(tmp_path):
    path = _write_json(tmp_path, [{"title": "Ship"}])

    with pytest.raises(ValueError, match="unknown status None"):
        load_tasks(path)


def test_non_string_assignee_raises_value_error(tmp_path):
    path = _write_json(tmp_path, [{"title": "Ship", "status": "todo", "assignee": 7}])

    with pytest.raises(ValueError, match="'assignee' must be a string"):
        load_tasks(path)


def test_error_reports_the_index_of_the_first_bad_task(tmp_path):
    path = _write_json(
        tmp_path,
        [
            {"title": "fine", "status": "todo"},
            {"title": "second", "status": "todo"},
            {"title": "third", "status": "nope"},
        ],
    )

    with pytest.raises(ValueError, match="task #3"):
        load_tasks(path)


# --- count_by_status ---------------------------------------------------------


def test_count_by_status_includes_zero_for_unused_statuses():
    tasks = [Task("a", "done"), Task("b", "done")]

    assert count_by_status(tasks) == {"todo": 0, "in_progress": 0, "done": 2}


def test_count_by_status_empty_list_is_all_zero():
    assert count_by_status([]) == {"todo": 0, "in_progress": 0, "done": 0}


def test_count_by_status_counts_mixed_statuses():
    tasks = [
        Task("a", "todo"),
        Task("b", "in_progress"),
        Task("c", "in_progress"),
        Task("d", "done"),
    ]

    assert count_by_status(tasks) == {"todo": 1, "in_progress": 2, "done": 1}


# --- percent_done ------------------------------------------------------------


def test_percent_done_is_zero_for_empty_list():
    assert percent_done([]) == 0


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["todo"], 0),
        (["in_progress"], 0),
        (["done"], 100),
        (["done", "todo"], 50),
        (["done", "todo", "todo"], 33),  # 1/3 rounds down
        (["done", "done", "todo"], 66),  # 2/3 rounds down
        (["done", "in_progress", "todo", "done"], 50),
    ],
)
def test_percent_done_rounds_down(statuses, expected):
    tasks = [Task(f"t{i}", s) for i, s in enumerate(statuses)]

    assert percent_done(tasks) == expected


def test_percent_done_only_counts_done_not_in_progress():
    tasks = [Task("a", "in_progress"), Task("b", "in_progress")]

    assert percent_done(tasks) == 0


# --- render_html -------------------------------------------------------------


def test_render_html_is_a_complete_page():
    html = render_html([Task("a", "done")])

    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html and "</html>" in html
    assert "<style>" in html
    assert "<table>" in html


def test_render_html_shows_counts_and_percent():
    tasks = [
        Task("a", "todo"),
        Task("b", "in_progress"),
        Task("c", "done"),
        Task("d", "done"),
    ]

    html = render_html(tasks)

    assert '<div class="count">1</div>' in html  # todo card
    assert '<div class="count">2</div>' in html  # done card
    assert "Progress: 50% done" in html
    assert 'style="width: 50%"' in html
    assert 'aria-valuenow="50"' in html


def test_render_html_lists_every_task_with_label_and_assignee():
    tasks = [
        Task("Alpha", "in_progress", "ann"),
        Task("Beta", "todo", None),
    ]

    html = render_html(tasks)

    assert "<td>Alpha</td>" in html
    assert 'class="status-in_progress">In progress</td>' in html
    assert "<td>ann</td>" in html
    assert "<td>Beta</td>" in html
    assert 'class="status-todo">To do</td>' in html
    assert "<td>&mdash;</td>" in html


def test_render_html_empty_list_shows_placeholder_and_zero_progress():
    html = render_html([])

    assert "No tasks." in html
    assert "Progress: 0% done" in html
    assert 'style="width: 0%"' in html


def test_render_html_escapes_task_title():
    html = render_html([Task("<b>x</b>", "todo")])

    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


def test_render_html_escapes_assignee():
    html = render_html([Task("ok", "todo", '<script>alert("hi")</script>')])

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&quot;hi&quot;)&lt;/script&gt;" in html


def test_render_html_escapes_title_loaded_from_json(tmp_path):
    path = _write_json(tmp_path, [{"title": "<b>x</b>", "status": "done"}])

    html = render_html(load_tasks(path))

    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


# --- CLI ---------------------------------------------------------------------


def test_cli_writes_output_file(tmp_path):
    src = _write_json(tmp_path, [{"title": "<b>x</b>", "status": "done"}])
    out = tmp_path / "out.html"

    assert main([str(src), "-o", str(out)]) == 0

    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>")
    assert "&lt;b&gt;x&lt;/b&gt;" in page
    assert "Progress: 100% done" in page


def test_cli_default_output_is_dashboard_html(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = _write_json(tmp_path, [{"title": "a", "status": "todo"}])

    assert main([str(src)]) == 0

    assert (
        (tmp_path / "dashboard.html")
        .read_text(encoding="utf-8")
        .startswith("<!DOCTYPE html>")
    )


def test_cli_invalid_json_prints_error_and_returns_1(tmp_path, capsys):
    src = tmp_path / "bad.json"
    src.write_text("not json", encoding="utf-8")
    out = tmp_path / "out.html"

    assert main([str(src), "-o", str(out)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: invalid JSON")
    assert not out.exists()


def test_cli_validation_error_prints_error_and_returns_1(tmp_path, capsys):
    src = _write_json(tmp_path, [{"title": "a", "status": "blocked"}])
    out = tmp_path / "out.html"

    assert main([str(src), "-o", str(out)]) == 1

    assert "unknown status 'blocked'" in capsys.readouterr().err
    assert not out.exists()


def test_cli_missing_input_file_returns_1(tmp_path, capsys):
    assert main([str(tmp_path / "missing.json"), "-o", str(tmp_path / "o.html")]) == 1

    assert "error: cannot read" in capsys.readouterr().err


def test_cli_requires_input_argument(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])

    assert exc.value.code == 2
    assert "input" in capsys.readouterr().err
