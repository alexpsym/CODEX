"""Helper for percentile calculations."""

from __future__ import annotations

import pandas as pd


def percentile_rank(values: list[float], current: float) -> float:
    """Return percentile rank of ``current`` relative to ``values``."""
    try:
        if not values:
            return 0.0
        series = pd.Series(values + [current])
        return float(series.rank(pct=True).iloc[-1])
    except (ValueError, TypeError):
        return 0.0
