from __future__ import annotations

import re
from typing import Iterable, Optional

_KNOWN_OANDA_CODES = {
    "AUD",
    "CAD",
    "CHF",
    "EUR",
    "GBP",
    "HKD",
    "JPY",
    "NZD",
    "SGD",
    "TRY",
    "USD",
    "ZAR",
    "XAU",
    "XAG",
}


def norm_symbol(raw: str) -> str:
    """Uppercase alnum-only symbol key."""
    return re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()


def normalize_oanda_symbol_query(raw: str, available: list[str] | None = None) -> str:
    """Normalize OANDA full-pair inputs while preserving full-pair-only behavior."""
    value = str(raw or "").strip().upper()
    if not value:
        raise ValueError("Instrument is required")

    if "_" in value and len(value) >= 7:
        return value

    lookup = norm_symbol(value)
    if available:
        mapping = {norm_symbol(inst): str(inst) for inst in available if inst}
        if lookup in mapping:
            return mapping[lookup]

    if len(lookup) == 6 and lookup.isalpha():
        return f"{lookup[:3]}_{lookup[3:]}"
    return value


def is_likely_oanda_pair(raw: str) -> bool:
    value = str(raw or "").strip().upper()
    if not value:
        return False
    if re.fullmatch(r"[A-Z]{3}_[A-Z]{3}", value):
        base, quote = value.split("_", 1)
        return base in _KNOWN_OANDA_CODES and quote in _KNOWN_OANDA_CODES
    compact = norm_symbol(value)
    if re.fullmatch(r"[A-Z]{6}", compact):
        base = compact[:3]
        quote = compact[3:]
        return base in _KNOWN_OANDA_CODES and quote in _KNOWN_OANDA_CODES
    return False


def _sorted_candidates(candidates: Iterable[str], preferred_quotes: tuple[str, ...]) -> list[str]:
    quote_rank = {quote: idx for idx, quote in enumerate(preferred_quotes)}

    def rank(symbol: str) -> tuple[int, int, str]:
        idx = len(preferred_quotes)
        for quote, quote_idx in quote_rank.items():
            if symbol.endswith(quote):
                idx = quote_idx
                break
        return (idx, len(symbol), symbol)

    return sorted(set(candidates), key=rank)


def resolve_bybit_symbol_from_choices(
    raw: str,
    symbols: list[str] | set[str],
    preferred_quotes: tuple[str, ...] = ("USDT", "USDC", "USD"),
    exact_first: bool = True,
) -> dict | None:
    """Resolve user symbol to a concrete Bybit symbol from known choices."""
    normalized = norm_symbol(raw)
    if not normalized:
        return None

    all_symbols = [str(sym or "").strip().upper() for sym in symbols if str(sym or "").strip()]
    symbol_set = set(all_symbols)
    if not symbol_set:
        return None

    if exact_first and normalized in symbol_set:
        return {
            "input": raw,
            "normalized": normalized,
            "resolved_symbol": normalized,
            "source": "bybit",
        }

    for quote in preferred_quotes:
        candidate = normalized + quote
        if candidate in symbol_set:
            return {
                "input": raw,
                "normalized": normalized,
                "resolved_symbol": candidate,
                "source": "bybit",
            }

    starts = [sym for sym in all_symbols if sym.startswith(normalized)]
    if starts:
        ordered = _sorted_candidates(starts, preferred_quotes)
        return {
            "input": raw,
            "normalized": normalized,
            "resolved_symbol": ordered[0],
            "source": "bybit",
            "candidates": ordered[:10],
        }

    contains = [sym for sym in all_symbols if normalized in sym]
    if contains:
        ordered = _sorted_candidates(contains, preferred_quotes)
        return {
            "input": raw,
            "normalized": normalized,
            "resolved_symbol": ordered[0],
            "source": "bybit",
            "candidates": ordered[:10],
        }

    return None
