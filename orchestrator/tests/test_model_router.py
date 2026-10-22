import pytest

from orchestrator.app.config import Settings
from orchestrator.app.model_router import ModelRouter


def test_router_uses_configured_defaults() -> None:
    settings = Settings(
        _env_file=None,
        default_model="sonnet",
        default_effort="medium",
    )

    decision = ModelRouter(settings).route()

    assert decision.model == "sonnet"
    assert decision.effort == "medium"


def test_router_respects_explicit_overrides() -> None:
    router = ModelRouter(Settings(_env_file=None, default_model="sonnet"))

    decision = router.route(model="opus", effort="high")

    assert decision.model == "opus"
    assert decision.effort == "high"


def test_router_rejects_empty_model() -> None:
    router = ModelRouter(Settings(_env_file=None))

    with pytest.raises(ValueError, match="Model cannot be empty"):
        router.route(model="   ")


def test_router_rejects_invalid_effort() -> None:
    router = ModelRouter(Settings(_env_file=None))

    with pytest.raises(ValueError, match="Unsupported effort"):
        router.route(effort="invalid")
