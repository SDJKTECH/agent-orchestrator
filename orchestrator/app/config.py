from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
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

    # Per-role routing used by the multi-agent pipeline. None falls back to
    # the defaults above.
    builder_model: str | None = None
    builder_effort: EffortLevel | None = "high"
    tester_model: str | None = "haiku"
    tester_effort: EffortLevel | None = "medium"
    reviewer_model: str | None = "opus"
    reviewer_effort: EffortLevel | None = "high"

    # Verification gate
    verification_ruff_target: str = "orchestrator"

    # GitHub issue intake
    issue_source: Literal["gh", "file"] = "gh"
    issue_file_path: Path | None = None
    github_repo: str | None = None
    # "gh" uses the GitHub CLI; "rest" calls the GitHub API directly (no CLI).
    github_backend: Literal["gh", "rest"] = "gh"
    # Needed by the rest backend only to write (comments, pull requests).
    github_token: SecretStr | None = None
    # Only issues carrying this label are picked up (gh source only).
    issue_label: str | None = "agent-task"

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

    # Optional budget guardrail; None means no configured limit.
    max_task_cost_usd: float | None = Field(
        default=None,
        gt=0,
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
