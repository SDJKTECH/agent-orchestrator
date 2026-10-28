# Git helpers: validate a repo, create and remove worktrees, commit and push.
# Branch and worktree names are checked, and git is run without a shell.
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from orchestrator.app.core.exceptions import RepositoryError


def validate_repository(repository_path: Path) -> Path:
    """Validate a local Git repository and return its root path."""
    path = repository_path.expanduser().resolve()

    if not path.exists():
        raise RepositoryError(f"Repository path does not exist: {path}")

    if not path.is_dir():
        raise RepositoryError(f"Repository path is not a directory: {path}")

    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "rev-parse",
                "--show-toplevel",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise RepositoryError(
            "Git executable was not found. Install Git and ensure it is on PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RepositoryError(f"Not a valid Git repository: {path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError(f"Git repository validation timed out: {path}") from exc

    return Path(result.stdout.strip()).resolve()


def _run_git(
    repository_path: Path,
    *args: str,
    timeout: int = 30,
) -> str:
    """Run a Git command and return its standard output."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_path), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except FileNotFoundError as exc:
        raise RepositoryError("Git executable was not found.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or "Unknown Git error"
        raise RepositoryError(message) from exc
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError("Git command timed out.") from exc


def create_worktree(
    repository_path: Path,
    worktrees_root: Path,
    task_id: str,
    base_branch: str = "main",
) -> Path:
    """Create an isolated branch and worktree for a task."""
    repository = validate_repository(repository_path)
    task_id = task_id.strip()

    if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise RepositoryError(
            "task_id may contain only letters, numbers, underscores, and hyphens."
        )

    if not re.fullmatch(r"[A-Za-z0-9._/-]+", base_branch):
        raise RepositoryError("Invalid base branch name.")

    if base_branch.startswith("-") or ".." in base_branch:
        raise RepositoryError("Invalid base branch name.")

    worktrees_root = worktrees_root.expanduser().resolve()

    # Keep task worktrees outside the source repository.
    if worktrees_root == repository or repository in worktrees_root.parents:
        raise RepositoryError(
            "The worktrees directory must be outside the source repository."
        )

    if not worktrees_root.exists():
        worktrees_root.mkdir(parents=True, exist_ok=True)

    if not worktrees_root.is_dir():
        raise RepositoryError("Worktrees path is not a directory.")

    branch_name = f"agent/{task_id}"
    worktree_path = worktrees_root / task_id

    if worktree_path.exists():
        raise RepositoryError(f"Worktree destination already exists: {worktree_path}")

    existing_branches = _run_git(
        repository,
        "branch",
        "--list",
        branch_name,
    )

    if existing_branches:
        raise RepositoryError(f"Branch already exists: {branch_name}")

    _run_git(repository, "check-ref-format", "--branch", branch_name)
    try:
        _run_git(repository, "rev-parse", "--verify", f"refs/heads/{base_branch}")
    except RepositoryError:
        # A remote branch such as origin/main is a valid base too.
        _run_git(repository, "rev-parse", "--verify", f"refs/remotes/{base_branch}")

    try:
        _run_git(
            repository,
            "worktree",
            "add",
            "--no-track",
            "-b",
            branch_name,
            str(worktree_path),
            base_branch,
        )
    except RepositoryError:
        # Don't delete a destination or branch that we didn't create.
        raise

    return worktree_path


def list_worktrees(repository_path: Path) -> list[dict[str, str]]:
    """List Git worktrees registered to a repository."""
    repository = validate_repository(repository_path)

    output = _run_git(
        repository,
        "worktree",
        "list",
        "--porcelain",
    )

    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in output.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue

        key, _, value = line.partition(" ")

        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = "true"
        elif key == "bare":
            current["bare"] = "true"
        elif key == "locked":
            current["locked"] = value or "true"
        elif key == "prunable":
            current["prunable"] = value or "true"

    if current:
        worktrees.append(current)

    return worktrees


def remove_worktree(
    repository_path: Path,
    worktrees_root: Path,
    task_id: str,
    *,
    delete_branch: bool = False,
    force_branch: bool = False,
) -> None:
    """Remove a managed worktree, optionally deleting its task branch."""
    repository = validate_repository(repository_path)
    task_id = task_id.strip()

    if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise RepositoryError(
            "task_id may contain only letters, numbers, underscores, and hyphens."
        )

    root = worktrees_root.expanduser().resolve()
    target = (root / task_id).resolve()
    expected_branch = f"agent/{task_id}"

    if target == repository or repository in target.parents:
        raise RepositoryError(
            "Refusing to remove a worktree inside the source repository."
        )

    if not target.exists() or not target.is_dir():
        raise RepositoryError(f"Worktree does not exist: {target}")

    registered = list_worktrees(repository)

    matches = [
        item for item in registered if Path(item.get("path", "")).resolve() == target
    ]

    if len(matches) != 1:
        raise RepositoryError(f"Target is not a registered worktree: {target}")

    worktree = matches[0]

    if worktree.get("bare") == "true":
        raise RepositoryError("Refusing to remove a bare repository.")

    if worktree.get("branch") != expected_branch:
        raise RepositoryError(
            f"Worktree branch does not match expected branch {expected_branch}."
        )

    _run_git(
        repository,
        "worktree",
        "remove",
        str(target),
    )

    if delete_branch:
        _run_git(
            repository,
            "branch",
            "-D" if force_branch else "-d",
            expected_branch,
        )


def discard_leftover_worktree(
    repository_path: Path, worktrees_root: Path, task_id: str
) -> None:
    """Clear what a failed `remove_worktree` left behind (Windows file locks).

    Git may forget a worktree yet fail to delete its folder. Such an unregistered
    folder inside the worktrees root is deleted, stale registrations are pruned and
    the task branch is dropped, so the worktree can be created again.
    """
    repository = validate_repository(repository_path)

    if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id.strip()):
        raise RepositoryError(
            "task_id may contain only letters, numbers, underscores, and hyphens."
        )

    root = worktrees_root.expanduser().resolve()
    target = (root / task_id.strip()).resolve()

    if root not in target.parents or target == repository:
        raise RepositoryError(f"Refusing to clear a folder outside {root}: {target}")

    if any(
        Path(item.get("path", "")).resolve() == target
        for item in list_worktrees(repository)
    ):
        raise RepositoryError(f"Worktree is still registered: {target}")

    def make_writable(function, path, _error):
        os.chmod(path, stat.S_IWRITE)
        function(path)

    if target.exists():
        shutil.rmtree(target, onerror=make_writable)

    _run_git(repository, "worktree", "prune")

    try:
        _run_git(repository, "branch", "-D", f"agent/{task_id.strip()}")
    except RepositoryError:
        pass  # No such branch.


def fetch_remote(repository_path: Path, remote: str = "origin") -> None:
    """Update the remote-tracking branches (touches no checked-out branch)."""
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", remote) or remote.startswith("-"):
        raise RepositoryError(f"Invalid remote name: {remote}")

    _run_git(repository_path, "fetch", remote, timeout=120)


def _identity_args(path: Path) -> list[str]:
    """Supply a commit identity only when the repository has none configured."""
    args: list[str] = []

    for key, default in (
        ("user.name", "Agent Orchestrator"),
        ("user.email", "agent-orchestrator@localhost"),
    ):
        try:
            configured = _run_git(path, "config", "--get", key)
        except RepositoryError:
            configured = ""

        if not configured:
            args.extend(["-c", f"{key}={default}"])

    return args


def commit_worktree(worktree_path: Path, message: str) -> bool:
    """Commit every change in a worktree. Return False if there was nothing."""
    if not message.strip():
        raise RepositoryError("Commit message cannot be empty.")

    if not _run_git(worktree_path, "status", "--porcelain"):
        return False

    _run_git(worktree_path, "add", "-A")
    _run_git(worktree_path, *_identity_args(worktree_path), "commit", "-m", message)
    return True


def push_branch(worktree_path: Path, branch: str, remote: str = "origin") -> None:
    """Push a branch and set its upstream."""
    for value in (branch, remote):
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", value) or value.startswith("-"):
            raise RepositoryError(f"Invalid branch or remote name: {value}")

    _run_git(worktree_path, "push", "-u", remote, branch, timeout=120)
