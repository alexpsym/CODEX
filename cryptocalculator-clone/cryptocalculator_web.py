from flask import Flask, request, render_template_string
import json
import os
from pathlib import Path
import shutil
import sys
import webbrowser

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
)

BALANCE_ADAPTERS = {
    name: get_balance_fetcher(name) for name in EXECUTION_EXCHANGES.keys()
}

PRICE_MODE_NOTES = {
    key: (
        "Spot mode uses spot pricing and fees with no funding component."
        if meta["trade_mode"] == "spot"
        else "Linear mode uses perpetual contract pricing, funding and fee settings."
    )
    for key, meta in PRICE_SOURCES.items()
}

app = Flask(__name__)

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
</style>
<script>
function copy(text){navigator.clipboard.writeText(text);}
function copyFromId(id){
  const el = document.getElementById(id);
  if(el){copy(el.innerText);}
}
function downloadSummary(){
  const sumEl = document.getElementById('summary_text');
  if(!sumEl){return;}
  const blob = new Blob([sumEl.innerText], {type:'text/plain'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'trade_summary.txt';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
function toggleEntry(){
  const orderType = document.getElementById('order_type');
  const entryLabel = document.getElementById('entry_price_label');
  if(!orderType || !entryLabel){return;}
  entryLabel.style.display = orderType.value === 'market' ? 'none' : 'block';
}
function updatePriceMode(){
  const priceSource = document.getElementById('price_source');
  const note = document.getElementById('price_mode_note');
  const notes = {{ price_mode_notes|tojson }};
  if(!priceSource || !note){return;}
  note.innerText = notes[priceSource.value] || '';
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
});
</script>
</head>
<body>
<h1>Crypto Position Size Calculator</h1>
<div class="container">
<div class="form">
<form method="post">
  <label>Symbol: <input name="symbol" required></label><br>
  <label>Execution Exchange:
    <select name="execution_exchange" id="execution_exchange">
      {% for key, meta in execution_options %}
      <option value="{{ key }}" {{ 'selected' if execution_exchange == key else '' }}>{{ meta['label'] }}</option>
      {% endfor %}
    </select>
  </label><br>
  <label>Price Source:
    <select name="price_source" id="price_source">
      {% for key, meta in price_source_options %}
      <option value="{{ key }}" {{ 'selected' if price_source == key else '' }}>{{ meta['label'] }}</option>
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
  <label id="entry_price_label">Entry Price: <input name="entry_price" type="number" step="0.0001"></label><br>
  <label>Stop loss ticks: <input name="stop_loss_ticks" type="number" step="1" required></label><br>
  <label>Risk %: <input name="risk_percent" type="number" step="0.01" required></label><br>
  <label>Risk–reward ratio: <input name="rr_ratio" type="number" step="0.1" value="2" required></label><br>
  <button type="submit">Calculate</button><br>
  <button type="button" onclick="copy('https://app.signalstack.com/hook/6vSSkN1tYQLj3C1H3YQqpz')">Copy Webhook</button><br>
  {% if payload_json %}
    <button type="button" onclick="copyFromId('alert_json')">Copy JSON</button><br>
    <button type="button" onclick="downloadSummary()">Download Summary</button><br>
  {% else %}
    <button type="button" disabled>Copy JSON</button><br>
    <button type="button" disabled>Download Summary</button><br>
  {% endif %}
</form>
</div>
<div class="result">
{% if error %}<p style="color: red;">{{ error }}</p>{% endif %}
{% if payload_json %}<h2>Result</h2><pre id="alert_json">{{ payload_json }}</pre>{% endif %}
{% if show_selection %}
<h2>Execution Settings</h2>
<table border="1">
  <tr><th>Execution Exchange</th><td>{{ execution_exchange_label }}</td></tr>
  <tr><th>Price Source</th><td>{{ price_source_label }}</td></tr>
  <tr><th>Trade Mode</th><td>{{ trade_mode_label }}</td></tr>
</table>
{% endif %}
{% if summary %}
<h2>Summary</h2>
<pre id="summary_text">{{ summary }}</pre>
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
    execution_exchange = DEFAULT_EXECUTION_EXCHANGE
    price_source = DEFAULT_PRICE_SOURCE
    trade_mode = PRICE_SOURCES[price_source]["trade_mode"]
    show_selection = False
    if request.method == "POST":
        try:
            symbol = request.form["symbol"]
            direction = request.form["direction"]
            order_type = request.form["order_type"]
            entry_price = request.form.get("entry_price")
            stop_loss_ticks = float(request.form["stop_loss_ticks"])
            risk_percent = float(request.form["risk_percent"])
            rr_ratio = float(request.form["rr_ratio"])

            execution_exchange = request.form.get(
                "execution_exchange", DEFAULT_EXECUTION_EXCHANGE
            )
            if execution_exchange not in EXECUTION_EXCHANGES:
                execution_exchange = DEFAULT_EXECUTION_EXCHANGE

            price_source = request.form.get("price_source", DEFAULT_PRICE_SOURCE)
            if price_source not in PRICE_SOURCES:
                price_source = DEFAULT_PRICE_SOURCE

            trade_mode = PRICE_SOURCES[price_source]["trade_mode"]
            show_selection = True

            config = {
                "account_balance": "auto",
                "risk_percent": risk_percent,
                "rr_ratio": rr_ratio,
                "order_type": order_type,
                "symbol": symbol,
                "stop_loss_ticks": stop_loss_ticks,
                "direction": direction,
                "trade_mode": trade_mode,
                "execution_exchange": execution_exchange,
                "price_source": price_source,
            }
            if order_type == "limit" and entry_price:
                config["entry_price"] = float(entry_price)

            if str(config["account_balance"]).lower() == "auto":
                balance_adapter = BALANCE_ADAPTERS.get(execution_exchange)
                if balance_adapter is None:
                    raise ValueError(
                        f"Execution exchange '{execution_exchange}' is not supported."
                    )
                config["account_balance"] = balance_adapter()

            trade = calculate_trade(config)
            summary = format_trade(trade)
            risk_info = {k.replace("_", " ").title(): v for k, v in trade.items()}
            risk_info["Execution Exchange"] = EXECUTION_EXCHANGES[execution_exchange][
                "label"
            ]
            risk_info["Price Source"] = PRICE_SOURCES[price_source]["label"]
            payload = build_webhook_payload(trade)
            payload_json = json.dumps(payload, indent=2)
        except Exception as exc:  # pylint: disable=broad-except
            error = str(exc)
    execution_exchange_label = EXECUTION_EXCHANGES[execution_exchange]["label"]
    price_source_label = PRICE_SOURCES[price_source]["label"]
    trade_mode_label = TRADE_MODE_LABELS[trade_mode]
    return render_template_string(
        FORM_HTML,
        summary=summary,
        error=error,
        risk_info=risk_info,
        payload_json=payload_json,
        execution_exchange=execution_exchange,
        price_source=price_source,
        execution_exchange_label=execution_exchange_label,
        price_source_label=price_source_label,
        trade_mode_label=trade_mode_label,
        show_selection=show_selection,
        execution_options=sorted(EXECUTION_EXCHANGES.items()),
        price_source_options=sorted(PRICE_SOURCES.items()),
        price_mode_notes=PRICE_MODE_NOTES,
    )


def open_in_edge(url: str) -> bool:
    """Open ``url`` in Microsoft Edge if possible.

    The function tries a series of strategies so that HTML pages always launch in
    Edge, even when it is not the system default browser.  It returns ``True`` if
    Edge handled the URL and ``False`` when the caller should fall back to
    manual instructions.
    """

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
        browser = webbrowser.BackgroundBrowser(path)
        browser.open(url)
        return True

    for candidate in EDGE_FALLBACK_PATHS:
        if not candidate.exists():
            continue
        browser = webbrowser.BackgroundBrowser(str(candidate))
        browser.open(url)
        return True

    if sys.platform.startswith("win") and hasattr(os, "startfile"):
        try:
            os.startfile(f"microsoft-edge:{url}")  # type: ignore[attr-defined]
        except OSError:
            return False
        return True

    return False


def _serve_wsgi(app: Flask, host: str = "127.0.0.1", port: int = 5000) -> None:
    """Serve *app* using a production-ready WSGI HTTP server."""

    try:
        from waitress import serve
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError(
            "Running cryptocalculator_web.py now requires the 'waitress' package. "
            "Install it with 'pip install waitress'."
        ) from exc

    serve(app, host=host, port=port)


if __name__ == "__main__":
    host = "127.0.0.1"
    port = 5000
    url = f"http://{host}:{port}/"
    if not open_in_edge(url):
        print(
            f"Microsoft Edge was not found. Open {url} manually in Edge.",
            flush=True,
        )
    _serve_wsgi(app, host=host, port=port)
