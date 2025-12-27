"""Flask web front-end for the crypto trade calculator."""
from __future__ import annotations

import contextlib
import html
import io
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
    function toggleEntry(){
      const orderType = document.getElementById('order_type');
      const entryField = document.getElementById('entry_price_row');
      if(!orderType || !entryField){return;}
      entryField.style.display = orderType.value === 'market' ? 'none' : 'block';
    }
    function updatePriceMode(){
      const priceSource = document.getElementById('price_source');
      const note = document.getElementById('price_mode_note');
      const notes = {{ price_mode_notes|tojson }};
      if(!priceSource || !note){return;}
      note.innerText = notes[priceSource.value] || '';
    }
    async function loadSymbols(){
      const symbolInput = document.getElementById('symbol');
      if(!symbolInput){return;}
      try{
        const response = await fetch('/symbols/bybit');
        if(!response.ok){throw new Error('Failed to load symbols');}
        const data = await response.json();
        window.__symbolList = Array.isArray(data.symbols) ? data.symbols : [];
        updateSymbolSuggestions(symbolInput.value, true);
      } catch(err){
        console.warn(err);
      }
    }
    function updateSymbolSuggestions(value, forceShow){
      const symbolList = document.getElementById('symbol_list');
      const symbols = Array.isArray(window.__symbolList) ? window.__symbolList : [];
      if(!symbolList){return;}
      symbolList.innerHTML = '';
      const query = (value || '').trim().toUpperCase();
      const matches = query
        ? symbols.filter((symbol) => symbol.startsWith(query))
        : symbols.slice();
      matches.forEach((symbol) => {
        const item = document.createElement('div');
        item.className = 'symbol-item';
        item.textContent = symbol;
        item.addEventListener('click', () => {
          const input = document.getElementById('symbol');
          if(input){
            input.value = symbol;
          }
          symbolList.style.display = 'none';
        });
        symbolList.appendChild(item);
      });
      symbolList.style.display = (forceShow || query) && matches.length ? 'block' : 'none';
    }
    document.addEventListener('DOMContentLoaded', function(){
      const ot = document.getElementById('order_type');
      if(ot){
        ot.addEventListener('change', toggleEntry);
        toggleEntry();
      }
      const ps = document.getElementById('price_source');
      if(ps){
        ps.addEventListener('change', updatePriceMode);
        ps.addEventListener('change', loadSymbols);
        updatePriceMode();
      }
      const symbolInput = document.getElementById('symbol');
      if(symbolInput){
        symbolInput.addEventListener('input', (event) => updateSymbolSuggestions(event.target.value));
        symbolInput.addEventListener('focus', (event) => updateSymbolSuggestions(event.target.value, true));
      }
      document.addEventListener('click', (event) => {
        const symbolList = document.getElementById('symbol_list');
        const symbolInputEl = document.getElementById('symbol');
        if(!symbolList || !symbolInputEl){return;}
        if(event.target !== symbolInputEl && !symbolList.contains(event.target)){
          symbolList.style.display = 'none';
        }
      });
      loadSymbols();
    });
  </script>
</head>
<body>
  <h1>Crypto Position Size Calculator</h1>
  <p><a href="/options">Open Options Position Calculator</a></p>
  <div class="container">
    <div class="form">
      <form method="post">
        <label>Symbol: <input name="symbol" id="symbol" required></label><br>
        <label>Price Source:
          <select name="price_source" id="price_source">
            {% for key, meta in price_source_options %}
            <option value="{{ key }}" {{ 'selected' if key == price_source else '' }}>{{ meta['label'] }}</option>
            {% endfor %}
          </select>
        </label><br>
        <label>Execution Exchange:
          <select name="execution_exchange" id="execution_exchange">
            {% for key, meta in execution_options %}
            <option value="{{ key }}" {{ 'selected' if key == execution_exchange else '' }}>{{ meta['label'] }}</option>
            {% endfor %}
          </select>
        </label><br>
        <p id="price_mode_note"></p>
        <label>Account:
          <select name="account_mode" id="account_mode">
            <option value="live" {{ 'selected' if account_mode == 'live' else '' }}>Live</option>
            <option value="demo" {{ 'selected' if account_mode == 'demo' else '' }}>Demo</option>
          </select>
        </label><br>
        <label>Direction:
          <select name="direction">
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
        </label><br>
        <label>Order Type:
          <select name="order_type" id="order_type">
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
        </label><br>
        <div id="entry_price_row">
          <label>Entry Price: <input name="entry_price" type="number" step="0.0001"></label><br>
        </div>
        <label>Stop loss ticks: <input name="stop_loss_ticks" type="number" step="1" required></label><br>
        <label>Risk %: <input name="risk_percent" type="number" step="0.01" required></label><br>
        <label>Risk–reward ratio: <input name="rr_ratio" type="number" step="0.1" value="2" required></label><br>
        <label>Price → Execution rate:
          <input name="price_to_execution_rate" id="price_to_execution_rate" type="number" step="0.0001" min="0" value="{{ price_to_execution_rate }}" placeholder="e.g. 1.55">
        </label><br>
        <small>Use this when your price source is quoted in a different currency than your execution exchange.</small><br>
        <button type="submit">Calculate</button>
      </form>
      <h3>TradingView Webhook</h3>
      <div class="copy-row">
        <button type="button" onclick="copyText('{{ webhook_url }}','webhook_status')">Copy Webhook URL</button>
        <span class="copy-status" id="webhook_status"></span>
      </div>
      <div class="copy-box" id="webhook_url">{{ webhook_url }}</div>
      <p><strong>How offsets work:</strong></p>
      <ul>
        <li><strong>Buy/Long:</strong> TP = entry + tp_offset, SL = entry - sl_offset.</li>
        <li><strong>Sell/Short:</strong> TP = entry - tp_offset, SL = entry + sl_offset.</li>
      </ul>
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

OPTIONS_STYLE = """
<style>
  body { background-color: #121212; color: #fff; font-family: Arial, sans-serif; }
  button { display: block; margin: 10px 0; background-color: #333; color: #fff;
           padding: 8px 12px; border: 1px solid #555; cursor: pointer; }
  input { background-color: #222; color: #fff; border: 1px solid #555; }
  table td { padding: 4px; }
  a { color: #80b3ff; }
</style>
"""


def _options_page(content: str) -> str:
    return f"<!doctype html><html><head>{OPTIONS_STYLE}</head><body>{content}</body></html>"


_options_trader_instance: Optional[options_trader.BybitOptionsTrader] = None


def _get_options_trader() -> Optional[options_trader.BybitOptionsTrader]:
    global _options_trader_instance
    options_trader.configure_trading_environment(interactive=False)
    if _options_trader_instance is None:
        key, secret = options_trader.get_api_credentials({})
        if key and secret:
            _options_trader_instance = options_trader.BybitOptionsTrader(
                key, secret, options_trader.get_base_url()
            )
    return _options_trader_instance


@app.route("/", methods=["GET", "POST"])
def index():
    summary = None
    error = None
    risk_info = None
    payload_json = None
    export_json = None
    trade = None

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
            symbol = request.form["symbol"].strip().upper()
            direction = request.form.get("direction", "long")
            order_type = request.form.get("order_type", "market")
            entry_price_raw = request.form.get("entry_price")
            stop_loss_ticks = float(request.form["stop_loss_ticks"])
            risk_percent = float(request.form["risk_percent"])
            rr_ratio = float(request.form.get("rr_ratio", 2.0))

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
                    account_asset, account_type="UNIFIED", account_mode=account_mode
                )
            if execution_exchange == "coinspot":
                config.setdefault("account_asset", "AUD")

            trade = calculate_trade(config)
            summary = format_trade(trade)
            risk_info = {k.replace("_", " ").title(): v for k, v in trade.items()}
            payload_json = json.dumps(build_webhook_payload(trade), indent=2)

            price_source = trade.get("price_source", price_source)
            execution_exchange = trade.get("execution_exchange", execution_exchange)
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
        execution_options=sorted(EXECUTION_EXCHANGES.items()),
        price_source_options=sorted(PRICE_SOURCES.items()),
        price_mode_notes=PRICE_MODE_NOTES,
        webhook_url=PUBLIC_WEBHOOK_URL,
        export_json=export_json,
    )


@app.route("/options")
def options_index():
    return _options_page(
        """
        <h1>Options Position Calculator</h1>
        <button onclick=\"location.href='/options/trade'\">Create Trade</button>
        <button onclick=\"location.href='/options/show'\">Show Open Orders/Positions</button>
        <button onclick=\"location.href='/options/cancel'\">Cancel All Orders/Positions</button>
        <button onclick=\"location.href='/options/edit'\">Edit Open Order</button>
        <button onclick=\"location.href='/options/export_recent'\">Export Trade History (7 days)</button>
        <button onclick=\"location.href='/options/export_all'\">Export All Trade History</button>
        <button onclick=\"location.href='/options/delivery_recent'\">Export Delivery History (7 days)</button>
        <button onclick=\"location.href='/options/delivery_all'\">Export All Delivery History</button>
        <button onclick=\"location.href='/options/reduce'\">Place Reduce-Only Exits</button>
        <p><a href='/'>Back to Crypto Calculator</a></p>
        """
    )


@app.route("/options/trade", methods=["GET", "POST"])
def options_trade():
    if request.method == "POST":
        form = request.form
        trader = _get_options_trader()
        balance = options_trader.DEMO_BALANCE
        if trader is not None:
            api_bal = trader.get_wallet_balance()
            if api_bal > 0:
                balance = api_bal
        risk_percent = float(form.get("risk_percent", 0) or 0)
        risk_usd = balance * risk_percent / 100
        qty = float(form.get("quantity", 0) or 0)
        symbol = form.get("symbol", "").strip().upper()
        if not symbol:
            symbol = options_trader.build_option_symbol(
                form.get("base", ""),
                form.get("strike", ""),
                form.get("option_type", ""),
                form.get("expiry", ""),
                form.get("quote", "USDT"),
            )
        if qty <= 0 and risk_usd > 0:
            tick = options_trader.fetch_option_ticker(symbol)
            price = float(tick.get("markPrice", 0) or 0)
            qty = options_trader.compute_order_qty(risk_usd, price)
        cfg = {
            "symbol": symbol,
            "side": form.get("side", "Buy"),
            "quantity": qty,
            "limit_price": float(form["limit_price"]) if form.get("limit_price") else None,
            "risk_usd": risk_usd,
            "auto_trade": bool(form.get("auto_trade")),
        }
        buf = io.StringIO()
        error = None
        try:
            with contextlib.redirect_stdout(buf):
                options_trader.execute_trade_from_cfg(cfg)
        except Exception as exc:  # pragma: no cover - interactive error handling
            error = exc
            app.logger.exception("Options trade execution failed")
        output = buf.getvalue()
        if error:
            output += (
                "\n\nERROR: "
                + str(error)
                + "\nProvide BYBIT_API_KEY/BYBIT_API_SECRET env vars or add "
                "api_key/api_secret to the form's config fields."
            )
        return _options_page(
            "<pre>"
            + html.escape(output)
            + "</pre><a href='/options'>Back</a>"
        )

    balance = options_trader.DEMO_BALANCE
    trader = _get_options_trader()
    if trader is not None:
        api_bal = trader.get_wallet_balance()
        if api_bal > 0:
            balance = api_bal
    html_body = render_template_string(
        """
        <h2>Create Trade</h2>
        <p>Current Balance: {{balance}} USDT</p>
        <form method='post'>
        <table>
        <tr><td>Symbol (optional)</td><td><input name='symbol'></td></tr>
        <tr><td>Base</td><td><input name='base'></td></tr>
        <tr><td>Strike</td><td><input name='strike'></td></tr>
        <tr><td>Call/Put</td><td><input name='option_type'></td></tr>
        <tr><td>Expiry (D/M/YY)</td><td><input name='expiry'></td></tr>
        <tr><td>Quote</td><td><input name='quote' value='USDT'></td></tr>
        <tr><td>Side</td><td><input name='side' value='Buy'></td></tr>
        <tr><td>Quantity</td><td><input name='quantity' value='0'></td></tr>
        <tr><td>Limit Price</td><td><input name='limit_price'></td></tr>
        <tr><td>Risk %</td><td><input name='risk_percent' value='0'></td></tr>
        <tr><td>Auto Trade</td><td><input type='checkbox' name='auto_trade'></td></tr>
        </table>
        <button type='submit'>Submit Trade</button>
        </form>
        <a href='/options'>Back</a>
        """,
        balance=balance,
    )
    return _options_page(html_body)


