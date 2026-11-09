import asyncio
from pathlib import Path

import pytest

from render.trendline_executor import TrendlinePlanExecutor
from render.trendline_plans import TrendlinePlanStore


def _run_fixture_loop(coroutine):
    """Existing single-thread fixture harness: real tasks/locks, no socket self-pipe."""
    import selectors
    import time
    class FixtureSelector(selectors.SelectSelector):
        def select(self, timeout=None):
            if not self.get_map():
                if timeout:
                    time.sleep(timeout)
                return []
            return super().select(timeout)
    class FixtureLoop(asyncio.SelectorEventLoop):
        def _make_self_pipe(self):
            self._ssock = self._csock = None
        def _close_self_pipe(self):
            pass
        def _write_to_self(self):
            pass
    loop = FixtureLoop(FixtureSelector())
    try:
        return loop.run_until_complete(coroutine)
    finally:
        pending=asyncio.all_tasks(loop)
        for task in pending:task.cancel()
        if pending:loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


def _selected_fixture_executor(*args, **kwargs):
    executor = TrendlinePlanExecutor(*args, **kwargs)
    executor._eligible.update(p["plan_id"] for p in executor.store.load()["plans"].values() if p["status"] == "armed")
    return executor


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
    executor = _selected_fixture_executor(store, quote, calculate, submit, lambda _p: True, now_ms=lambda: 2500)
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
    executor = _selected_fixture_executor(store, quote, calculate, submit, lambda _p: False, now_ms=lambda: 2500)
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
        executor = _selected_fixture_executor(store, quote, calculate, submit, live_execution_authorized, now_ms=lambda: now[0])
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
        executor = _selected_fixture_executor(store, quote, calculate, forbidden, live_execution_authorized, now_ms=lambda:3000)
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
            return _selected_fixture_executor(self.store, self.quote, self.calculate, self.submit, live_execution_authorized, now_ms=lambda:self.now)
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
            f.executor._eligible.add(f.plan["plan_id"])  # Explicitly resume this trigger fixture only.
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


