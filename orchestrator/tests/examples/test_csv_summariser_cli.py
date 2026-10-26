import pytest

from orchestrator.examples.csv_summariser.cli import build_parser, format_summary, main
from orchestrator.examples.csv_summariser.stats import ColumnSummary


def _write(tmp_path, content, name="data.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(content.encode(encoding))
    return path


# --- main: successful runs ---------------------------------------------------


def test_main_prints_numeric_text_and_date_summaries(tmp_path, capsys):
    path = _write(
        tmp_path,
        "id,name,when\n1,ann,2024-01-02\n2,bob,2024-01-01\n,ann,\n",
    )

    assert main([str(path)]) == 0

    out = capsys.readouterr().out
    assert "id [integer]\n  count: 2\n  missing: 1\n" in out
    assert "  min: 1\n  max: 2\n  mean: 1.5\n  median: 1.5\n" in out
    assert "name [text]\n  count: 3\n  missing: 0\n" in out
    assert "  distinct: 2\n  top: 'ann' (2), 'bob' (1)\n" in out
    assert "when [date]\n  count: 2\n  missing: 1\n" in out
    assert "  min: 2024-01-01\n  max: 2024-01-02\n" in out


def test_main_separates_column_blocks_with_blank_line(tmp_path, capsys):
    path = _write(tmp_path, "a,b\n1,x\n")

    main([str(path)])

    blocks = capsys.readouterr().out.split("\n\n")
    assert [block.splitlines()[0] for block in blocks] == [
        "a [integer]",
        "b [text]",
    ]


def test_main_reports_header_only_file_columns(tmp_path, capsys):
    path = _write(tmp_path, "x,y\n")

    assert main([str(path)]) == 0

    out = capsys.readouterr().out
    assert "x [text]\n  count: 0\n  missing: 0\n" in out
    assert "y [text]\n  count: 0\n  missing: 0\n" in out
    assert "top:" not in out


def test_main_reports_empty_file(tmp_path, capsys):
    path = _write(tmp_path, "")

    assert main([str(path)]) == 0

    assert capsys.readouterr().out == "No columns found (empty file).\n"


def test_main_handles_short_and_long_rows(tmp_path, capsys):
    path = _write(tmp_path, "a,b\n1\n2,3,4\n")

    assert main([str(path)]) == 0

    out = capsys.readouterr().out
    assert "a [integer]\n  count: 2\n  missing: 0\n" in out
    assert "b [integer]\n  count: 1\n  missing: 1\n" in out


def test_main_prints_mixed_column_as_text(tmp_path, capsys):
    path = _write(tmp_path, "v\n1\nfoo\n")

    assert main([str(path)]) == 0

    out = capsys.readouterr().out
    assert "v [text]" in out
    assert "min:" not in out


def test_main_accepts_bom_prefixed_file(tmp_path, capsys):
    path = _write(tmp_path, "﻿id\n5\n", encoding="utf-8")

    assert main([str(path)]) == 0

    assert "id [integer]" in capsys.readouterr().out


# --- main: error handling ----------------------------------------------------


def test_main_returns_error_for_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "missing.csv")]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_main_returns_error_for_invalid_utf8(tmp_path, capsys):
    path = tmp_path / "bad.csv"
    path.write_bytes(b"name\n\xff\xfe\n")

    assert main([str(path)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")


def test_main_requires_input_argument(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2
    assert "input" in capsys.readouterr().err


def test_build_parser_accepts_path_argument(tmp_path):
    args = build_parser().parse_args([str(tmp_path / "f.csv")])

    assert args.input == tmp_path / "f.csv"


# --- format_summary ----------------------------------------------------------


def test_format_summary_numeric_block():
    summary = ColumnSummary(
        name="score",
        type="float",
        count=2,
        missing=1,
        min=0.5,
        max=2.0,
        mean=1.25,
        median=1.25,
    )

    assert format_summary(summary) == (
        "score [float]\n"
        "  count: 2\n"
        "  missing: 1\n"
        "  min: 0.5\n"
        "  max: 2\n"
        "  mean: 1.25\n"
        "  median: 1.25"
    )


def test_format_summary_text_block_quotes_top_values():
    summary = ColumnSummary(
        name="",
        type="text",
        count=3,
        missing=0,
        distinct=2,
        top_values=(("a", 2), ("it's", 1)),
    )

    assert format_summary(summary) == (
        "(unnamed) [text]\n"
        "  count: 3\n"
        "  missing: 0\n"
        "  distinct: 2\n"
        "  top: 'a' (2), \"it's\" (1)"
    )


def test_format_summary_date_block_omits_numeric_statistics():
    summary = ColumnSummary(
        name="when",
        type="date",
        count=1,
        missing=0,
        min="2024-01-01",
        max="2024-01-01",
    )

    text = format_summary(summary)

    assert "  min: 2024-01-01" in text
    assert "mean" not in text
    assert "median" not in text
    assert "distinct" not in text
