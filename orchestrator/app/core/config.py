# Application settings, read from environment variables and the .env file.
# Includes models, budgets, GitHub options and the fix-attempt limit.
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    """All application settings, read from environment variables and the .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Coding Agent Orchestrator"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"

    database_url: str = "sqlite:///./orchestrator.db"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Repository and Claude Code execution settings
    repository_path: Path | None = None
    claude_command: str = "claude"
    # Headless agents cannot answer permission prompts, so they must be told
    # what they may do. acceptEdits allows file edits inside the worktree; the
    # allowlist adds only test, lint and read-only git commands.
    claude_permission_mode: str | None = "acceptEdits"
    claude_allowed_tools: str = (
        "Bash(python -m pytest:*),Bash(python -m ruff:*),Bash(git status:*),"
        "Bash(git diff:*),Bash(git log:*),Bash(git show:*)"
    )

    # Agent execution limits
    max_concurrent_agents: int = Field(default=2, ge=1, le=20)
    agent_timeout_seconds: int = Field(default=1800, ge=1)
    verification_timeout_seconds: int = Field(default=300, ge=1)
    # Model and effort routing defaults
    default_model: str = "sonnet"
    default_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    # Per-role routing used by the multi-agent pipeline. None means the model and
    # effort written in the role's .claude/agents/<role>.md file are used, and the
    # defaults above if that file does not set them.
    builder_model: str | None = None
    builder_effort: EffortLevel | None = None
    tester_model: str | None = None
    tester_effort: EffortLevel | None = None
    reviewer_model: str | None = None
    reviewer_effort: EffortLevel | None = None

    # Verification gate
    verification_ruff_target: str = "orchestrator"

    # GitHub issue intake
    issue_source: Literal["gh", "file", "project", "jira"] = "gh"
    issue_file_path: Path | None = None
    github_repo: str | None = None
    # "gh" uses the GitHub CLI; "rest" calls the GitHub API directly (no CLI).
    github_backend: Literal["gh", "rest"] = "gh"
    # Needed by the rest backend only to write (comments, pull requests).
    github_token: SecretStr | None = None
    # Only issues carrying this label are picked up (gh source only).
    issue_label: str | None = "agent-task"

    # GitHub Projects (v2) board. With issue_source="project" the issues in the
    # board's Todo column are picked up, and the orchestrator moves each card
    # through the columns as its agents progress. Projects need the GraphQL API;
    # a user-owned board needs a classic token with the `project` scope.
    project_owner: str | None = None
    project_number: int | None = Field(default=None, ge=1)
    # Several boards, one per project: "4,5,6". Combined with project_number.
    project_numbers: str | None = None
    project_owner_type: Literal["user", "org"] = "user"
    # Falls back to github_token when not set.
    project_token: SecretStr | None = None

    # Jira Cloud. With issue_source="jira" the issues of jira_project that sit in
    # jira_trigger_status are picked up (or those matching jira_jql). Their Jira
    # status follows the pipeline and progress is commented on the Jira issue.
    jira_base_url: str | None = None  # https://your-site.atlassian.net
    jira_email: str | None = None
    jira_api_token: SecretStr | None = None
    jira_project: str | None = None  # project key, e.g. "PROJ"
    jira_trigger_status: str = "To Do"
    jira_jql: str | None = None  # overrides jira_project and jira_trigger_status
    # Issue types that are only containers: they give their Tasks context, but are
    # not built themselves. Comma-separated; empty picks up every type.
    jira_skip_types: str = "Epic,Story"
    # Overrides of the default board-column -> Jira status map, for example
    # "Building=In Progress,PR open=In Review,Failed=Blocked". "-" means no move.
    jira_status_map: str | None = None

    # Where the agents may put files for a Jira task: its code goes in
    # <jira_code_root>/<project name>/ and its tests in <jira_tests_root>/.
    # Anything else they touch sends the task back to the builder. An empty
    # jira_code_root turns the rule off.
    jira_code_root: str = "orchestrator/examples"
    jira_tests_root: str = "orchestrator/tests/examples"

    # Work out which tasks depend on each other (Jira only): a task that needs
    # another's code waits until that task's pull request is merged.
    plan_dependencies: bool = True

    # Live intake: poll for new issues and run the pipeline automatically.
    # 0 disables polling.
    poll_interval_seconds: int = Field(default=0, ge=0)
    # Post progress/result comments on the GitHub issue (opt-in, writes to GitHub).
    comment_on_issues: bool = False

    # Publishing (opt-in, writes to GitHub): push every agent branch, and open
    # a pull request into base_branch when the reviewer approves.
    publish_branches: bool = False
    open_pull_request: bool = False
    git_remote: str = "origin"
    base_branch: str = "main"

    # How many times the builder may be sent back to fix problems found by the
    # tester, the verification checks or the reviewer. 0 disables fixing.
    max_fix_attempts: int = Field(default=2, ge=0, le=5)

    # Optional budget guardrail; None means no configured limit.
    max_task_cost_usd: float | None = Field(
        default=None,
        gt=0,
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