def test_phase11_terminal_timestamps_and_durable_reconciliation_status(tmp_path, monkeypatch):
    import json
    from render.trendline_executor import DefiniteSubmissionFailure
    from render.trendline_plans import TrendlinePlanPersistenceError, live_execution_authorized

    def assert_time(plan, expected, terminal=False):
        assert plan["updated_at_ms"] == plan["lifecycle"]["last_transition_at_ms"] == expected
        if terminal: assert plan["execution_result"]["recorded_at_ms"] == expected

    async def scenario(mode):
        store = TrendlinePlanStore(tmp_path / mode / "plans.json")
        now, calls = [3000], []
        draft = store.create(_payload("oanda", test_trade=mode=="simulation"), now_ms=1000)
        plan = store.arm(draft["plan_id"], now_ms=2000)
        pid, key = plan["plan_id"], plan["execution_key"]
        original_claim = store.claim_trigger
        def claim(*args, **kwargs):
            result = original_claim(*args, **kwargs)
            assert_time(result["plan"], 3100)
            now[0] = 3200
            return result
        monkeypatch.setattr(store, "claim_trigger", claim)
        async def quote(_p):
            return {"instrument":"USD_JPY", "timestamp_ms":now[0], "bid":"99.9", "ask":"100", "tick_size":"0.1"}
        async def calculate(_p, _q):
            now[0] = 3100
            return {"trigger_quote":await quote(_p)}
        entered, release = asyncio.Event(), asyncio.Event()
        async def submit(p, _calc, execution_key):
            assert p["status"] == "submitting" and execution_key == key
            assert_time(store.get(pid), 3200)
            calls.append(execution_key)
            entered.set()
            await release.wait()
            if mode == "rejected": raise DefiniteSubmissionFailure("private")
            if mode == "uncertain": raise TimeoutError("private")
            if mode == "missing": return {"accepted":True}
            return {"accepted":True, "order_id":"mock-order"}
        if mode == "persistence":
            original_write = store._write
            def fail_submitting(registry):
                if registry["plans"][pid]["status"] == "submitting": raise TrendlinePlanPersistenceError("private disk failure")
                original_write(registry)
            monkeypatch.setattr(store, "_write", fail_submitting)
        executor = _selected_fixture_executor(store, quote, calculate, submit, live_execution_authorized, now_ms=lambda:now[0])
        if mode in {"simulation", "persistence"}:
            await executor.cycle()
            assert not calls
        else:
            task = asyncio.create_task(executor.cycle())
            await asyncio.wait_for(entered.wait(), 2)
            now[0] = 100 if mode == "backwards" else 3500
            if mode == "cancelled":
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
            else:
                release.set()
                await task
            assert calls == [key]
        final = store.get(pid)
        expected_status = "claimed" if mode=="persistence" else "failed" if mode=="rejected" else "uncertain" if mode in {"uncertain","missing","cancelled"} else "submitted"
        assert final["status"] == expected_status and final["execution_key"] == key
        if mode != "persistence": assert_time(final, 3200 if mode in {"backwards","simulation"} else 3500, terminal=True)
        count = int(expected_status in {"claimed","submitting","uncertain"})
        assert executor.status()["reconciliation_required"] == count
        if mode != "cancelled": assert executor.last_cycle["reconciliation_required"] == count
        before = list(calls)
        await executor.cycle()
        assert calls == before

    async def run():
        for mode in ("success","rejected","uncertain","missing","cancelled","backwards","simulation","persistence"):
            await scenario(mode)
        store = TrendlinePlanStore(tmp_path / "reconstructed.json")
        trigger = {"trigger_timestamp_ms":3000,"trigger_price":"100","trigger_kind":"touch"}
        for state in ("claimed","submitting","uncertain","submitted","failed"):
            draft = store.create(_payload("oanda"), now_ms=1000)
            pid, key = draft["plan_id"], draft["execution_key"]
            # All durable mutations independently normalize a stale caller clock.
            assert_time(store.update(pid, {"rr_target":"3"}, now_ms=0), 1000)
            store.arm(pid, now_ms=2000)
            assert_time(store.claim_trigger(pid, trigger, now_ms=0)["plan"], 2000)
            if state != "claimed": assert_time(store.begin_submission(pid, execution_key=key, now_ms=4000), 4000)
            if state in {"uncertain","submitted","failed"}:
                final = store.resolve_claim(pid, status=state, outcome={"outcome":"executed" if state=="submitted" else state}, now_ms=0)
                assert_time(final, 4000, terminal=True)
        async def forbidden(*a): raise AssertionError("No adapter may be invoked")
        executor = _selected_fixture_executor(TrendlinePlanStore(store.path), forbidden, forbidden, forbidden, live_execution_authorized)
        assert executor.last_cycle is None
        status = executor.status()
        assert not status["running"] and status["reconciliation_required"] == 3
        assert "manual broker reconciliation" in status["reconciliation_message"].lower()
        await executor.cycle()
        assert executor.last_cycle["reconciliation_required"] == 3
        # Read/lock failures are explicitly unknown, including before any cycle.
        for failure in (OSError("secret URL account"), TrendlinePlanPersistenceError("secret lock")):
            with monkeypatch.context() as patch:
                patch.setattr(executor.store, "load", lambda: (_ for _ in ()).throw(failure))
                status = executor.status()
                assert status["reconciliation_required"] is None and "unknown" in status["reconciliation_message"]
                assert "Automatic retry remains disabled" in status["reconciliation_message"]
                assert "secret" not in json.dumps(status)
                await executor.cycle()
                assert executor.last_cycle["reconciliation_required"] is None
                assert "secret" not in json.dumps(executor.status())
        assert executor.status()["reconciliation_required"] == 3
        for transition in ("cancel", "expire", "authorize"):
            p = store.create({**_payload("oanda"), "account":"live", "expiry_at_ms":2000 if transition=="expire" else None}, now_ms=1000)
            pid = p["plan_id"]
            if transition=="authorize":
                p = store.authorize_live_and_arm(pid, now_ms=0)
                assert_time(p, 1000)
                assert p["live_authorization"]["authorized_at_ms"] == 1000
                assert_time(store.cancel(pid, now_ms=0), 1000)
            else:
                store.update(pid, {"rr_target":"3"}, now_ms=3000)
                assert_time(getattr(store, transition)(pid, now_ms=0), 3000)
    asyncio.run(run())


