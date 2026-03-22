from flask import Flask, request, render_template_string, make_response, jsonify
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from shutil import which
from typing import Iterable, Optional
import webbrowser
import requests

# Must be the external Render URL (not 127.0.0.1) so “Copy Webhook” is correct.
PUBLIC_WEBHOOK_URL = os.getenv(
    "PUBLIC_WEBHOOK_URL", "https://codex-rdqh.onrender.com/webhook"
)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL") or PUBLIC_WEBHOOK_URL.rsplit("/", 1)[0]
LOGGER = logging.getLogger(__name__)


def _open_edge(url: str) -> None:
    """Open ``url`` in Microsoft Edge or raise an error if unavailable."""

    errors = []

    # First, try the webbrowser registry.
    for controller in ("msedge", "edge"):
        try:
            browser = webbrowser.get(controller)
            browser.open(url)
            return
        except webbrowser.Error as exc:
            errors.append(f"{controller}: {exc}")

    # Fall back to invoking the executable directly without ever touching the
    # system's default browser.  This covers situations where the controller
    # isn't registered but Edge is installed in a standard location.
    candidate_executables = (
        "msedge",
        "msedge.exe",
        "microsoft-edge",
        "microsoft-edge.exe",
    )
    candidate_paths = []
    for name in candidate_executables:
        path = which(name)
        if path:
            candidate_paths.append(path)

    # Common Windows installation directories where Edge might live even if it
    # is not on PATH.  ``Path`` handles Windows drive letters transparently on
    # any platform, so these checks are safe.
    program_files = [
        Path(os_path)
        for os_path in (
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        )
    ]
    for candidate in program_files:
        if candidate.exists():
            candidate_paths.append(str(candidate))

    # Remove duplicates while preserving discovery order.
    seen_paths = set()
    unique_paths = []
    for path in candidate_paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        unique_paths.append(path)
    candidate_paths = unique_paths

    for path in candidate_paths:
        try:
            subprocess.Popen([path, url])
            return
        except Exception as exc:  # pragma: no cover - platform dependent
            errors.append(f"{path}: {exc}")

    error_message = "Microsoft Edge could not be launched."
    if errors:
        error_message += " Details: " + "; ".join(errors)
    raise RuntimeError(error_message)


def _format_price_distance(value: float, precision: int) -> str:
    """Format a price distance without trimming significant decimals."""

    precision = max(0, precision)
    formatted = f"{value:.{precision}f}"
    # Remove trailing zeros, but keep at least one decimal place so the
    # resulting string remains a valid decimal representation.
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


# Fallback instrument list if the API is unavailable
DEFAULT_INSTRUMENTS = [
    'AU200_AUD', 'AUD_CAD', 'AUD_CHF', 'AUD_JPY', 'AUD_NZD', 'AUD_USD',
    'BCO_USD', 'BTC_USD', 'CAD_CHF', 'CAD_JPY', 'CHF_JPY', 'CN50_USD',
    'DE40_EUR', 'ES35_EUR', 'EU50_EUR', 'EUR_AUD', 'EUR_CAD', 'EUR_CHF',
    'EUR_GBP', 'EUR_JPY', 'EUR_NZD', 'EUR_USD', 'FRA40_EUR', 'GBP_AUD',
    'GBP_CAD', 'GBP_CHF', 'GBP_JPY', 'GBP_NZD', 'GBP_USD', 'HK33_HKD',
    'JP225_USD', 'NAS100_USD', 'NATGAS_USD', 'NZD_CAD', 'NZD_CHF',
    'NZD_JPY', 'NZD_USD', 'SG30_SGD', 'SPX500_USD', 'UK100_GBP',
    'US30_USD', 'USD_CAD', 'USD_CHF', 'USD_JPY', 'WTICO_USD', 'XAG_USD',
    'XAU_USD'
]

# Import helper functions directly without relying on the old CLI tool
from oanda_api import (
    get_account_details,
    get_available_instruments,
    get_instrument_details,
    get_price,
    build_order,
)

