"""Flask web front-end for the crypto trade calculator."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import sys
import threading
import time
import webbrowser
from typing import Dict, Optional

import requests
from flask import Flask, jsonify, render_template_string, request

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
    BYBIT_LINEAR_URL,
    BYBIT_SPOT_URL,
)
import options_trader

app = Flask(__name__)


@app.get("/symbols/bybit")
def bybit_symbols_placeholder():
    return jsonify({"symbols": [], "detail": "Symbol lookup is disabled."})

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
    .container {display:flex; align-items:flex-start;}
    .form {margin-right:20px;}
    .result {margin-left:20px;}
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
  </style>
  <script>
    function copyText(text, statusId){
      const status = document.getElementById(statusId);
      const done = () => { if(status){ status.innerText = 'Copied!'; setTimeout(() => status.innerText = '', 2000);} };
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(done).catch(() => {});
      } else {
        const temp = document.createElement('textarea');
        temp.value = text;
        document.body.appendChild(temp);
        temp.select();
        try { document.execCommand('copy'); done(); } finally { document.body.removeChild(temp); }
      }
    }
    function copyFromElement(elementId, statusId){
      const el = document.getElementById(elementId);
      if(!el){return;}
      copyText(el.innerText, statusId);
    }
    function exportResult(){
      const payload = document.getElementById('export_json');
      if(!payload || !payload.innerText.trim()){
        alert('Calculate a trade first to export the result.');
        return;
      }
      const blob = new Blob([payload.innerText], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      link.href = url;
      link.download = `crypto-trade-${timestamp}.json`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    }
    function setButtonGroupValue(inputId, value, dispatchChange=true){
      const input = document.getElementById(inputId);
      if(!input){return;}
      input.value = value;
      const group = document.querySelector(`[data-input="${inputId}"]`);
      if(group){
        group.querySelectorAll('button[data-value]').forEach((btn) => {
          btn.classList.toggle('active', btn.dataset.value === value);
        });
      }
      if(dispatchChange){
        input.dispatchEvent(new Event('change'));
      }
    }
    function bindButtonGroup(inputId){
      const group = document.querySelector(`[data-input="${inputId}"]`);
      if(!group){return;}
      group.querySelectorAll('button[data-value]').forEach((btn) => {
        btn.addEventListener('click', () => setButtonGroupValue(inputId, btn.dataset.value));
      });
      const input = document.getElementById(inputId);
      if(input){
        setButtonGroupValue(inputId, input.value, false);
      }
    }
    function toggleEntry(){
      const orderType = document.getElementById('order_type');
      const entryField = document.getElementById('entry_price_row');
      if(!orderType || !entryField){return;}
      entryField.style.display = orderType.value === 'market' ? 'none' : 'block';
    }
    function toggleOptionsEntry(){
      const orderType = document.getElementById('options_order_type');
      const entryField = document.getElementById('options_limit_price_row');
      if(!orderType || !entryField){return;}
      entryField.style.display = orderType.value === 'limit' ? 'block' : 'none';
    }
    function updatePriceMode(){
      const priceSource = document.getElementById('price_source');
      const note = document.getElementById('price_mode_note');
      const notes = {{ price_mode_notes|tojson }};
      if(!priceSource || !note){return;}
      note.innerText = notes[priceSource.value] || '';
    }
    function updateTradeType(){
      const selector = document.getElementById('trade_type');
      const optionsSection = document.getElementById('options_section');
      const cryptoSection = document.getElementById('crypto_section');
      if(!selector || !optionsSection || !cryptoSection){return;}
      const isOptions = selector.value === 'options';
      optionsSection.classList.toggle('hidden', !isOptions);
      cryptoSection.classList.toggle('hidden', isOptions);
      const cryptoRequired = ['symbol', 'stop_loss_ticks', 'risk_percent', 'rr_ratio'];
      cryptoRequired.forEach((fieldId) => {
        const el = document.getElementById(fieldId);
        if(!el){return;}
        if(isOptions){
          el.removeAttribute('required');
        } else {
          el.setAttribute('required', 'required');
        }
      });
    }
    async function enterNow(){
      const payloadEl = document.getElementById('alert_json');
      if(!payloadEl || !payloadEl.innerText.trim()){
        alert('Calculate a trade first to enable immediate entry.');
        return;
      }
      const ok = confirm('Place a live market order immediately? This cannot be undone.');
      if(!ok){return;}
      let payload = null;
      try{
        payload = JSON.parse(payloadEl.innerText);
      } catch (err) {
        alert('Could not parse the current payload. Recalculate and try again.');
        return;
      }
      const resultBox = document.getElementById('execute_result');
      if(resultBox){
        resultBox.classList.remove('hidden');
        resultBox.innerText = 'Submitting market order...';
      }
      try{
        const resp = await fetch('/execute_now', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if(resultBox){
          resultBox.innerText = JSON.stringify(data, null, 2);
        }
      } catch (err) {
        if(resultBox){
          resultBox.innerText = `Error: ${err}`;
        }
      }
    }
    async function refreshOptionMinQty(){
      const base = document.getElementById('options_base');
      const strike = document.getElementById('options_strike');
      const optType = document.getElementById('options_type');
      const expiry = document.getElementById('options_expiry');
      const qtyStep = document.getElementById('options_qty_step');
      if(!base || !strike || !optType || !expiry || !qtyStep){return;}
      const params = new URLSearchParams({
        base: base.value || '',
        strike: strike.value || '',
        option_type: optType.value || '',
        expiry: expiry.value || '',
      });
      try{
        const response = await fetch(`/options/min-qty?${params.toString()}`);
        if(!response.ok){throw new Error('Failed to load min qty');}
        const data = await response.json();
        qtyStep.value = data.min_qty || '0.01';
      } catch(err){
        qtyStep.value = qtyStep.value || '0.01';
      }
    }
    function adjustOptionsQty(direction){
      const qtyInput = document.getElementById('options_quantity');
      const qtyStep = document.getElementById('options_qty_step');
      if(!qtyInput || !qtyStep){return;}
      const step = parseFloat(qtyStep.value || '0.01');
      const current = parseFloat(qtyInput.value || '0');
      let next = current + (direction * step);
      if(next < 0){next = 0;}
      const decimals = (qtyStep.value.split('.')[1] || '').length;
      qtyInput.value = next.toFixed(decimals);
    }
    document.addEventListener('DOMContentLoaded', function(){
      const ot = document.getElementById('order_type');
      if(ot){
        ot.addEventListener('change', toggleEntry);
        toggleEntry();
      }
      const oot = document.getElementById('options_order_type');
      if(oot){
        oot.addEventListener('change', toggleOptionsEntry);
        toggleOptionsEntry();
      }
      const ps = document.getElementById('price_source');
      if(ps){
        ps.addEventListener('change', updatePriceMode);
        updatePriceMode();
      }
      const tradeType = document.getElementById('trade_type');
      if(tradeType){
        tradeType.addEventListener('change', updateTradeType);
      }
      updateTradeType();
      ['trade_type', 'account_mode', 'direction', 'order_type', 'options_order_type', 'options_type', 'options_side', 'price_source', 'execution_exchange', 'options_base'].forEach(bindButtonGroup);
      ['options_strike', 'options_expiry'].forEach((fieldId) => {
        const el = document.getElementById(fieldId);
        if(el){
          el.addEventListener('change', refreshOptionMinQty);
          el.addEventListener('blur', refreshOptionMinQty);
        }
      });
      refreshOptionMinQty();
    });
  </script>
</head>
<body>
  <h1>Crypto Position Size Calculator</h1>
  <div class="container">
    <div class="form">
      <form method="post">
        <label>Trade Type:</label>
        <div class="button-group" data-input="trade_type">
          <button type="button" data-value="perpetual">Perpetual Futures</button>
          <button type="button" data-value="spot">Spot</button>
          <button type="button" data-value="options">Options</button>
        </div>
        <input type="hidden" name="trade_type" id="trade_type" value="{{ trade_type }}">
        <label>Account:</label>
        <div class="button-group" data-input="account_mode">
          <button type="button" data-value="live">Live</button>
          <button type="button" data-value="demo">Demo</button>
        </div>
        <input type="hidden" name="account_mode" id="account_mode" value="{{ account_mode }}">
        <div id="crypto_section" class="trade-section">
          <label>Symbol: <input name="symbol" id="symbol"></label><br>
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
          <div id="entry_price_row">
            <label>Entry Price: <input name="entry_price" type="number" step="0.0001"></label><br>
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
          <label>Order Type:</label>
          <div class="button-group" data-input="options_order_type">
            <button type="button" data-value="market">Market</button>
            <button type="button" data-value="limit">Limit</button>
          </div>
          <input type="hidden" name="options_order_type" id="options_order_type" value="{{ options_order_type }}">
          <label>Base:</label>
          <div class="button-group" data-input="options_base">
            {% for base in options_base_options %}
            <button type="button" data-value="{{ base }}">{{ base }}</button>
            {% endfor %}
          </div>
          <input type="hidden" name="options_base" id="options_base" value="{{ options_base }}">
          <label>Strike: <input name="options_strike" id="options_strike"></label><br>
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
          <label>Quantity:</label>
          <div class="button-group">
            <button type="button" onclick="adjustOptionsQty(-1)">-</button>
            <button type="button" onclick="adjustOptionsQty(1)">+</button>
          </div>
          <input type="hidden" id="options_qty_step" value="{{ options_qty_step }}">
          <input name="options_quantity" id="options_quantity" value="{{ options_quantity }}"><br>
          <div id="options_limit_price_row">
            <label>Limit Price: <input name="options_limit_price"></label><br>
          </div>
          <label>Risk %: <input name="options_risk_percent" value="0"></label><br>
          <label>TP Multiplier: <input name="options_tp_multiplier" value="3"></label><br>
          <div class="copy-row">
            <button type="submit" name="options_action" value="journal">Journal last 30 days</button>
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
        <div class="copy-row">
          <button type="button" class="danger-button" onclick="enterNow()">Enter now via market order</button>
        </div>
        <p class="danger-note">This immediately submits a live market order. Use with extreme caution.</p>
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
    </div>
  </div>
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
    if trade_type not in {"perpetual", "spot", "options"}:
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

    options_base = request.form.get("options_base", "BTC").strip().upper()
    if options_base not in options_trader.get_supported_option_bases():
        options_base = options_trader.get_supported_option_bases()[0]

    options_type = request.form.get("options_type", "Call").strip().capitalize()
    if options_type not in {"Call", "Put"}:
        options_type = "Call"

    options_side = request.form.get("options_side", "Buy").strip().capitalize()
    if options_side not in {"Buy", "Sell"}:
        options_side = "Buy"

    execution_exchange = request.form.get(
        "execution_exchange", DEFAULT_EXECUTION_EXCHANGE
    ).lower()
    if execution_exchange not in EXECUTION_EXCHANGES:
        execution_exchange = DEFAULT_EXECUTION_EXCHANGE

    price_source = request.form.get("price_source", DEFAULT_PRICE_SOURCE).lower()
    if price_source not in PRICE_SOURCES:
        price_source = DEFAULT_PRICE_SOURCE

    trade_mode = PRICE_SOURCES[price_source]["trade_mode"]
    account_mode = request.form.get("account_mode", "live").strip().lower()
    if account_mode not in {"live", "demo"}:
        account_mode = "live"
    price_to_execution_rate = request.form.get("price_to_execution_rate", "").strip()

    if request.method == "POST":
        try:
            if trade_type == "options":
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
                if options_action in {"journal", "open_orders", "open_positions"}:
                    if trader is None:
                        raise ValueError("Options credentials are not configured.")
                    if options_action == "journal":
                        options_output = options_trader.build_journal_report(
                            trader, days=30
                        )
                    elif options_action == "open_orders":
                        orders = trader.get_open_orders(symbol_filter)
                        options_output = json.dumps(orders, indent=2)
                    elif options_action == "open_positions":
                        positions = trader.get_positions(symbol_filter)
                        options_output = json.dumps(positions, indent=2)
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
                        qty = options_trader.compute_order_qty(risk_usd, mark_price, min_qty)
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
                        "tp_offset": round(tp_offset, 6) if tp_offset is not None else None,
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
                symbol = request.form.get("symbol", "").strip().upper()
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
                payload_json = json.dumps(build_webhook_payload(trade), indent=2)

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

    options_qty_step = options_trader.MIN_ORDER_QTY
    if request.method == "POST":
        strike_value = request.form.get("options_strike", "").strip()
        expiry_value = request.form.get("options_expiry", "").strip()
        if strike_value and expiry_value:
            try:
                symbol_step = options_trader.build_option_symbol(
                    options_base,
                    strike_value,
                    options_type,
                    expiry_value,
                    "USDT",
                )
                options_qty_step = options_trader.get_min_order_qty(symbol_step)
            except Exception:  # pylint: disable=broad-except
                options_qty_step = options_trader.MIN_ORDER_QTY

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
        options_order_type=options_order_type,
        options_base=options_base,
        options_type=options_type,
        options_side=options_side,
        options_base_options=options_trader.get_supported_option_bases(),
        options_qty_step=options_qty_step,
        options_quantity=request.form.get("options_quantity", "0"),
        options_output=options_output,
        execution_options=sorted(EXECUTION_EXCHANGES.items()),
        price_source_options=sorted(PRICE_SOURCES.items()),
        price_mode_notes=PRICE_MODE_NOTES,
        webhook_url=PUBLIC_WEBHOOK_URL,
        export_json=export_json,
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
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError(
            "Running cryptocalculator_web.py requires the 'waitress' package. "
            "Install it with 'pip install waitress'."
        ) from exc

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
