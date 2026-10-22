import pytest
from pydantic import ValidationError

from orchestrator.app.config import Settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.database_url == "sqlite:///./orchestrator.db"
    assert settings.max_concurrent_agents == 2
    assert settings.agent_timeout_seconds == 1800
    assert settings.default_model == "sonnet"
    assert settings.default_effort == "medium"


def test_environment_variables_override_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_CONCURRENT_AGENTS", "4")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.max_concurrent_agents == 4
    assert settings.log_level == "DEBUG"


def test_invalid_concurrency_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_concurrent_agents=0)


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="TRACE")


def test_model_and_effort_environment_variables_override_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEFAULT_MODEL", "opus")
    monkeypatch.setenv("DEFAULT_EFFORT", "high")

    settings = Settings(_env_file=None)

    assert settings.default_model == "opus"
    assert settings.default_effort == "high"


def test_invalid_default_effort_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, default_effort="invalid")
