from dataclasses import dataclass
from typing import Literal

from orchestrator.app.core.config import Settings, get_settings

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]

ALLOWED_EFFORTS: set[str] = {"low", "medium", "high", "xhigh", "max"}


@dataclass(frozen=True)
class ModelRoutingDecision:
    """Resolved model and effort for an agent execution."""

    model: str
    effort: EffortLevel


class ModelRouter:
    """Resolve model and effort settings for agent execution."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def route(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        role: str | None = None,
    ) -> ModelRoutingDecision:
        """Resolve explicit overrides, then role settings, then defaults."""
        role_model = getattr(self.settings, f"{role}_model", None) if role else None
        role_effort = getattr(self.settings, f"{role}_effort", None) if role else None

        resolved_model = model or role_model or self.settings.default_model
        resolved_effort = effort or role_effort or self.settings.default_effort

        if not resolved_model.strip():
            raise ValueError("Model cannot be empty.")

        if resolved_effort not in ALLOWED_EFFORTS:
            raise ValueError(f"Unsupported effort level: {resolved_effort}")

        return ModelRoutingDecision(
            model=resolved_model,
            effort=resolved_effort,  # type: ignore[arg-type]
        )
