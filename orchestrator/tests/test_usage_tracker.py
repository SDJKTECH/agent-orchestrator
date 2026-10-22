import json

from orchestrator.app.usage_tracker import UsageMetrics, parse_usage


def test_parse_claude_usage_json() -> None:
    output = {
        "session_id": "session-123",
        "total_cost_usd": 0.0379284,
        "usage": {
            "input_tokens": 2,
            "output_tokens": 4,
            "cache_creation_input_tokens": 9117,
            "cache_read_input_tokens": 14164,
        },
        "modelUsage": {
            "claude-sonnet-5-5": {
                "inputTokens": 2,
                "outputTokens": 4,
                "costUSD": 0.0379284,
            }
        },
    }

    metrics = parse_usage(json.dumps(output))

    assert metrics == UsageMetrics(
        input_tokens=2,
        output_tokens=4,
        cache_creation_input_tokens=9117,
        cache_read_input_tokens=14164,
        reported_cost_usd=0.0379284,
        session_id="session-123",
        model="claude-sonnet-5-5",
    )


def test_parse_missing_usage_returns_unavailable_metrics() -> None:
    metrics = parse_usage('{"result": "OK"}')

    assert metrics == UsageMetrics()


def test_parse_invalid_json_returns_unavailable_metrics() -> None:
    assert parse_usage("not valid JSON") == UsageMetrics()


def test_parse_rejects_negative_or_invalid_usage_values() -> None:
    metrics = parse_usage(
        json.dumps(
            {
                "usage": {
                    "input_tokens": -1,
                    "output_tokens": "4",
                    "cache_creation_input_tokens": True,
                    "cache_read_input_tokens": 0,
                },
                "total_cost_usd": -0.5,
            }
        )
    )

    assert metrics.input_tokens is None
    assert metrics.output_tokens is None
    assert metrics.cache_creation_input_tokens is None
    assert metrics.cache_read_input_tokens == 0
    assert metrics.reported_cost_usd is None
