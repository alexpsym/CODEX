"""Local, durable trendline-plan primitives.

This module deliberately has no HTTP, broker, scheduler, or order-submission
dependency.  Future services can evaluate quotes and atomically claim a plan,
but only a later execution phase may submit an order.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from uuid import uuid4


SCHEMA_VERSION = 1
REGISTRY_SCHEMA_VERSION = 1
DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "trendline_plans.json"
MAX_QUOTE_AGE_MS = 300_000
MAX_OBSERVATION_GAP_MS = 3_600_000

_LOCK = threading.RLock()
_BROKERS = {"bybit", "oanda"}
_ACTIONS = {"buy", "sell"}
_ORDER_INTENTS = {"market", "limit"}
_TRIGGER_MODES = {"touch", "confirmed_cross"}
_CROSS_DIRECTIONS = {"either", "upward", "downward"}
_PRICE_BASES = {"executable", "bid", "ask", "midpoint"}
_RISK_MODES = {"fixed_aud", "percent"}
_STATUSES = {
    "draft", "armed", "disabled", "cancelled", "expired", "claimed",
    "submitting", "submitted", "failed", "uncertain",
}
_CLAIMED_STATUSES = {"claimed", "submitting", "submitted", "failed", "uncertain"}
_IMMUTABLE_FIELDS = {
    "schema_version", "plan_id", "execution_key", "revision", "created_at_ms",
    "updated_at_ms", "status", "lifecycle", "trigger_claim", "execution_result",
}
_ORDER_METADATA_FIELDS = (
    "setup", "pattern", "ema", "vwap", "aths_atls", "round_number",
)


class TrendlinePlanError(ValueError):
    """Base error for invalid plans and illegal state transitions."""


class TrendlinePlanPersistenceError(TrendlinePlanError):
    """Raised instead of silently replacing unreadable or invalid local state."""


def utc_epoch_ms() -> int:
    """Return UTC epoch milliseconds without involving local timezone settings."""
    import time
    return time.time_ns() // 1_000_000


def _int_ms(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TrendlinePlanError(f"{field} must be a non-negative integer UTC epoch milliseconds value.")
    return value


def _positive_int(value: object, field: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if allow_zero else 1):
        qualifier = "non-negative" if allow_zero else "positive"
        raise TrendlinePlanError(f"{field} must be a {qualifier} integer.")
    return value


def _decimal(value: object, field: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise TrendlinePlanError(f"{field} must be a Decimal-compatible value.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TrendlinePlanError(f"{field} must be a valid Decimal value.") from exc
    if not parsed.is_finite():
        raise TrendlinePlanError(f"{field} must be finite.")
    if positive and parsed <= 0:
        raise TrendlinePlanError(f"{field} must be positive.")
    if non_negative and parsed < 0:
        raise TrendlinePlanError(f"{field} must be non-negative.")
    return parsed


def _decimal_string(value: object, field: str, *, positive: bool = False, non_negative: bool = False) -> str:
    parsed = _decimal(value, field, positive=positive, non_negative=non_negative)
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _enum(value: object, field: str, values: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in values:
        raise TrendlinePlanError(f"{field} must be one of: {', '.join(sorted(values))}.")
    return normalized


def _text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TrendlinePlanError(f"{field} is required.")
    return text


def _normalise_anchor(value: object, field: str) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise TrendlinePlanError(f"{field} must be an object.")
    return {
        "timestamp_ms": _int_ms(value.get("timestamp_ms"), f"{field}.timestamp_ms"),
        "price": _decimal_string(value.get("price"), f"{field}.price", positive=True),
    }


def _normalise_order_metadata(value: object) -> Dict[str, Optional[str]]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise TrendlinePlanError("order_metadata must be an object.")
    result: Dict[str, Optional[str]] = {}
    for field in _ORDER_METADATA_FIELDS:
        raw = value.get(field)
        if raw is None:
            result[field] = None
        elif isinstance(raw, (str, int)) and not isinstance(raw, bool):
            result[field] = str(raw).strip() or None
        else:
            raise TrendlinePlanError(f"order_metadata.{field} must be text or null.")
    extra = set(value) - set(_ORDER_METADATA_FIELDS)
    if extra:
        raise TrendlinePlanError(f"Unsupported order_metadata fields: {', '.join(sorted(extra))}.")
    return result


def _normalise_claim(value: object, execution_key: str) -> Optional[Dict[str, object]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TrendlinePlanError("trigger_claim must be an object or null.")
    key = _text(value.get("execution_key"), "trigger_claim.execution_key")
    if key != execution_key:
        raise TrendlinePlanError("trigger_claim.execution_key must match the immutable execution_key.")
    claim = {
        "execution_key": key,
        "claimed_at_ms": _int_ms(value.get("claimed_at_ms"), "trigger_claim.claimed_at_ms"),
        "trigger_timestamp_ms": _int_ms(value.get("trigger_timestamp_ms"), "trigger_claim.trigger_timestamp_ms"),
        "trigger_price": _decimal_string(value.get("trigger_price"), "trigger_claim.trigger_price", positive=True),
        "trigger_kind": _text(value.get("trigger_kind"), "trigger_claim.trigger_kind"),
    }
    return claim


def _normalise_execution_result(value: object) -> Optional[Dict[str, object]]:
    """Keep only a small, credential-free durable terminal outcome."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TrendlinePlanError("execution_result must be an object or null.")
    outcome = _enum(value.get("outcome"), "execution_result.outcome", {"executed", "simulated", "failed", "uncertain"})
    result = {"outcome": outcome, "recorded_at_ms": _int_ms(value.get("recorded_at_ms"), "execution_result.recorded_at_ms")}
    for field in ("message", "order_id"):
        raw = value.get(field)
        if raw is not None:
            result[field] = _text(raw, f"execution_result.{field}")[:500]
    return result


