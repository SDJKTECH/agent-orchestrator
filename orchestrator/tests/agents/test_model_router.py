import pytest

from orchestrator.app.agents.model_router import ModelRouter
from orchestrator.app.core.config import Settings


def write_agent(root, role, *lines) -> None:
    folder = root / ".claude" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{role}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    settings = Settings(
        _env_file=None,
        builder_effort="high",
        tester_model="haiku",
        reviewer_model="opus",
        reviewer_effort="high",
    )
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


def test_router_uses_the_model_and_effort_written_in_the_agent_file(tmp_path) -> None:
    write_agent(
        tmp_path, "tester", "---", "name: tester", "model: opus", "effort: low", "---"
    )
    router = ModelRouter(Settings(_env_file=None, repository_path=tmp_path))

    decision = router.route(role="tester")

    assert (decision.model, decision.effort) == ("opus", "low")


def test_settings_and_overrides_beat_the_agent_file(tmp_path) -> None:
    write_agent(tmp_path, "builder", "---", "model: opus", "effort: low", "---")
    router = ModelRouter(
        Settings(_env_file=None, repository_path=tmp_path, builder_model="haiku")
    )

    assert router.route(role="builder").model == "haiku"
    assert router.route(role="builder").effort == "low"
    assert router.route(role="builder", effort="max").effort == "max"


def test_agent_file_edits_apply_without_a_restart(tmp_path) -> None:
    router = ModelRouter(Settings(_env_file=None, repository_path=tmp_path))
    write_agent(tmp_path, "builder", "---", "model: haiku", "---")
    assert router.route(role="builder").model == "haiku"

    write_agent(tmp_path, "builder", "---", "model: opus", "---")

    assert router.route(role="builder").model == "opus"


def test_a_missing_or_incomplete_agent_file_falls_back_to_defaults(tmp_path) -> None:
    write_agent(tmp_path, "reviewer", "no front matter here")
    router = ModelRouter(Settings(_env_file=None, repository_path=tmp_path))

    assert router.route(role="reviewer").model == "sonnet"
    assert router.route(role="builder").effort == "medium"


def test_the_worktree_copy_is_used_when_the_repository_has_none(tmp_path) -> None:
    worktree = tmp_path / "wt"
    write_agent(worktree, "tester", "---", "model: opus", "---")
    router = ModelRouter(Settings(_env_file=None))

    decision = router.route(
        role="tester", definition=worktree / ".claude" / "agents" / "tester.md"
    )

    assert decision.model == "opus"
