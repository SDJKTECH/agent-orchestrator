"""Stand-in for the Claude Code CLI so the demo runs offline at no cost.

It accepts the same flags the orchestrator passes (`--print`, `--output-format`,
`--agent`, `--model`, `--effort`, `--max-budget-usd`, then the prompt), does a
small amount of role-specific work in the current directory, and prints
Claude-style JSON containing usage and cost.

Set FAKE_CLAUDE_DELAY (seconds, default 6) to control how long each run takes.
"""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

MODEL_IDS = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}

# Illustrative USD prices per million input/output tokens.
PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0)}


def parse_args(argv: list[str]) -> tuple[dict[str, str], str]:
    options: dict[str, str] = {}
    index = 0

    while index < len(argv) - 1:
        token = argv[index]
        if token in {"--agent", "--model", "--effort", "--max-budget-usd"}:
            options[token.lstrip("-")] = argv[index + 1]
            index += 2
        elif token in {"--output-format"}:
            index += 2
        else:
            index += 1

    return options, argv[-1]


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:24].strip("_")
    slug = slug or "feature"
    return slug if slug[0].isalpha() else f"feature_{slug}"


def task_title(prompt: str) -> str:
    match = re.search(r"^Task:\s*(.+)$", prompt, re.MULTILINE)
    return match.group(1).strip() if match else "feature"


def build(slug: str, title: str) -> str:
    summary = re.sub(r"[^A-Za-z0-9 ]+", "", title)[:60].strip()
    module = Path("sample_app") / f"{slug}.py"
    module.parent.mkdir(exist_ok=True)
    module.write_text(
        f'"""{summary}."""\n\n\n'
        f"def {slug}(value: int) -> int:\n"
        f'    """Implementation for: {summary}."""\n'
        "    return value * 2\n",
        encoding="utf-8",
    )
    return f"Implemented `{slug}` in {module.as_posix()}."


def write_tests(slug: str) -> str:
    tests = Path("tests")
    tests.mkdir(exist_ok=True)
    path = tests / f"test_{slug}.py"
    path.write_text(
        f"from sample_app.{slug} import {slug}\n\n\n"
        f"def test_{slug}_doubles_value() -> None:\n"
        f"    assert {slug}(2) == 4\n",
        encoding="utf-8",
    )
    return f"Added {path.as_posix()} covering `{slug}`."


def review(prompt: str, slug: str) -> str:
    if "DEMO:REJECT" in prompt:
        return (
            f"The implementation of `{slug}` does not meet the acceptance "
            "criteria.\n\nVERDICT: REJECT - the requirements are not satisfied."
        )
    return (
        f"Reviewed `{slug}` and its tests; the change is focused and covered.\n\n"
        "VERDICT: APPROVE - implementation and tests look correct."
    )


def main() -> int:
    options, prompt = parse_args(sys.argv[1:])
    role = options.get("agent", "builder")
    model_alias = options.get("model", "sonnet")
    family = next((name for name in MODEL_IDS if name in model_alias), "sonnet")

    delay = float(os.environ.get("FAKE_CLAUDE_DELAY", "6"))
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        time.sleep(0.2)

    title = task_title(prompt)
    slug = slugify(title)

    if role == "tester":
        message = write_tests(slug)
    elif role == "reviewer":
        message = review(prompt, slug)
    else:
        message = build(slug, title)

    input_tokens = 800 + len(prompt)
    output_tokens = 300 + 40 * len(message) // 10
    cache_read = 9000 if role != "builder" else 2000
    price_in, price_out = PRICES[family]
    cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000

    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": message,
                "session_id": str(uuid.uuid4()),
                "total_cost_usd": round(cost, 6),
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": 1500,
                    "cache_read_input_tokens": cache_read,
                },
                "modelUsage": {
                    MODEL_IDS[family]: {
                        "inputTokens": input_tokens,
                        "outputTokens": output_tokens,
                        "costUSD": round(cost, 6),
                    }
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