def validate_plan(record: Mapping[str, object]) -> Dict[str, object]:
    """Return a canonical, JSON-safe plan record or raise without mutation."""
    if not isinstance(record, Mapping):
        raise TrendlinePlanError("Plan record must be an object.")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise TrendlinePlanError(f"Unsupported plan schema_version: {record.get('schema_version')!r}.")
    anchors_raw = record.get("anchors")
    if not isinstance(anchors_raw, list) or len(anchors_raw) != 2:
        raise TrendlinePlanError("anchors must contain exactly two anchors.")
    anchor_1 = _normalise_anchor(anchors_raw[0], "anchors[0]")
    anchor_2 = _normalise_anchor(anchors_raw[1], "anchors[1]")
    if int(anchor_2["timestamp_ms"]) <= int(anchor_1["timestamp_ms"]):
        raise TrendlinePlanError("Anchor 2 must be later than anchor 1.")
    created_at_ms = _int_ms(record.get("created_at_ms"), "created_at_ms")
    updated_at_ms = _int_ms(record.get("updated_at_ms"), "updated_at_ms")
    if updated_at_ms < created_at_ms:
        raise TrendlinePlanError("updated_at_ms cannot precede created_at_ms.")
    expiry_raw = record.get("expiry_at_ms")
    expiry_at_ms = None if expiry_raw is None else _int_ms(expiry_raw, "expiry_at_ms")
    if expiry_at_ms is not None and expiry_at_ms < created_at_ms:
        raise TrendlinePlanError("expiry_at_ms cannot precede created_at_ms.")
    trigger_mode = _enum(record.get("trigger_mode"), "trigger_mode", _TRIGGER_MODES)
    cross_direction = _enum(record.get("cross_direction"), "cross_direction", _CROSS_DIRECTIONS)
    if trigger_mode == "touch" and cross_direction != "either":
        raise TrendlinePlanError("touch plans must use cross_direction=either.")
    status = _enum(record.get("status"), "status", _STATUSES)
    plan_id = _text(record.get("plan_id"), "plan_id")
    execution_key = _text(record.get("execution_key"), "execution_key")
    revision = _positive_int(record.get("revision"), "revision")
    if not isinstance(record.get("right_extension"), bool):
        raise TrendlinePlanError("right_extension must be boolean.")
    if not isinstance(record.get("test_trade"), bool):
        raise TrendlinePlanError("test_trade must be boolean.")
    if not isinstance(record.get("lifecycle"), Mapping):
        raise TrendlinePlanError("lifecycle must be an object.")
    lifecycle = {
        "last_transition": _text(record["lifecycle"].get("last_transition"), "lifecycle.last_transition"),
        "last_transition_at_ms": _int_ms(record["lifecycle"].get("last_transition_at_ms"), "lifecycle.last_transition_at_ms"),
    }
    claim = _normalise_claim(record.get("trigger_claim"), execution_key)
    execution_result = _normalise_execution_result(record.get("execution_result"))
    if claim is not None and status not in _CLAIMED_STATUSES:
        raise TrendlinePlanError("Only claimed/submitting/submitted/failed/uncertain plans may have a trigger_claim.")
    if claim is None and status in _CLAIMED_STATUSES:
        raise TrendlinePlanError("Claimed/submitting/submitted/failed/uncertain plans require trigger_claim.")
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "revision": revision,
        "created_at_ms": created_at_ms,
        "updated_at_ms": updated_at_ms,
        "broker": _enum(record.get("broker"), "broker", _BROKERS),
        "account": _text(record.get("account"), "account"),
        "instrument": _text(record.get("instrument"), "instrument").upper(),
        "action": _enum(record.get("action"), "action", _ACTIONS),
        "order_intent": _enum(record.get("order_intent"), "order_intent", _ORDER_INTENTS),
        "test_trade": record["test_trade"],
        "anchors": [anchor_1, anchor_2],
        "right_extension": record["right_extension"],
        "trigger_mode": trigger_mode,
        "cross_direction": cross_direction,
        "trigger_price_basis": _enum(record.get("trigger_price_basis"), "trigger_price_basis", _PRICE_BASES),
        "tolerance_ticks": _positive_int(record.get("tolerance_ticks"), "tolerance_ticks", allow_zero=True),
        "max_quote_age_ms": _bounded_ms(record.get("max_quote_age_ms"), "max_quote_age_ms", MAX_QUOTE_AGE_MS),
        "max_observation_gap_ms": _bounded_ms(record.get("max_observation_gap_ms"), "max_observation_gap_ms", MAX_OBSERVATION_GAP_MS),
        "expiry_at_ms": expiry_at_ms,
        "risk_mode": _enum(record.get("risk_mode"), "risk_mode", _RISK_MODES),
        "risk_value": _decimal_string(record.get("risk_value"), "risk_value", positive=True),
        "stop_loss_ticks": _positive_int(record.get("stop_loss_ticks"), "stop_loss_ticks"),
        "rr_target": _decimal_string(record.get("rr_target"), "rr_target", positive=True),
        "timeframe": _text(record.get("timeframe"), "timeframe"),
        "order_metadata": _normalise_order_metadata(record.get("order_metadata")),
        "status": status,
        "lifecycle": lifecycle,
        "execution_key": execution_key,
        "trigger_claim": claim,
        "execution_result": execution_result,
    }


