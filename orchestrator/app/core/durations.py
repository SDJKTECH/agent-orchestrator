# Helper that formats a duration in seconds as text such as '5.2s', '2m 5s' or '1h 2m'.
# Returns 'n/a' for a missing value and rejects negative or non-finite ones.
import math

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600


def format_duration(seconds: float | None) -> str:
    """Format a number of seconds for display.

    ``None`` becomes ``"n/a"``; under a minute uses one decimal place
    (``"5.2s"``), under an hour uses minutes and seconds (``"2m 5s"``), and
    anything longer uses hours and minutes (``"1h 2m"``). Larger units
    truncate the remainder. Negative or non-finite values raise ``ValueError``.
    """
    if seconds is None:
        return "n/a"
    if not math.isfinite(seconds):
        raise ValueError(f"Duration must be finite, got {seconds!r}")
    if seconds < 0:
        raise ValueError(f"Duration must not be negative, got {seconds!r}")

    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds:.1f}s"

    whole_seconds = int(seconds)
    if seconds < _SECONDS_PER_HOUR:
        minutes, secs = divmod(whole_seconds, _SECONDS_PER_MINUTE)
        return f"{minutes}m {secs}s"

    hours, remainder = divmod(whole_seconds, _SECONDS_PER_HOUR)
    return f"{hours}h {remainder // _SECONDS_PER_MINUTE}m"
