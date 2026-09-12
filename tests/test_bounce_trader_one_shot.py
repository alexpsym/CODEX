import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_bybit_worker_stops_after_own_entry_triggers_without_another_entry(monkeypatch) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", "test-key")
    monkeypatch.setenv("BYBIT_API_SECRET", "test-secret")
    worker = _load_module(
        "one_shot_bybit_worker",
        ROOT / "bybit_trigger_bounce_trader" / "bybit_trigger_bounce_trader.py",
    )
    worker._get_instrument_filters = lambda symbol: worker.InstrumentFilters(1.0, 1.0, 0.001)
    worker._get_last_price = lambda symbol: 101.0
    worker.cache_bybit_demo_tpsl_request = lambda **kwargs: None
    worker._get_open_position = lambda symbol: None
    mutations = []
    worker._signed_post = lambda path, body, timeout=10: mutations.append(path) or {"result": {"orderId": "own-order"}}
    order_id = worker._place_or_amend_conditional_market(
        symbol="BTCUSDT", side="Buy", qty="0.001", trigger_price=100.0,
        trigger_direction=2, tp=None, sl=None, order_link_id="own-link",
    )
    tracked = {"id": order_id, "link": "own-link", "strategy": "ema"}
    snapshots = iter([
        {"result": {"list": [{"orderId": order_id, "orderLinkId": "own-link", "orderStatus": "Untriggered", "cumExecQty": "0"}]}},
        {"result": {"list": [{"orderId": order_id, "orderLinkId": "own-link", "orderStatus": "Filled", "cumExecQty": "0.001"}]}},
    ])
    status_calls = []
    worker._signed_get = lambda path, params, timeout=10: status_calls.append(path) or next(snapshots)

    assert worker._advance_tracked_entry("BTCUSDT", tracked) == "armed"
    assert worker._place_or_amend_conditional_market(
        symbol="BTCUSDT", side="Buy", qty="0.001", trigger_price=99.0,
        trigger_direction=2, tp=None, sl=None, order_link_id="own-link", tracked_order_id=order_id,
    ) == order_id
    assert worker._advance_tracked_entry("BTCUSDT", tracked) == "triggered"
    assert worker._advance_tracked_entry("BTCUSDT", tracked) == "triggered"
    assert mutations == ["/v5/order/create", "/v5/order/amend"]
    assert status_calls == ["/v5/order/realtime", "/v5/order/realtime"]

    worker._signed_post = lambda path, body, timeout=10: mutations.append(path) or (_ for _ in ()).throw(RuntimeError("timeout"))
    with pytest.raises(RuntimeError, match="timeout"):
        worker._place_or_amend_conditional_market(
            symbol="BTCUSDT", side="Buy", qty="1", trigger_price=100.0,
            trigger_direction=2, tp=None, sl=None, order_link_id="own-link", tracked_order_id="own-order",
        )
    assert mutations[-1:] == ["/v5/order/amend"]
    assert mutations.count("/v5/order/create") == 1


def test_oanda_worker_stops_after_tracked_entry_triggers_without_replacement(monkeypatch) -> None:
    worker = _load_module(
        "one_shot_oanda_worker",
        ROOT / "bybit_trigger_bounce_trader" / "oanda_trigger_bounce_trader.py",
    )
    worker.POLL_SECONDS = 0
    worker._tick_size = lambda instrument: 1.0
    triggers = iter([100.0, 101.0])
    worker._trigger_price = lambda instrument: next(triggers)
    worker.time.sleep = lambda seconds: None
    placed = []
    worker._place_pending_order = lambda instrument, trigger: placed.append((instrument, trigger)) or "own-order"
    worker._pending_orders = lambda instrument: (_ for _ in ()).throw(AssertionError("pendingOrders must not drive rearming"))
    worker._cancel_order = lambda order_id: {"orderCancelTransaction": {"id": "cancel"}}
    states = iter([
        ("armed", {"id": "own-order", "state": "PENDING", "price": "100"}),
        ("triggered", {"id": "own-order", "state": "FILLED", "orderFillTransaction": {"id": "fill"}}),
    ])
    worker._tracked_order_state = lambda order_id: next(states)

    worker.main()

    assert placed == [(worker.INSTRUMENT, 100.0)]