def test_arm_selected_monitor_session_and_submission_guards(tmp_path, monkeypatch):
    from render.trendline_plans import TrendlinePlanError, live_execution_authorized

    async def run():
        store = TrendlinePlanStore(tmp_path / "selected.json")
        drafts = [store.create({**_payload("bybit", test_trade=i == 2), "account": "live" if i == 1 else "demo"}, now_ms=1000) for i in range(3)]
        calls = []
        async def quote(p):
            calls.append(("quote", p["plan_id"]))
            return {"instrument": p["instrument"], "timestamp_ms": 2500, "bid": "99.9", "ask": "100", "tick_size": "0.1"}
        async def calculate(p, q): return {"trigger_quote": q}
        async def submit(p, c, key):
            calls.append(("submit", p["plan_id"]))
            return {"accepted": True, "order_id": "only-one"}
        e = TrendlinePlanExecutor(store, quote, calculate, submit, live_execution_authorized, now_ms=lambda: 2500)
        async def idle(): await e._stop.wait()
        monkeypatch.setattr(e, "_run", idle)
        await e.cycle()
        assert calls == []  # Drafts remain inert.
        d = drafts[0]; generation = e.generation
        async def arm(d, confirm=False, gen=None):
            return await e.arm(d["plan_id"], revision=d["revision"], generation=gen or e.generation, confirm_live=confirm)
        results = await asyncio.gather(arm(d), arm(d), return_exceptions=True)
        assert sum(isinstance(x, dict) for x in results) == 1
        assert sum(isinstance(x, TrendlinePlanError) for x in results) == 1
        task = e._task
        e._previous[d["plan_id"]] = {"sentinel": True}
        with pytest.raises(TrendlinePlanError): await arm(drafts[1])
        assert store.get(drafts[1]["plan_id"]) == drafts[1]
        await arm(drafts[1], True)
        assert e._task is task and e._previous[d["plan_id"]] == {"sentinel": True}
        await e.stop()
        paused = store.get(d["plan_id"])
        with pytest.raises(TrendlinePlanError): await arm(paused, gen=generation)
        assert not e.status()["running"] and not e._eligible
        await e.start()  # Internal compatibility never resumes plans.
        await e.cycle()
        assert calls == []
        await arm(paused)
        assert not e._previous and not e.eligible(drafts[1]["plan_id"])
        await e.cycle(); await e.cycle()
        assert calls.count(("submit", d["plan_id"])) == 1
        assert store.get(d["plan_id"])["execution_key"] == d["execution_key"]
        await arm(drafts[2]); await e.cycle()
        assert store.get(drafts[2]["plan_id"])["execution_result"]["outcome"] == "executed"
        assert calls.count(("submit", drafts[2]["plan_id"])) == 1
        await e.stop()
        reconstructed = TrendlinePlanExecutor(store, quote, calculate, submit, live_execution_authorized, now_ms=lambda:2500)
        before = list(calls); await reconstructed.cycle(); assert calls == before
        # Startup failure and durable write failure preserve the inactive revision.
        fresh = store.create(_payload("bybit"), now_ms=1000)
        async def fail(): raise RuntimeError("startup failed")
        monkeypatch.setattr(e, "_start", fail)
        with pytest.raises(RuntimeError): await arm(fresh)
        assert store.get(fresh["plan_id"]) == fresh and not e.eligible(fresh["plan_id"])
        # A Stop during final calculation invalidates the candidate before claim.
        monkeypatch.undo()
        e2 = TrendlinePlanExecutor(store, quote, calculate, submit, live_execution_authorized, now_ms=lambda:2500)
        async def idle2(): await e2._stop.wait()
        monkeypatch.setattr(e2, "_run", idle2)
        await e2.arm(fresh["plan_id"], revision=fresh["revision"], generation=e2.generation)
        async def stopping(p,q):
            await e2.stop()
            return {"trigger_quote":q}
        e2.calculate=stopping
        await e2.cycle()
        assert store.get(fresh["plan_id"])["trigger_claim"] is None
        # Actual worker startup and write rollback, with no adapter or live store.
        real_store = TrendlinePlanStore(tmp_path / "worker.json")
        inactive = real_store.create(_payload("bybit", test_trade=True), now_ms=1000)
        real_calls = []
        async def real_quote(p):
            real_calls.append(p["plan_id"])
            return {"instrument": p["instrument"], "timestamp_ms":3000,"bid":"98.9","ask":"99","tick_size":"0.1"}
        real = TrendlinePlanExecutor(real_store, real_quote, calculate, submit, live_execution_authorized, now_ms=lambda:3000, interval_seconds=0.01)
        async def dead_worker(): raise RuntimeError("startup failed")
        with monkeypatch.context() as patch:
            patch.setattr(real, "_run", dead_worker)
            with pytest.raises(RuntimeError):
                await real.arm(inactive["plan_id"], revision=inactive["revision"], generation=real.generation)
        assert not real.status()["running"] and real_store.get(inactive["plan_id"]) == inactive
        with monkeypatch.context() as patch:
            def cannot_schedule(*args, **kwargs): raise RuntimeError("cannot schedule")
            patch.setattr(asyncio, "create_task", cannot_schedule)
            with pytest.raises(RuntimeError):
                await real.arm(inactive["plan_id"], revision=inactive["revision"], generation=real.generation)
        assert not real.status()["running"] and real_store.get(inactive["plan_id"]) == inactive
        original_bytes = real_store.path.read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(real_store, "_write", lambda *a: (_ for _ in ()).throw(RuntimeError("disk")))
            with pytest.raises(RuntimeError):
                await real.arm(inactive["plan_id"], revision=inactive["revision"], generation=real.generation)
        assert real_store.path.read_bytes() == original_bytes and not real.status()["running"] and real_calls == []
        armed = await real.arm(inactive["plan_id"], revision=inactive["revision"], generation=real.generation)
        worker = real._task
        await real.cycle()
        assert real_calls == [inactive["plan_id"]]
        await real.stop()
        assert worker.done() and not real._previous and not real._eligible
        # Cross needs new ordered observations after Stop, even on the same worker object.
        cross = real_store.create({**_payload("bybit", test_trade=True),"trigger_mode":"confirmed_cross"}, now_ms=1000)
        prices = iter([(3100,"99"),(3200,"101"),(3300,"99")]); clock=[3100]
        async def cross_quote(p):
            from decimal import Decimal
            stamp, price = next(prices); clock[0]=stamp
            return {"instrument":p["instrument"],"timestamp_ms":stamp,"bid":str(Decimal(price)-Decimal("0.1")),"ask":price,"tick_size":"0.1"}
        real.quote=cross_quote;real.now_ms=lambda:clock[0]
        # A controlled loop prevents background ticks consuming this ordered fixture.
        async def idle_real(): await real._stop.wait()
        with monkeypatch.context() as patch:
            patch.setattr(real, "_run", idle_real)
            active=await real.arm(cross["plan_id"],revision=cross["revision"],generation=real.generation)
            await real.cycle()
            assert cross["plan_id"] in real._previous
            await real.stop()
            await real.arm(cross["plan_id"],revision=active["revision"],generation=real.generation)
            await real.cycle()  # First price above line must seed, not cross old below-line sample.
            assert real_store.get(cross["plan_id"])["trigger_claim"] is None
            await real.cycle()
            assert real_store.get(cross["plan_id"])["execution_result"]["outcome"] == "executed"
            assert not real.eligible(inactive["plan_id"])
            await real.stop()
        # Simulation-era Bybit Test arms cannot silently adopt the new execution policy.
        legacy_store=TrendlinePlanStore(tmp_path/"legacy-policy.json")
        legacy=legacy_store.arm(legacy_store.create(_payload("bybit",test_trade=True),now_ms=1000)["plan_id"],now_ms=1500)
        registry=legacy_store.load();registry["plans"][legacy["plan_id"]]["execution_policy"]=None;legacy_store._write(registry)
        legacy_executor=TrendlinePlanExecutor(legacy_store,quote,calculate,submit,live_execution_authorized,now_ms=lambda:2500)
        legacy_executor._eligible.add(legacy["plan_id"])
        before=list(calls);await legacy_executor.cycle();assert calls==before
        with pytest.raises(TrendlinePlanError):
            legacy_store.claim_trigger(legacy["plan_id"],{"trigger_timestamp_ms":2500,"trigger_price":"100","trigger_kind":"touch"},now_ms=2500)
        await legacy_executor.stop()
        async def idle_legacy():await legacy_executor._stop.wait()
        monkeypatch.setattr(legacy_executor,"_run",idle_legacy)
        await legacy_executor.arm(legacy["plan_id"],revision=legacy["revision"],generation=legacy_executor.generation)
        await legacy_executor.cycle();await legacy_executor.cycle()
        assert calls.count(("submit",legacy["plan_id"]))==1
        await legacy_executor.stop()
        oanda=legacy_store.arm(legacy_store.create(_payload("oanda",test_trade=True),now_ms=1000)["plan_id"],now_ms=1500)
        simulator=_selected_fixture_executor(legacy_store,quote,calculate,submit,live_execution_authorized,now_ms=lambda:2500)
        before=list(calls);await simulator.cycle()
        assert legacy_store.get(oanda["plan_id"])["execution_result"]["outcome"]=="simulated"
        assert not any(kind=="submit" and pid==oanda["plan_id"] for kind,pid in calls[len(before):])
    _run_fixture_loop(run())


