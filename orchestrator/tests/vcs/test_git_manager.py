import subprocess
from pathlib import Path

import pytest

from orchestrator.app.core.exceptions import RepositoryError
from orchestrator.app.vcs.git_manager import (
    create_worktree,
    discard_leftover_worktree,
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
    from orchestrator.app.vcs.git_manager import create_worktree

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
    from orchestrator.app.vcs.git_manager import create_worktree

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


def test_discard_leftover_worktree_clears_a_folder_git_no_longer_knows(
    tmp_path: Path,
) -> None:
    from orchestrator.tests.helpers import init_repository

    repo = tmp_path / "repo"
    init_repository(repo)
    root = tmp_path / "wt"
    worktree = create_worktree(repo, root, "task-9")
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
        check=True,
        capture_output=True,
    )
    leftover = root / "task-9"
    leftover.mkdir()
    (leftover / "stuck.txt").write_text("x", encoding="utf-8")

    discard_leftover_worktree(repo, root, "task-9")

    assert not leftover.exists()
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "agent/task-9"],
        capture_output=True,
        text=True,
    ).stdout
    assert branches.strip() == ""
    assert create_worktree(repo, root, "task-9").is_dir()


def test_discard_leftover_worktree_refuses_a_registered_worktree(
    tmp_path: Path,
) -> None:
    from orchestrator.tests.helpers import init_repository

    repo = tmp_path / "repo"
    init_repository(repo)
    root = tmp_path / "wt"
    worktree = create_worktree(repo, root, "task-9")

    with pytest.raises(RepositoryError, match="still registered"):
        discard_leftover_worktree(repo, root, "task-9")

    assert worktree.is_dir()


def test_changed_paths_lists_modified_new_and_deleted_files(tmp_path: Path) -> None:
    from orchestrator.app.vcs.git_manager import changed_paths
    from orchestrator.tests.helpers import init_repository

    repo = tmp_path / "repo"
    init_repository(repo)
    (repo / "keep.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "more"],
        check=True,
        capture_output=True,
    )

    assert changed_paths(repo) == []

    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "keep.txt").unlink()
    (repo / "pkg").mkdir()
    (repo / "pkg" / "new file.py").write_text("x = 1\n", encoding="utf-8")

    assert changed_paths(repo) == ["README.md", "keep.txt", "pkg/new file.py"]
