# Chooses the Claude model and effort level for each agent role.
# Explicit arguments win, then per-role settings, then the model and effort written
# in the role's .claude/agents/<role>.md file, then the defaults.
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from orchestrator.app.core.config import Settings, get_settings

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]

ALLOWED_EFFORTS: set[str] = {"low", "medium", "high", "xhigh", "max"}


def read_agent_frontmatter(path: Path) -> dict[str, str]:
    """The `key: value` lines between the `---` markers at the top of an agent file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    if not lines or lines[0].strip() != "---":
        return {}

    values: dict[str, str] = {}

    for line in lines[1:]:
        if line.strip() == "---":
            break

        key, _, value = line.partition(":")
        if value.strip():
            values[key.strip()] = value.strip()

    return values


@dataclass(frozen=True)
class ModelRoutingDecision:
    """Resolved model and effort for an agent execution."""

    model: str
    effort: EffortLevel


class ModelRouter:
    """Resolve model and effort settings for agent execution."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _written_in_agent_file(
        self, role: str | None, definition: Path | None
    ) -> dict[str, str]:
        if not role:
            return {}

        repository = getattr(self.settings, "repository_path", None)
        candidates = [
            Path(repository) / ".claude" / "agents" / f"{role}.md"
            if repository
            else None,
            definition,
        ]

        for path in candidates:
            if path is not None and path.is_file():
                return read_agent_frontmatter(path)

        return {}

    def route(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        role: str | None = None,
        definition: Path | None = None,
    ) -> ModelRoutingDecision:
        """Resolve overrides, role settings, the agent file, then defaults.

        The agent file is read on every call, so editing it applies to the next
        agent without a restart. The repository's copy is preferred (it holds edits
        not yet committed); `definition` is used when the repository has none.
        """
        role_model = getattr(self.settings, f"{role}_model", None) if role else None
        role_effort = getattr(self.settings, f"{role}_effort", None) if role else None

        written = self._written_in_agent_file(role, definition)

        resolved_model = (
            model or role_model or written.get("model") or self.settings.default_model
        )
        resolved_effort = (
            effort
            or role_effort
            or written.get("effort")
            or self.settings.default_effort
        )

        if not resolved_model.strip():
            raise ValueError("Model cannot be empty.")

        if resolved_effort not in ALLOWED_EFFORTS:
            raise ValueError(f"Unsupported effort level: {resolved_effort}")

        return ModelRoutingDecision(
            model=resolved_model,
            effort=resolved_effort,  # type: ignore[arg-type]
        )