def test_missed_touch_latest_rejection_and_valid_trigger_paths(tmp_path):
    from decimal import Decimal
    from render.trendline_plans import projected_price, live_execution_authorized
    from render.trendline_executor import InvalidQuote
    async def scenario(name):
        store=TrendlinePlanStore(tmp_path / name / "plans.json")
        payload={**_payload("bybit",test_trade=name!="single"),"instrument":"XRPUSDT","action":"sell","stop_loss_ticks":11,
                 "anchors":[{"timestamp_ms":1794109020000,"price":"1.1614084957785737"},{"timestamp_ms":1794109440000,"price":"1.1605589895838992"}]}
        plan=store.arm(store.create(payload,now_ms=1794109200000)["plan_id"],now_ms=1794109201000)
        t=1794109500000;clock=[t];ticks=[];posts=[];calculations=[]
        def snap(stamp,offset):
            bid=projected_price(plan,stamp)+Decimal(offset)
            return {"instrument":"XRPUSDT","timestamp_ms":stamp,"bid":str(bid),"ask":str(bid+Decimal("0.0001")),"tick_size":"0.0001"}
        sequence=[snap(t,"0")]
        if name in {"gap","duplicate","reverse"}:sequence=[snap(t,"-0.001"),snap(t+1000,"0.001")]
        if name=="duplicate":sequence=[snap(t,"-0.001"),snap(t,"0.001"),snap(t+2000,"0.001"),snap(t+3000,"-0.001")]
        if name=="reverse":sequence=[snap(t,"-0.001"),snap(t-1,"0.001"),snap(t+2000,"0.001"),snap(t+3000,"-0.001")]
        async def quote(p):
            q=sequence.pop(0);clock[0]=max(clock[0],q["timestamp_ms"]);ticks.append(q);return q
        async def calculate(p,q):
            calculations.append(True)
            if name=="away":return {"trigger_quote":snap(t,"-0.001")}
            if name=="invalid_final":return {"trigger_quote":{**q,"timestamp_ms":t+1}}
            return {"trigger_quote":q}
        async def submit(p,c,key):posts.append(key);return {"accepted":True,"order_id":"one"}
        e=_selected_fixture_executor(store,quote,calculate,submit,live_execution_authorized,now_ms=lambda:clock[0])
        while sequence:
            await e.cycle()
            if name in {"duplicate","reverse"} and len(ticks)==2:
                assert e.latest_evaluations[plan["plan_id"]]["reason"] == ("quote_duplicate" if name=="duplicate" else "quote_out_of_order")
                assert not e._previous and store.get(plan["plan_id"])["trigger_claim"] is None
            if name in {"duplicate","reverse"} and len(ticks)==3:assert store.get(plan["plan_id"])["trigger_claim"] is None
        latest=e.status()["latest_evaluations"][plan["plan_id"]]
        if name in {"away","invalid_final"}:
            assert store.get(plan["plan_id"])["trigger_claim"] is None and not posts
            assert latest["stage"] == ("final_evaluation" if name=="away" else "final_quote")
            if name=="away":assert latest["evaluation"]["classification"]=="below"
            else:assert latest["reason"]=="quote_future"
        else:
            final=store.get(plan["plan_id"]);assert final["status"]=="submitted"
            assert final["trigger_claim"]["trigger_kind"] == ("entered_band" if name in {"inside","single"} else "gap_through")
            assert len(posts)==(1 if name=="single" else 0)
            before=len(ticks);await e.cycle();assert len(ticks)==before
            assert len(calculations)==1
        # Bound latest records and retain no exception/body strings.
        for i in range(140):e._record({**plan,"plan_id":str(i)},"candidate_quote","quote_pending")
        assert len(e.latest_evaluations)==128
    async def run():
        for name in ("inside","gap","single","duplicate","reverse","away","invalid_final"):await scenario(name)
    _run_fixture_loop(run())