def _bounded_ms(value: object, field: str, maximum: int) -> int:
    parsed = _positive_int(value, field)
    if parsed > maximum:
        raise TrendlinePlanError(f"{field} must not exceed {maximum} milliseconds.")
    return parsed


def projected_price(plan: Mapping[str, object], timestamp_ms: int) -> Decimal:
    """Calculate the unrounded line price. Tick rounding is evaluation-only."""
    canonical = validate_plan(plan)
    t = _int_ms(timestamp_ms, "timestamp_ms")
    first, second = canonical["anchors"]
    t1 = int(first["timestamp_ms"])
    t2 = int(second["timestamp_ms"])
    if t2 <= t1:  # Defensive even though validate_plan already guarantees it.
        raise TrendlinePlanError("Anchor 2 must be later than anchor 1.")
    p1 = _decimal(first["price"], "anchors[0].price", positive=True)
    p2 = _decimal(second["price"], "anchors[1].price", positive=True)
    return p1 + ((p2 - p1) / Decimal(t2 - t1)) * Decimal(t - t1)


def _observation(observation: Mapping[str, object], field: str) -> Dict[str, object]:
    if not isinstance(observation, Mapping):
        raise TrendlinePlanError(f"{field} must be an object.")
    return {
        "timestamp_ms": _int_ms(observation.get("timestamp_ms"), f"{field}.timestamp_ms"),
        "bid": _decimal(observation.get("bid"), f"{field}.bid", positive=True),
        "ask": _decimal(observation.get("ask"), f"{field}.ask", positive=True),
    }


def selected_trigger_price(plan: Mapping[str, object], observation: Mapping[str, object]) -> Decimal:
    canonical = validate_plan(plan)
    quote = _observation(observation, "observation")
    basis = str(canonical["trigger_price_basis"])
    if basis == "bid":
        return quote["bid"]
    if basis == "ask":
        return quote["ask"]
    if basis == "midpoint":
        return (quote["bid"] + quote["ask"]) / Decimal("2")
    return quote["ask"] if canonical["action"] == "buy" else quote["bid"]


def _line_eligibility(plan: Mapping[str, object], timestamp_ms: int) -> Optional[str]:
    first, second = plan["anchors"]
    t1 = int(first["timestamp_ms"])
    t2 = int(second["timestamp_ms"])
    if timestamp_ms < t1:
        return "before_first_anchor"
    if not plan["right_extension"] and timestamp_ms > t2:
        return "right_extension_disabled"
    return None


def _classify(price: Decimal, line: Decimal, tolerance: Decimal) -> str:
    if price < line - tolerance:
        return "below"
    if price > line + tolerance:
        return "above"
    return "inside"


def _previous_reseed_reason(plan: Mapping[str, object], current: Mapping[str, object], previous: Optional[Mapping[str, object]], now_ms: int) -> Optional[str]:
    if previous is None:
        return "previous_missing_reseed"
    try:
        prior = _observation(previous, "previous_observation")
    except TrendlinePlanError:
        return "previous_invalid_reseed"
    prior_ts = int(prior["timestamp_ms"])
    current_ts = int(current["timestamp_ms"])
    if prior_ts > now_ms:
        return "previous_future_reseed"
    if now_ms - prior_ts > int(plan["max_quote_age_ms"]):
        return "previous_stale_reseed"
    if prior_ts >= current_ts:
        return "previous_out_of_order_reseed"
    if current_ts - prior_ts > int(plan["max_observation_gap_ms"]):
        return "previous_gap_reseed"
    if _line_eligibility(plan, prior_ts) is not None:
        return "previous_line_ineligible_reseed"
    return None


def evaluate_trigger(
    plan: Mapping[str, object],
    observation: Mapping[str, object],
    *,
    previous_observation: Optional[Mapping[str, object]] = None,
    now_ms: Optional[int] = None,
    tick_size: object,
) -> Dict[str, object]:
    """Purely evaluate a quote; this function cannot persist or submit an order."""
    canonical = validate_plan(plan)
    now = utc_epoch_ms() if now_ms is None else _int_ms(now_ms, "now_ms")
    quote = _observation(observation, "observation")
    timestamp = int(quote["timestamp_ms"])
    result: Dict[str, object] = {"triggered": False, "reason": None, "trigger_price": None}
    if canonical["status"] != "armed":
        result["reason"] = "plan_not_armed"
        return result
    if canonical["trigger_claim"] is not None:
        result["reason"] = "already_claimed"
        return result
    expiry = canonical["expiry_at_ms"]
    if expiry is not None and now >= int(expiry):
        result["reason"] = "expired"
        return result
    if timestamp > now:
        result["reason"] = "current_observation_future"
        return result
    if now - timestamp > int(canonical["max_quote_age_ms"]):
        result["reason"] = "current_observation_stale"
        return result
    line_reason = _line_eligibility(canonical, timestamp)
    if line_reason:
        result["reason"] = line_reason
        return result
    tick = _decimal(tick_size, "tick_size", positive=True)
    price = selected_trigger_price(canonical, quote)
    line = projected_price(canonical, timestamp)
    tolerance = Decimal(int(canonical["tolerance_ticks"])) * tick
    side = _classify(price, line, tolerance)
    result.update({"trigger_price": _decimal_string(price, "trigger_price", positive=True), "projected_price": _decimal_string(line, "projected_price", positive=True), "classification": side})

    # A touch inside the band is complete with the current fresh quote alone.
    if canonical["trigger_mode"] == "touch" and side == "inside":
        result.update({"triggered": True, "reason": "touch", "trigger_kind": "entered_band"})
        return result

    reseed_reason = _previous_reseed_reason(canonical, quote, previous_observation, now)
    if reseed_reason:
        result["reason"] = reseed_reason
        return result
    prior = _observation(previous_observation or {}, "previous_observation")
    prior_price = selected_trigger_price(canonical, prior)
    prior_line = projected_price(canonical, int(prior["timestamp_ms"]))
    prior_side = _classify(prior_price, prior_line, tolerance)
    result["previous_classification"] = prior_side

    if canonical["trigger_mode"] == "touch":
        if {prior_side, side} == {"below", "above"}:
            result.update({"triggered": True, "reason": "touch_gap_through", "trigger_kind": "gap_through"})
        else:
            result["reason"] = "touch_not_reached"
        return result

    if prior_side not in {"below", "above"} or side not in {"below", "above"}:
        result["reason"] = "confirmed_cross_requires_outside_sides"
        return result
    if prior_side == side:
        result["reason"] = "confirmed_cross_same_side"
        return result
    direction = "upward" if prior_side == "below" and side == "above" else "downward"
    if canonical["cross_direction"] not in {"either", direction}:
        result["reason"] = "confirmed_cross_direction_filtered"
        return result
    result.update({"triggered": True, "reason": "confirmed_cross", "trigger_kind": direction})
    return result


@dataclass
class TrendlinePlanStore:
    """A same-process locked, atomically persisted plan registry."""

    path: Path = DEFAULT_PATH

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def _empty_registry(self) -> Dict[str, object]:
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "plans": {}}

    def _read(self) -> Dict[str, object]:
        if not self.path.exists():
            return self._empty_registry()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise TrendlinePlanPersistenceError(f"Unable to load trendline plan registry: {exc}") from exc
        if not isinstance(raw, Mapping) or raw.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise TrendlinePlanPersistenceError("Trendline plan registry has an unsupported or missing schema_version.")
        plans = raw.get("plans")
        if not isinstance(plans, Mapping):
            raise TrendlinePlanPersistenceError("Trendline plan registry plans must be an object.")
        canonical: Dict[str, object] = {}
        try:
            for plan_id, record in plans.items():
                plan = validate_plan(record)
                if plan_id != plan["plan_id"]:
                    raise TrendlinePlanError("Registry plan key must match plan_id.")
                canonical[str(plan_id)] = plan
        except TrendlinePlanError as exc:
            raise TrendlinePlanPersistenceError(f"Invalid persisted trendline plan registry: {exc}") from exc
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "plans": canonical}

    def load(self) -> Dict[str, object]:
        with _LOCK:
            return copy.deepcopy(self._read())

    def _write(self, registry: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(registry, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except OSError as exc:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise TrendlinePlanPersistenceError(f"Unable to atomically save trendline plan registry: {exc}") from exc

    def create(self, payload: Mapping[str, object], *, now_ms: Optional[int] = None) -> Dict[str, object]:
        now = utc_epoch_ms() if now_ms is None else _int_ms(now_ms, "now_ms")
        if not isinstance(payload, Mapping):
            raise TrendlinePlanError("Plan payload must be an object.")
        supplied_immutable = set(payload) & _IMMUTABLE_FIELDS
        if supplied_immutable:
            raise TrendlinePlanError(f"Create does not accept immutable fields: {', '.join(sorted(supplied_immutable))}.")
        record = dict(payload)
        record.update({
            "schema_version": SCHEMA_VERSION,
            "plan_id": uuid4().hex,
            "execution_key": uuid4().hex,
            "revision": 1,
            "created_at_ms": now,
            "updated_at_ms": now,
            "status": "draft",
            "lifecycle": {"last_transition": "created", "last_transition_at_ms": now},
            "trigger_claim": None,
            "execution_result": None,
        })
        plan = validate_plan(record)
        with _LOCK:
            registry = self._read()
            registry["plans"][plan["plan_id"]] = plan
            self._write(registry)
        return copy.deepcopy(plan)

    def get(self, plan_id: str) -> Dict[str, object]:
        with _LOCK:
            registry = self._read()
            try:
                return copy.deepcopy(registry["plans"][_text(plan_id, "plan_id")])
            except KeyError as exc:
                raise TrendlinePlanError("Unknown trendline plan.") from exc

    def update(self, plan_id: str, changes: Mapping[str, object], *, now_ms: Optional[int] = None) -> Dict[str, object]:
        now = utc_epoch_ms() if now_ms is None else _int_ms(now_ms, "now_ms")
        if not isinstance(changes, Mapping):
            raise TrendlinePlanError("Plan changes must be an object.")
        blocked = set(changes) & _IMMUTABLE_FIELDS
        if blocked:
            raise TrendlinePlanError(f"Plan update cannot change immutable fields: {', '.join(sorted(blocked))}.")
        with _LOCK:
            registry = self._read()
            plan = registry["plans"].get(_text(plan_id, "plan_id"))
            if plan is None:
                raise TrendlinePlanError("Unknown trendline plan.")
            if plan["status"] != "draft":
                raise TrendlinePlanError("Only draft plans may be updated.")
            updated = copy.deepcopy(plan)
            updated.update(copy.deepcopy(dict(changes)))
            updated["revision"] = int(plan["revision"]) + 1
            updated["updated_at_ms"] = now
            updated["lifecycle"] = {"last_transition": "updated", "last_transition_at_ms": now}
            canonical = validate_plan(updated)
            registry["plans"][canonical["plan_id"]] = canonical
            self._write(registry)
            return copy.deepcopy(canonical)

    def _transition(self, plan_id: str, *, target: str, event: str, now_ms: int) -> Dict[str, object]:
        registry = self._read()
        plan = registry["plans"].get(_text(plan_id, "plan_id"))
        if plan is None:
            raise TrendlinePlanError("Unknown trendline plan.")
        if target == "armed":
            if plan["status"] != "draft" or plan["trigger_claim"] is not None:
                raise TrendlinePlanError("Only an unclaimed draft plan may be armed.")
            expiry = plan["expiry_at_ms"]
            if expiry is not None and now_ms >= int(expiry):
                raise TrendlinePlanError("Expired plans cannot be armed.")
        elif target == "cancelled":
            if plan["status"] not in {"draft", "armed", "disabled"}:
                raise TrendlinePlanError("Only draft, armed, or disabled plans may be cancelled.")
        elif target == "expired":
            if plan["status"] not in {"draft", "armed", "disabled"}:
                raise TrendlinePlanError("Only unclaimed inactive plans may expire.")
            if plan["expiry_at_ms"] is None or now_ms < int(plan["expiry_at_ms"]):
                raise TrendlinePlanError("Plan expiry has not been reached.")
        else:
            raise TrendlinePlanError("Unsupported transition.")
        next_plan = copy.deepcopy(plan)
        next_plan["status"] = target
        next_plan["revision"] = int(plan["revision"]) + 1
        next_plan["updated_at_ms"] = now_ms
        next_plan["lifecycle"] = {"last_transition": event, "last_transition_at_ms": now_ms}
        canonical = validate_plan(next_plan)
        registry["plans"][canonical["plan_id"]] = canonical
        self._write(registry)
        return copy.deepcopy(canonical)

    def arm(self, plan_id: str, *, now_ms: Optional[int] = None) -> Dict[str, object]:
        now = utc_epoch_ms() if now_ms is None else _int_ms(now_ms, "now_ms")
        with _LOCK:
            return self._transition(plan_id, target="armed", event="armed", now_ms=now)

    def cancel(self, plan_id: str, *, now_ms: Optional[int] = None) -> Dict[str, object]:
        now = utc_epoch_ms() if now_ms is None else _int_ms(now_ms, "now_ms")
        with _LOCK:
            return self._transition(plan_id, target="cancelled", event="cancelled", now_ms=now)

    def expire(self, plan_id: str, *, now_ms: Optional[int] = None) -> Dict[str, object]:
        now = utc_epoch_ms() if now_ms is None else _int_ms(now_ms, "now_ms")
        with _LOCK:
            return self._transition(plan_id, target="expired", event="expired", now_ms=now)

    def claim_trigger(self, plan_id: str, trigger: Mapping[str, object], *, now_ms: Optional[int] = None) -> Dict[str, object]:
        """Persist an idempotent claim before returning it; never submit an order."""
        now = utc_epoch_ms() if now_ms is None else _int_ms(now_ms, "now_ms")
        if not isinstance(trigger, Mapping):
            raise TrendlinePlanError("trigger must be an object.")
        with _LOCK:
            registry = self._read()
            plan = registry["plans"].get(_text(plan_id, "plan_id"))
            if plan is None:
                raise TrendlinePlanError("Unknown trendline plan.")
            if plan["trigger_claim"] is not None:
                return {"claimed": False, "duplicate": True, "execution_key": plan["execution_key"], "plan": copy.deepcopy(plan)}
            if plan["status"] != "armed":
                raise TrendlinePlanError("Only armed, unclaimed plans may be trigger-claimed.")
            expiry = plan["expiry_at_ms"]
            if expiry is not None and now >= int(expiry):
                raise TrendlinePlanError("Expired plans cannot be trigger-claimed.")
            claim = _normalise_claim({
                "execution_key": plan["execution_key"],
                "claimed_at_ms": now,
                "trigger_timestamp_ms": trigger.get("trigger_timestamp_ms"),
                "trigger_price": trigger.get("trigger_price"),
                "trigger_kind": trigger.get("trigger_kind"),
            }, str(plan["execution_key"]))
            next_plan = copy.deepcopy(plan)
            next_plan["status"] = "claimed"
            next_plan["trigger_claim"] = claim
            next_plan["revision"] = int(plan["revision"]) + 1
            next_plan["updated_at_ms"] = now
            next_plan["lifecycle"] = {"last_transition": "trigger_claimed", "last_transition_at_ms": now}
            canonical = validate_plan(next_plan)
            registry["plans"][canonical["plan_id"]] = canonical
            self._write(registry)
            return {"claimed": True, "duplicate": False, "execution_key": canonical["execution_key"], "plan": copy.deepcopy(canonical)}

    def resolve_claim(self, plan_id: str, *, status: str, outcome: Mapping[str, object], now_ms: Optional[int] = None) -> Dict[str, object]:
        """Persist a final claimed outcome; callers must never retry it automatically."""
        now = utc_epoch_ms() if now_ms is None else _int_ms(now_ms, "now_ms")
        if status not in {"submitted", "failed", "uncertain"}:
            raise TrendlinePlanError("Claim resolution status must be submitted, failed, or uncertain.")
        with _LOCK:
            registry = self._read()
            plan = registry["plans"].get(_text(plan_id, "plan_id"))
            if plan is None:
                raise TrendlinePlanError("Unknown trendline plan.")
            if plan["status"] != "claimed" or plan["trigger_claim"] is None:
                raise TrendlinePlanError("Only a claimed plan may be resolved.")
            updated = copy.deepcopy(plan)
            updated["status"] = status
            updated["execution_result"] = _normalise_execution_result({**dict(outcome), "recorded_at_ms": now})
            updated["revision"] = int(plan["revision"]) + 1
            updated["updated_at_ms"] = now
            updated["lifecycle"] = {"last_transition": f"claim_{status}", "last_transition_at_ms": now}
            canonical = validate_plan(updated)
            registry["plans"][canonical["plan_id"]] = canonical
            self._write(registry)
            return copy.deepcopy(canonical)
