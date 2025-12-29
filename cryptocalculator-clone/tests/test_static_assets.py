import os
import shutil
import subprocess

import pytest


def test_static_js_parses():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to check frontend assets")
    base_dir = os.path.dirname(os.path.dirname(__file__))
    script_path = os.path.join(base_dir, "static", "cryptocalculator.js")
    result = subprocess.run(
        [node, "--check", script_path],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
