import pytest

from orchestrator.app.core.text_utils import slugify


def test_slugify_turns_title_into_hyphenated_slug() -> None:
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_lowercases_text() -> None:
    assert slugify("ALL CAPS Title") == "all-caps-title"


def test_slugify_collapses_runs_of_non_alphanumerics_into_one_hyphen() -> None:
    assert slugify("a   b---c!!!d") == "a-b-c-d"


def test_slugify_strips_leading_and_trailing_hyphens() -> None:
    assert slugify("  --A  b--  ") == "a-b"


def test_slugify_keeps_digits() -> None:
    assert slugify("Task 5: Release v2.0") == "task-5-release-v2-0"


def test_slugify_replaces_single_non_alphanumeric_with_hyphen() -> None:
    assert slugify("foo_bar") == "foo-bar"


def test_slugify_leaves_already_clean_slug_unchanged() -> None:
    assert slugify("already-a-slug") == "already-a-slug"


@pytest.mark.parametrize("text", ["", "   ", "!!!", "-_-", " --- "])
def test_slugify_returns_untitled_when_nothing_remains(text: str) -> None:
    assert slugify(text) == "untitled"
