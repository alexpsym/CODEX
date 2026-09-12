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
    with pytest.raises(RuntimeError, match="Fixed Risk \(AUD\) sizing unavailable"):
        worker._place_pending_order("EUR_USD", 1.1)
    assert len(posts) == 1
