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


def test_calculator_js_parses_with_node() -> None:
    node = shutil.which("node")
    assert node, "node is required for JS syntax check"
    subprocess.run([node, "--check", str(JS_PATH)], check=True)
