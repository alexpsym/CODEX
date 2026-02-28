"""Flask web front-end for the crypto trade calculator."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Dict, Optional

import requests
from flask import Flask, jsonify, render_template_string, request, Response

from bybit_credentials import resolve_bybit_credentials_for
from cryptocalculator import (
    DEFAULT_EXECUTION_EXCHANGE,
    DEFAULT_PRICE_SOURCE,
    EXECUTION_EXCHANGES,
    PRICE_SOURCES,
    TRADE_MODE_LABELS,
    build_webhook_payload,
    calculate_trade,
    format_trade,
    get_balance_fetcher,
    get_execution_requirements,
    BYBIT_INSTRUMENT_INFO_LINEAR,
    BYBIT_INSTRUMENT_INFO_SPOT,
    BYBIT_LINEAR_URL,
    BYBIT_SPOT_URL,
)
import options_trader

app = Flask(__name__)

_BYBIT_SYMBOL_CACHE_TTL_SECONDS = int(
    os.getenv("BYBIT_SYMBOL_CACHE_TTL_SECONDS", "3600")
)
_BYBIT_SYMBOL_CACHE: Dict[str, Dict[str, object]] = {
    "linear": {"ts": 0.0, "symbols": []},
    "spot": {"ts": 0.0, "symbols": []},
}


def _norm_symbol(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (value or "")).upper()


def _bybit_category_for_price_source(price_source: str) -> str:
    meta = PRICE_SOURCES.get(price_source) or {}
    trade_mode = str(meta.get("trade_mode") or "").lower()
    return "spot" if trade_mode == "spot" else "linear"


def _fetch_bybit_symbols(category: str) -> list[str]:
    url = (
        BYBIT_INSTRUMENT_INFO_SPOT
        if category == "spot"
        else BYBIT_INSTRUMENT_INFO_LINEAR
    )
    symbols: list[str] = []
    cursor: Optional[str] = None
    for _ in range(10):
        params: Dict[str, object] = {"limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json() or {}
        result = payload.get("result") or {}
        items = result.get("list") or []
        if isinstance(items, list):
            for inst in items:
                if not isinstance(inst, dict):
                    continue
                sym = inst.get("symbol")
                if sym:
                    symbols.append(str(sym).upper())
        cursor = result.get("nextPageCursor")
        if not cursor:
            break
    return sorted(set(symbols))


def _get_bybit_symbols_cached(category: str) -> list[str]:
    category = "spot" if category == "spot" else "linear"
    now = time.time()
    entry = _BYBIT_SYMBOL_CACHE.get(category)
    if entry:
        ts = float(entry.get("ts") or 0.0)
        cached = entry.get("symbols")
        if isinstance(cached, list) and (now - ts) <= _BYBIT_SYMBOL_CACHE_TTL_SECONDS:
            return cached

    try:
        symbols = _fetch_bybit_symbols(category)
    except Exception:
        if entry and isinstance(entry.get("symbols"), list):
            return entry["symbols"]  # type: ignore[return-value]
        return []

    _BYBIT_SYMBOL_CACHE[category] = {"ts": now, "symbols": symbols}
    return symbols


def _resolve_crypto_symbol(raw: str, price_source: str) -> Optional[Dict[str, object]]:
    want = _norm_symbol(raw)
    if not want:
        return None

    meta = PRICE_SOURCES.get(price_source) or {}
    exchange = str(meta.get("exchange") or "").lower()
    category = _bybit_category_for_price_source(price_source)

    if exchange == "bybit":
        symbols = _get_bybit_symbols_cached(category)
        symbol_set = set(symbols)

        if want in symbol_set:
            return {
                "input": raw,
                "normalized": want,
                "resolved_symbol": want,
                "source": "bybit",
            }

        preferred_quotes = (
            ["USDT", "USDC", "USD"]
            if category != "spot"
            else ["USDT", "USDC", "USD", "BTC", "ETH"]
        )
        for quote in preferred_quotes:
            cand = want + quote
            if cand in symbol_set:
                return {
                    "input": raw,
                    "normalized": want,
                    "resolved_symbol": cand,
                    "source": "bybit",
                }

        starts = [s for s in symbols if s.startswith(want)]
        if starts:
            starts.sort(key=lambda s: (0 if s.endswith("USDT") else 1, len(s), s))
            return {
                "input": raw,
                "normalized": want,
                "resolved_symbol": starts[0],
                "source": "bybit",
                "candidates": starts[:10],
            }

        contains = [s for s in symbols if want in s]
        if contains:
            contains.sort(key=lambda s: (0 if s.endswith("USDT") else 1, len(s), s))
            return {
                "input": raw,
                "normalized": want,
                "resolved_symbol": contains[0],
                "source": "bybit",
                "candidates": contains[:10],
            }
        return None

    if exchange == "coinspot":
        if want.endswith(("USDT", "AUD", "USD", "BTC", "ETH")) and len(want) > 3:
            return {
                "input": raw,
                "normalized": want,
                "resolved_symbol": want,
                "source": "coinspot",
            }
        return {
            "input": raw,
            "normalized": want,
            "resolved_symbol": want + "USDT",
            "source": "coinspot",
        }

    return {"input": raw, "normalized": want, "resolved_symbol": want}


@app.get("/symbols/bybit")
def bybit_symbols_placeholder():
    return jsonify({"symbols": [], "detail": "Symbol lookup is disabled."})


@app.get("/api/resolve-symbol")
def resolve_symbol_api():
    raw = request.args.get("symbol", "")
    price_source = (
        request.args.get("price_source", DEFAULT_PRICE_SOURCE).strip().lower()
        or DEFAULT_PRICE_SOURCE
    )
    if price_source not in PRICE_SOURCES:
        price_source = DEFAULT_PRICE_SOURCE

    resolved = _resolve_crypto_symbol(raw, price_source)
    if not resolved or not resolved.get("resolved_symbol"):
        return (
            jsonify(
                {
                    "detail": f"No match for '{raw}'",
                    "input": raw,
                    "price_source": price_source,
                }
            ),
            404,
        )
    resolved["price_source"] = price_source
    return jsonify(resolved)

PRICE_MODE_NOTES = {
    key: (
        "Spot mode uses spot pricing and fees with no funding component."
        if meta["trade_mode"] == "spot"
        else "Linear mode uses perpetual contract pricing, funding and fee settings."
    )
    for key, meta in PRICE_SOURCES.items()
}

BALANCE_ADAPTERS = {name: get_balance_fetcher(name) for name in EXECUTION_EXCHANGES}
PUBLIC_WEBHOOK_URL = os.getenv(
    "PUBLIC_WEBHOOK_URL", "https://codex-rdqh.onrender.com/webhook"
)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL") or PUBLIC_WEBHOOK_URL.rsplit("/", 1)[0]
BUILD_TIMESTAMP = os.getenv("DEPLOY_TIMESTAMP") or time.strftime(
    "%Y-%m-%d %H:%M:%S %Z"
)


def _get_git_sha() -> str:
    env_sha = os.getenv("GIT_SHA") or os.getenv("RENDER_GIT_COMMIT")
    if env_sha:
        return env_sha[:7]
    try:
        repo_root = Path(__file__).resolve().parent.parent
        output = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return output.decode("utf-8").strip() or "unknown"


BUILD_SHA = _get_git_sha()
DEFAULT_CRYPTO_QTY_STEP = 0.01
DEFAULT_CRYPTO_SYMBOL = "BTCUSDT"
HARD_CODED_OPTIONS_MIN_QTY = {
    "BTC": 0.01,
    "ETH": 0.1,
    "SOL": 1,
    "XRP": 10,
    "MNT": 10,
    "DOGE": 100,
}


def _fetch_master_balance(
    account_mode: str, coin: str = "USDT", account_type: str = "UNIFIED"
) -> float:
    url = f"{PUBLIC_BASE_URL.rstrip('/')}/api/bybit/balance"
    resp = requests.get(
        url,
        params={"account": account_mode, "coin": coin, "account_type": account_type},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    return float(payload["balance"])

FORM_HTML = """
<!doctype html>
<html>
<head>
  <title>Crypto Position Size Calculator</title>
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
    .qty-row {display:flex; align-items:center; gap:8px; flex-wrap:wrap;}
    .min-note {font-size:14px; color:#e2e8f0;}
    .symbol-row {display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:4px 0 10px;}
    .symbol-row input {min-width: 180px;}
    .specs-panel {margin-top: 12px; max-width:980px;}
    .table-wrap { overflow:auto; max-height:70vh; border-radius: 12px; border: 1px solid #1f2937; background: #0b1220; }
    .specs-table { width: 100%; border-collapse: collapse; min-width: 480px; }
    .specs-table th, .specs-table td { text-align:left; padding:0.55rem 0.65rem; border-bottom:1px solid #1f2937; font-size:0.9rem; vertical-align:top; }
    .specs-table td { white-space: normal; word-break: break-word; }
    .specs-table th { background:#0f172a; color:#cbd5e1; position:sticky; top:0; z-index:1; }
    .specs-table tbody tr:nth-child(odd) { background:#0b1220; }
    .specs-table tbody tr:nth-child(even) { background:#0c1526; }
    .specs-table tbody tr:hover { background:#111c33; }
    .specs-table td:first-child { color:#cbd5e1; width: 46%; }
    .specs-table td:nth-child(2) { text-align:right; font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; color:#f8fafc; }
    .specs-table th:nth-child(2) { text-align:right; }
    .specs-section-row td { background:#0f172a !important; color:#e2e8f0; font-weight:800; text-transform:uppercase; letter-spacing:0.06em; font-size:0.78rem; border-bottom:1px solid #1f2937; }
    .specs-summary { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin:10px 0 12px; }
    .specs-card { background:#0f172a; border:1px solid #1f2937; border-radius:12px; padding:10px; }
    .specs-card .label { color:#94a3b8; font-size:12px; margin-bottom:6px; }
    .specs-card .value { color:#f8fafc; font-weight:800; font-size:16px; font-variant-numeric: tabular-nums; }
    .specs-card .sub { color:#9ca3af; font-size:12px; margin-top:4px; }
  </style>
</head>
<body data-app-root="{{ app_root }}">
  <h1>Crypto Position Size Calculator</h1>
  <div id="js_data"
       data-price-mode-notes='{{ price_mode_notes|tojson|e }}'
       data-options-min-qty-map='{{ options_min_qty_map|tojson|e }}'
       style="display:none;"></div>
  <div class="container">
    <div class="form">
      <form method="post">
        <label>Trade Type:</label>
        <div class="button-group" data-input="trade_type">
          <button type="button" data-value="perpetual">Perpetual Futures</button>
          <button type="button" data-value="spot">Spot</button>
          <button type="button" data-value="options">Options</button>
          <button type="button" data-value="trendline_options">Trendline Options</button>
        </div>
        <input type="hidden" name="trade_type" id="trade_type" value="{{ trade_type }}">
        <label>Account:</label>
        <div class="button-group" data-input="account_mode">
          <button type="button" data-value="live">Live</button>
          <button type="button" data-value="demo">Demo</button>
        </div>
        <input type="hidden" name="account_mode" id="account_mode" value="{{ account_mode }}">
        <div id="crypto_section" class="trade-section">
          <label>Symbol:</label>
          <div class="symbol-row">
            <input name="symbol" id="symbol" value="{{ symbol }}" autocomplete="off">
            <button type="button" id="symbol_specs_btn">Specs</button>
            <span class="copy-status" id="symbol_status"></span>
          </div>
          <label>Price Source:</label>
          <div class="button-group" data-input="price_source">
            {% for key, meta in price_source_options %}
            <button type="button" data-value="{{ key }}">{{ meta['label'] }}</button>
            {% endfor %}
          </div>
          <input type="hidden" name="price_source" id="price_source" value="{{ price_source }}">
          <label>Execution Exchange:</label>
          <div class="button-group" data-input="execution_exchange">
            {% for key, meta in execution_options %}
            <button type="button" data-value="{{ key }}">{{ meta['label'] }}</button>
            {% endfor %}
          </div>
          <input type="hidden" name="execution_exchange" id="execution_exchange" value="{{ execution_exchange }}">
          <p id="price_mode_note"></p>
          <label>Direction:</label>
          <div class="button-group" data-input="direction">
            <button type="button" data-value="long">Long</button>
            <button type="button" data-value="short">Short</button>
          </div>
          <input type="hidden" name="direction" id="direction" value="{{ direction }}">
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
          <div id="entry_price_row">
            <label>Entry Price: <input name="entry_price" type="number" step="0.0001"></label><br>
          </div>
          <div id="limit_cancel_row">
            <label>Limit cancel offset (abs): <input name="limit_cancel_offset" type="number" step="0.0001" value="{{ limit_cancel_offset or '' }}"></label><br>
            <label>Limit cancel offset (%): <input name="limit_cancel_offset_pct" type="number" step="0.01" value="{{ limit_cancel_offset_pct or '' }}"></label><br>
            <small>Cancel pending limit orders if price moves away by the given distance.</small><br>
          </div>
          <label>Stop loss ticks: <input name="stop_loss_ticks" id="stop_loss_ticks" type="number" step="1"></label><br>
          <label>Risk %: <input name="risk_percent" id="risk_percent" type="number" step="0.01"></label><br>
          <label>Risk–reward ratio: <input name="rr_ratio" id="rr_ratio" type="number" step="0.1" value="2"></label><br>
          <label>Price → Execution rate:
            <input name="price_to_execution_rate" id="price_to_execution_rate" type="number" step="0.0001" min="0" value="{{ price_to_execution_rate }}" placeholder="e.g. 1.55">
          </label><br>
          <small>Use this when your price source is quoted in a different currency than your execution exchange.</small><br>
        </div>
        <div id="options_section" class="trade-section hidden">
          <div id="options_order_type_row">
            <label>Order Type:</label>
            <div class="button-group" data-input="options_order_type">
              <button type="button" data-value="market">Market</button>
              <button type="button" data-value="limit">Limit</button>
            </div>
            <input type="hidden" name="options_order_type" id="options_order_type" value="{{ options_order_type }}">
          </div>
          <label>Base:</label>
          <div class="button-group" data-input="options_base">
            {% for base in options_base_options %}
            <button type="button" data-value="{{ base }}">{{ base }}</button>
            {% endfor %}
          </div>
          <input type="hidden" name="options_base" id="options_base" value="{{ options_base }}">
          <label>Call/Put:</label>
          <div class="button-group" data-input="options_type">
            <button type="button" data-value="Call">Call</button>
            <button type="button" data-value="Put">Put</button>
          </div>
          <input type="hidden" name="options_type" id="options_type" value="{{ options_type }}">
          <label>Expiry (D/M/YY): <input name="options_expiry" id="options_expiry"></label><br>
          <label>Quote: USDT</label><br>
          <label>Side:</label>
          <div class="button-group" data-input="options_side">
            <button type="button" data-value="Buy">Buy</button>
            <button type="button" data-value="Sell">Sell</button>
          </div>
          <input type="hidden" name="options_side" id="options_side" value="{{ options_side }}">
          <div id="options_manual_fields">
            <label>Strike: <input name="options_strike" id="options_strike"></label><br>
            <div class="qty-row">
              <label for="options_quantity">Quantity:</label>
              <input name="options_quantity" id="options_quantity" value="{{ options_quantity }}">
              <span id="options_min_qty_note" class="min-note"></span>
            </div>
          </div>
          <div id="options_trendline_fields" class="hidden">
            <label>Risk (USDT, incl fees):
              <input name="options_risk_usdt" id="options_risk_usdt" type="number" step="0.01"
                     value="{{ options_risk_usdt or 5 }}">
            </label><br>
            <label>Max overage (USDT):
              <input name="options_risk_tolerance" id="options_risk_tolerance" type="number" step="0.01"
                     value="{{ options_risk_tolerance or 0.5 }}">
            </label><br>
            <label>Fee mode:
              <select name="options_fee_mode" id="options_fee_mode">
                <option value="roundtrip" {% if options_fee_mode != "open" %}selected{% endif %}>roundtrip</option>
                <option value="open" {% if options_fee_mode == "open" %}selected{% endif %}>open</option>
              </select>
            </label><br>
          </div>
          <div id="options_limit_price_row">
            <label>Limit Price: <input name="options_limit_price"></label><br>
          </div>
          <label>Risk %: <input name="options_risk_percent" value="0"></label><br>
          <label>TP Multiplier: <input name="options_tp_multiplier" value="3"></label><br>
          <div class="copy-row">
            <button type="submit" name="options_action" value="journal">Journal last 30 days</button>
            <button type="submit" name="options_action" value="journal_csv">Download journal CSV</button>
            <button type="submit" name="options_action" value="open_orders">Show open orders</button>
            <button type="submit" name="options_action" value="open_positions">Show open positions</button>
          </div>
        </div>
        <button type="submit" name="options_action" value="calculate">Calculate</button>
      </form>
      <h3>TradingView Webhook</h3>
      <div class="copy-row">
        <button type="button" onclick="copyText('{{ webhook_url }}','webhook_status')">Copy Webhook URL</button>
        <span class="copy-status" id="webhook_status"></span>
      </div>
      <div class="copy-box" id="webhook_url">{{ webhook_url }}</div>
    </div>
    <div class="result">
      {% if error %}<p style="color: red;">{{ error }}</p>{% endif %}
      {% if payload_json %}
        <h2>Result</h2>
        <pre id="alert_json">{{ payload_json }}</pre>
        <pre id="tv_payload" style="display:none;">{{ payload_json }}</pre>
        {% if export_json %}
        <pre id="export_json" style="display:none;">{{ export_json }}</pre>
        {% endif %}
        <div class="copy-row">
          <button type="button" onclick="copyFromElement('alert_json','payload_status')">Copy TradingView Message</button>
          <span class="copy-status" id="payload_status"></span>
        </div>
        <div class="copy-row">
          <button type="button" onclick="exportResult()">Export Result</button>
        </div>
        <!-- metadata from the last calculation (prevents toggling without recalculating) -->
        <div id="calc_meta"
             data-trade-type="{{ trade_type }}"
             data-order-type="{{ order_type }}"
             data-options-order-type="{{ options_order_type }}"
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
      {% if options_output %}
        <h2>Options Output</h2>
        <pre id="options_output">{{ options_output }}</pre>
      {% endif %}
      {% if summary %}
        <h2>Summary</h2>
        <pre id="summary_text">{{ summary }}</pre>
      {% endif %}
      {% if selection_info %}
        <h2>Execution Settings</h2>
        <table border="1">
          <tr><th>Execution Exchange</th><td>{{ selection_info.execution_label }}</td></tr>
          <tr><th>Price Source</th><td>{{ selection_info.price_label }}</td></tr>
          <tr><th>Trade Mode</th><td>{{ selection_info.trade_mode_label }}</td></tr>
          <tr><th>Account</th><td>{{ selection_info.account_mode|capitalize }}</td></tr>
        </table>
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
      <div id="embedded_specs" class="trade-section specs-panel" style="display:none;">
        <h2>Instrument Specs</h2>
        <div class="copy-status" id="embedded_specs_status"></div>
        <div id="embedded_specs_summary" class="specs-summary"></div>
        <div class="table-wrap">
          <table class="specs-table">
            <thead><tr><th>Field</th><th>Value</th></tr></thead>
            <tbody id="embedded_specs_rows"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
  <footer>
  </footer>
  <script src="{{ app_root }}/static/cryptocalculator.js"></script>
</body>
</html>
"""

_options_trader_instances: Dict[str, options_trader.BybitOptionsTrader] = {}


def _get_options_trader(account_mode: str) -> Optional[options_trader.BybitOptionsTrader]:
    options_trader.configure_trading_environment(interactive=False)
    account_key = "demo" if account_mode == "demo" else "live"
    if account_key in _options_trader_instances:
        return _options_trader_instances[account_key]
    _mode, api_key, api_secret, base_url, _key_source = resolve_bybit_credentials_for(
        account_key
    )
    if api_key and api_secret:
        _options_trader_instances[account_key] = options_trader.BybitOptionsTrader(
            api_key, api_secret, base_url
        )
    return _options_trader_instances.get(account_key)


@app.get("/options/min-qty")
def options_min_qty():
    base = request.args.get("base", "").strip().upper()
    strike = request.args.get("strike", "").strip()
    option_type = request.args.get("option_type", "").strip()
    expiry = request.args.get("expiry", "").strip()
    if not base:
        return jsonify({"min_qty": options_trader.MIN_ORDER_QTY})
    try:
        symbol = options_trader.build_option_symbol(
            base, strike, option_type, expiry, "USDT"
        )
        min_qty = options_trader.get_min_order_qty(symbol)
    except Exception:  # pylint: disable=broad-except
        min_qty = options_trader.MIN_ORDER_QTY
    return jsonify({"min_qty": min_qty})


@app.get("/options/min-qty-base")
def options_min_qty_base():
    base = request.args.get("base", "").strip().upper()
    quote = request.args.get("quote", "USDT").strip().upper() or "USDT"
    min_qty = HARD_CODED_OPTIONS_MIN_QTY.get(base)

    if not base:
        return jsonify(
            {
                "base": base,
                "quote": quote,
                "min_qty": None,
                "stale": False,
                "source": "static",
                "error": "missing base",
            }
        )

    return jsonify(
        {
            "base": base,
            "quote": quote,
            "min_qty": min_qty,
            "stale": False,
            "source": "static",
        }
    )


@app.get("/min-qty")
def crypto_min_qty():
    symbol = request.args.get("symbol", "").strip().upper()
    execution_exchange = request.args.get(
        "execution_exchange", DEFAULT_EXECUTION_EXCHANGE
    ).lower()
    price_source = request.args.get("price_source", DEFAULT_PRICE_SOURCE).lower()
    if execution_exchange not in EXECUTION_EXCHANGES:
        execution_exchange = DEFAULT_EXECUTION_EXCHANGE
    if price_source not in PRICE_SOURCES:
        price_source = DEFAULT_PRICE_SOURCE
    trade_mode = PRICE_SOURCES[price_source]["trade_mode"]
    if not symbol:
        return jsonify({"min_qty": DEFAULT_CRYPTO_QTY_STEP})
    try:
        min_qty, _qty_step, _fee_rate = get_execution_requirements(
            execution_exchange,
            symbol,
            trade_mode,
            {"execution_exchange": execution_exchange, "trade_mode": trade_mode},
        )
    except Exception:  # pylint: disable=broad-except
        min_qty = DEFAULT_CRYPTO_QTY_STEP
    return jsonify({"min_qty": min_qty})


@app.route("/", methods=["GET", "POST"])
def index():
    summary = None
    error = None
    risk_info = None
    payload_json = None
    export_json = None
    trade = None
    options_output = None

    trade_type = request.form.get("trade_type", "perpetual").strip().lower()
    if trade_type not in {"perpetual", "spot", "options", "trendline_options"}:
        trade_type = "perpetual"

    direction = request.form.get("direction", "long").strip().lower()
    if direction not in {"long", "short"}:
        direction = "long"

    order_type = request.form.get("order_type", "market").strip().lower()
    if order_type not in {"market", "limit"}:
        order_type = "market"

    options_order_type = request.form.get("options_order_type", "market").strip().lower()
    if options_order_type not in {"market", "limit"}:
        options_order_type = "market"
    if trade_type == "trendline_options":
        options_order_type = "market"

    options_base_options = options_trader.get_supported_option_bases_cached()
    options_base_set = {
        base.strip().upper()
        for base in options_base_options
        if base and base.strip()
    }
    options_base_set.update(options_trader.DEFAULT_OPTION_BASES)
    options_base_options = sorted(options_base_set)
    app.logger.info("Options base options: %s", options_base_options)

    options_base = request.form.get("options_base", "BTC").strip().upper()
    if options_base not in options_base_options:
        options_base = options_base_options[0]

    options_type = request.form.get("options_type", "Call").strip().capitalize()
    if options_type not in {"Call", "Put"}:
        options_type = "Call"

    options_side = request.form.get("options_side", "Buy").strip().capitalize()
    if options_side not in {"Buy", "Sell"}:
        options_side = "Buy"

    symbol = request.form.get("symbol", "").strip().upper()
    if not symbol:
        symbol = DEFAULT_CRYPTO_SYMBOL

    execution_exchange = request.form.get(
        "execution_exchange", DEFAULT_EXECUTION_EXCHANGE
    ).lower()
    if execution_exchange not in EXECUTION_EXCHANGES:
        execution_exchange = DEFAULT_EXECUTION_EXCHANGE

    price_source = request.form.get("price_source", DEFAULT_PRICE_SOURCE).lower()
    if price_source not in PRICE_SOURCES:
        price_source = DEFAULT_PRICE_SOURCE

    if symbol and not any(
        symbol.endswith(sfx) for sfx in ("USDT", "USDC", "USD", "AUD", "BTC", "ETH")
    ):
        resolved = _resolve_crypto_symbol(symbol, price_source)
        if resolved and resolved.get("resolved_symbol"):
            symbol = str(resolved["resolved_symbol"]).upper()

    trade_mode = PRICE_SOURCES[price_source]["trade_mode"]
    account_mode = request.form.get("account_mode", "live").strip().lower()
    if account_mode not in {"live", "demo"}:
        account_mode = "live"
    price_to_execution_rate = request.form.get("price_to_execution_rate", "").strip()
    quantity = request.form.get("quantity", "")
    track_pending = request.form.get("track_pending", "no")
    limit_cancel_offset_raw = request.form.get("limit_cancel_offset", "").strip()
    limit_cancel_offset_pct_raw = request.form.get("limit_cancel_offset_pct", "").strip()

    if request.method == "POST":
        try:
            if trade_type in {"options", "trendline_options"}:
                options_action = request.form.get("options_action", "calculate")
                trader = _get_options_trader(account_mode)
                symbol_filter = None
                if (
                    request.form.get("options_strike")
                    and request.form.get("options_expiry")
                    and options_type
                ):
                    symbol_filter = options_trader.build_option_symbol(
                        options_base,
                        request.form.get("options_strike", ""),
                        options_type,
                        request.form.get("options_expiry", ""),
                        "USDT",
                    )
                if options_action in {
                    "journal",
                    "journal_csv",
                    "open_orders",
                    "open_positions",
                }:
                    if trader is None:
                        raise ValueError("Options credentials are not configured.")
                    if options_action == "journal":
                        options_output = options_trader.build_journal_report(
                            trader, days=30
                        )
                    elif options_action == "journal_csv":
                        csv_data = options_trader.build_journal_csv(trader, days=30)
                        return Response(
                            csv_data,
                            mimetype="text/csv",
                            headers={
                                "Content-Disposition": "attachment; filename=options_journal.csv"
                            },
                        )
                    elif options_action == "open_orders":
                        orders = trader.get_open_orders(symbol_filter)
                        options_output = options_trader.format_open_orders(orders)
                    elif options_action == "open_positions":
                        positions = trader.get_positions(symbol_filter)
                        options_output = options_trader.format_open_positions(positions)
                else:
                    if trade_type == "trendline_options":
                        if options_side.lower() != "buy":
                            raise ValueError(
                                "Trendline Options mode only supports Buy (risk is capped by premium)."
                            )
                        risk_usdt = float(
                            request.form.get("options_risk_usdt", 0) or 0
                        )
                        tol_usdt = float(
                            request.form.get("options_risk_tolerance", 0.5) or 0.5
                        )
                        fee_mode = (
                            request.form.get("options_fee_mode") or "roundtrip"
                        ).strip().lower()
                        if risk_usdt <= 0:
                            raise ValueError("Risk (USDT) must be > 0.")
                        payload = {
                            "account": account_mode,
                            "trade_mode": "options",
                            "options_mode": "trendline",
                            "action": "buy",
                            "order_type": "market",
                            "base_coin": options_base,
                            "option_type": options_type,
                            "expiry": request.form.get("options_expiry", ""),
                            "risk_usdt": round(risk_usdt, 4),
                            "risk_tolerance_usdt": round(tol_usdt, 4),
                            "fee_mode": fee_mode,
                            "tp_multiplier": float(
                                request.form.get("options_tp_multiplier", 3) or 3
                            ),
                        }
                        payload_json = json.dumps(payload, indent=2)
                        options_output = "\n".join(
                            [
                                "Trendline Options mode:",
                                f"Base: {options_base}",
                                f"Type: {options_type}",
                                f"Expiry: {payload['expiry']}",
                                f"Risk (USDT): {risk_usdt} (+{tol_usdt} tolerance)",
                                f"Order type: {options_order_type}",
                                f"Fee mode: {fee_mode}",
                            ]
                        )
                    else:
                        balance = options_trader.DEMO_BALANCE
                        if trader is not None:
                            api_bal = trader.get_wallet_balance()
                            if api_bal > 0:
                                balance = api_bal
                        risk_percent = float(
                            request.form.get("options_risk_percent", 0) or 0
                        )
                        risk_usd = balance * risk_percent / 100
                        qty = float(request.form.get("options_quantity", 0) or 0)
                        symbol = options_trader.build_option_symbol(
                            options_base,
                            request.form.get("options_strike", ""),
                            options_type,
                            request.form.get("options_expiry", ""),
                            "USDT",
                        )
                        tick = options_trader.fetch_option_ticker(
                            symbol, base_url=trader.base_url if trader else None
                        )
                        mark_price = float(tick.get("markPrice", 0) or 0)
                        if qty <= 0 and risk_usd > 0:
                            min_qty = options_trader.get_min_order_qty(
                                symbol, base_url=trader.base_url if trader else None
                            )
                            qty = options_trader.compute_order_qty(
                                risk_usd, mark_price, min_qty
                            )
                        limit_price = None
                        if (
                            options_order_type == "limit"
                            and request.form.get("options_limit_price")
                        ):
                            limit_price = options_trader.round_to_tick(
                                float(request.form["options_limit_price"]), symbol
                            )
                        entry_price = limit_price or mark_price
                        tp_multiplier = float(
                            request.form.get("options_tp_multiplier", 3) or 3
                        )
                        tp_offset = None
                        if entry_price and tp_multiplier and tp_multiplier > 0:
                            tp_offset = entry_price * (tp_multiplier - 1)
                        action = "buy" if options_side.lower() == "buy" else "sell"
                        if tp_offset is not None and action == "sell":
                            tp_offset = -tp_offset
                        payload = {
                            "symbol": symbol,
                            "action": action,
                            "quantity": round(qty, 3),
                            "account": account_mode,
                            "trade_mode": "options",
                            "order_type": options_order_type,
                            "price": round(entry_price, 8)
                            if options_order_type == "limit"
                            else None,
                            "tp_offset": round(tp_offset, 6)
                            if tp_offset is not None
                            else None,
                            "tp_multiplier": tp_multiplier,
                        }
                        payload_json = json.dumps(payload, indent=2)
                        options_output = "\n".join(
                            [
                                f"Symbol: {symbol}",
                                f"Side: {options_side}",
                                f"Quantity: {qty}",
                                f"Mark price: {mark_price}",
                                f"Entry price: {entry_price}",
                                f"TP multiplier: {tp_multiplier}",
                            ]
                        )
            else:
                if not symbol:
                    raise ValueError("Symbol is required for spot/perpetual trades.")
                entry_price_raw = request.form.get("entry_price")
                stop_loss_ticks_raw = request.form.get("stop_loss_ticks", "").strip()
                risk_percent_raw = request.form.get("risk_percent", "").strip()
                rr_ratio_raw = request.form.get("rr_ratio", "").strip()
                if not stop_loss_ticks_raw or not risk_percent_raw or not rr_ratio_raw:
                    raise ValueError("Stop loss ticks, risk %, and RR ratio are required.")
                stop_loss_ticks = float(stop_loss_ticks_raw)
                risk_percent = float(risk_percent_raw)
                rr_ratio = float(rr_ratio_raw)

                config: Dict[str, object] = {
                    "symbol": symbol,
                    "direction": direction,
                    "order_type": order_type,
                    "stop_loss_ticks": stop_loss_ticks,
                    "risk_percent": risk_percent,
                    "rr_ratio": rr_ratio,
                    "price_source": price_source,
                    "execution_exchange": execution_exchange,
                    "account_balance": "auto",
                    "account_mode": account_mode,
                }
                config["trade_mode"] = trade_mode
                if price_to_execution_rate:
                    config["price_to_execution_rate"] = float(price_to_execution_rate)
                if order_type == "limit" and entry_price_raw:
                    config["entry_price"] = float(entry_price_raw)

                balance_fetcher = BALANCE_ADAPTERS.get(execution_exchange)
                if balance_fetcher is None:
                    raise ValueError(
                        f"Execution exchange '{execution_exchange}' is not supported."
                    )
                account_asset = "AUD" if execution_exchange == "coinspot" else "USDT"
                if execution_exchange == "bybit":
                    config["account_balance"] = _fetch_master_balance(
                        account_mode, coin=account_asset, account_type="UNIFIED"
                    )
                else:
                    config["account_balance"] = balance_fetcher(
                        account_asset,
                        account_type="UNIFIED",
                        account_mode=account_mode,
                    )
                if execution_exchange == "coinspot":
                    config.setdefault("account_asset", "AUD")

                trade = calculate_trade(config)
                summary = format_trade(trade)
                risk_info = {k.replace("_", " ").title(): v for k, v in trade.items()}
                payload = build_webhook_payload(trade)

                limit_cancel_offset = (
                    float(limit_cancel_offset_raw) if limit_cancel_offset_raw else None
                )
                limit_cancel_offset_pct = (
                    float(limit_cancel_offset_pct_raw) if limit_cancel_offset_pct_raw else None
                )
                if limit_cancel_offset is not None:
                    payload["limit_cancel_offset"] = limit_cancel_offset
                    risk_info["Limit cancel offset"] = limit_cancel_offset
                if limit_cancel_offset_pct is not None:
                    payload["limit_cancel_offset_pct"] = limit_cancel_offset_pct
                    risk_info["Limit cancel offset %"] = limit_cancel_offset_pct

                if str(track_pending).lower() == "yes":
                    symbol_safe = "".join(
                        ch for ch in str(trade["symbol"]) if ch.isalnum() or ch in "_-"
                    )
                    dir_safe = "".join(
                        ch
                        for ch in str(trade["direction"])
                        if ch.isalnum() or ch in "_-"
                    )
                    ot_safe = "".join(
                        ch
                        for ch in str(trade["order_type"])
                        if ch.isalnum() or ch in "_-"
                    )
                    ex_safe = "".join(
                        ch
                        for ch in str(trade.get("execution_exchange", ""))
                        if ch.isalnum() or ch in "_-"
                    )
                    webhook_id = (
                        f"calc_crypto_{account_mode}_{ex_safe}_{symbol_safe}_"
                        f"{dir_safe}_{ot_safe}"
                    )

                    side = "buy" if str(trade["direction"]).lower() == "long" else "sell"

                    pending_item = {
                        "id": webhook_id,
                        "broker": "WEBHOOK",
                        "account": account_mode,
                        "category": str(
                            trade.get("execution_exchange", "BYBIT")
                        ).upper(),
                        "instrument": str(trade["symbol"]),
                        "type": "webhook",
                        "side": side,
                        "size": str(trade.get("quantity")),
                        "entry_price": trade.get("entry_price_execution"),
                        "order_price": trade.get("entry_price_execution"),
                        "current_price": trade.get("entry_price_execution"),
                        "stop_loss": trade.get("stop_price_execution"),
                        "take_profit": trade.get("target_price_execution"),
                        "leverage": None,
                        "opened_at": int(time.time()),
                        "status": "WAITING",
                        "enabled": True,
                        "source": "cryptocalculator-clone",
                        "limit_cancel_offset": limit_cancel_offset,
                        "limit_cancel_offset_pct": limit_cancel_offset_pct,
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
                        payload["pending_webhook_id"] = pending_id
                        risk_info["Pending webhook id"] = pending_id
                    except Exception as exc:
                        risk_info["Pending webhook error"] = str(exc)

                payload_json = json.dumps(payload, indent=2)

                price_source = trade.get("price_source", price_source)
                execution_exchange = trade.get(
                    "execution_exchange", execution_exchange
                )
                trade_mode = trade.get("trade_mode", trade_mode)
        except Exception as exc:  # pylint: disable=broad-except
            error = str(exc)

    selection_info: Optional[Dict[str, str]] = None
    if request.method == "POST" and not error and trade:
        selection_info = {
            "execution_label": EXECUTION_EXCHANGES[execution_exchange]["label"],
            "price_label": PRICE_SOURCES[price_source]["label"],
            "trade_mode_label": TRADE_MODE_LABELS[trade_mode],
            "account_mode": account_mode,
        }
        export_payload = {
            "summary": summary,
            "execution_settings": selection_info,
            "position_details": risk_info,
            "webhook_message": json.loads(payload_json) if payload_json else None,
            "trade": trade,
        }
        export_json = json.dumps(export_payload, indent=2)

    return render_template_string(
        FORM_HTML,
        summary=summary,
        error=error,
        risk_info=risk_info,
        payload_json=payload_json,
        selection_info=selection_info,
        execution_exchange=execution_exchange,
        price_source=price_source,
        trade_mode=trade_mode,
        account_mode=account_mode,
        price_to_execution_rate=price_to_execution_rate,
        trade_type=trade_type,
        direction=direction,
        order_type=order_type,
        symbol=symbol,
        quantity=quantity,
        options_order_type=options_order_type,
        options_base=options_base,
        options_type=options_type,
        options_side=options_side,
        options_base_options=options_base_options,
        options_quantity=request.form.get("options_quantity", "0"),
        options_risk_usdt=request.form.get("options_risk_usdt"),
        options_risk_tolerance=request.form.get("options_risk_tolerance"),
        options_fee_mode=(request.form.get("options_fee_mode") or "roundtrip")
        .strip()
        .lower(),
        options_output=options_output,
        execution_options=sorted(EXECUTION_EXCHANGES.items()),
        price_source_options=sorted(PRICE_SOURCES.items()),
        price_mode_notes=PRICE_MODE_NOTES,
        webhook_url=PUBLIC_WEBHOOK_URL,
        export_json=export_json,
        build_sha=BUILD_SHA,
        build_timestamp=BUILD_TIMESTAMP,
        app_root=(
            request.headers.get("x-forwarded-prefix", "").rstrip("/")
            or request.script_root.rstrip("/")
        ),
        options_min_qty_map=HARD_CODED_OPTIONS_MIN_QTY,
        track_pending=track_pending,
        limit_cancel_offset=limit_cancel_offset_raw,
        limit_cancel_offset_pct=limit_cancel_offset_pct_raw,
    )


@app.post("/execute_now")
def execute_now():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "detail": "Missing JSON payload."}), 400
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


EDGE_CONTROLLER_NAMES = ("microsoft-edge", "msedge")
EDGE_EXECUTABLE_NAMES = (
    "microsoft-edge",
    "microsoft-edge-stable",
    "microsoft-edge-beta",
    "microsoft-edge-dev",
    "msedge",
)
EDGE_FALLBACK_PATHS = (
    Path(r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"),
    Path(r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
)


def open_in_edge(url: str) -> bool:
    """Try to open *url* in Microsoft Edge."""

    for name in EDGE_CONTROLLER_NAMES:
        try:
            browser = webbrowser.get(name)
        except webbrowser.Error:
            continue
        browser.open(url)
        return True

    for executable in EDGE_EXECUTABLE_NAMES:
        path = shutil.which(executable)
        if not path:
            continue
        webbrowser.BackgroundBrowser(path).open(url)
        return True

    for candidate in EDGE_FALLBACK_PATHS:
        if not candidate.exists():
            continue
        webbrowser.BackgroundBrowser(str(candidate)).open(url)
        return True

    if sys.platform.startswith("win") and hasattr(os, "startfile"):
        try:
            os.startfile(f"microsoft-edge:{url}")  # type: ignore[attr-defined]
        except OSError:
            return False
        return True

    return False


def _serve_wsgi(app: Flask, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Serve *app* using Waitress, prompting to install it if missing."""

    try:
        from waitress import serve
    except ModuleNotFoundError:  # pragma: no cover - dependency error path
        app.run(host=host, port=port, threaded=True)
        return

    serve(app, host=host, port=port)


def _is_port_available(host: str, port: int) -> bool:
    if port == 0:
        return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _wait_for_server(host: str, port: int, timeout: float = 10.0) -> bool:
    connect_host = host
    if host in {"0.0.0.0", "::"}:
        connect_host = "127.0.0.1"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            try:
                sock.connect((connect_host, port))
            except OSError:
                time.sleep(0.05)
                continue
            return True
    return False


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("CRYPTOCALCULATOR_PORT") or os.getenv("PORT", "5000"))
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))
    if not is_render and not _is_port_available(host, port):
        fallback_port = _pick_free_port(host)
        print(
            f"Port {port} is already in use; using {fallback_port} instead.",
            flush=True,
        )
        port = fallback_port
    url = f"http://{host}:{port}/"
    server_thread = threading.Thread(
        target=_serve_wsgi, args=(app, host, port), daemon=True
    )
    server_thread.start()
    _wait_for_server(host, port)
    if sys.stdout.isatty() and not is_render:
        if not open_in_edge(url):
            print(f"Open {url} in your browser to view the calculator.", flush=True)
    print(f"Serving cryptocalculator on {url}", flush=True)
    server_thread.join()
