#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "render" / "data" / "trading_journal_view_cache.json"
REPORT_PATH = ROOT / "render" / "data" / "trading_journal_report.html"


def main() -> int:
    if not SNAPSHOT_PATH.exists():
        print(f"Snapshot not found: {SNAPSHOT_PATH}")
        return 1
    payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    stats = payload.get("stats") or {}
    diagnostics = payload.get("diagnostics") or {}
    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Trading Journal Report</title>
<style>body{{font-family:Arial,sans-serif;margin:20px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:6px;font-size:12px}}th{{background:#f5f5f5}}</style>
</head><body>
<h1>Trading Journal Report</h1>
<p>Generated snapshot: {payload.get('generated_at','')}</p>
<p>Total rows: {len(items)}</p>
<h2>Overview</h2>
<pre>{json.dumps((stats or {}).get('groups', {}).get('overview', {}), indent=2)}</pre>
<h2>Diagnostics</h2>
<pre>{json.dumps(diagnostics, indent=2)}</pre>
<h2>Rows</h2>
<table>
<tr><th>Close Time</th><th>Account</th><th>Symbol</th><th>Side</th><th>Net Profit</th><th>Result %</th><th>Balance After</th></tr>
{''.join(f"<tr><td>{r.get('close_time') or r.get('open_time') or ''}</td><td>{r.get('account_label') or r.get('account') or ''}</td><td>{r.get('symbol') or ''}</td><td>{r.get('side') or ''}</td><td>{r.get('net_profit') or ''}</td><td>{r.get('result_pct') or ''}</td><td>{r.get('analysis_balance_after_trade') or r.get('balance_after_trade') or r.get('cashflow_new_balance') or ''}</td></tr>" for r in items)}
</table>
</body></html>"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
