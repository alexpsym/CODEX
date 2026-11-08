"""Local-only, injected executor for durable Phase 8 trendline plans.

It deliberately has no broker SDK imports: master_service provides the canonical
fresh quote/calculation/submission adapters, while tests provide fakes.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from uuid import uuid4
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from render.trendline_plans import TrendlinePlanError, TrendlinePlanStore, evaluate_trigger, utc_epoch_ms

AsyncValue = Callable[..., Awaitable[Mapping[str, object]] | Mapping[str, object]]


class DefiniteSubmissionFailure(Exception):
    """Raised only for a known pre-POST failure or negative acknowledgement."""


class InvalidQuote(ValueError):
    """A snapshot cannot safely establish a trigger."""

    def __init__(self, reason: str = "quote_invalid", inputs: Optional[Mapping[str, object]] = None):
        super().__init__("Fresh valid broker quote required.")
        self.reason = reason
        self.inputs = dict(inputs or {})


def _quote_inputs(snapshot: object, now_ms: int) -> Dict[str, object]:
    """Only bounded numeric quote fields; never retain broker bodies or errors."""
    result: Dict[str, object] = {"now_ms": now_ms}
    if not isinstance(snapshot, Mapping):
        return result
    stamp = snapshot.get("timestamp_ms")
    if type(stamp) is int and 0 <= stamp < 10**16:
        result["timestamp_ms"] = stamp
    for key in ("bid", "ask", "tick_size"):
        value = snapshot.get(key)
        if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
            continue
        text = str(value)
        try:
            if len(text) <= 64 and Decimal(text).is_finite():
                result[key] = text
        except InvalidOperation:
            pass
    clock = snapshot.get("clock")
    if isinstance(clock, Mapping):
        for key in ("offset_ms", "synced_at_ms", "rtt_ms"):
            if type(clock.get(key)) is int:
                result["clock_" + key] = clock[key]
    return result


def quote_comparison_now(snapshot: object, local_now_ms: int) -> int:
    """Compare authentic Bybit UTC against an independently measured UTC bound.

    The estimate expires after at most five minutes, RTT at most two seconds,
    offset at most three seconds (the existing drift warning boundary). Allow
    half the measured RTT, 1ms rounding and 100ppm drift since synchronization.
    Use the upper bound for both future validation and conservative quote age.
    Source timestamps are never shifted; projection and ordering remain native.
    Legacy injected/OANDA snapshots without clock metadata use local UTC.
    """
    clock = snapshot.get("clock") if isinstance(snapshot, Mapping) else None
    if clock is None:
        return local_now_ms
    inputs = _quote_inputs(snapshot, local_now_ms)
    if not isinstance(clock, Mapping) or clock.get("domain") != "bybit" or not isinstance(clock.get("source"), str) or re.fullmatch(r"[A-Za-z0-9.:-]{1,128}", clock["source"]) is None:
        raise InvalidQuote("clock_metadata_invalid", inputs)
    if any(type(clock.get(k)) is not int for k in ("offset_ms", "synced_at_ms", "rtt_ms", "valid_for_ms")):
        raise InvalidQuote("clock_metadata_invalid", inputs)
    age = local_now_ms - clock["synced_at_ms"]
    inputs["clock_sync_age_ms"] = age
    if not 0 < clock["valid_for_ms"] <= 300_000 or age < 0:
        raise InvalidQuote("clock_metadata_invalid", inputs)
    if age > clock["valid_for_ms"]:
        raise InvalidQuote("clock_sync_stale", inputs)
    if not 0 <= clock["rtt_ms"] <= 2000 or abs(clock["offset_ms"]) > 3000:
        raise InvalidQuote("clock_sync_uncertain", inputs)
    uncertainty = (clock["rtt_ms"] + 1) // 2 + 1 + (age + 9999) // 10000
    return local_now_ms + clock["offset_ms"] + uncertainty


def require_same_quote_clock(candidate: Mapping[str, object], final: Mapping[str, object]) -> None:
    first, second = candidate.get("clock"), final.get("clock")
    if first is None and second is None:
        return
    if not isinstance(first, Mapping) or not isinstance(second, Mapping) or any(first.get(k) != second.get(k) for k in ("domain", "source")):
        raise InvalidQuote("quote_clock_mismatch")


def validate_quote(snapshot: object, instrument: str, now_ms: int, max_age_ms: int) -> Dict[str, object]:
    """Validate candidate and final quotes identically without coercing timestamps."""
    if not isinstance(snapshot, Mapping) or snapshot.get("instrument") != instrument:
        raise InvalidQuote("quote_symbol_mismatch", _quote_inputs(snapshot, now_ms))
    comparison_now = quote_comparison_now(snapshot, now_ms)
    inputs = _quote_inputs(snapshot, now_ms)
    if snapshot.get("clock") is not None:
        inputs["comparison_now_ms"] = comparison_now
        inputs["clock_uncertainty_ms"] = comparison_now - now_ms - snapshot["clock"]["offset_ms"]
    stamp = snapshot.get("timestamp_ms")
    if type(stamp) is not int or stamp < 0:
        raise InvalidQuote("quote_timestamp_invalid", _quote_inputs(snapshot, now_ms))
    if stamp > comparison_now:
        raise InvalidQuote("quote_future", inputs)
    if comparison_now - stamp > max_age_ms:
        raise InvalidQuote("quote_stale", inputs)
    try:
        values = [Decimal(str(snapshot[k])) for k in ("bid", "ask", "tick_size")]
        if any(not v.is_finite() or v <= 0 for v in values):
            raise InvalidQuote("quote_prices_invalid", _quote_inputs(snapshot, now_ms))
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidQuote("quote_prices_invalid", _quote_inputs(snapshot, now_ms)) from exc
    if values[0] >= values[1]:
        raise InvalidQuote("quote_spread_invalid", _quote_inputs(snapshot, now_ms))
    result = {"instrument": instrument, "timestamp_ms": stamp, **dict(zip(("bid", "ask", "tick_size"), map(str, values)))}
    if snapshot.get("clock") is not None:
        result["clock"] = dict(snapshot["clock"])
    return result


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
    _cycle_guard: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    last_cycle: Optional[Dict[str, object]] = field(default=None, init=False)
    last_error: Optional[str] = field(default=None, init=False)
    latest_evaluations: Dict[str, Dict[str, object]] = field(default_factory=dict, init=False)
    _control_guard: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _eligible: set[str] = field(default_factory=set, init=False)
    generation: str = field(default_factory=lambda: uuid4().hex, init=False)

    def eligible(self, plan_id: str) -> bool:
        return plan_id in self._eligible

    def _record(self, plan: Mapping[str, object], stage: str, reason: str, *, quote: object = None, evaluation: Optional[Mapping[str, object]] = None) -> None:
        plan_id = str(plan["plan_id"])
        item = {"revision": plan["revision"], "stage": stage, "reason": reason,
                "inputs": _quote_inputs(quote, self.now_ms())}
        if evaluation is not None:
            item["evaluation"] = {key: evaluation[key] for key in ("reason", "trigger_price", "projected_price", "classification", "previous_classification") if key in evaluation}
        if isinstance(quote, Mapping) and quote.get("clock") is not None:
            try:
                item["inputs"]["comparison_now_ms"] = quote_comparison_now(quote, item["inputs"]["now_ms"])
            except InvalidQuote:
                pass
        previous = self.latest_evaluations.get(plan_id)
        if previous and reason in {"quote_pending", "calculation_pending", "checking_current_plan"}:
            item = {**previous, "pending_stage": stage, "pending_at_ms": self.now_ms()}
        self.latest_evaluations.pop(plan_id, None)
        self.latest_evaluations[plan_id] = item
        while len(self.latest_evaluations) > 128:
            self.latest_evaluations.pop(next(iter(self.latest_evaluations)))

    async def arm(self, plan_id: str, *, revision: int, generation: str, confirm_live: bool = False) -> Dict[str, object]:
        async with self._control_guard:
            if generation != self.generation:
                raise TrendlinePlanError("Monitoring changed; refresh plans before Arm.")
            existing = self.store.get(plan_id)
            if type(revision) is not int or existing["revision"] != revision:
                raise TrendlinePlanError("Plan changed; refresh plans before Arm.")
            if existing["status"] not in {"draft", "armed"} or existing["trigger_claim"] is not None or self.eligible(plan_id):
                raise TrendlinePlanError("Plan is already monitoring or cannot be armed.")
            live = existing["account"] == "live" and not existing["test_trade"]
            if live and confirm_live is not True:
                raise TrendlinePlanError("Live non-test plans require explicit local execution confirmation.")
            started = await self._start()
            try:
                plan = self.store.resume_and_arm(plan_id, expected_revision=revision, confirm_live=confirm_live, now_ms=self.now_ms())
            except BaseException:
                if started:
                    await self._stop_worker()
                raise
            self._previous.pop(plan_id, None)
            self.latest_evaluations.pop(plan_id, None)
            self._eligible.add(plan_id)
            return plan

    def _reconciliation_status(self) -> Dict[str, object]:
        try:
            registry = self.store.load()
            count = sum(p["status"] in {"claimed", "submitting", "uncertain"} for p in registry["plans"].values())
        except Exception:
            return {"reconciliation_required": None,
                    "reconciliation_message": "Reconciliation status unknown: registry unavailable. Automatic retry remains disabled."}
        return {"reconciliation_required": count,
                "reconciliation_message": "Unresolved claims require manual broker reconciliation; automatic retry is disabled." if count else None}

    def status(self) -> Dict[str, object]:
        return {"running": self._task is not None and not self._task.done(), "generation": self.generation, "last_cycle": self.last_cycle, "last_error": self.last_error, "latest_evaluations": dict(self.latest_evaluations),
                **self._reconciliation_status()}

    async def start(self) -> bool:
        async with self._control_guard:
            return await self._start()

    async def _start(self) -> bool:
        if self.status()["running"]:
            return False
        self._previous.clear()
        self._stop = asyncio.Event()
        worker = self._run()
        try:
            self._task = asyncio.create_task(worker, name="local-trendline-plan-executor")
        except BaseException:
            worker.close()
            raise
        try:
            await asyncio.sleep(0)
            if self._task.done():
                self._task.result()
                raise TrendlinePlanError("Monitoring worker did not start; plan remains inactive.")
        except BaseException:
            self._stop.set()
            if not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._previous.clear()
            self._eligible.clear()
            raise
        return True

    async def stop(self) -> bool:
        async with self._control_guard:
            self.generation = uuid4().hex
            self._eligible.clear()
            return await self._stop_worker()

    async def _stop_worker(self) -> bool:
        task = self._task
        if task is None or task.done():
            self._previous.clear()
            return False
        self._stop.set()
        try:
            await task
        finally:
            self._previous.clear()
        return True

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                await self.cycle()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._previous.clear()
            self._eligible.clear()

    async def cycle(self) -> Dict[str, object]:
        async with self._cycle_guard:
            return await self._cycle()

    async def _cycle(self) -> Dict[str, object]:
        now = self.now_ms()
        processed = 0
        cycle_error = None
        try:
            registry = self.store.load()
            for plan in registry["plans"].values():
                if not self.eligible(str(plan["plan_id"])) or plan["status"] != "armed" or plan["trigger_claim"] is not None:
                    continue
                try:
                    plan_now = self.now_ms()
                    if plan["expiry_at_ms"] is not None and plan_now >= int(plan["expiry_at_ms"]):
                        self.store.expire(str(plan["plan_id"]), now_ms=plan_now)
                        continue
                    await self._evaluate_one(plan)
                except Exception as exc:
                    self._previous.pop(str(plan["plan_id"]), None)
                    cycle_error = _safe_error(exc)
                    latest = self.latest_evaluations.get(str(plan["plan_id"]), {})
                    self._record(plan, str(latest.get("pending_stage", latest.get("stage", "evaluation"))), exc.reason if isinstance(exc, InvalidQuote) else "executor_error")
                    if isinstance(exc, InvalidQuote):
                        self.latest_evaluations[str(plan["plan_id"])]["inputs"] = exc.inputs
                processed += 1
            self.last_error = cycle_error
        except Exception as exc:  # Cycle remains safe; no unclaimed plan was submitted.
            self._previous.clear()
            self.last_error = _safe_error(exc)
        except asyncio.CancelledError:
            self._previous.clear()
            raise
        self.last_cycle = {"at_ms": now, "processed": processed, **self._reconciliation_status()}
        return self.last_cycle

    async def _evaluate_one(self, plan: Mapping[str, object]) -> None:
        plan_id = str(plan["plan_id"])
        if not self.eligible(plan_id):
            return
        if not plan["test_trade"] and not self.live_confirmed(plan):
            self._record(plan, "authorization", "live_confirmation_required")
            return
        if plan["order_intent"] == "limit" and not plan.get("limit_entry_price"):
            raise TrendlinePlanError("Limit entry required.")
        self._record(plan, "candidate_quote", "quote_pending")
        raw_quote = await _await(self.quote(plan))
        if plan["broker"] != "bybit" and isinstance(raw_quote, Mapping) and raw_quote.get("clock") is not None:
            raise InvalidQuote("quote_clock_mismatch")
        quote = validate_quote(raw_quote, str(plan["instrument"]), self.now_ms(), int(plan["max_quote_age_ms"]))
        tick_size = quote.get("tick_size")
        observation = {"timestamp_ms": quote.get("timestamp_ms"), "bid": quote.get("bid"), "ask": quote.get("ask")}
        previous = self._previous.get(plan_id)
        if previous is not None and int(observation["timestamp_ms"]) <= int(previous["timestamp_ms"]):
            raise InvalidQuote("quote_duplicate" if observation["timestamp_ms"] == previous["timestamp_ms"] else "quote_out_of_order", {**_quote_inputs(quote, self.now_ms()), "previous_timestamp_ms": previous["timestamp_ms"]})
        self._previous[plan_id] = observation
        try:
            evaluation = evaluate_trigger(plan, observation, previous_observation=previous, now_ms=quote_comparison_now(quote, self.now_ms()), tick_size=tick_size)
        except TrendlinePlanError:
            self._previous.pop(plan_id, None)
            self._record(plan, "candidate_evaluation", "plan_validation_failed", quote=quote)
            return
        self._record(plan, "candidate_evaluation", str(evaluation.get("reason")), quote=quote, evaluation=evaluation)
        if not evaluation.get("triggered"):
            self._previous[plan_id] = observation
            return
        # The calculator obtains the exact final quote used for sizing.  Re-test
        # that quote before a claim so a moved-away candidate cannot execute.
        self._record(plan, "final_calculation", "calculation_pending", quote=quote, evaluation=evaluation)
        calculated = await _await(self.calculate(plan, quote))
        final_quote = calculated.get("trigger_quote") if isinstance(calculated, Mapping) else None
        self._record(plan, "final_quote", "quote_pending", quote=final_quote)
        final_quote = validate_quote(final_quote, str(plan["instrument"]), self.now_ms(), int(plan["max_quote_age_ms"]))
        require_same_quote_clock(quote, final_quote)
        final_observation = {"timestamp_ms": final_quote.get("timestamp_ms"), "bid": final_quote.get("bid"), "ask": final_quote.get("ask")}
        if int(final_observation["timestamp_ms"]) < int(observation["timestamp_ms"]):
            raise InvalidQuote("final_quote_out_of_order", _quote_inputs(final_quote, self.now_ms()))
        self._previous[plan_id] = final_observation
        final_now = quote_comparison_now(final_quote, self.now_ms())
        final_eval = evaluate_trigger(plan, final_observation, previous_observation=previous, now_ms=final_now, tick_size=final_quote.get("tick_size"))
        self._record(plan, "final_evaluation", str(final_eval.get("reason")), quote=final_quote, evaluation=final_eval)
        if not final_eval.get("triggered"):
            self._previous[plan_id] = final_observation
            return
        # Re-read after the final calculation: cancellation, expiry, revision or
        # replacement invalidate the candidate before the atomic claim.
        fresh = self.store.get(plan_id)
        self._record(plan, "claim", "checking_current_plan", quote=final_quote, evaluation=final_eval)
        if fresh["status"] != "armed" or fresh["trigger_claim"] is not None or fresh["revision"] != plan["revision"] or fresh["execution_key"] != plan["execution_key"] or fresh.get("live_authorization") != plan.get("live_authorization"):
            self._record(plan, "claim", "plan_changed_before_claim", quote=final_quote)
            return
        if not fresh["test_trade"] and not self.live_confirmed(fresh):
            self._record(plan, "claim", "live_confirmation_required", quote=final_quote)
            return
        claim_local_now = self.now_ms()
        claim_now = quote_comparison_now(final_quote, claim_local_now)
        if not self.eligible(plan_id):
            self._record(plan, "claim", "paused_before_claim", quote=final_quote)
            return
        validate_quote(final_quote, str(plan["instrument"]), claim_local_now, int(plan["max_quote_age_ms"]))
        if fresh["expiry_at_ms"] is not None and claim_now >= int(fresh["expiry_at_ms"]):
            self._record(plan, "claim", "expired_before_claim", quote=final_quote)
            return
        claim = self.store.claim_trigger(plan_id, {"trigger_timestamp_ms": final_observation["timestamp_ms"], "trigger_price": final_eval["trigger_price"], "trigger_kind": final_eval["trigger_kind"]}, now_ms=claim_now, expected_revision=int(plan["revision"]), expected_execution_key=str(plan["execution_key"]))
        if not claim["claimed"]:
            self._record(plan, "claim", "duplicate_claim", quote=final_quote)
            return
        claimed = claim["plan"]
        if claimed["test_trade"]:
            self.store.resolve_claim(plan_id, status="submitted", outcome={"outcome": "simulated", "message": "Test plan triggered; no broker order was sent."}, now_ms=self.now_ms())
            self._record(plan, "complete", "simulated", quote=final_quote, evaluation=final_eval)
            return
        # A failed durable transition leaves the claim unresolved and MUST NOT
        # call the adapter. Reconstruction only processes armed plans.
        submitting = self.store.begin_submission(plan_id, execution_key=str(claim["execution_key"]), now_ms=self.now_ms())
        self._record(plan, "submission", "submission_started", quote=final_quote, evaluation=final_eval)
        try:
            result = await _await(self.submit(submitting, calculated, str(claim["execution_key"])))
        except asyncio.CancelledError:
            try:
                self.store.resolve_claim(plan_id, status="uncertain", outcome={"outcome": "uncertain", "message": "Submission interrupted; manual broker reconciliation required."}, now_ms=self.now_ms())
            except Exception:
                # If disk/lock is unavailable, durable submitting still blocks
                # retries. Never replace cancellation with a persistence error.
                pass
            raise
        except DefiniteSubmissionFailure as exc:
            self.store.resolve_claim(plan_id, status="failed", outcome={"outcome": "failed", "message": _safe_error(exc)}, now_ms=self.now_ms())
        except Exception as exc:
            self.store.resolve_claim(plan_id, status="uncertain", outcome={"outcome": "uncertain", "message": _safe_error(exc)}, now_ms=self.now_ms())
        else:
            order_id = result.get("order_id") if isinstance(result, Mapping) else None
            if not isinstance(result, Mapping) or result.get("accepted") is not True or not valid_order_reference(order_id):
                self.store.resolve_claim(plan_id, status="uncertain", outcome={"outcome": "uncertain", "message": "Broker acceptance reference was missing."}, now_ms=self.now_ms())
                return
            self.store.resolve_claim(plan_id, status="submitted", outcome={"outcome": "executed", "message": "Broker accepted order.", "order_id": order_id}, now_ms=self.now_ms())


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, InvalidQuote):
        return "QUOTE_INVALID: Fresh valid broker quote required."
    if isinstance(exc, DefiniteSubmissionFailure):
        return "ORDER_REJECTED: Order was not accepted."
    return "EXECUTOR_ERROR: Local validation or broker operation failed."
