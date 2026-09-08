"""Local-only, injected executor for durable Phase 8 trendline plans.

It deliberately has no broker SDK imports: master_service provides the canonical
fresh quote/calculation/submission adapters, while tests provide fakes.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from render.trendline_plans import TrendlinePlanError, TrendlinePlanStore, evaluate_trigger, utc_epoch_ms

AsyncValue = Callable[..., Awaitable[Mapping[str, object]] | Mapping[str, object]]


class DefiniteSubmissionFailure(Exception):
    """Raised only for a known pre-POST failure or negative acknowledgement."""


class InvalidQuote(ValueError):
    """A snapshot cannot safely establish a trigger."""


def validate_quote(snapshot: object, instrument: str, now_ms: int, max_age_ms: int) -> Dict[str, object]:
    """Validate candidate and final quotes identically without coercing timestamps."""
    if not isinstance(snapshot, Mapping) or snapshot.get("instrument") != instrument:
        raise InvalidQuote()
    stamp = snapshot.get("timestamp_ms")
    if type(stamp) is not int or stamp < 0 or not 0 <= now_ms - stamp <= max_age_ms:
        raise InvalidQuote()
    try:
        values = [Decimal(str(snapshot[k])) for k in ("bid", "ask", "tick_size")]
        if any(not v.is_finite() or v <= 0 for v in values) or values[0] >= values[1]:
            raise InvalidQuote()
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidQuote() from exc
    return {"instrument": instrument, "timestamp_ms": stamp, **dict(zip(("bid", "ask", "tick_size"), map(str, values)))}


def valid_order_reference(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None


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
        cycle_error = None
        try:
            registry = self.store.load()
            for plan in registry["plans"].values():
                if plan["status"] != "armed" or plan["trigger_claim"] is not None:
                    continue
                try:
                    plan_now = self.now_ms()
                    if plan["expiry_at_ms"] is not None and plan_now >= int(plan["expiry_at_ms"]):
                        self.store.expire(str(plan["plan_id"]), now_ms=plan_now)
                        continue
                    await self._evaluate_one(plan)
                except Exception as exc:
                    cycle_error = _safe_error(exc)
                processed += 1
            self.last_cycle = {"at_ms": now, "processed": processed}
            self.last_error = cycle_error
        except Exception as exc:  # Cycle remains safe; no unclaimed plan was submitted.
            self.last_error = _safe_error(exc)
            self.last_cycle = {"at_ms": now, "processed": processed}
        return self.last_cycle

    async def _evaluate_one(self, plan: Mapping[str, object]) -> None:
        plan_id = str(plan["plan_id"])
        if not plan["test_trade"] and not self.live_confirmed(plan):
            return
        if plan["order_intent"] == "limit" and not plan.get("limit_entry_price"):
            raise TrendlinePlanError("Limit entry required.")
        raw_quote = await _await(self.quote(plan))
        quote = validate_quote(raw_quote, str(plan["instrument"]), self.now_ms(), int(plan["max_quote_age_ms"]))
        tick_size = quote.get("tick_size")
        observation = {"timestamp_ms": quote.get("timestamp_ms"), "bid": quote.get("bid"), "ask": quote.get("ask")}
        previous = self._previous.get(plan_id)
        self._previous[plan_id] = observation
        try:
            evaluation = evaluate_trigger(plan, observation, previous_observation=previous, now_ms=self.now_ms(), tick_size=tick_size)
        except TrendlinePlanError:
            self._previous.pop(plan_id, None)
            return
        if not evaluation.get("triggered"):
            self._previous[plan_id] = observation
            return
        # The calculator obtains the exact final quote used for sizing.  Re-test
        # that quote before a claim so a moved-away candidate cannot execute.
        calculated = await _await(self.calculate(plan, quote))
        final_quote = calculated.get("trigger_quote") if isinstance(calculated, Mapping) else None
        final_quote = validate_quote(final_quote, str(plan["instrument"]), self.now_ms(), int(plan["max_quote_age_ms"]))
        final_observation = {"timestamp_ms": final_quote.get("timestamp_ms"), "bid": final_quote.get("bid"), "ask": final_quote.get("ask")}
        if int(final_observation["timestamp_ms"]) < int(observation["timestamp_ms"]):
            raise InvalidQuote()
        self._previous[plan_id] = final_observation
        final_now = self.now_ms()
        final_eval = evaluate_trigger(plan, final_observation, previous_observation=previous, now_ms=final_now, tick_size=final_quote.get("tick_size"))
        if not final_eval.get("triggered"):
            self._previous[plan_id] = final_observation
            return
        # Re-read after the final calculation: cancellation, expiry, revision or
        # replacement invalidate the candidate before the atomic claim.
        fresh = self.store.get(plan_id)
        if fresh["status"] != "armed" or fresh["trigger_claim"] is not None or fresh["revision"] != plan["revision"] or fresh["execution_key"] != plan["execution_key"] or fresh.get("live_authorization") != plan.get("live_authorization"):
            return
        if not fresh["test_trade"] and not self.live_confirmed(fresh):
            return
        claim_now = self.now_ms()
        validate_quote(final_quote, str(plan["instrument"]), claim_now, int(plan["max_quote_age_ms"]))
        if fresh["expiry_at_ms"] is not None and claim_now >= int(fresh["expiry_at_ms"]):
            return
        claim = self.store.claim_trigger(plan_id, {"trigger_timestamp_ms": final_observation["timestamp_ms"], "trigger_price": final_eval["trigger_price"], "trigger_kind": final_eval["trigger_kind"]}, now_ms=claim_now, expected_revision=int(plan["revision"]), expected_execution_key=str(plan["execution_key"]))
        if not claim["claimed"]:
            return
        claimed = claim["plan"]
        if claimed["test_trade"]:
            self.store.resolve_claim(plan_id, status="submitted", outcome={"outcome": "simulated", "message": "Test plan triggered; no broker order was sent."}, now_ms=claim_now)
            return
        try:
            result = await _await(self.submit(claimed, calculated, str(claim["execution_key"])))
        except DefiniteSubmissionFailure as exc:
            self.store.resolve_claim(plan_id, status="failed", outcome={"outcome": "failed", "message": _safe_error(exc)}, now_ms=claim_now)
        except Exception as exc:
            self.store.resolve_claim(plan_id, status="uncertain", outcome={"outcome": "uncertain", "message": _safe_error(exc)}, now_ms=claim_now)
        else:
            order_id = result.get("order_id") if isinstance(result, Mapping) else None
            if not isinstance(result, Mapping) or result.get("accepted") is not True or not valid_order_reference(order_id):
                self.store.resolve_claim(plan_id, status="uncertain", outcome={"outcome": "uncertain", "message": "Broker acceptance reference was missing."}, now_ms=claim_now)
                return
            self.store.resolve_claim(plan_id, status="submitted", outcome={"outcome": "executed", "message": "Broker accepted order.", "order_id": order_id}, now_ms=claim_now)


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, InvalidQuote):
        return "QUOTE_INVALID: Fresh valid broker quote required."
    if isinstance(exc, DefiniteSubmissionFailure):
        return "ORDER_REJECTED: Order was not accepted."
    return "EXECUTOR_ERROR: Local validation or broker operation failed."
