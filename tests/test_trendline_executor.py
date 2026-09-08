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
    async def calculate(_plan, fresh_quote): calls["calculate"] += 1; assert fresh_quote["timestamp_ms"] == 2500; return {"entry_price": "100", "stop_loss_price": "99", "take_profit_price": "102", "quantity": "1", "trigger_quote": fresh_quote}
    async def submit(_plan, calc, key): calls["submit"] += 1; assert calc["take_profit_price"] == "102" and key; return {"accepted": True, "order_id": "one"}
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
    async def calculate(_plan, quote): return {"quantity": "1", "trigger_quote": quote}
    async def submit(*_args): submitted.append(True); return {}
    executor = TrendlinePlanExecutor(store, quote, calculate, submit, lambda _p: False, now_ms=lambda: 2500)
    asyncio.run(executor.cycle())
    assert store.get(stale["plan_id"])["status"] == "armed"
    assert store.get(test["plan_id"])["status"] == "submitted"
    assert not submitted


def test_executor_final_snapshot_outcomes_and_authorization(tmp_path: Path, monkeypatch) -> None:
    from render.trendline_executor import DefiniteSubmissionFailure, validate_quote
    from render.trendline_plans import live_execution_authorized, TrendlinePlanError

    def snapshot(price="100", stamp=3000, **changes):
        from decimal import Decimal
        return {"instrument": "USD_JPY", "bid": str(Decimal(price) - Decimal("0.1")),
                "ask": price, "timestamp_ms": stamp, "tick_size": "0.1", **changes}

    async def scenario(name, *, mode="touch", candidate="100", final="100", mutation=None, result=None, error=None, final_changes=None, live=False, test=False):
        store = TrendlinePlanStore(tmp_path / name / "plans.json")
        now = [2000]
        payload = {**_payload("oanda", test_trade=test), "trigger_mode": mode,
                   "account": "live" if live else "demo", "expiry_at_ms": 6000}
        draft = store.create(payload, now_ms=1000)
        plan = (store.authorize_live_and_arm if live else store.arm)(draft["plan_id"], now_ms=1500)
        counters = {"quote": 0, "calculate": 0, "submit": 0}
        async def quote(_plan):
            counters["quote"] += 1
            now[0] = 2000 if counters["quote"] == 1 else 3000
            return snapshot("99" if counters["quote"] == 1 else candidate, now[0])
        async def calculate(_plan, _quote):
            counters["calculate"] += 1
            now[0] = 3100
            if mutation == "cancel": store.cancel(plan["plan_id"], now_ms=now[0])
            if mutation == "expire": now[0] = 6000
            if mutation == "error": raise ValueError("secret account https://private token")
            return {"quantity": "4", "trigger_quote": snapshot(final, 3100, **(final_changes or {}))}
        async def submit(_plan, calc, key):
            assert store.get(plan["plan_id"])["status"] == "claimed"
            assert key == plan["execution_key"] and calc["quantity"] == "4"
            counters["submit"] += 1
            if error: raise error
            return result if result is not None else {"accepted": True, "order_id": "oanda-101"}
        executor = TrendlinePlanExecutor(store, quote, calculate, submit, live_execution_authorized, now_ms=lambda: now[0])
        await executor.cycle()  # Establish the actual pre-candidate observation.
        assert counters == {"quote": 1, "calculate": 0, "submit": 0}
        await executor.cycle()
        resolved = store.get(plan["plan_id"])
        if resolved["status"] in {"submitted", "failed", "uncertain"}:
            before = dict(counters)
            await executor.cycle()
            assert counters == before
        return resolved, counters, executor

    async def run():
        for name, mode, price in (("touch", "touch", "100"), ("cross", "confirmed_cross", "101"), ("gap", "touch", "101")):
            plan, counts, _ = await scenario(name, mode=mode, candidate=price, final=price, live=True)
            assert plan["status"] == "submitted" and counts["submit"] == 1
            assert plan["trigger_claim"]["claimed_at_ms"] == 3100
            assert plan["execution_result"]["order_id"] == "oanda-101"
            assert plan["live_authorization"]["plan_revision"] < plan["revision"]
        for name, args in (("back", {"mode":"confirmed_cross", "candidate":"101", "final":"99"}),
                           ("cancel", {"mutation":"cancel"}), ("expiry", {"mutation":"expire"}),
                           ("calcfail", {"mutation":"error"})):
            plan, counts, _ = await scenario(name, **args)
            assert plan["trigger_claim"] is None and counts["submit"] == 0
        invalid = [{"instrument":"EUR_USD"}, {"bid":"NaN"}, {"bid":"101"}, {"ask":"0"},
                   {"tick_size":"0"}, {"timestamp_ms":-1}, {"timestamp_ms":99999}, {"timestamp_ms":True}]
        for index, changes in enumerate(invalid):
            # Build malformed data without passing timestamp_ms twice to snapshot.
            if "timestamp_ms" in changes:
                with pytest.raises(ValueError): validate_quote(snapshot(**changes), "USD_JPY", 3100, 60000)
                continue
            plan, counts, _ = await scenario(f"invalid-{index}", final_changes=changes)
            assert plan["trigger_claim"] is None and counts["submit"] == 0
        for name, error, result, expected in (
            ("negative", DefiniteSubmissionFailure("private body"), None, "failed"),
            ("timeout", TimeoutError("https://private/token"), None, "uncertain"),
            ("ambiguousvalue", ValueError("Authorization: private"), None, "uncertain"),
            ("malformed", None, {"accepted":True}, "uncertain")):
            plan, counts, _ = await scenario(name, error=error, result=result)
            assert plan["status"] == expected and counts["submit"] == 1
            assert "private" not in str(plan) and "Authorization" not in str(plan)
        plan, counts, _ = await scenario("simulation", test=True)
        assert plan["execution_result"]["outcome"] == "simulated" and counts["submit"] == 0

        store = TrendlinePlanStore(tmp_path / "isolation.json")
        bad = store.arm(store.create(_payload("oanda"), now_ms=1000)["plan_id"], now_ms=1500)
        good = store.arm(store.create(_payload("oanda", test_trade=True), now_ms=1000)["plan_id"], now_ms=1500)
        async def quote(p):
            if p["plan_id"] == bad["plan_id"]: raise RuntimeError("https://private account-id header")
            return snapshot()
        async def calculate(p, q): return {"trigger_quote":q}
        async def forbidden(*args): raise AssertionError("No POST")
        executor = TrendlinePlanExecutor(store, quote, calculate, forbidden, live_execution_authorized, now_ms=lambda:3000)
        await executor.cycle()
        assert store.get(good["plan_id"])["status"] == "submitted"
        assert executor.last_error == "EXECUTOR_ERROR: Local validation or broker operation failed."
        store.cancel(bad["plan_id"], now_ms=3000)
        await executor.cycle()
        assert executor.last_error is None
        monkeypatch.setattr(store, "load", lambda: (_ for _ in ()).throw(RuntimeError("secret URL")))
        await executor.cycle()
        assert "secret" not in executor.last_error

        auth_store = TrendlinePlanStore(tmp_path / "authorization.json")
        for event in ("cancel", "expire"):
            draft = auth_store.create({**_payload("oanda"), "account":"live", "expiry_at_ms":3000}, now_ms=1000)
            armed = auth_store.authorize_live_and_arm(draft["plan_id"], now_ms=1500)
            assert live_execution_authorized(armed)
            assert not live_execution_authorized({**armed, "revision":armed["revision"]+1})
            resolved = getattr(auth_store, event)(armed["plan_id"], now_ms=3000)
            assert resolved["live_authorization"] is None
        draft = auth_store.create({**_payload("oanda"), "account":"live", "expiry_at_ms":3000}, now_ms=1000)
        with pytest.raises(TrendlinePlanError): auth_store.authorize_live_and_arm(draft["plan_id"], now_ms=3000)
        legacy = auth_store.arm(draft["plan_id"], now_ms=2000)
        assert not live_execution_authorized(legacy)
        with pytest.raises(TrendlinePlanError): auth_store.claim_trigger(legacy["plan_id"], {}, now_ms=2500)
        assert auth_store.get(legacy["plan_id"])["trigger_claim"] is None
    asyncio.run(run())
