"""Compute percentage price range for a given block of klines."""

from __future__ import annotations


def calculate_price_range_percent(klines: list, block_size: int) -> float:
    """Return the high-low percentage of the latest block of ``block_size`` klines."""
    try:
        sorted_klines = sorted(klines, key=lambda k: int(k[0]))
        if len(sorted_klines) < block_size:
            return 0.0
        latest_block = sorted_klines[-block_size:]
        highs = [float(k[2]) for k in latest_block]
        lows = [float(k[3]) for k in latest_block]
        high = max(highs)
        low = min(lows)
        if low == 0:
            return 0.0
        return (high - low) / low * 100
    except (ValueError, IndexError, TypeError):
        return 0.0
