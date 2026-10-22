from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Model and effort routing defaults
    default_model: str = "sonnet"
    default_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    # Optional budget guardrail; None means no configured limit.
    max_task_cost_usd: float | None = Field(
        default=None,
        gt=0,
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
