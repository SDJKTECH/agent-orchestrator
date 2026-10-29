# Reads token counts, cost and session ID from Claude Code's JSON output.
# Returns None for any value that the output does not report.
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageMetrics:
    """Tokens, cost, session and final message read from one agent run."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    reported_cost_usd: float | None = None
    session_id: str | None = None
    model: str | None = None
    result_text: str | None = None
    num_turns: int | None = None


def parse_usage(stdout: str) -> UsageMetrics:
    """Parse usage metrics from Claude Code's JSON output."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return UsageMetrics()

    if not isinstance(payload, dict):
        return UsageMetrics()

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    model_usage = payload.get("modelUsage")
    if not isinstance(model_usage, dict):
        model_usage = {}

    model = None
    if model_usage:
        # Claude Code reports per-model usage keyed by canonical model name.
        model = next(iter(model_usage), None)

    return UsageMetrics(
        input_tokens=_non_negative_int(usage.get("input_tokens")),
        output_tokens=_non_negative_int(usage.get("output_tokens")),
        cache_creation_input_tokens=_non_negative_int(
            usage.get("cache_creation_input_tokens")
        ),
        cache_read_input_tokens=_non_negative_int(usage.get("cache_read_input_tokens")),
        reported_cost_usd=_non_negative_number(payload.get("total_cost_usd")),
        session_id=_non_empty_string(payload.get("session_id")),
        model=_non_empty_string(model),
        result_text=_non_empty_string(payload.get("result")),
        num_turns=_non_negative_int(payload.get("num_turns")),
    )


def _non_negative_int(value: object) -> int | None:
    """The value if it is a non-negative integer, else None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _non_negative_number(value: object) -> float | None:
    """The value if it is a non-negative number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return float(value)


def _non_empty_string(value: object) -> str | None:
    """The value if it is a non-empty string, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value