app = Flask(__name__)

# Stores details of the most recently calculated trade so it can be
# downloaded later.
last_trade_specs = None

_OANDA_INSTRUMENT_CACHE_TTL_SECONDS = int(
    os.getenv('OANDA_INSTRUMENT_CACHE_TTL_SECONDS', '3600')
)
_OANDA_INSTRUMENT_CACHE = {
    'live': {'ts': 0.0, 'items': None},
    'demo': {'ts': 0.0, 'items': None},
}


def _get_available_instruments_cached(account_mode: str) -> Optional[list[str]]:
    mode = (account_mode or 'live').strip().lower()
    if mode not in {'live', 'demo'}:
        mode = 'live'
    now = time.time()
    entry = _OANDA_INSTRUMENT_CACHE.get(mode) or {}
    ts = float(entry.get('ts') or 0.0)
    items = entry.get('items')
    if isinstance(items, list) and (now - ts) <= _OANDA_INSTRUMENT_CACHE_TTL_SECONDS:
        return items
    try:
        LOGGER.info('OANDA_CALC_CALL get_available_instruments mode=%s', mode)
        instruments = sorted(get_available_instruments(mode))
    except Exception:
        instruments = None
    _OANDA_INSTRUMENT_CACHE[mode] = {'ts': now, 'items': instruments}
    return instruments


def _instrument_lookup_key(value: str) -> str:
    """Return a canonical lookup key for an instrument string."""

    return "".join(ch for ch in value.upper() if ch.isalnum())


def _resolve_instrument(user_value: str, available: Optional[Iterable[str]]) -> str:
    """Match ``user_value`` against *available* instruments case-insensitively."""

    if not user_value or not user_value.strip():
        raise ValueError("Instrument is required")

    lookup_key = _instrument_lookup_key(user_value)
    choices = list(available) if available else DEFAULT_INSTRUMENTS
    mapping = {_instrument_lookup_key(inst): inst for inst in choices}
    match = mapping.get(lookup_key)
    if match:
        return match
    raise ValueError(f"Instrument {user_value} not available")


@app.get('/api/resolve-instrument')
def resolve_instrument_api():
    raw = request.args.get('instrument', '')
    account_mode = request.args.get('account_mode', 'live')
    if not raw or not str(raw).strip():
        return jsonify({'detail': 'instrument is required'}), 400

    available = _get_available_instruments_cached(account_mode)
    try:
        resolved = _resolve_instrument(raw, available)
    except Exception as exc:
        return jsonify({'detail': str(exc), 'input': raw}), 404
    return jsonify({'input': raw, 'resolved': resolved, 'account_mode': account_mode})

