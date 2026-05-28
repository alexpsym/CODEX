import shutil
import subprocess
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "calculator.js"
INSTRUMENT_JS_PATH = ROOT / "render" / "static" / "instrument_specs.js"


def test_calculator_payload_uses_spread_state() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "const payload = {" in js
    assert "...state," in js
    assert not re.search(r"(?m)^\\s*\\.state,", js)
    assert "risk_reward: $('calc-rr').value" in js
    assert "take_profit_ticks:" not in js
    assert "target-toggle" not in js
    assert "updateTargetModeUi" not in js
    assert "Estimated total loss in AUD" not in js
    assert "Estimated reward in AUD" not in js
    assert "['Tick size', q.tick_size]" not in js
    assert "['Notional', q.notional]" not in js
    assert "fmtPriceLike(q.target_price, tickSize)" in js
    assert "fmtR(q.effective_rr_net)" in js
    assert "/api/instrument-specs?query=" in js
    assert "calc-instrument-specs" in js
    assert "max-height:340px;overflow:auto" not in js
    assert "Type a symbol to load instrument specs." not in js
    assert "['R:R', fmtR(q.rr)]" not in js
    assert "formatPercentFromFraction" in js
    assert "compactNumber" in js
    assert "AbortController" in js

    assert "quoteStatus" in js
    assert "calc-quote-status" in js
    assert "submitBtn.disabled" in js
    assert "state.quote && state.quoteStatus === 'ready'" in js
    assert "timeframe-toggle" in js
    assert "calc-timeframe" not in js


def test_calculator_js_parses_with_node() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS syntax check"
    subprocess.run([node, "--check", str(JS_PATH)], check=True)


def test_calculator_js_displays_and_copies_webhook_url() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "calc-webhook-url" in js
    assert "calc-webhook-copy-url" in js
    assert "No webhook URL to copy." in js
    assert "Webhook URL copied." in js


def test_instrument_specs_js_parses_with_node() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS syntax check"
    subprocess.run([node, "--check", str(INSTRUMENT_JS_PATH)], check=True)

def test_specs_labels_and_ranges_present() -> None:
    js = JS_PATH.read_text(encoding="utf-8") + INSTRUMENT_JS_PATH.read_text(encoding="utf-8")
    assert "volume24h (USD)" in js
    assert "volume24h (base units)" not in js
    assert "turnover24h (USD)" not in js
    assert "range 1m (%)" in js
    assert "range monthly (%)" in js
    assert "btc-reference-row" in js


def test_calculator_timeframe_display_labels() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "DAILY" in js
    assert "WEEKLY" in js
    assert "MONTHLY" in js
    assert "['1d','1D']" not in js
    assert "['1w','1W']" not in js
    assert "['1mo','1MO']" not in js
    assert "['1d','DAILY']" in js
    assert "['1w','WEEKLY']" in js
    assert "['1mo','MONTHLY']" in js
