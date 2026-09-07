import copy
import json

import pytest

from render import trendline_plans as plans


def _payload(**overrides):
    value = {
        "broker": "bybit",
        "account": "demo",
        "instrument": "BTCUSDT",
        "action": "buy",
        "order_intent": "market",
        "test_trade": True,
        "anchors": [
            {"timestamp_ms": 10_000, "price": "1.0"},
            {"timestamp_ms": 20_000, "price": "1.2"},
        ],
        "right_extension": True,
        "trigger_mode": "touch",
        "cross_direction": "either",
        "trigger_price_basis": "executable",
        "tolerance_ticks": 1,
        "max_quote_age_ms": 10_000,
        "max_observation_gap_ms": 4_000,
        "expiry_at_ms": None,
        "risk_mode": "fixed_aud",
        "risk_value": "10.00",
        "stop_loss_ticks": 100,
        "rr_target": "2.00",
        "timeframe": "15m",
        "order_metadata": {
            "setup": "Trendline",
            "pattern": None,
            "ema": "No",
            "vwap": "No",
            "aths_atls": "No",
            "round_number": "No",
        },
    }
    value.update(overrides)
    return value


def _arm(store, payload=None):
    created = store.create(payload or _payload(), now_ms=5_000)
    return store.arm(created["plan_id"], now_ms=6_000)


