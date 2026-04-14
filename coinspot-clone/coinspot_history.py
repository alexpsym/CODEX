"""Fetch CoinSpot history and save it as CSV files.

Render-safe:
- No input() calls
- Credentials validated lazily (so importing won't crash master_service)
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import zipfile

try:
    from coinspot import ReadOnlyAPIV2  # type: ignore
except Exception:
    ReadOnlyAPIV2 = None  # type: ignore


def _require_coinspot() -> None:
    if ReadOnlyAPIV2 is None:
        raise RuntimeError("The 'coinspot' package is required. Install it with: pip install coinspot")


def _get_credentials(
    api_key: Optional[str] = None, api_secret: Optional[str] = None
) -> Tuple[str, str]:
    key = (api_key or os.getenv("COINSPOT_API_KEY") or "").strip()
    secret = (api_secret or os.getenv("COINSPOT_API_SECRET") or "").strip()
    if not key or not secret:
        raise RuntimeError(
            "Missing CoinSpot credentials. Set COINSPOT_API_KEY and COINSPOT_API_SECRET."
        )
    return key, secret


def _parse_date(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Invalid date format. Use YYYY-MM-DD.") from exc
    return text


def fetch_history(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Collect deposit, withdrawal, order, and transaction history."""
    _require_coinspot()
    key, secret = _get_credentials(api_key, api_secret)
    api = ReadOnlyAPIV2(key, secret)

    # CoinSpot SDK returns dicts -> defensively coerce shapes.
    def _list_from(resp: Any, key_name: str) -> List[Dict[str, Any]]:
        if isinstance(resp, dict):
            value = resp.get(key_name, [])
            return value if isinstance(value, list) else []
        return []

    deposits_resp = api.deposit_history(start_date, end_date)
    withdrawals_resp = api.withdrawal_history(start_date, end_date)
    orders_resp = api.order_history(start_date=start_date, end_date=end_date, limit=500)
    market_resp = api.market_order_history(start_date=start_date, end_date=end_date, limit=500)
    sr_resp = api.send_receive_history(start_date, end_date)

    deposits = _list_from(deposits_resp, "deposits")
    withdrawals = _list_from(withdrawals_resp, "withdrawals")

    buyorders = orders_resp.get("buyorders", []) if isinstance(orders_resp, dict) else []
    sellorders = orders_resp.get("sellorders", []) if isinstance(orders_resp, dict) else []
    orders = (buyorders if isinstance(buyorders, list) else []) + (
        sellorders if isinstance(sellorders, list) else []
    )

    mbuy = market_resp.get("buyorders", []) if isinstance(market_resp, dict) else []
    msell = market_resp.get("sellorders", []) if isinstance(market_resp, dict) else []
    market_orders = (mbuy if isinstance(mbuy, list) else []) + (
        msell if isinstance(msell, list) else []
    )

    sendtx = sr_resp.get("sendtransactions", []) if isinstance(sr_resp, dict) else []
    recvtx = sr_resp.get("receivetransactions", []) if isinstance(sr_resp, dict) else []
    send_receive = (sendtx if isinstance(sendtx, list) else []) + (
        recvtx if isinstance(recvtx, list) else []
    )

    return {
        "deposits": deposits,
        "withdrawals": withdrawals,
        "orders": orders,
        "market_orders": market_orders,
        "send_receive": send_receive,
    }


def write_csvs(history: Dict[str, List[Dict[str, Any]]], output_dir: Path) -> List[Path]:
    """Write each history list to a CSV file inside output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    for name, items in history.items():
        if not items:
            continue

        fieldnames = sorted({k for item in items if isinstance(item, dict) for k in item.keys()})
        out_path = output_dir / f"{name}.csv"
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in items:
                if not isinstance(row, dict):
                    continue
                writer.writerow({fn: row.get(fn, "") for fn in fieldnames})
        written.append(out_path)

    return written


def export_zip(
    start_date: Optional[str],
    end_date: Optional[str],
    *,
    output_path: Path,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Path:
    """Fetch history, write CSVs, zip them to output_path, return output_path."""
    history = fetch_history(start_date, end_date, api_key=api_key, api_secret=api_secret)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_path.parent / f".tmp_coinspot_{output_path.stem}"
    if tmp_dir.exists():
        for p in tmp_dir.glob("*"):
            try:
                p.unlink()
            except Exception:
                pass
    tmp_dir.mkdir(parents=True, exist_ok=True)

    csv_paths = write_csvs(history, tmp_dir)

    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in csv_paths:
            zf.write(p, arcname=p.name)

    # best-effort cleanup
    for p in tmp_dir.glob("*"):
        try:
            p.unlink()
        except Exception:
            pass
    try:
        tmp_dir.rmdir()
    except Exception:
        pass

    return output_path


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export CoinSpot history to CSV/ZIP.")
    p.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    p.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    p.add_argument(
        "--complete",
        action="store_true",
        help="Fetch complete history (ignores start/end).",
    )
    p.add_argument(
        "--out",
        default=".",
        help="Output directory (default: current directory).",
    )
    p.add_argument(
        "--zip-name",
        default="coinspot_history.zip",
        help="ZIP filename (default: coinspot_history.zip).",
    )
    return p.parse_args(argv)


def main() -> None:
    ns = _parse_args(list(os.sys.argv[1:]))

    start = None if ns.complete else _parse_date(ns.start_date)
    end = None if ns.complete else _parse_date(ns.end_date)

    out_dir = Path(ns.out).resolve()
    zip_path = out_dir / ns.zip_name

    export_zip(start, end, output_path=zip_path)
    print(f"Saved {zip_path.name}")


if __name__ == "__main__":
    main()
