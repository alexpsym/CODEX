"""Functions for computing price correlation."""

from __future__ import annotations

import pandas as pd



def calculate_price_correlation(
    symbol_klines: list,
    btc_klines: list,
    minutes: int,
) -> float:
    """Return the Pearson correlation of minute returns over ``minutes``."""
    try:
        s_sorted = sorted(symbol_klines, key=lambda k: int(k[0]))
        b_sorted = sorted(btc_klines, key=lambda k: int(k[0]))

        if len(s_sorted) < minutes + 1 or len(b_sorted) < minutes + 1:
            return 0.0

        s_closes = [float(k[4]) for k in s_sorted[-(minutes + 1):]]
        b_closes = [float(k[4]) for k in b_sorted[-(minutes + 1):]]

        s_ret = [
            (s_closes[i + 1] - s_closes[i]) / s_closes[i]
            for i in range(minutes)
        ]
        b_ret = [
            (b_closes[i + 1] - b_closes[i]) / b_closes[i]
            for i in range(minutes)
        ]

        if len(set(s_ret)) <= 1 or len(set(b_ret)) <= 1:
            return 0.0

        return float(pd.Series(s_ret).corr(pd.Series(b_ret)))
    except (IndexError, ValueError, TypeError):
        return 0.0


def calculate_returns(klines: list, minutes: int) -> list[float]:
    """Return minute returns for the latest ``minutes`` intervals."""
    try:
        sorted_klines = sorted(klines, key=lambda k: int(k[0]))
        if len(sorted_klines) < minutes + 1:
            return []
        closes = [float(k[4]) for k in sorted_klines[-(minutes + 1):]]
        return [
            (closes[i + 1] - closes[i]) / closes[i] for i in range(minutes)
        ]
    except (IndexError, ValueError, TypeError):
        return []