FORM_HTML = """
<!doctype html>
<html>
<head>
<title>{{ page_title }}</title>
<style>
  body {background:black; color:white; font-family:Arial, sans-serif;}
  input, select, button {margin:4px 0;}
  .container {display:flex; align-items:flex-start; gap:20px;}
  .form {flex:0 0 520px; max-width:520px;}
  .result {flex:1 1 640px; max-width:980px;}
  .copy-row {display:flex; gap:8px; align-items:center; margin:6px 0;}
  .copy-row button {cursor:pointer;}
  .copy-status {font-size:12px; color:#9ca3af;}
  .copy-box {background:#111827; border:1px solid #1f2937; padding:8px; border-radius:6px; color:#e5e7eb; max-width:520px; white-space:pre-wrap;}
  .trade-section {padding:10px; border:1px solid #1f2937; margin-bottom:12px;}
  .hidden {display:none;}
  .button-group {display:flex; flex-wrap:wrap; gap:8px; margin:6px 0;}
  .button-group button {background:#1f2937; color:#e2e8f0; border:1px solid #334155; border-radius:8px; padding:6px 12px; font-weight:700;}
  .button-group button.active {background:#2563eb; color:#fff; border-color:#60a5fa;}
  .danger-button {background:#b91c1c; color:#fff; border:1px solid #ef4444; font-weight:800;}
  .danger-note {color:#fca5a5; font-size:12px;}
  .symbol-row {display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:4px 0 10px;}
  .symbol-row input {min-width: 180px;}
  .specs-panel {margin-top: 12px; max-width:980px;}
  .table-wrap { overflow:auto; max-height:70vh; border-radius: 12px; border: 1px solid #1f2937; background: #0b1220; }
  .specs-table { width: 100%; border-collapse: collapse; min-width: 480px; }
  .specs-table th, .specs-table td { text-align:left; padding:0.55rem 0.65rem; border-bottom:1px solid #1f2937; font-size:0.9rem; vertical-align:top; }
  .specs-table td { white-space: normal; word-break: break-word; }
  .specs-table th { background:#0f172a; color:#cbd5e1; position:sticky; top:0; z-index:1; }
</style>
</head>
<body data-app-root="{{ app_root }}">
{% if not embedded %}<h1>OANDA Position Size Calculator</h1>{% endif %}
<div class="container">
  <div class="form">
    <form method="post" action="{{ form_action }}">
      <div class="trade-section">
        <label>Account:</label>
        <div class="button-group" data-input="account_mode">
          <button type="button" data-value="live">Live</button>
          <button type="button" data-value="demo">Demo</button>
        </div>
        <input type="hidden" name="account_mode" id="account_mode" value="{{ account_mode }}">
        <label>Instrument:</label>
        <div class="symbol-row">
          <input name="instrument" id="instrument" value="{{ instrument_input or '' }}" required autocomplete="off">
          <button type="button" id="instrument_specs_btn">Specs</button>
          <span class="copy-status" id="instrument_status"></span>
        </div>
        <label>Side:</label>
        <div class="button-group" data-input="side">
          <button type="button" data-value="buy">Buy</button>
          <button type="button" data-value="sell">Sell</button>
        </div>
        <input type="hidden" name="side" id="side" value="{{ side }}">
        <label>Order Type:</label>
        <div class="button-group" data-input="order_type">
          <button type="button" data-value="market">Market</button>
          <button type="button" data-value="limit">Limit</button>
        </div>
        <input type="hidden" name="order_type" id="order_type" value="{{ order_type }}">
        <label>Show in Dashboard Open Orders:</label>
        <div class="button-group" data-input="track_pending">
          <button type="button" data-value="yes">Yes</button>
          <button type="button" data-value="no">No</button>
        </div>
        <input type="hidden" name="track_pending" id="track_pending" value="{{ track_pending }}">
        <div id="entry_price_row" style="{% if order_type == 'market' %}display:none;{% endif %}">
          <label>Entry Price:
            <input name="entry_price" id="entry_price" type="number" step="0.0001" value="{{ entry_price or '' }}" {% if order_type == 'limit' %}required{% endif %}>
          </label><br>
          <label>Limit cancel offset (abs):
            <input name="limit_cancel_offset" id="limit_cancel_offset" type="number" step="0.0001" value="{{ limit_cancel_offset or '' }}">
          </label><br>
          <label>Limit cancel offset (%):
            <input name="limit_cancel_offset_pct" id="limit_cancel_offset_pct" type="number" step="0.01" value="{{ limit_cancel_offset_pct or '' }}">
          </label><br>
          <small>Cancel pending limit orders if price moves away by the given distance.</small><br>
        </div>
        <label>Stop loss (ticks): <input name="stop_ticks" id="stop_ticks" type="number" step="1" value="{{ stop_ticks or '' }}" required></label><br>
        <label>Risk mode:</label>
        <div class="button-group" data-input="risk_mode">
          <button type="button" data-value="percent">Percent</button>
          <button type="button" data-value="amount">Fixed Amount</button>
        </div>
        <input type="hidden" name="risk_mode" id="risk_mode" value="{{ risk_mode }}">
        <div id="risk_percent_row" style="{% if risk_mode != 'percent' %}display:none;{% endif %}">
          <label>Risk %: <input name="risk_pct" id="risk_pct" type="number" step="0.01" value="{{ risk_pct or '' }}" {% if risk_mode == 'percent' %}required{% endif %}></label><br>
        </div>
        <div id="risk_amount_row" style="{% if risk_mode != 'amount' %}display:none;{% endif %}">
          <label>Risk amount AUD: <input name="risk_aud" id="risk_aud" type="number" step="0.01" value="{{ risk_aud or '' }}" {% if risk_mode == 'amount' %}required{% endif %}></label><br>
        </div>
        <label>Risk–reward ratio: <input name="rr_ratio" id="rr_ratio" type="number" step="0.1" value="{{ rr_ratio or '2' }}" required></label><br>
        <button type="submit">Calculate</button>
      </div>
      <h3>TradingView Webhook</h3>
      <div class="copy-row">
        <button type="button" onclick="copyText('{{ webhook_url }}','webhook_status')">Copy Webhook URL</button>
        <span class="copy-status" id="webhook_status"></span>
      </div>
      <div class="copy-box" id="webhook_url">{{ webhook_url }}</div>
    </form>
  </div>
  <div class="result">
    {% if error %}<p style="color: red;">{{ error }}</p>{% endif %}
    <div id="embedded_specs" class="trade-section specs-panel hidden">
      <h2>Instrument Specs</h2>
      <div class="copy-status" id="embedded_specs_status"></div>
      <div class="table-wrap">
        <table class="specs-table">
          <thead><tr><th>Field</th><th>Value</th></tr></thead>
          <tbody id="embedded_specs_rows"></tbody>
        </table>
      </div>
    </div>
    {% if alert_json %}
      <h2>Result</h2>
      <pre id="alert_json">{{ alert_json }}</pre>
      <pre id="tv_payload" style="display:none;">{{ alert_json }}</pre>
      <div class="copy-row">
        <button type="button" onclick="copyFromElement('alert_json','payload_status')">Copy TradingView Message</button>
        <span class="copy-status" id="payload_status"></span>
      </div>
      <div class="copy-row">
        <button type="button" onclick="window.location='{{ download_url }}'">Download Specs</button>
      </div>
      <div id="calc_meta"
           data-order-type="{{ order_type }}"
           style="display:none;"></div>
      <div class="copy-row">
        <button id="execute_market_btn" type="button" class="danger-button" onclick="enterNow()">
          Enter now via market order
        </button>
        <button id="execute_limit_btn" type="button" class="danger-button" onclick="placeLimitOrder()"
                style="display:none;">
          Place limit order
        </button>
      </div>
      <p id="execute_market_note" class="danger-note">
        This immediately submits a live market order. Use with extreme caution.
      </p>
      <p id="execute_limit_note" class="danger-note" style="display:none;">
        This submits a live limit order (GTC) at the entry price. Use with extreme caution.
      </p>
      <pre id="execute_result" class="copy-box hidden"></pre>
    {% endif %}
    {% if risk_info %}
    <h2>Position Details</h2>
    <table border="1">
      <tr><th>Item</th><th>Value</th></tr>
      {% for key, value in risk_info.items() %}
      <tr><td>{{ key }}</td><td>{{ value }}</td></tr>
      {% endfor %}
    </table>
    {% endif %}
  </div>
</div>
<script src="{{ app_root }}/static/oanda_calculator.js"></script>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    alert_json = None
    error = None
    risk_info = None
    instrument = None
    instrument_input = None
    available_instruments = None
    global last_trade_specs
    app_root = (request.headers.get("x-forwarded-prefix", "").rstrip("/") or "")
    webhook_url = PUBLIC_WEBHOOK_URL
    embedded = str(request.args.get("embedded", "")).strip().lower() in {"1", "true", "yes", "on"}
    page_title = (request.args.get("title") or "").strip() or "OANDA Position Size Calculator"
    form_action = request.full_path if embedded and request.query_string else request.path
    if form_action.endswith("?"):
        form_action = form_action[:-1]
    download_url = f"{app_root}/download_specs" if app_root else "/download_specs"
    last_trade_specs = None
    account_mode = request.form.get("account_mode", "live")
    side = request.form.get("side", "buy")
    order_type = request.form.get("order_type", "market")
    track_pending = request.form.get("track_pending", "no")
    risk_mode = request.form.get("risk_mode", "percent")
    stop_ticks = request.form.get("stop_ticks", "")
    risk_pct = request.form.get("risk_pct", "")
    risk_aud = request.form.get("risk_aud", "")
    rr_ratio = request.form.get("rr_ratio", "2")
    entry_price = request.form.get("entry_price", "")
    limit_cancel_offset = request.form.get("limit_cancel_offset", "")
    limit_cancel_offset_pct = request.form.get("limit_cancel_offset_pct", "")
    if request.method == "POST":
        try:
            instrument_input = request.form["instrument"]
            available_instruments = _get_available_instruments_cached(account_mode)
            instrument = _resolve_instrument(instrument_input, available_instruments)
            instrument_input = instrument
            side = request.form["side"]
            stop_ticks = float(request.form["stop_ticks"])
            risk_mode = request.form["risk_mode"]
            order_type = request.form.get("order_type", "market")
            rr_ratio = float(request.form["rr_ratio"])
            entry_price = request.form.get("entry_price")
            entry_price_value = None
            if order_type == "limit":
                if entry_price is None or not str(entry_price).strip():
                    raise ValueError("Entry price is required for limit orders.")
                entry_price_value = float(entry_price)
                if entry_price_value <= 0:
                    raise ValueError("Entry price must be positive.")
            cancel_offset_value = (
                float(limit_cancel_offset) if str(limit_cancel_offset).strip() else None
            )
            cancel_offset_pct_value = (
                float(limit_cancel_offset_pct)
                if str(limit_cancel_offset_pct).strip()
                else None
            )

            LOGGER.info("OANDA_CALC_CALL get_account_details mode=%s", account_mode)
            account = get_account_details(account_mode)
            balance = float(account["account"]["balance"])
            margin_available = float(account["account"].get("marginAvailable", balance))
            account_currency = account["account"].get("currency", "AUD")

            LOGGER.info(
                "OANDA_CALC_CALL get_instrument_details mode=%s instrument=%s",
                account_mode,
                instrument,
            )
            details = get_instrument_details(instrument, account_mode)
            pip_location = int(details.get("pipLocation", -4))
            display_precision = int(details.get("displayPrecision", abs(pip_location)))
            units_precision = int(details.get("tradeUnitsPrecision", 0))
            # ``displayPrecision`` defines how many decimal places OANDA quotes
            # for an instrument.  The smallest price movement (a single tick)
            # is therefore ``10`` raised to the negative of this precision.
            # Using ``pipLocation`` for currencies previously produced a tick
            # size one order of magnitude too large (e.g. GBPUSD calculated
            # 0.0001 instead of 0.00001), so always derive the tick size from
            # ``displayPrecision``.
            tick_size = 10 ** (-display_precision)
            margin_rate = float(details.get("marginRate", 0.05))
            instrument_type = details.get("type", "CURRENCY")

            LOGGER.info(
                "OANDA_CALC_CALL get_price mode=%s instrument=%s",
                account_mode,
                instrument,
            )
            price = get_price(instrument, account_mode)
            price_reference = entry_price_value if entry_price_value is not None else price

            if risk_mode == "percent":
                risk_pct = float(request.form.get("risk_pct", 0))
                risk_amount = balance * risk_pct / 100.0
            else:
                risk_amount = float(request.form.get("risk_aud", 0))

            if risk_amount <= 0:
                raise ValueError("Risk amount must be positive")
            quote_currency = instrument.split("_")[1]
            risk_amount_quote = risk_amount
            conversion_rate = 1.0
            conversion_inverse = False
            if quote_currency != account_currency:
                pair = f"{account_currency}_{quote_currency}"
                try:
                    LOGGER.info(
                        "OANDA_CALC_CALL get_price mode=%s instrument=%s",
                        account_mode,
                        pair,
                    )
                    conversion_rate = get_price(pair, account_mode)
                    risk_amount_quote = risk_amount * conversion_rate
                except Exception:
                    pair = f"{quote_currency}_{account_currency}"
                    LOGGER.info(
                        "OANDA_CALC_CALL get_price mode=%s instrument=%s",
                        account_mode,
                        pair,
                    )
                    conversion_rate = get_price(pair, account_mode)
                    risk_amount_quote = risk_amount / conversion_rate
                    conversion_inverse = True

            treated_as_cfd = instrument_type in ("CFD", "METAL")

            if treated_as_cfd:
                # For CFDs and metals each unit's value per tick is fixed and
                # does not depend on the current price. Metals like XAG_USD
                # behave the same way as index CFDs here.
                units = risk_amount_quote / (stop_ticks * tick_size)
            else:
                # Currency pairs require multiplying by the current price to
                # convert the risk amount into the instrument's base units.
                units = risk_amount * price_reference / (stop_ticks * tick_size)

            # Different instruments allow different unit precision.
            if units_precision <= 0:
                units = int(round(units))
            else:
                units = round(units, units_precision)

            sl_distance = stop_ticks * tick_size
            if treated_as_cfd:
                risk_loss_value = units * sl_distance
                if quote_currency != account_currency:
                    if conversion_inverse:
                        risk_loss_value = risk_loss_value * conversion_rate
                    else:
                        risk_loss_value = risk_loss_value / conversion_rate
            else:
                risk_loss_value = units * sl_distance / price_reference
            max_loss = risk_amount * 1.1
            if risk_loss_value > max_loss and units > 1:
                if treated_as_cfd:
                    max_loss_quote = max_loss
                    if quote_currency != account_currency:
                        if conversion_inverse:
                            max_loss_quote = max_loss / conversion_rate
                        else:
                            max_loss_quote = max_loss * conversion_rate
                    units = max_loss_quote / sl_distance
                else:
                    units = max_loss * price_reference / sl_distance
                if units_precision <= 0:
                    units = int(units)
                else:
                    units = round(units, units_precision)
                if treated_as_cfd:
                    risk_loss_value = units * sl_distance
                    if quote_currency != account_currency:
                        if conversion_inverse:
                            risk_loss_value = risk_loss_value * conversion_rate
                        else:
                            risk_loss_value = risk_loss_value / conversion_rate
                else:
                    risk_loss_value = units * sl_distance / price_reference

            tp_distance = sl_distance * rr_ratio
            if treated_as_cfd:
                tp_value = units * tp_distance
                if quote_currency != account_currency:
                    if conversion_inverse:
                        tp_value = tp_value * conversion_rate
                    else:
                        tp_value = tp_value / conversion_rate
            else:
                tp_value = units * tp_distance / price_reference

            sl_distance_str = _format_price_distance(sl_distance, display_precision)
            tp_distance_str = _format_price_distance(tp_distance, display_precision)

            required_margin = units * price_reference * margin_rate
            if quote_currency != account_currency:
                pair = f"{account_currency}_{quote_currency}"
                try:
                    LOGGER.info(
                        "OANDA_CALC_CALL get_price mode=%s instrument=%s",
                        account_mode,
                        pair,
                    )
                    conversion_rate = get_price(pair, account_mode)
                    required_margin = required_margin / conversion_rate
                except Exception:
                    pair = f"{quote_currency}_{account_currency}"
                    LOGGER.info(
                        "OANDA_CALC_CALL get_price mode=%s instrument=%s",
                        account_mode,
                        pair,
                    )
                    conversion_rate = get_price(pair, account_mode)
                    required_margin = required_margin * conversion_rate
            if required_margin > margin_available:
                raise ValueError(
                    f"Not enough margin. Required {required_margin:.2f} {account_currency}, "
                    f"available {margin_available:.2f} {account_currency}"
                )

            if side.lower() == "buy":
                sl_price = price_reference - sl_distance
                tp_price = price_reference + tp_distance
                sl_price_str = f"{sl_price:.{display_precision}f}"
                tp_price_str = f"{tp_price:.{display_precision}f}"
                alert_qty = round(units, units_precision)
                alert = {
                    "script_name": "oanda-calculator-clone",
                    "account": account_mode,
                    "symbol": instrument,
                    "action": side,
                    "quantity": alert_qty,
                    "order_type": order_type,
                    "take_profit_price": f"{{{{close}}}} + {tp_distance_str}",
                    "stop_loss_price": f"{{{{close}}}} - {sl_distance_str}",
                    "take_profit_price_value": tp_price_str,
                    "stop_loss_price_value": sl_price_str,
                }
            else:
                sl_price = price_reference + sl_distance
                tp_price = price_reference - tp_distance
                sl_price_str = f"{sl_price:.{display_precision}f}"
                tp_price_str = f"{tp_price:.{display_precision}f}"
                alert_qty = round(units, units_precision)
                alert = {
                    "script_name": "oanda-calculator-clone",
                    "account": account_mode,
                    "symbol": instrument,
                    "action": side,
                    "quantity": alert_qty,
                    "order_type": order_type,
                    "take_profit_price": f"{{{{close}}}} - {tp_distance_str}",
                    "stop_loss_price": f"{{{{close}}}} + {sl_distance_str}",
                    "take_profit_price_value": tp_price_str,
                    "stop_loss_price_value": sl_price_str,
                }

            if entry_price_value is not None:
                alert["entry_price"] = entry_price_value
            if cancel_offset_value is not None:
                alert["limit_cancel_offset"] = cancel_offset_value
            if cancel_offset_pct_value is not None:
                alert["limit_cancel_offset_pct"] = cancel_offset_pct_value
            order = build_order(
                instrument,
                side,
                units,
                sl_price,
                tp_price,
                units_precision,
                order_type=order_type,
                entry_price=entry_price_value,
                price_precision=display_precision,
            )
            result = json.dumps(order, indent=2)
            alert_json = json.dumps(alert, indent=2)
            risk_info = {
                "Instrument": instrument,
                "Side": side,
                "Order Type": order_type.capitalize(),
                "Price": f"{price_reference:.{display_precision}f}",
                "Units": str(units),
                f"Specified Risk {account_currency}": f"{risk_amount:.2f}",
                f"Actual Risk {account_currency}": f"{risk_loss_value:.2f}",
                "Stop Loss Price": f"{sl_price:.{display_precision}f}",
                "Take Profit Price": f"{tp_price:.{display_precision}f}",
                f"Potential Profit {account_currency}": f"{tp_value:.2f}",
                f"Required Margin {account_currency}": f"{required_margin:.2f}",
                f"Margin Available {account_currency}": f"{margin_available:.2f}",
            }
            if entry_price_value is not None:
                risk_info["Entry Price"] = f"{entry_price_value:.{display_precision}f}"
            if cancel_offset_value is not None:
                risk_info["Limit cancel offset"] = cancel_offset_value
            if cancel_offset_pct_value is not None:
                risk_info["Limit cancel offset %"] = cancel_offset_pct_value

            # Optionally register this as a pending webhook so it appears in the dashboard.
            if str(track_pending).lower() == "yes":
                safe_symbol = "".join(
                    ch for ch in instrument if ch.isalnum() or ch in "_-"
                )
                safe_side = "".join(ch for ch in side if ch.isalnum() or ch in "_-")
                safe_ot = "".join(
                    ch for ch in order_type if ch.isalnum() or ch in "_-"
                )
                webhook_id = (
                    f"calc_oanda_{account_mode}_{safe_symbol}_{safe_side}_{safe_ot}"
                )

                pending_item = {
                    "id": webhook_id,
                    "broker": "WEBHOOK",
                    "account": account_mode,
                    "category": "OANDA",
                    "instrument": instrument,
                    "type": "webhook",
                    "side": side,
                    "size": str(alert_qty),
                    "entry_price": (
                        f"{entry_price_value:.{display_precision}f}"
                        if entry_price_value is not None
                        else None
                    ),
                    "order_price": (
                        f"{entry_price_value:.{display_precision}f}"
                        if entry_price_value is not None
                        else None
                    ),
                    "current_price": f"{price:.{display_precision}f}",
                    "stop_loss": sl_price_str,
                    "take_profit": tp_price_str,
                    "leverage": None,
                    "opened_at": int(time.time()),
                    "status": "WAITING",
                    "enabled": True,
                    "source": "oanda-calculator-clone",
                    "limit_cancel_offset": cancel_offset_value,
                    "limit_cancel_offset_pct": cancel_offset_pct_value,
                }

                try:
                    resp = requests.post(
                        f"{PUBLIC_BASE_URL.rstrip('/')}/api/pending-webhooks",
                        json=pending_item,
                        timeout=10,
                    )
                    resp.raise_for_status()
                    saved = (resp.json() or {}).get("item") or {}
                    pending_id = saved.get("id", webhook_id)
                    alert["pending_webhook_id"] = pending_id
                    risk_info["Pending webhook id"] = pending_id
                except Exception as exc:
                    risk_info["Pending webhook error"] = str(exc)

            # Store the trade details so they can be downloaded later.
            last_trade_specs = {
                "order": order,
                "risk_info": risk_info,
                "alert": alert,
            }
        except Exception as exc:
            error = str(exc)

    return render_template_string(
        FORM_HTML,
        result=result,
        error=error,
        instrument=instrument,
        instrument_input=instrument_input,
        alert_json=alert_json,
        risk_info=risk_info,
        webhook_url=webhook_url,
        download_url=download_url,
        app_root=app_root,
        account_mode=account_mode,
        side=side,
        order_type=order_type,
        risk_mode=risk_mode,
        stop_ticks=stop_ticks,
        risk_pct=risk_pct,
        risk_aud=risk_aud,
        rr_ratio=rr_ratio,
        entry_price=entry_price,
        limit_cancel_offset=limit_cancel_offset,
        limit_cancel_offset_pct=limit_cancel_offset_pct,
        track_pending=track_pending,
        embedded=embedded,
        page_title=page_title,
        form_action=form_action,
    )


@app.route("/download_specs")
def download_specs():
    """Return a JSON file with the most recently calculated trade."""
    if not last_trade_specs:
        payload = {"error": "No trade calculated yet"}
    else:
        payload = last_trade_specs
    resp = make_response(json.dumps(payload, indent=2))
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = "attachment; filename=trade_specs.json"
    return resp


@app.post("/execute_now")
def execute_now():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "detail": "Missing JSON payload."}), 400
    if "script_name" not in payload:
        payload["script_name"] = "oanda-calculator-clone"
    try:
        response = requests.post(PUBLIC_WEBHOOK_URL, json=payload, timeout=15)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}
        return jsonify({"status": "ok", "response": data})
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"status": "error", "detail": str(exc)}), 400


def _serve_wsgi(app: Flask, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Serve *app* with a production-grade WSGI HTTP server."""

    try:
        from waitress import serve
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError(
            "Running oanda_calculator_web.py now requires the 'waitress' package. "
            "Install it with 'pip install waitress'."
        ) from exc

    serve(app, host=host, port=port)


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("OANDA_CALCULATOR_PORT") or os.getenv("PORT", "5000"))
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))
    url = f"http://{host}:{port}/"
    if sys.stdout.isatty() and not is_render:
        try:
            _open_edge(url)
        except Exception:
            print(f"Open {url} in your browser to view the calculator.", flush=True)
    print(f"Serving oanda-calculator on {url}", flush=True)
    _serve_wsgi(app, host=host, port=port)
