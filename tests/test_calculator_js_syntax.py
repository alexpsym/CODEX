import shutil
import subprocess
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "render" / "static" / "calculator.js"


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
    assert "AbortController" in js
    assert "timeframe-toggle" in js
    assert "calc-timeframe" not in js


def test_calculator_js_parses_with_node() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS syntax check"
    subprocess.run([node, "--check", str(JS_PATH)], check=True)
