from flask import Flask, request, render_template_string, make_response
import json
import subprocess
from pathlib import Path
from shutil import which
from typing import Iterable, Optional
import webbrowser


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

FORM_HTML = """
<!doctype html>
<html>
<head>
<title>OANDA Position Size Calculator</title>
<style>
  body {background:black; color:white; font-family:Arial, sans-serif;}
  input, select, button {margin:4px 0;}
</style>
<script>
function copy(text){navigator.clipboard.writeText(text);}
function copyFromId(id){
  const el = document.getElementById(id);
  if(el){copy(el.innerText);} 
}
function toggleRisk(v){
  document.getElementById('risk_percent').style.display = v=='percent'?'block':'none';
  document.getElementById('risk_amount').style.display = v=='amount'?'block':'none';
}
</script>
</head>
<body>
<h1>OANDA Position Size Calculator</h1>
<form method="post">
  <label>Instrument:
    <input name="instrument" value="{{ instrument_input or '' }}" required>
  </label><br>
  <label>Side:
    <select name="side">
      <option value="buy">Buy</option>
      <option value="sell">Sell</option>
    </select>
  </label><br>
  <label>Stop loss (ticks): <input name="stop_ticks" type="number" step="1" required></label><br>
  <label>Risk mode:
    <select name="risk_mode" onchange="toggleRisk(this.value)">
      <option value="percent">Percent</option>
      <option value="amount">Fixed Amount</option>
    </select>
  </label><br>
  <div id="risk_percent">
    <label>Risk %: <input name="risk_pct" type="number" step="0.01"></label><br>
  </div>
  <div id="risk_amount" style="display:none">
    <label>Risk amount AUD: <input name="risk_aud" type="number" step="0.01"></label><br>
  </div>
  <label>Risk–reward ratio: <input name="rr_ratio" type="number" step="0.1" value="2" required></label><br>
  <button type="submit">Calculate</button><br>
  <button type="button" onclick="copy('https://app.signalstack.com/hook/kiwPq16apN3xpy5eMPDovH')">Copy Webhook</button><br>
  {% if alert_json %}
    <button type="button" onclick="copyFromId('alert_json')">Copy JSON</button>
  {% else %}
    <button type="button" disabled>Copy JSON</button>
  {% endif %}
  <button type="button" onclick="window.location='/download_specs'">Download Specs</button>
</form>
{% if error %}<p style="color: red;">{{ error }}</p>{% endif %}
{% if alert_json %}<h2>Result</h2><pre id="alert_json">{{ alert_json }}</pre>{% endif %}
{% if risk_info %}
<h2>Position Details</h2>
<table border="1">
  <tr><th>Item</th><th>Value</th></tr>
  {% for key, value in risk_info.items() %}
  <tr><td>{{ key }}</td><td>{{ value }}</td></tr>
  {% endfor %}
</table>
{% endif %}
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
    last_trade_specs = None
    if request.method == "POST":
        try:
            instrument_input = request.form["instrument"]
            try:
                available_instruments = sorted(get_available_instruments())
            except Exception:
                available_instruments = None
            instrument = _resolve_instrument(instrument_input, available_instruments)
            instrument_input = instrument
            side = request.form["side"]
            stop_ticks = float(request.form["stop_ticks"])
            risk_mode = request.form["risk_mode"]
            rr_ratio = float(request.form["rr_ratio"])

            account = get_account_details()
            balance = float(account["account"]["balance"])
            margin_available = float(account["account"].get("marginAvailable", balance))
            account_currency = account["account"].get("currency", "AUD")

            details = get_instrument_details(instrument)
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

            price = get_price(instrument)

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
                    conversion_rate = get_price(pair)
                    risk_amount_quote = risk_amount * conversion_rate
                except Exception:
                    pair = f"{quote_currency}_{account_currency}"
                    conversion_rate = get_price(pair)
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
                units = risk_amount * price / (stop_ticks * tick_size)

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
                risk_loss_value = units * sl_distance / price
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
                    units = max_loss * price / sl_distance
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
                    risk_loss_value = units * sl_distance / price

            tp_distance = sl_distance * rr_ratio
            if treated_as_cfd:
                tp_value = units * tp_distance
                if quote_currency != account_currency:
                    if conversion_inverse:
                        tp_value = tp_value * conversion_rate
                    else:
                        tp_value = tp_value / conversion_rate
            else:
                tp_value = units * tp_distance / price

            sl_distance_str = _format_price_distance(sl_distance, display_precision)
            tp_distance_str = _format_price_distance(tp_distance, display_precision)

            required_margin = units * price * margin_rate
            if quote_currency != account_currency:
                pair = f"{account_currency}_{quote_currency}"
                try:
                    conversion_rate = get_price(pair)
                    required_margin = required_margin / conversion_rate
                except Exception:
                    pair = f"{quote_currency}_{account_currency}"
                    conversion_rate = get_price(pair)
                    required_margin = required_margin * conversion_rate
            if required_margin > margin_available:
                raise ValueError(
                    f"Not enough margin. Required {required_margin:.2f} {account_currency}, "
                    f"available {margin_available:.2f} {account_currency}"
                )

            if side.lower() == "buy":
                sl_price = price - sl_distance
                tp_price = price + tp_distance
                alert_qty = round(units, units_precision)
                alert = {
                    "symbol": instrument,
                    "action": side,
                    "quantity": alert_qty,
                    "take_profit_price": f"{{{{close}}}} + {tp_distance_str}",
                    "stop_loss_price": f"{{{{close}}}} - {sl_distance_str}",
                }
            else:
                sl_price = price + sl_distance
                tp_price = price - tp_distance
                alert_qty = round(units, units_precision)
                alert = {
                    "symbol": instrument,
                    "action": side,
                    "quantity": alert_qty,
                    "take_profit_price": f"{{{{close}}}} - {tp_distance_str}",
                    "stop_loss_price": f"{{{{close}}}} + {sl_distance_str}",
                }

            order = build_order(instrument, side, units, sl_price, tp_price, units_precision)
            result = json.dumps(order, indent=2)
            alert_json = json.dumps(alert, indent=2)
            risk_info = {
                "Instrument": instrument,
                "Side": side,
                "Price": f"{price:.5f}",
                "Units": str(units),
                f"Specified Risk {account_currency}": f"{risk_amount:.2f}",
                f"Actual Risk {account_currency}": f"{risk_loss_value:.2f}",
                "Stop Loss Price": f"{sl_price:.5f}",
                "Take Profit Price": f"{tp_price:.5f}",
                f"Potential Profit {account_currency}": f"{tp_value:.2f}",
                f"Required Margin {account_currency}": f"{required_margin:.2f}",
                f"Margin Available {account_currency}": f"{margin_available:.2f}",
            }

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
    host = "127.0.0.1"
    port = 5000
    _open_edge(f"http://{host}:{port}/")
    _serve_wsgi(app, host=host, port=port)
