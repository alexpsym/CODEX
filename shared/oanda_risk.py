"""Pure Decimal helpers shared by OANDA fixed-AUD sizing callers."""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Dict, Mapping, Optional, Tuple


def floor_to_precision(value: Decimal, precision: int) -> Decimal:
    return value.quantize(Decimal("1").scaleb(-max(0, int(precision))), rounding=ROUND_DOWN)


def aud_to_home_currency(amount_aud: Decimal, account_home_ccy: str, mid_prices: Mapping[str, object]) -> Decimal:
    home = str(account_home_ccy or "").strip().upper()
    if amount_aud <= 0:
        return amount_aud
    if not home:
        raise ValueError("OANDA account home currency unavailable for AUD conversion.")
    if home == "AUD":
        return amount_aud
    direct = Decimal(str(mid_prices.get(f"AUD_{home}") or "0"))
    if direct > 0:
        return amount_aud * direct
    inverse = Decimal(str(mid_prices.get(f"{home}_AUD") or "0"))
    if inverse > 0:
        return amount_aud / inverse
    raise ValueError(f"Unable to resolve AUD->{home} conversion from OANDA pricing.")


def quote_home_factors(prices_payload: Mapping[str, object], row: Mapping[str, object], quote_ccy: str, account_home_ccy: str) -> Tuple[Decimal, Decimal, Decimal]:
    quote = str(quote_ccy or "").strip().upper()
    home = str(account_home_ccy or "").strip().upper()
    if quote and home and quote == home:
        return Decimal("1"), Decimal("1"), Decimal("1")

    def pick(conversions: object) -> Optional[Tuple[Decimal, Decimal, Decimal]]:
        if not isinstance(conversions, list):
            return None
        for item in conversions:
            if not isinstance(item, dict) or str(item.get("currency") or "").strip().upper() != quote:
                continue
            gain = Decimal(str(item.get("accountGain") or "0"))
            loss = Decimal(str(item.get("accountLoss") or "0"))
            value = Decimal(str(item.get("positionValue") or "0"))
            if gain > 0 and loss > 0 and value > 0:
                return gain, loss, value
        return None

    for conversions in (prices_payload.get("homeConversions"), row.get("homeConversions")):
        found = pick(conversions)
        if found is not None:
            return found
    deprecated = row.get("quoteHomeConversionFactors")
    if isinstance(deprecated, dict):
        gain = Decimal(str(deprecated.get("positiveUnits") or "0"))
        loss = Decimal(str(deprecated.get("negativeUnits") or "0"))
        if gain > 0 and loss > 0:
            return gain, loss, gain
    raise ValueError(f"missing usable conversion factors for {quote or quote_ccy}")


def size_fixed_risk_units(*, risk_home: Decimal, entry: Decimal, stop: Decimal, bid: Decimal, ask: Decimal, loss_factor: Decimal, units_precision: int, minimum_trade_size: Decimal, maximum_order_units: Decimal, maximum_position_size: Decimal) -> Dict[str, Decimal]:
    spread = max(Decimal("0"), ask - bid)
    loss_per_unit = (abs(entry - stop) + spread) * loss_factor
    if risk_home <= 0 or loss_per_unit <= 0:
        raise ValueError("Invalid fixed-AUD risk or stop distance.")
    raw = risk_home / loss_per_unit
    units = floor_to_precision(raw, units_precision)
    if units < minimum_trade_size:
        raise ValueError("Calculated units are below minimum trade size.")
    if maximum_order_units > 0 and units > maximum_order_units:
        raise ValueError("Calculated units exceed OANDA maximumOrderUnits.")
    if maximum_position_size > 0 and units > maximum_position_size:
        raise ValueError("Calculated units exceed OANDA maximumPositionSize.")
    return {"units": units, "units_raw": raw, "loss_per_unit_home": loss_per_unit, "spread_quote": spread}
