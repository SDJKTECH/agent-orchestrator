import subprocess
from pathlib import Path

import pytest

from orchestrator.app.exceptions import RepositoryError
from orchestrator.app.git_manager import (
    create_worktree,
    list_worktrees,
    remove_worktree,
    validate_repository,
)


def test_validate_repository_returns_git_root(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", str(tmp_path)],
        check=True,
        capture_output=True,
    )

    result = validate_repository(tmp_path)

    assert result == tmp_path.resolve()


def test_validate_repository_rejects_missing_path(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing"

    with pytest.raises(RepositoryError, match="does not exist"):
        validate_repository(missing_path)


def test_validate_repository_rejects_non_git_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(RepositoryError, match="Not a valid Git repository"):
        validate_repository(tmp_path)


def test_create_worktree_creates_branch_and_directory(
    tmp_path: Path,
) -> None:
    from orchestrator.app.git_manager import create_worktree

    repository = tmp_path / "repository"
    repository.mkdir()

    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "test@example.com",
        ],
        check=True,
    )

    (repository / "README.md").write_text("Test repository\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "README.md"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    worktrees_root = tmp_path / "worktrees"

    result = create_worktree(
        repository,
        worktrees_root,
        "task-001",
    )

    assert result == (worktrees_root / "task-001").resolve()
    assert (result / "README.md").read_text(encoding="utf-8") == "Test repository\n"

    branches = subprocess.run(
        ["git", "-C", str(repository), "branch", "--list", "agent/task-001"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "agent/task-001" in branches.stdout


def test_create_worktree_rejects_existing_destination(
    tmp_path: Path,
) -> None:
    from orchestrator.app.git_manager import create_worktree

    repository = tmp_path / "repository"
    repository.mkdir()

    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "test@example.com",
        ],
        check=True,
    )
    (repository / "README.md").write_text("Test\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "README.md"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    worktrees_root = tmp_path / "worktrees"
    (worktrees_root / "task-001").mkdir(parents=True)

    with pytest.raises(RepositoryError, match="destination already exists"):
        create_worktree(
            repository,
            worktrees_root,
            "task-001",
        )


def test_list_worktrees_includes_main_and_task_worktree(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "test@example.com",
        ],
        check=True,
    )
    (repository / "README.md").write_text("Test\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "README.md"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    worktrees_root = tmp_path / "worktrees"
    create_worktree(repository, worktrees_root, "task-list")

    result = list_worktrees(repository)

    assert len(result) == 2
    assert any(item.get("branch") == "main" for item in result)
    assert any(item.get("branch") == "agent/task-list" for item in result)


def test_remove_worktree_removes_registered_task_worktree(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "test@example.com",
        ],
        check=True,
    )
    (repository / "README.md").write_text("Test\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "README.md"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    worktrees_root = tmp_path / "worktrees"
    target = create_worktree(repository, worktrees_root, "task-remove")

    remove_worktree(repository, worktrees_root, "task-remove")

    assert not target.exists()
    assert not any(
        item.get("branch") == "agent/task-remove" for item in list_worktrees(repository)
    )
    assert repository.exists()
