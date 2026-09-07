import asyncio
from pathlib import Path

import pytest

from render.trendline_executor import TrendlinePlanExecutor
from render.trendline_plans import TrendlinePlanStore


def _payload(broker: str, *, test_trade: bool = False) -> dict:
    return {
        "broker": broker, "account": "demo", "instrument": "BTCUSDT" if broker == "bybit" else "USD_JPY",
        "action": "buy", "order_intent": "market", "test_trade": test_trade,
        "anchors": [{"timestamp_ms": 1000, "price": "100"}, {"timestamp_ms": 2000, "price": "100"}],
        "right_extension": True, "trigger_mode": "touch", "cross_direction": "either", "trigger_price_basis": "executable", "tolerance_ticks": 0,
        "max_quote_age_ms": 60000, "max_observation_gap_ms": 300000, "expiry_at_ms": None,
        "risk_mode": "fixed_aud", "risk_value": "10", "stop_loss_ticks": 10, "rr_target": "2", "timeframe": "1h",
        "order_metadata": {},
    }


@pytest.mark.parametrize("broker", ["bybit", "oanda"])
def test_trendline_executor_fresh_claim_recalculation_and_one_submission(tmp_path: Path, broker: str) -> None:
    store = TrendlinePlanStore(tmp_path / "plans.json")
    plan = store.arm(store.create(_payload(broker), now_ms=1000)["plan_id"], now_ms=1500)
    calls = {"calculate": 0, "submit": 0}

    async def quote(_plan): return {"instrument": plan["instrument"], "timestamp_ms": 2500, "bid": "99.9", "ask": "100", "tick_size": "0.1"}
    async def calculate(_plan, fresh_quote): calls["calculate"] += 1; assert fresh_quote["timestamp_ms"] == 2500; return {"entry_price": "100", "stop_loss_price": "99", "take_profit_price": "102", "quantity": "1"}
    async def submit(_plan, calc, key): calls["submit"] += 1; assert calc["take_profit_price"] == "102" and key; return {"order_id": "one"}
    executor = TrendlinePlanExecutor(store, quote, calculate, submit, lambda _p: True, now_ms=lambda: 2500)
    asyncio.run(executor.cycle())
    final = store.get(plan["plan_id"])
    assert final["status"] == "submitted" and final["execution_result"]["outcome"] == "executed"
    assert calls == {"calculate": 1, "submit": 1}
    asyncio.run(executor.cycle())
    assert calls == {"calculate": 1, "submit": 1}


def test_trendline_executor_rejects_stale_and_simulates_test_without_submission(tmp_path: Path) -> None:
    store = TrendlinePlanStore(tmp_path / "plans.json")
    stale = store.arm(store.create(_payload("bybit"), now_ms=1000)["plan_id"], now_ms=1500)
    test = store.arm(store.create(_payload("oanda", test_trade=True), now_ms=1000)["plan_id"], now_ms=1500)
    submitted = []
    async def quote(plan): return {"instrument": plan["instrument"], "timestamp_ms": 1 if plan["plan_id"] == stale["plan_id"] else 2500, "bid": "99.9", "ask": "100", "tick_size": "0.1"}
    async def calculate(*_args): return {"quantity": "1"}
    async def submit(*_args): submitted.append(True); return {}
    executor = TrendlinePlanExecutor(store, quote, calculate, submit, lambda _p: False, now_ms=lambda: 2500)
    asyncio.run(executor.cycle())
    assert store.get(stale["plan_id"])["status"] == "armed"
    assert store.get(test["plan_id"])["status"] == "submitted"
    assert not submitted
