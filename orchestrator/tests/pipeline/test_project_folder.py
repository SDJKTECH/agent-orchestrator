from types import SimpleNamespace

import pytest

from orchestrator.app.pipeline.project_folder import (
    FolderRule,
    folder_name,
    folder_rule,
)

CODE_ROOT = "orchestrator/examples"
TESTS_ROOT = "orchestrator/tests/examples"


def jira_task(project: str = "Snake Game", key: str | None = "SG-1"):
    return SimpleNamespace(
        jira_key=key,
        milestone=SimpleNamespace(project=SimpleNamespace(name=project)),
    )


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ("Snake Game", "snake_game"),
        ("CSV-Summariser", "csv_summariser"),
        ("  Task   Dashboard!  ", "task_dashboard"),
        ("2048", "project_2048"),
        ("!!!", "untitled"),
    ],
)
def test_folder_name_is_a_package_safe_name(project: str, expected: str) -> None:
    assert folder_name(project) == expected


def test_rule_points_at_the_project_folder() -> None:
    rule = folder_rule(jira_task(), CODE_ROOT, TESTS_ROOT)

    assert rule == FolderRule(
        folder="snake_game",
        code_dir="orchestrator/examples/snake_game/",
        tests_dir="orchestrator/tests/examples/",
    )


def test_rule_ignores_trailing_slashes_in_the_roots() -> None:
    rule = folder_rule(jira_task(), "orchestrator/examples/", "orchestrator/t/")

    assert rule.code_dir == "orchestrator/examples/snake_game/"
    assert rule.tests_dir == "orchestrator/t/"


@pytest.mark.parametrize(
    "task",
    [
        jira_task(key=None),
        SimpleNamespace(jira_key="SG-1", milestone=None),
    ],
)
def test_no_rule_for_tasks_that_are_not_jira_tasks(task) -> None:
    assert folder_rule(task, CODE_ROOT, TESTS_ROOT) is None


@pytest.mark.parametrize(
    ("code_root", "tests_root"), [("", TESTS_ROOT), (CODE_ROOT, "")]
)
def test_no_rule_when_a_root_is_empty(code_root: str, tests_root: str) -> None:
    assert folder_rule(jira_task(), code_root, tests_root) is None


def test_allows_only_the_project_code_and_tests() -> None:
    rule = folder_rule(jira_task(), CODE_ROOT, TESTS_ROOT)

    allowed = [
        "orchestrator/examples/snake_game/engine.py",
        "orchestrator/examples/snake_game/sub/deep.py",
        "orchestrator/tests/examples/test_snake_game_engine.py",
        "orchestrator/tests/examples/snake_game/__init__.py",
        "Orchestrator/Examples/Snake_Game/app.py",
    ]
    denied = [
        "orchestrator/app/main.py",
        "orchestrator/examples/csv_summariser/cli.py",
        "orchestrator/examples/snake_game_old/x.py",
        "orchestrator/examples/snake_gamer.py",
        "orchestrator/tests/examples/test_csv_summariser_cli.py",
        "orchestrator/tests/examples/test_other.py",
        "snake_game/engine.py",
        "requirements.txt",
        ".github/workflows/ci.yml",
    ]

    assert rule.outside(allowed) == []
    assert rule.outside(denied) == denied


def test_describe_names_the_locations() -> None:
    text = folder_rule(jira_task(), CODE_ROOT, TESTS_ROOT).describe()

    assert "orchestrator/examples/snake_game/" in text
    assert "orchestrator/tests/examples/" in text
    assert "test_snake_game_" in text
