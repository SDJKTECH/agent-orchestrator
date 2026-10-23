# Helper that formats a token count as short text such as '1.2K' or '2.5M'.
# Returns 'n/a' for a missing count.
_THOUSAND = 1_000
_MILLION = 1_000_000


def _format_scaled(count: int, unit: int, suffix: str) -> str:
    # Truncate (not round) to one decimal using integer math, so values such as
    # 999_999 render as "999.9K" rather than rolling over to "1000.0K".
    tenths = count * 10 // unit
    whole, decimal = divmod(tenths, 10)
    return f"{whole}.{decimal}{suffix}"


def format_tokens(count: int | None) -> str:
    """Format a token count as a short, readable string.

    ``None`` becomes ``"n/a"``; counts under 1,000 are shown as is
    (``"999"``), counts under 1,000,000 use thousands with one decimal
    (``"1.2K"``), and larger counts use millions with one decimal
    (``"2.5M"``). The decimal is truncated rather than rounded so a value never
    displays as the next unit up. Negative counts raise ``ValueError``.
    """
    if count is None:
        return "n/a"
    if count < 0:
        raise ValueError(f"Token count must not be negative, got {count!r}")

    if count < _THOUSAND:
        return str(count)
    if count < _MILLION:
        return _format_scaled(count, _THOUSAND, "K")
    return _format_scaled(count, _MILLION, "M")
