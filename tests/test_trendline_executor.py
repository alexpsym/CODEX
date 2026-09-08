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
            assert store.get(plan["plan_id"])["status"] == "submitting"
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


def test_executor_restart_reconnect_submission_cancellation_and_duplicates(tmp_path, monkeypatch):
    from decimal import Decimal
    from render.trendline_plans import TrendlinePlanPersistenceError, live_execution_authorized

    class Fixture:
        def __init__(self, name, mode="confirmed_cross", **overrides):
            self.store = TrendlinePlanStore(tmp_path / name / "plans.json")
            payload = {**_payload("oanda"), "trigger_mode":mode, "max_quote_age_ms":2000, "max_observation_gap_ms":1000, **overrides}
            self.plan = self.store.arm(self.store.create(payload, now_ms=1000)["plan_id"], now_ms=1500)
            self.now, self.price, self.fault = 3000, "99", None
            self.calls = {"quote":0, "calculate":0, "submit":0}
            self.executor = self.build()
        def build(self):
            return TrendlinePlanExecutor(self.store, self.quote, self.calculate, self.submit, live_execution_authorized, now_ms=lambda:self.now)
        def snapshot(self):
            return {"instrument":"USD_JPY", "bid":str(Decimal(self.price)-Decimal("0.1")), "ask":self.price, "tick_size":"0.1", "timestamp_ms":self.now}
        async def quote(self, plan):
            self.calls["quote"] += 1
            if self.fault == "disconnect": raise ConnectionError("private")
            q = self.snapshot()
            changes = {"stale":{"timestamp_ms":0}, "future":{"timestamp_ms":self.now+1}, "wrong":{"instrument":"EUR_USD"}, "malformed":{"bid":"NaN"}}
            q.update(changes.get(self.fault, {}))
            return q
        async def calculate(self, plan, q):
            self.calls["calculate"] += 1
            if self.fault == "calculation": raise ValueError("private")
            if self.fault == "expiry": self.now = self.plan["expiry_at_ms"]
            final = dict(q)
            if self.fault == "final": final["instrument"] = "EUR_USD"
            return {"trigger_quote":final, "quantity":"4"}
        async def submit(self, plan, calculation, key):
            assert self.store.get(plan["plan_id"])["status"] == "submitting"
            assert key == self.plan["execution_key"]
            self.calls["submit"] += 1
            return {"accepted":True, "order_id":"mock-order"}
        async def step(self, now, price, fault=None):
            self.now, self.price, self.fault = now, price, fault
            await self.executor.cycle()

    async def run():
        for mode in ("confirmed_cross", "touch"):
            for fault in ("disconnect", "stale", "future", "wrong", "malformed", "calculation", "final", "reversed"):
                f = Fixture(mode+fault, mode)
                await f.step(3000, "99")
                await f.step(2900 if fault=="reversed" else 3100, "101", fault)
                assert f.calls["submit"] == 0 and not f.executor._previous
                await f.step(3200, "101")  # Reconnect seeds; must not use pre-fault 99.
                assert f.calls["submit"] == 0
                await f.step(3300, "99")
                assert f.calls["submit"] == 1
                assert f.store.get(f.plan["plan_id"])["trigger_claim"]["trigger_price"] == "99"
                await f.step(3400, "101")
                assert f.calls["submit"] == 1
            for elapsed in (1100, 10000):
                f = Fixture(mode+str(elapsed), mode)
                await f.step(3000, "99")
                await f.step(3000+elapsed, "101")
                assert f.calls["submit"] == 0
                await f.step(3100+elapsed, "99")
                assert f.calls["submit"] == 1
            f = Fixture(mode+"restart", mode)
            await f.step(3000, "99")
            f.executor = f.build()
            await f.step(3100, "101")
            assert f.calls["submit"] == 0
            # Exercise real lifecycle methods with a stub loop, not monitoring.
            async def stub_loop(): await f.executor._stop.wait()
            monkeypatch.setattr(f.executor, "_run", stub_loop)
            assert await f.executor.start()
            assert not f.executor._previous and not await f.executor.start()
            f.executor._previous["stub"] = {}
            assert await f.executor.stop() and not f.executor._previous
            assert not await f.executor.stop()
            await f.step(3200, "99")
            assert f.calls["submit"] == 0
            await f.step(3300, "101")
            assert f.calls["submit"] == 1
            assert f.store.get(f.plan["plan_id"])["trigger_claim"]["trigger_price"] == "101"

        touch = Fixture("direct-touch", "touch")
        await touch.step(3000, "100")
        assert touch.calls["submit"] == 1
        # Expiry before acquisition, during final calculation, immediately at claim.
        for boundary in ("before", "calculation", "claim"):
            f = Fixture("expiry-"+boundary, "touch", expiry_at_ms=4000)
            if boundary == "claim":
                original_get = f.store.get
                def advancing_get(pid):
                    record = original_get(pid)
                    f.now = 4000
                    return record
                monkeypatch.setattr(f.store, "get", advancing_get)
            await f.step(4000 if boundary=="before" else 3000, "100", "expiry" if boundary=="calculation" else None)
            assert f.calls["submit"] == 0
            assert f.store.load()["plans"][f.plan["plan_id"]]["trigger_claim"] is None
            if boundary == "before": assert f.calls["quote"] == 0

        for failure in ("disk", "lock"):
            f = Fixture("begin-"+failure, "touch")
            def fail_begin(*a, **kw): raise TrendlinePlanPersistenceError("Registry unavailable")
            monkeypatch.setattr(f.store, "begin_submission", fail_begin)
            await f.step(3000, "100")
            assert f.calls["submit"] == 0 and f.store.get(f.plan["plan_id"])["status"] == "claimed"
            restarted = f.build()
            await restarted.cycle()
            assert f.calls["submit"] == 0 and restarted.status()["reconciliation_required"] == 1

        # Cancellation after durable submitting preserves cancellation and uncertainty.
        for disk_failure in (False, True):
            f = Fixture("cancel-"+str(disk_failure), "touch")
            entered = asyncio.Event()
            async def gated_submit(plan, calc, key):
                assert f.store.get(plan["plan_id"])["status"] == "submitting"
                f.calls["submit"] += 1
                entered.set()
                await asyncio.Event().wait()
            f.executor.submit = gated_submit
            if disk_failure:
                monkeypatch.setattr(f.store, "resolve_claim", lambda *a, **kw: (_ for _ in ()).throw(TrendlinePlanPersistenceError("disk")))
            f.price = "100"
            task = asyncio.create_task(f.executor.cycle())
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert not f.executor._previous
            assert f.store.get(f.plan["plan_id"])["status"] == ("submitting" if disk_failure else "uncertain")
            await f.build().cycle()
            assert f.calls["submit"] == 1

        # Both instances see armed and finish calculation before contending to claim.
        f = Fixture("competing", "touch")
        f.price = "100"
        second = f.build()
        ready, go = asyncio.Event(), asyncio.Event()
        arrivals = 0
        async def gated_calculation(plan, q):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2: ready.set()
            await go.wait()
            return {"trigger_quote":q}
        f.executor.calculate = second.calculate = gated_calculation
        tasks = [asyncio.create_task(e.cycle()) for e in (f.executor, second)]
        await asyncio.wait_for(ready.wait(), 2)
        go.set()
        await asyncio.gather(*tasks)
        assert f.calls["submit"] == 1
        await asyncio.gather(f.executor.cycle(), f.executor.cycle(), second.cycle(), f.build().cycle())
        assert f.calls["submit"] == 1
        # Every persisted unresolved/terminal status is inert after reconstruction.
        for status in ("claimed", "submitting", "submitted", "failed", "uncertain"):
            f = Fixture("inert-"+status, "touch")
            pid, key = f.plan["plan_id"], f.plan["execution_key"]
            f.store.claim_trigger(pid, {"trigger_timestamp_ms":3000, "trigger_price":"100", "trigger_kind":"touch"}, now_ms=3000)
            if status != "claimed": f.store.begin_submission(pid, execution_key=key, now_ms=3000)
            if status not in {"claimed", "submitting"}:
                f.store.resolve_claim(pid, status=status, outcome={"outcome":"executed" if status=="submitted" else status}, now_ms=3000)
            reconstructed = f.build()
            await reconstructed.cycle()
            assert f.calls == {"quote":0, "calculate":0, "submit":0}
            assert f.store.get(pid)["status"] == status
            if status in {"claimed", "submitting"}: assert "manual broker reconciliation" in reconstructed.status()["reconciliation_message"]
    asyncio.run(run())
