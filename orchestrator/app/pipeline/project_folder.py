# Decides where in the repository the agents may put a Jira task's files.
# Code goes in <code_root>/<project name>/, tests in <tests_root>/test_<name>*.py
# or <tests_root>/<name>/.
from dataclasses import dataclass

from orchestrator.app.core.text_utils import slugify
from orchestrator.app.db.models import Task


@dataclass(frozen=True)
class FolderRule:
    """The only places a task's changes may land."""

    folder: str
    code_dir: str
    tests_dir: str

    def _prefixes(self) -> tuple[str, ...]:
        """Lowercase path prefixes that are allowed."""
        return (
            self.code_dir.lower(),
            f"{self.tests_dir}test_{self.folder}".lower(),
            f"{self.tests_dir}{self.folder}/".lower(),
        )

    def allows(self, path: str) -> bool:
        """True when the path is inside the task's code or test location."""
        path = path.replace("\\", "/").removeprefix("./").lower()
        return path.startswith(self._prefixes())

    def outside(self, paths: list[str]) -> list[str]:
        """The paths that are not allowed."""
        return [path for path in paths if not self.allows(path)]

    def describe(self) -> str:
        """The instruction given to the agents."""
        return (
            f"Put all application code for this task inside `{self.code_dir}` "
            f"(a Python package named `{self.folder}`; add an `__init__.py` if it "
            f"is missing) and all its tests in `{self.tests_dir}` as "
            f"`test_{self.folder}_*.py` files (or in a `{self.folder}/` subfolder "
            "there with an `__init__.py`). Do not create, change or delete any "
            "other file in the repository."
        )


def folder_name(project_name: str) -> str:
    """A package-safe folder name for a project, e.g. 'Snake Game' -> snake_game."""
    name = slugify(project_name).replace("-", "_")
    return f"project_{name}" if name[0].isdigit() else name


def folder_rule(
    task: Task, code_root: str | None, tests_root: str | None
) -> FolderRule | None:
    """The rule for a Jira task, or None when the task is not restricted."""
    milestone = task.milestone

    if not code_root or not tests_root or not task.jira_key or milestone is None:
        return None

    folder = folder_name(milestone.project.name)
    return FolderRule(
        folder=folder,
        code_dir=f"{code_root.strip('/')}/{folder}/",
        tests_dir=f"{tests_root.strip('/')}/",
    )
