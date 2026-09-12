import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_fx_fixed_aud_config_reaches_oanda_worker_without_spawning(monkeypatch) -> None:
    app = _load("fixed_aud_bounce_app", ROOT / "bybit_trigger_bounce_trader" / "app.py")
    config = dict(app.DEFAULT_CONFIG, market="fx", risk_mode="fixed_aud", risk_aud="10", sl_ticks="12")
    monkeypatch.setattr(app.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")))
    env = app._build_oanda_env(config, instrument="EUR_USD", session_id="oa-test")
    assert env["BOUNCE_RISK_MODE"] == "fixed_aud"
    assert env["BOUNCE_RISK_AUD"] == "10"
    assert env["BOUNCE_OANDA_INSTRUMENT"] == "EUR_USD"


def test_oanda_fixed_aud_sizing_uses_shared_rules_and_fails_closed(monkeypatch) -> None:
    worker = _load("fixed_aud_oanda_worker", ROOT / "bybit_trigger_bounce_trader" / "oanda_trigger_bounce_trader.py")
    worker.RISK_MODE = "fixed_aud"
    worker.RISK_AUD = Decimal("10")
    worker.SL_TICKS = 10.0
    worker.RR_RATIO = 2.0
    worker.SIDE = "Buy"
    monkeypatch.setattr(worker, "_oanda_account_id", lambda: "acct")
    monkeypatch.setattr(worker.oanda_api, "get_price", lambda *args, **kwargs: 1.0)
    posts = []
    missing_conversion = {"value": False}

    def request(method, endpoint, *, params=None, json_body=None):
        if method == "POST":
            posts.append((endpoint, json_body))
            return {"orderCreateTransaction": {"id": "order-1"}}
        if endpoint.endswith("/instruments"):
            return {"instruments": [{"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "maximumOrderUnits": "1000000", "maximumPositionSize": "1000000"}]}
        if endpoint.endswith("/summary"):
            return {"account": {"currency": "USD"}}
        if endpoint.endswith("/pricing"):
            rows = [{"instrument": "EUR_USD", "bids": [{"price": "1.10000"}], "asks": [{"price": "1.10020"}]}]
            if not missing_conversion["value"]:
                rows.append({"instrument": "AUD_USD", "bids": [{"price": "0.60000"}], "asks": [{"price": "0.60000"}]})
            return {"prices": rows, "homeConversions": [{"currency": "USD", "accountGain": "1", "accountLoss": "1", "positionValue": "1"}]}
        raise AssertionError(f"unexpected request {method} {endpoint}")

    monkeypatch.setattr(worker, "_request", request)
    units, stop, entry, precision = worker._fixed_aud_order_values("EUR_USD", 1.1)
    assert units == Decimal("20000")
    sized = worker.oanda_risk.size_fixed_risk_units(
        risk_home=Decimal("6"), entry=entry, stop=stop, bid=Decimal("1.10000"), ask=Decimal("1.10020"),
        loss_factor=Decimal("1"), units_precision=0, minimum_trade_size=Decimal("1"),
        maximum_order_units=Decimal("1000000"), maximum_position_size=Decimal("1000000"),
    )
    assert (sized["loss_per_unit_home"] * abs(units)) / Decimal("0.6") <= Decimal("10")
    assert precision == 5
    assert worker._place_pending_order("EUR_USD", 1.1) == "order-1"
    assert len(posts) == 1
    missing_conversion["value"] = True
    with pytest.raises(RuntimeError, match=r"Fixed Risk \(AUD\) sizing unavailable"):
        worker._place_pending_order("EUR_USD", 1.1)
    assert len(posts) == 1


def _capture_order(monkeypatch, *, instrument: str, side: str, display_precision: int, trigger: str, current: str, loss_factor: str, risk_mode: str):
    worker = _load(f"tick_{instrument}_{risk_mode}_{side}", ROOT / "bybit_trigger_bounce_trader" / "oanda_trigger_bounce_trader.py")
    worker.RISK_MODE = risk_mode
    worker.RISK_AUD = Decimal("10")
    worker.DEFAULT_QTY = 1000.6
    worker.SL_TICKS = 10.0
    worker.RR_RATIO = 2.0
    worker.SIDE = side
    monkeypatch.setattr(worker, "_oanda_account_id", lambda: "acct")
    monkeypatch.setattr(worker.oanda_api, "get_price", lambda *args, **kwargs: current)
    meta = {"displayPrecision": display_precision, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "maximumOrderUnits": "1000000", "maximumPositionSize": "1000000"}
    monkeypatch.setattr(worker.oanda_api, "get_instrument_details", lambda *args, **kwargs: dict(meta))
    posts = []
    quote = instrument.split("_", 1)[1]
    entry = Decimal(trigger)
    spread = Decimal("0.00020") if display_precision == 5 else Decimal("0.002")
    bid = entry - spread / 2
    ask = entry + spread / 2

    def request(method, endpoint, *, params=None, json_body=None):
        if method == "POST":
            posts.append(json_body["order"])
            return {"orderCreateTransaction": {"id": "order-1"}}
        if endpoint.endswith("/instruments"):
            return {"instruments": [dict(meta)]}
        if endpoint.endswith("/summary"):
            return {"account": {"currency": "USD"}}
        if endpoint.endswith("/pricing"):
            return {
                "prices": [
                    {"instrument": instrument, "bids": [{"price": str(bid)}], "asks": [{"price": str(ask)}]},
                    {"instrument": "AUD_USD", "bids": [{"price": "0.60000"}], "asks": [{"price": "0.60000"}]},
                ],
                "homeConversions": [{"currency": quote, "accountGain": loss_factor, "accountLoss": loss_factor, "positionValue": loss_factor}],
            }
        raise AssertionError(f"unexpected request {method} {endpoint}")

    monkeypatch.setattr(worker, "_request", request)
    assert worker._place_pending_order(instrument, float(trigger)) == "order-1"
    assert len(posts) == 1
    return posts[0], Decimal(str(bid)), Decimal(str(ask)), Decimal(loss_factor)


@pytest.mark.parametrize(
    "instrument,side,display_precision,trigger,current,loss_factor",
    [
        ("EUR_USD", "Buy", 5, "1.100006", "1.00000", "1"),
        ("USD_JPY", "Sell", 3, "150.0064", "151.000", "0.006"),
    ],
    ids=["eurusd", "usdjpy"],
)
def test_oanda_tick_distances_match_across_risk_modes(monkeypatch, instrument, side, display_precision, trigger, current, loss_factor) -> None:
    legacy, _bid, _ask, _factor = _capture_order(
        monkeypatch, instrument=instrument, side=side, display_precision=display_precision,
        trigger=trigger, current=current, loss_factor=loss_factor, risk_mode="fixed_qty",
    )
    fixed, bid, ask, factor = _capture_order(
        monkeypatch, instrument=instrument, side=side, display_precision=display_precision,
        trigger=trigger, current=current, loss_factor=loss_factor, risk_mode="fixed_aud",
    )
    entry = Decimal(fixed["price"])
    stop = Decimal(fixed["stopLossOnFill"]["price"])
    target = Decimal(fixed["takeProfitOnFill"]["price"])
    tick = Decimal("1").scaleb(-display_precision)
    assert fixed["price"] == legacy["price"]
    assert fixed["stopLossOnFill"]["price"] == legacy["stopLossOnFill"]["price"]
    assert fixed["takeProfitOnFill"]["price"] == legacy["takeProfitOnFill"]["price"]
    assert abs(entry - stop) == Decimal("10") * tick
    assert abs(target - entry) == Decimal("20") * tick
    assert legacy["units"] == ("1001" if side == "Buy" else "-1001")
    estimated_aud_loss = (abs(entry - stop) + (ask - bid)) * factor * abs(Decimal(fixed["units"])) / Decimal("0.6")
    assert estimated_aud_loss <= Decimal("10")


def test_oanda_legacy_quantity_rounding_is_preserved(monkeypatch) -> None:
    order, _bid, _ask, _factor = _capture_order(
        monkeypatch, instrument="EUR_USD", side="Buy", display_precision=5,
        trigger="1.100006", current="1.00000", loss_factor="1", risk_mode="fixed_qty",
    )
    app = _load("tick_label_bounce_app", ROOT / "bybit_trigger_bounce_trader" / "app.py")
    assert order["units"] == "1001"
    assert "Stop Loss (ticks)" in app.FORM_HTML
    assert "SL Ticks / Pips" not in app.FORM_HTML


def test_fixed_aud_missing_metadata_blocks_order(monkeypatch) -> None:
    worker = _load("tick_missing_meta_worker", ROOT / "bybit_trigger_bounce_trader" / "oanda_trigger_bounce_trader.py")
    worker.RISK_MODE = "fixed_aud"
    worker.RISK_AUD = Decimal("10")
    worker.SL_TICKS = 10.0
    monkeypatch.setattr(worker, "_oanda_account_id", lambda: "acct")
    monkeypatch.setattr(worker.oanda_api, "get_price", lambda *args, **kwargs: "1.10000")
    posts = []

    def request(method, endpoint, *, params=None, json_body=None):
        if method == "POST":
            posts.append(json_body)
            return {"orderCreateTransaction": {"id": "unexpected"}}
        if endpoint.endswith("/instruments"):
            return {"instruments": [{"displayPrecision": 5, "tradeUnitsPrecision": 0, "minimumTradeSize": "1", "maximumOrderUnits": "1000000"}]}
        raise AssertionError(f"unexpected request {method} {endpoint}")

    monkeypatch.setattr(worker, "_request", request)
    with pytest.raises(RuntimeError, match="metadata is incomplete"):
        worker._place_pending_order("EUR_USD", 1.1)
    assert posts == []
