"""Local-only, injected executor for durable Phase 8 trendline plans.

It deliberately has no broker SDK imports: master_service provides the canonical
fresh quote/calculation/submission adapters, while tests provide fakes.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from render.trendline_plans import TrendlinePlanError, TrendlinePlanStore, evaluate_trigger, utc_epoch_ms

AsyncValue = Callable[..., Awaitable[Mapping[str, object]] | Mapping[str, object]]


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@dataclass
class TrendlinePlanExecutor:
    store: TrendlinePlanStore
    quote: AsyncValue
    calculate: AsyncValue
    submit: AsyncValue
    live_confirmed: Callable[[Mapping[str, object]], bool]
    now_ms: Callable[[], int] = utc_epoch_ms
    interval_seconds: float = 1.0
    _task: Optional[asyncio.Task] = field(default=None, init=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _previous: Dict[str, Dict[str, object]] = field(default_factory=dict, init=False)
    last_cycle: Optional[Dict[str, object]] = field(default=None, init=False)
    last_error: Optional[str] = field(default=None, init=False)

    def status(self) -> Dict[str, object]:
        return {"running": self._task is not None and not self._task.done(), "last_cycle": self.last_cycle, "last_error": self.last_error}

    async def start(self) -> bool:
        if self.status()["running"]:
            return False
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="local-trendline-plan-executor")
        return True

    async def stop(self) -> bool:
        task = self._task
        if task is None or task.done():
            return False
        self._stop.set()
        await task
        return True

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.cycle()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def cycle(self) -> Dict[str, object]:
        now = self.now_ms()
        processed = 0
        try:
            registry = self.store.load()
            for plan in registry["plans"].values():
                if plan["status"] != "armed" or plan["trigger_claim"] is not None:
                    continue
                if plan["expiry_at_ms"] is not None and now >= int(plan["expiry_at_ms"]):
                    self.store.expire(str(plan["plan_id"]), now_ms=now)
                    continue
                try:
                    await self._evaluate_one(plan, now)
                except Exception as exc:
                    self.last_error = _safe_error(exc)
                processed += 1
            self.last_cycle = {"at_ms": now, "processed": processed}
            self.last_error = self.last_error if self.last_error else None
        except Exception as exc:  # Cycle remains safe; no unclaimed plan was submitted.
            self.last_error = str(exc)[:500]
            self.last_cycle = {"at_ms": now, "processed": processed}
        return self.last_cycle

    async def _evaluate_one(self, plan: Mapping[str, object], now: int) -> None:
        plan_id = str(plan["plan_id"])
        quote = await _await(self.quote(plan))
        if not isinstance(quote, Mapping) or str(quote.get("instrument") or "").upper() != str(plan["instrument"]).upper():
            self._previous.pop(plan_id, None)
            return
        tick_size = quote.get("tick_size")
        observation = {"timestamp_ms": quote.get("timestamp_ms"), "bid": quote.get("bid"), "ask": quote.get("ask")}
        try:
            evaluation = evaluate_trigger(plan, observation, previous_observation=self._previous.get(plan_id), now_ms=now, tick_size=tick_size)
        except TrendlinePlanError:
            self._previous.pop(plan_id, None)
            return
        self._previous[plan_id] = observation
        if not evaluation.get("triggered"):
            return
        # The calculator obtains the exact final quote used for sizing.  Re-test
        # that quote before a claim so a moved-away candidate cannot execute.
        calculated = await _await(self.calculate(plan, quote))
        final_quote = calculated.get("trigger_quote") if isinstance(calculated, Mapping) else None
        if not isinstance(final_quote, Mapping):
            return
        final_observation = {"timestamp_ms": final_quote.get("timestamp_ms"), "bid": final_quote.get("bid"), "ask": final_quote.get("ask")}
        final_eval = evaluate_trigger(plan, final_observation, previous_observation=self._previous.get(plan_id), now_ms=now, tick_size=final_quote.get("tick_size"))
        if not final_eval.get("triggered"):
            return
        # Re-read after the final calculation: cancellation, expiry, revision or
        # replacement invalidate the candidate before the atomic claim.
        fresh = self.store.get(plan_id)
        if fresh["status"] != "armed" or fresh["trigger_claim"] is not None or fresh["revision"] != plan["revision"] or fresh["execution_key"] != plan["execution_key"]:
            return
        if not fresh["test_trade"] and not self.live_confirmed(fresh):
            return
        claim = self.store.claim_trigger(plan_id, {"trigger_timestamp_ms": final_observation["timestamp_ms"], "trigger_price": final_eval["trigger_price"], "trigger_kind": final_eval["trigger_kind"]}, now_ms=now)
        if not claim["claimed"]:
            return
        claimed = claim["plan"]
        if claimed["test_trade"]:
            self.store.resolve_claim(plan_id, status="submitted", outcome={"outcome": "simulated", "message": "Test plan triggered; no broker order was sent."}, now_ms=now)
            return
        try:
            result = await _await(self.submit(claimed, calculated, str(claim["execution_key"])))
        except ValueError as exc:
            self.store.resolve_claim(plan_id, status="failed", outcome={"outcome": "failed", "message": _safe_error(exc)}, now_ms=now)
        except Exception as exc:
            self.store.resolve_claim(plan_id, status="uncertain", outcome={"outcome": "uncertain", "message": _safe_error(exc)}, now_ms=now)
        else:
            body = (result or {}).get("order") if isinstance(result, Mapping) else {}
            order_id = str((result or {}).get("order_id") or (result or {}).get("orderId") or (body or {}).get("orderId") or (body or {}).get("id") or "") or None
            self.store.resolve_claim(plan_id, status="submitted", outcome={"outcome": "executed", "message": "Broker accepted order.", "order_id": order_id}, now_ms=now)


def _safe_error(exc: Exception) -> str:
    text = str(exc or exc.__class__.__name__).replace("\n", " ")[:500]
    return "Trendline execution validation failed." if any(token in text.lower() for token in ("token", "secret", "authorization", "http://", "https://")) else text
