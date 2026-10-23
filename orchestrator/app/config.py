from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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
    # Only issues carrying this label are picked up (gh source only).
    issue_label: str | None = "agent-task"

    # Live intake: poll for new issues and run the pipeline automatically.
    # 0 disables polling.
    poll_interval_seconds: int = Field(default=0, ge=0)
    # Post progress/result comments on the GitHub issue (opt-in, writes to GitHub).
    comment_on_issues: bool = False

    # Optional budget guardrail; None means no configured limit.
    max_task_cost_usd: float | None = Field(
        default=None,
        gt=0,
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
