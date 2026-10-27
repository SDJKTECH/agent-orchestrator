"""Row filtering for CSV data.

Rows are mappings of column name to string value (e.g. from
:class:`csv.DictReader`). A condition is written as ``<column><op><value>``,
for example ``age>30`` or ``city=Paris``. Supported operators are ``=``,
``!=``, ``>``, ``>=``, ``<`` and ``<=``.

Comparison is numeric when both the cell and the condition value parse as
numbers, otherwise it is a text comparison.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

_OPERATORS: Final[dict[str, Callable[[Any, Any], bool]]] = {
    "=": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}

_CONDITION_RE: Final = re.compile(r"\s*([^=!<>]+?)\s*(!=|>=|<=|=|>|<)\s*(.*?)\s*")


class FilterError(ValueError):
    """Raised for a malformed condition or an unknown column."""


@dataclass(frozen=True)
class Condition:
    """A single ``column <operator> value`` test."""

    column: str
    operator: str
    value: str


def parse_condition(text: str) -> Condition:
    """Parse ``text`` such as ``"age>30"`` into a :class:`Condition`."""
    match = _CONDITION_RE.fullmatch(text)
    if match is None:
        raise FilterError(
            f"Malformed condition {text!r}: expected <column><op><value> "
            f"with op one of {', '.join(_OPERATORS)}"
        )
    column, op, value = match.groups()
    return Condition(column=column, operator=op, value=value)


def _to_number(text: str) -> float | None:
    try:
        number = float(text.strip())
    except ValueError:
        return None
    # Reject "nan"/"inf" so they compare as text.
    return number if number == number and abs(number) != float("inf") else None


def _matches(cell: str, condition: Condition) -> bool:
    compare = _OPERATORS[condition.operator]
    left, right = _to_number(cell), _to_number(condition.value)
    if left is not None and right is not None:
        return compare(left, right)
    return compare(cell, condition.value)


def apply_filters(
    rows: Iterable[Mapping[str, str]], conditions: Sequence[Condition]
) -> list[Mapping[str, str]]:
    """Return the rows that satisfy every condition.

    Raises :class:`FilterError` if a condition names a column a row lacks.
    """
    result: list[Mapping[str, str]] = []
    for row in rows:
        keep = True
        for condition in conditions:
            if condition.column not in row:
                raise FilterError(f"Unknown column {condition.column!r}")
            if keep and not _matches(row[condition.column] or "", condition):
                keep = False
        if keep:
            result.append(row)
    return result
