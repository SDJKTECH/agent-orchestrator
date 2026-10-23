# Helper that formats a USD cost as text such as '$0.42' or '$0.0031'.
# Returns 'n/a' for a missing cost and rejects negative or non-finite ones.
import math


def format_cost(amount: float | None) -> str:
    """Format a USD cost for display.

    ``None`` becomes ``"n/a"``, zero becomes ``"$0.00"``, amounts of at least
    $0.01 use 2 decimals, and smaller positive amounts use 4 decimals.
    Raises ``ValueError`` for negative or non-finite amounts.
    """
    if amount is None:
        return "n/a"
    if math.isnan(amount) or math.isinf(amount):
        raise ValueError(f"Cost must be a finite number, got {amount}")
    if amount < 0:
        raise ValueError(f"Cost must not be negative, got {amount}")
    if amount == 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"
