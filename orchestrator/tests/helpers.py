"""Helpers shared by tests in several packages."""

import subprocess
from pathlib import Path

from orchestrator.app.github.intake import Issue


class StaticIssueSource:
    """An issue source that returns a fixed list."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues

    def list_issues(self) -> list[Issue]:
        return self.issues


def init_repository(path: Path) -> None:
    """Create a git repository with one commit on `main`."""
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    (path / "README.md").write_text("Test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )
