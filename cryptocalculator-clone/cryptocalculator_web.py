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

from flask import Flask, jsonify, render_template_string, request
import requests

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

app = Flask(__name__)

PRICE_MODE_NOTES = {
    key: (
        "Spot mode uses spot pricing and fees with no funding component."
        if meta["trade_mode"] == "spot"
        else "Linear mode uses perpetual contract pricing, funding and fee settings."
    )
    for key, meta in PRICE_SOURCES.items()
}

BALANCE_ADAPTERS = {name: get_balance_fetcher(name) for name in EXECUTION_EXCHANGES}
SYMBOL_CACHE: Dict[str, Dict[str, object]] = {}
SYMBOL_CACHE_TTL = 300
PUBLIC_WEBHOOK_URL = os.getenv("PUBLIC_WEBHOOK_URL", "https://codex-rdqh.onrender.com/webhook")

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
    .symbol-wrap {position:relative; display:block;}
    .symbol-list {position:absolute; z-index:10; left:0; right:0; max-height:220px; overflow-y:auto; background:#111827; border:1px solid #1f2937; border-radius:6px; padding:4px 0; display:none;}
    .symbol-item {padding:6px 10px; cursor:pointer;}
    .symbol-item:hover {background:#1f2937;}
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
  <div class="container">
    <div class="form">
      <form method="post">
        <label>Symbol:
          <span class="symbol-wrap">
            <input name="symbol" id="symbol" autocomplete="off" required>
            <div id="symbol_list" class="symbol-list"></div>
          </span>
        </label><br>
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
    price_to_execution_rate = request.form.get("price_to_execution_rate", "").strip()

    if request.method == "POST":
        try:
            symbol = request.form["symbol"].strip()
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
            config["account_balance"] = balance_fetcher()
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
        price_to_execution_rate=price_to_execution_rate,
        execution_options=sorted(EXECUTION_EXCHANGES.items()),
        price_source_options=sorted(PRICE_SOURCES.items()),
        price_mode_notes=PRICE_MODE_NOTES,
        webhook_url=PUBLIC_WEBHOOK_URL,
        export_json=export_json,
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


def _fetch_bybit_symbols(trade_mode: str) -> list[str]:
    url = BYBIT_SPOT_URL if trade_mode == "spot" else BYBIT_LINEAR_URL
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    symbols = [
        item.get("symbol", "").upper()
        for item in resp.json().get("result", {}).get("list", [])
        if item.get("symbol")
    ]
    return sorted(set(symbols))


def _fetch_bybit_all_symbols() -> list[str]:
    symbols = _fetch_bybit_symbols("spot") + _fetch_bybit_symbols("linear")
    return sorted(set(symbols))


def _fetch_coinspot_symbols() -> list[str]:
    url = "https://www.coinspot.com.au/pubapi/v2/latest"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    prices = data.get("prices", {})
    if isinstance(prices, dict):
        symbols = [key.upper() for key in prices.keys() if key]
    else:
        symbols = []
    return sorted(set(symbols))


def _get_cached_symbols(cache_key: str) -> Optional[list[str]]:
    cached = SYMBOL_CACHE.get(cache_key)
    if not cached:
        return None
    if time.time() - cached["timestamp"] > SYMBOL_CACHE_TTL:
        return None
    return cached["symbols"]  # type: ignore[return-value]


def _set_cached_symbols(cache_key: str, symbols: list[str]) -> None:
    SYMBOL_CACHE[cache_key] = {"timestamp": time.time(), "symbols": symbols}


@app.get("/symbols/bybit")
def bybit_symbol_lookup():
    cache_key = "bybit:all"
    cached = _get_cached_symbols(cache_key)
    if cached is not None:
        return jsonify({"symbols": cached})
    try:
        symbols = _fetch_bybit_all_symbols()
    except Exception as exc:  # pragma: no cover - network fallback
        return jsonify({"symbols": [], "error": str(exc)})
    _set_cached_symbols(cache_key, symbols)
    return jsonify({"symbols": symbols})


@app.get("/symbols")
def symbol_lookup():
    price_source = request.args.get("price_source", DEFAULT_PRICE_SOURCE).lower()
    if price_source not in PRICE_SOURCES:
        price_source = DEFAULT_PRICE_SOURCE
    exchange = PRICE_SOURCES[price_source]["exchange"]
    trade_mode = PRICE_SOURCES[price_source]["trade_mode"]
    cache_key = f"{exchange}:{trade_mode}"
    cached = _get_cached_symbols(cache_key)
    if cached is not None:
        return jsonify({"symbols": cached})

    try:
        if exchange == "bybit":
            symbols = _fetch_bybit_symbols(trade_mode)
        elif exchange == "coinspot":
            symbols = _fetch_coinspot_symbols()
        else:
            symbols = []
    except Exception as exc:  # pragma: no cover - network fallback
        return jsonify({"symbols": [], "error": str(exc)})

    _set_cached_symbols(cache_key, symbols)
    return jsonify({"symbols": symbols})


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
