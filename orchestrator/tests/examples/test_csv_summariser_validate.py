"""Tests for the CSV validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.examples.csv_summariser.validate import validate_csv


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "data.csv"
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_valid_file_has_no_problems(tmp_path: Path) -> None:
    assert validate_csv(_write(tmp_path, "a,b\n1,2\n3,4\n")) == []


def test_empty_file_has_no_problems(tmp_path: Path) -> None:
    assert validate_csv(_write(tmp_path, "")) == []


def test_header_only_has_no_problems(tmp_path: Path) -> None:
    assert validate_csv(_write(tmp_path, "a,b\n")) == []


def test_accepts_str_path(tmp_path: Path) -> None:
    assert validate_csv(str(_write(tmp_path, "a,b\n1,2\n"))) == []


def test_too_few_fields(tmp_path: Path) -> None:
    problems = validate_csv(_write(tmp_path, "a,b,c\n1,2,3\n4,5\n"))
    assert problems == ["Row 3: too few fields (expected 3, got 2)"]


def test_too_many_fields(tmp_path: Path) -> None:
    problems = validate_csv(_write(tmp_path, "a,b\n1,2,3\n"))
    assert problems == ["Row 2: too many fields (expected 2, got 3)"]


def test_duplicate_header_names(tmp_path: Path) -> None:
    problems = validate_csv(_write(tmp_path, "a,b,a\n1,2,3\n"))
    assert len(problems) == 1
    assert "duplicate column name 'a'" in problems[0]
    assert "columns 1 and 3" in problems[0]


def test_duplicate_rows(tmp_path: Path) -> None:
    problems = validate_csv(_write(tmp_path, "a,b\n1,2\n3,4\n1,2\n1,2\n"))
    assert problems == [
        "Row 4: duplicate of row 2",
        "Row 5: duplicate of row 2",
    ]


def test_blank_lines_are_not_reported(tmp_path: Path) -> None:
    assert validate_csv(_write(tmp_path, "a,b\n\n1,2\n")) == []


def test_bom_is_stripped_from_header(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbfa,a\n1,2\n")
    problems = validate_csv(path)
    assert len(problems) == 1
    assert "'a'" in problems[0]


def test_quoted_fields_with_commas_and_newlines(tmp_path: Path) -> None:
    assert validate_csv(_write(tmp_path, 'a,b\n"x,y","l1\nl2"\n')) == []


def test_multiple_problem_kinds_together(tmp_path: Path) -> None:
    problems = validate_csv(_write(tmp_path, "a,b\n1\n1\n1,2,3\n"))
    assert problems == [
        "Row 2: too few fields (expected 2, got 1)",
        "Row 3: too few fields (expected 2, got 1)",
        "Row 3: duplicate of row 2",
        "Row 4: too many fields (expected 2, got 3)",
    ]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validate_csv(tmp_path / "nope.csv")
