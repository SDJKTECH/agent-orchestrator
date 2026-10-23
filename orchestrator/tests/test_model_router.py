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


def test_router_uses_role_settings_between_override_and_default() -> None:
    settings = Settings(_env_file=None)
    router = ModelRouter(settings)

    builder = router.route(role="builder")
    tester = router.route(role="tester")
    reviewer = router.route(role="reviewer")
    override = router.route(role="reviewer", model="sonnet", effort="low")

    assert (builder.model, builder.effort) == ("sonnet", "high")
    assert (tester.model, tester.effort) == ("haiku", "medium")
    assert (reviewer.model, reviewer.effort) == ("opus", "high")
    assert (override.model, override.effort) == ("sonnet", "low")


def test_router_falls_back_to_defaults_for_unknown_role() -> None:
    decision = ModelRouter(Settings(_env_file=None)).route(role="architect")

    assert decision.model == "sonnet"
    assert decision.effort == "medium"