def test_fresh_xrp_broker_clock_bounds_and_native_ordering(tmp_path):
    from render.trendline_executor import validate_quote, quote_comparison_now, require_same_quote_clock, InvalidQuote
    now=1794128670000
    clock={"domain":"bybit","source":"api-demo.bybit.com","offset_ms":797,"synced_at_ms":now-1000,"rtt_ms":692,"valid_for_ms":300000}
    native={"instrument":"XRPUSDT","timestamp_ms":now+797,"bid":"1.1639","ask":"1.1640","tick_size":"0.0001"}
    with pytest.raises(InvalidQuote) as old:validate_quote(native,"XRPUSDT",now,60000)
    assert old.value.reason=="quote_future"  # The independently measured offset reproduces the raw-local defect.
    fixed=validate_quote({**native,"clock":clock},"XRPUSDT",now,60000)
    assert fixed["timestamp_ms"]==native["timestamp_ms"] and fixed["clock"]==clock
    assert quote_comparison_now(fixed,now)==now+797+348
    assert quote_comparison_now(fixed,now+1000)==now+1000+797+348
    for change,reason in [({"timestamp_ms":now+10000},"quote_future"),({"timestamp_ms":now-60000},"quote_stale"),({"timestamp_ms":"bad"},"quote_timestamp_invalid"),({"ask":"1.1639"},"quote_spread_invalid"),({"tick_size":"NaN"},"quote_prices_invalid")]:
        with pytest.raises(InvalidQuote) as bad:validate_quote({**fixed,**change},"XRPUSDT",now,60000)
        assert bad.value.reason==reason
    for change,reason in [({"synced_at_ms":now-300001},"clock_sync_stale"),({"synced_at_ms":now+1},"clock_metadata_invalid"),({"rtt_ms":2001},"clock_sync_uncertain"),({"offset_ms":3001},"clock_sync_uncertain"),({"offset_ms":True},"clock_metadata_invalid"),({"domain":"oanda"},"clock_metadata_invalid")]:
        with pytest.raises(InvalidQuote) as bad:validate_quote({**fixed,"clock":{**clock,**change}},"XRPUSDT",now,60000)
        assert bad.value.reason==reason
    negative={**native,"timestamp_ms":now-797,"clock":{**clock,"offset_ms":-797}}
    assert validate_quote(negative,"XRPUSDT",now,60000)["timestamp_ms"]==now-797
    with pytest.raises(InvalidQuote):require_same_quote_clock(fixed,{**fixed,"clock":{**clock,"source":"api.bybit.com"}})
    with pytest.raises(InvalidQuote):require_same_quote_clock(fixed,native)
    store=TrendlinePlanStore(tmp_path/'pending.json');plan=store.create(_payload('bybit'),now_ms=1000)
    e=TrendlinePlanExecutor(store,lambda *a:{},lambda *a:{},lambda *a:{},lambda p:True,now_ms=lambda:now)
    e._record(plan,'candidate_quote','quote_future',quote=fixed)
    e._record(plan,'candidate_quote','quote_pending')
    assert e.latest_evaluations[plan['plan_id']]['reason']=='quote_future'
    assert e.latest_evaluations[plan['plan_id']]['pending_stage']=='candidate_quote'