def test_trendline_plan_schema_projection_evaluation_and_atomic_claims(tmp_path):
    store = plans.TrendlinePlanStore(tmp_path / "trendline_plans.json")
    created = store.create(_payload(), now_ms=5_000)
    registry = store.load()
    assert registry["schema_version"] == plans.REGISTRY_SCHEMA_VERSION
    assert registry["plans"][created["plan_id"]] == created
    assert json.loads(store.path.read_text(encoding="utf-8")) == registry
    assert created["anchors"][0]["price"] == "1"
    assert created["risk_value"] == "10"
    assert created["execution_key"]

    broken = copy.deepcopy(created)
    broken["anchors"][1]["timestamp_ms"] = broken["anchors"][0]["timestamp_ms"]
    with pytest.raises(plans.TrendlinePlanError, match="later"):
        plans.validate_plan(broken)
    with pytest.raises(plans.TrendlinePlanError, match="touch plans"):
        store.create(_payload(cross_direction="upward"), now_ms=5_000)
    with pytest.raises(plans.TrendlinePlanError, match="positive"):
        store.create(_payload(risk_value="0"), now_ms=5_000)
    with pytest.raises(plans.TrendlinePlanError, match="cannot precede"):
        store.create(_payload(expiry_at_ms=4_999), now_ms=5_000)

    armed = store.arm(created["plan_id"], now_ms=6_000)
    assert plans.projected_price(armed, 10_000) == plans.Decimal("1")
    assert plans.projected_price(armed, 20_000) == plans.Decimal("1.2")
    assert plans.projected_price(armed, 30_000) == plans.Decimal("1.4")
    assert plans.selected_trigger_price(armed, {"timestamp_ms": 20_000, "bid": "1.19", "ask": "1.21"}) == plans.Decimal("1.21")
    midpoint = copy.deepcopy(armed)
    midpoint["trigger_price_basis"] = "midpoint"
    assert plans.selected_trigger_price(midpoint, {"timestamp_ms": 20_000, "bid": "1.19", "ask": "1.21"}) == plans.Decimal("1.20")
    sell = copy.deepcopy(armed)
    sell["action"] = "sell"
    assert plans.selected_trigger_price(sell, {"timestamp_ms": 20_000, "bid": "1.19", "ask": "1.21"}) == plans.Decimal("1.19")

    no_extension = copy.deepcopy(armed)
    no_extension["right_extension"] = False
    assert plans.evaluate_trigger(no_extension, {"timestamp_ms": 9_999, "bid": "1", "ask": "1"}, now_ms=10_000, tick_size="0.01")["reason"] == "before_first_anchor"
    assert plans.evaluate_trigger(no_extension, {"timestamp_ms": 20_001, "bid": "1.2", "ask": "1.2"}, now_ms=20_001, tick_size="0.01")["reason"] == "right_extension_disabled"

    # Touch uses executable ask for buys, accepts an inside-band quote, and
    # also accepts a fresh complete gap through the band at the current quote.
    touched = plans.evaluate_trigger(armed, {"timestamp_ms": 20_000, "bid": "1.195", "ask": "1.205"}, now_ms=20_000, tick_size="0.01")
    assert touched["triggered"] and touched["trigger_kind"] == "entered_band"
    gap = plans.evaluate_trigger(
        armed,
        {"timestamp_ms": 20_000, "bid": "1.245", "ask": "1.25"},
        previous_observation={"timestamp_ms": 19_000, "bid": "1.145", "ask": "1.15"},
        now_ms=20_000,
        tick_size="0.01",
    )
    assert gap["triggered"] and gap["trigger_kind"] == "gap_through"
    assert gap["trigger_price"] == "1.25"

    upward = _arm(store, _payload(trigger_mode="confirmed_cross", cross_direction="upward"))
    crossing = plans.evaluate_trigger(
        upward,
        {"timestamp_ms": 20_000, "bid": "1.245", "ask": "1.25"},
        previous_observation={"timestamp_ms": 19_000, "bid": "1.145", "ask": "1.15"},
        now_ms=20_000,
        tick_size="0.01",
    )
    assert crossing["triggered"] and crossing["trigger_kind"] == "upward"
    downward = _arm(store, _payload(trigger_mode="confirmed_cross", cross_direction="downward"))
    down_cross = plans.evaluate_trigger(
        downward,
        {"timestamp_ms": 20_000, "bid": "1.145", "ask": "1.15"},
        previous_observation={"timestamp_ms": 19_000, "bid": "1.245", "ask": "1.25"},
        now_ms=20_000,
        tick_size="0.01",
    )
    assert down_cross["triggered"] and down_cross["trigger_kind"] == "downward"
    tolerance = plans.evaluate_trigger(
        upward,
        {"timestamp_ms": 20_000, "bid": "1.205", "ask": "1.21"},
        previous_observation={"timestamp_ms": 19_000, "bid": "1.145", "ask": "1.15"},
        now_ms=20_000,
        tick_size="0.01",
    )
    assert not tolerance["triggered"] and tolerance["reason"] == "confirmed_cross_requires_outside_sides"

    assert plans.evaluate_trigger(upward, {"timestamp_ms": 20_001, "bid": "1", "ask": "1"}, now_ms=20_000, tick_size="0.01")["reason"] == "current_observation_future"
    stale = plans.evaluate_trigger(upward, {"timestamp_ms": 20_000, "bid": "1.25", "ask": "1.25"}, previous_observation={"timestamp_ms": 9_999, "bid": "1", "ask": "1"}, now_ms=20_000, tick_size="0.01")
    assert stale["reason"] == "previous_stale_reseed"
    future = plans.evaluate_trigger(upward, {"timestamp_ms": 20_000, "bid": "1.25", "ask": "1.25"}, previous_observation={"timestamp_ms": 20_002, "bid": "1", "ask": "1"}, now_ms=20_001, tick_size="0.01")
    assert future["reason"] == "previous_future_reseed"
    out_of_order = plans.evaluate_trigger(upward, {"timestamp_ms": 20_000, "bid": "1.25", "ask": "1.25"}, previous_observation={"timestamp_ms": 20_000, "bid": "1", "ask": "1"}, now_ms=20_000, tick_size="0.01")
    assert out_of_order["reason"] == "previous_out_of_order_reseed"
    gap_plan = copy.deepcopy(upward)
    gap_plan["max_observation_gap_ms"] = 500
    excessive = plans.evaluate_trigger(gap_plan, {"timestamp_ms": 20_000, "bid": "1.25", "ask": "1.25"}, previous_observation={"timestamp_ms": 19_000, "bid": "1", "ask": "1"}, now_ms=20_000, tick_size="0.01")
    assert excessive["reason"] == "previous_gap_reseed"

    expiring = _arm(store, _payload(expiry_at_ms=7_000))
    assert plans.evaluate_trigger(expiring, {"timestamp_ms": 7_000, "bid": "1.2", "ask": "1.2"}, now_ms=7_000, tick_size="0.01")["reason"] == "expired"
    assert store.expire(expiring["plan_id"], now_ms=7_000)["status"] == "expired"
    assert plans.evaluate_trigger(store.get(expiring["plan_id"]), {"timestamp_ms": 7_000, "bid": "1.2", "ask": "1.2"}, now_ms=7_000, tick_size="0.01")["reason"] == "plan_not_armed"

    claimed = store.claim_trigger(
        armed["plan_id"],
        {"trigger_timestamp_ms": 20_000, "trigger_price": "1.205", "trigger_kind": "entered_band"},
        now_ms=20_001,
    )
    assert claimed["claimed"] and claimed["plan"]["status"] == "claimed"
    duplicate = store.claim_trigger(
        armed["plan_id"],
        {"trigger_timestamp_ms": 20_002, "trigger_price": "1.21", "trigger_kind": "duplicate"},
        now_ms=20_002,
    )
    assert not duplicate["claimed"] and duplicate["duplicate"]
    assert duplicate["execution_key"] == claimed["execution_key"]
    reloaded = plans.TrendlinePlanStore(store.path).get(armed["plan_id"])
    assert reloaded["status"] == "claimed" and reloaded["trigger_claim"] == claimed["plan"]["trigger_claim"]
    with pytest.raises(plans.TrendlinePlanError, match="may be armed"):
        store.arm(armed["plan_id"], now_ms=20_003)

    original_state = store.path.read_text(encoding="utf-8")
    store.path.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(plans.TrendlinePlanPersistenceError, match="Unable to load"):
        store.load()
    assert store.path.read_text(encoding="utf-8") == "{ not valid json"
    store.path.write_text(original_state, encoding="utf-8")
    corrupted = json.loads(original_state)
    one_plan = next(iter(corrupted["plans"].values()))
    one_plan["instrument"] = ""
    store.path.write_text(json.dumps(corrupted), encoding="utf-8")
    with pytest.raises(plans.TrendlinePlanPersistenceError, match="Invalid persisted"):
        store.load()
