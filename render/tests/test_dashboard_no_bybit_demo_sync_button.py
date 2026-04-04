from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_recent_trades_has_no_bybit_demo_sync_button() -> None:
    html_template = _read("render/master_service.py")
    dashboard_js = _read("render/static/dashboard.js")

    forbidden_markers = (
        "bybit-demo-sync-btn",
        "Sync Bybit Demo",
        "syncBybitDemoNow",
        "/api/bybit-demo/sync",
    )

    combined = f"{html_template}\n{dashboard_js}"
    for marker in forbidden_markers:
        assert marker not in combined, f"Unexpected marker still present: {marker}"