def _options_requires_trader() -> Optional[options_trader.BybitOptionsTrader]:
    trader = _get_options_trader()
    if trader is None:
        return None
    return trader


@app.route("/options/show")
def options_show():
    trader = _options_requires_trader()
    if trader is None:
        return _options_page("No trader available. Configure API keys.<br><a href='/options'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        options_trader.show_open(trader)
    return _options_page(
        "<pre>" + html.escape(buf.getvalue()) + "</pre><a href='/options'>Back</a>"
    )


@app.route("/options/cancel")
def options_cancel():
    trader = _options_requires_trader()
    if trader is None:
        return _options_page("No trader available. Configure API keys.<br><a href='/options'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        options_trader.cancel_all(trader)
    return _options_page(
        "<pre>" + html.escape(buf.getvalue()) + "</pre><a href='/options'>Back</a>"
    )


@app.route("/options/edit", methods=["GET", "POST"])
def options_edit():
    trader = _options_requires_trader()
    if trader is None:
        return _options_page("No trader available. Configure API keys.<br><a href='/options'>Back</a>")
    if request.method == "POST":
        oid = request.form.get("order_id", "")
        price = request.form.get("price")
        qty = request.form.get("qty")
        price_val = float(price) if price else None
        qty_val = float(qty) if qty else None
        trader.amend_order(oid, price_val, qty_val)
        return _options_page("Order amended.<br><a href='/options'>Back</a>")
    html_body = render_template_string(
        """
        <h2>Edit Open Order</h2>
        <form method='post'>
        Order ID: <input name='order_id'><br>
        New Price: <input name='price'><br>
        New Qty: <input name='qty'><br>
        <button type='submit'>Submit</button>
        </form>
        <a href='/options'>Back</a>
        """
    )
    return _options_page(html_body)


@app.route("/options/export_recent")
def options_export_recent():
    trader = _options_requires_trader()
    if trader is None:
        return _options_page("No trader available. Configure API keys.<br><a href='/options'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        options_trader.export_recent_trade_history(trader)
    return _options_page(
        "<pre>" + html.escape(buf.getvalue()) + "</pre><a href='/options'>Back</a>"
    )


@app.route("/options/export_all")
def options_export_all():
    trader = _options_requires_trader()
    if trader is None:
        return _options_page("No trader available. Configure API keys.<br><a href='/options'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        options_trader.export_all_trade_history(trader)
    return _options_page(
        "<pre>" + html.escape(buf.getvalue()) + "</pre><a href='/options'>Back</a>"
    )


@app.route("/options/delivery_recent")
def options_delivery_recent():
    trader = _options_requires_trader()
    if trader is None:
        return _options_page("No trader available. Configure API keys.<br><a href='/options'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        options_trader.export_recent_delivery_history(trader)
    return _options_page(
        "<pre>" + html.escape(buf.getvalue()) + "</pre><a href='/options'>Back</a>"
    )


@app.route("/options/delivery_all")
def options_delivery_all():
    trader = _options_requires_trader()
    if trader is None:
        return _options_page("No trader available. Configure API keys.<br><a href='/options'>Back</a>")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        options_trader.export_all_delivery_history(trader)
    return _options_page(
        "<pre>" + html.escape(buf.getvalue()) + "</pre><a href='/options'>Back</a>"
    )


@app.route("/options/reduce")
def options_reduce():
    trader = _options_requires_trader()
    if trader is None:
        return _options_page("No trader available. Configure API keys.<br><a href='/options'>Back</a>")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            options_trader.set_profit_targets(trader)
    except Exception as exc:  # pragma: no cover - just in case
        app.logger.exception("Failed to set profit targets")
        return _options_page(
            "<pre>Failed to set profit targets: "
            + html.escape(str(exc))
            + "</pre><a href='/options'>Back</a>"
        )
    return _options_page(
        "<pre>" + html.escape(buf.getvalue()) + "</pre><a href='/options'>Back</a>"
    )


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
