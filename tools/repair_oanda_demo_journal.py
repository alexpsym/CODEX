from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from render import master_service
from tools.master_journal_workbook import (
    read_master_journal_source,
    update_master_journal_workbook_data_only,
)


def _default_demo_csv_path() -> Path:
    upload_dir = ROOT / "render" / "uploads" / "oanda-history"
    candidates = list(upload_dir.glob("oanda_history_demo_*.csv"))
    if not candidates:
        return upload_dir / "oanda_history_demo.csv"
    return max(
        candidates,
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )


def _build_snapshot(
    items: List[Dict[str, object]],
    cashflow_ledger: Dict[str, object],
) -> Dict[str, object]:
    trade_items = [
        master_service._backfill_trade_row_context_fields(dict(row))
        for row in items
        if master_service._row_type(row) == "trade"
    ]
    non_trade_items = [
        dict(row) for row in items if master_service._row_type(row) != "trade"
    ]
    trade_items = master_service._enrich_trade_row_metrics(trade_items)
    timeline = master_service._build_journal_balance_timelines(
        trade_items,
        cashflow_ledger,
        [],
    )
    timeline_rows = (
        timeline.get("rows")
        if isinstance(timeline.get("rows"), list)
        else trade_items
    )
    trade_items = master_service._enrich_trade_row_metrics(
        [
            master_service._backfill_trade_row_context_fields(dict(row))
            for row in timeline_rows
            if isinstance(row, dict)
        ]
    )
    snapshot_items = sorted(
        [*trade_items, *non_trade_items],
        key=master_service._row_sort_dt,
        reverse=True,
    )
    balances = (
        timeline.get("balances")
        if isinstance(timeline.get("balances"), list)
        else []
    )
    return {
        "generated_at": master_service._utc_now_iso(),
        "items": snapshot_items,
        "balances": balances,
        "stats": master_service._compute_journal_stats_with_period_reports(
            snapshot_items,
            balances,
        ),
        "diagnostics": {
            "source": "oanda_demo_transaction_export_repair",
            "canonical_oanda_rows": 0,
        },
    }


def repair(
    *,
    workbook_path: Path,
    csv_path: Path,
    journal_state_path: Path,
    state_backup_path: Path,
    update_workbook: bool = True,
) -> Dict[str, object]:
    source = read_master_journal_source(workbook_path)
    existing = [
        dict(row)
        for row in source.get("items") or []
        if isinstance(row, dict)
    ]
    parsed = master_service._journal_rows_from_oanda_transaction_history_frame(
        pd.read_csv(csv_path, encoding="utf-8-sig"),
        account_mode="demo",
        account_label="OANDA DEMO",
        source_path=str(csv_path),
    )
    canonical = [
        dict(row)
        for row in parsed.get("rows") or []
        if isinstance(row, dict)
    ]
    prepared, stale_ids = master_service._prepare_oanda_canonical_replacements(
        existing,
        canonical,
    )
    existing = master_service._sanitize_oanda_demo_commission_fields(
        existing,
        raw_export_rows=prepared,
    )

    by_id: Dict[str, Dict[str, object]] = {
        str(row.get("id") or "").strip(): row
        for row in existing
        if str(row.get("id") or "").strip()
        and str(row.get("id") or "").strip() not in stale_ids
    }
    for row in prepared:
        row_id = str(row.get("id") or "").strip()
        if not row_id:
            continue
        if row_id in by_id:
            by_id[row_id] = master_service._merge_trading_journal_row(
                by_id[row_id],
                row,
            )
        else:
            by_id[row_id] = row

    repaired = master_service._sanitize_oanda_demo_commission_fields(
        by_id.values(),
        raw_export_rows=prepared,
    )
    repaired_by_id = {
        str(row.get("id") or "").strip(): row
        for row in repaired
        if str(row.get("id") or "").strip()
    }
    expected_ids = sorted(repaired_by_id)
    required_ids = {
        "oanda_export:demo:589:594",
        "oanda_export:demo:598:601",
        "oanda_export:demo:604:608",
        "oanda_export:demo:612:615",
        "oanda_export:demo:618:622",
        "oanda_export:demo:626:630",
    }
    missing_required = sorted(required_ids - set(repaired_by_id))
    if missing_required:
        raise RuntimeError(f"Missing required canonical rows: {missing_required}")

    demo_rows = [
        row
        for row in repaired
        if master_service._canonical_oanda_account_label(row) == "OANDA DEMO"
        and master_service._row_type(row) == "trade"
    ]
    commission_total_mismatches = []
    for row in demo_rows:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        expected = master_service._to_float(
            metrics.get("oanda_displayed_commission_total")
        ) or 0.0
        actual = master_service._to_float(row.get("commission")) or 0.0
        fees = master_service._to_float(row.get("fees")) or 0.0
        broker_fees = master_service._to_float(
            metrics.get("oanda_actual_commission_total")
        ) or 0.0
        financing = master_service._to_float(
            metrics.get("oanda_financing_commission_total")
        ) or 0.0
        component_total = abs(broker_fees) + abs(financing)
        includes_financing = bool(
            metrics.get("oanda_commission_includes_financing")
        )
        if (
            abs(actual - expected) > 1e-12
            or abs(fees - expected) > 1e-12
            or abs(expected - component_total) > 1e-12
            or includes_financing != (abs(financing) > 1e-12)
        ):
            commission_total_mismatches.append(str(row.get("id") or ""))
    if commission_total_mismatches:
        raise RuntimeError(
            "Invalid OANDA DEMO displayed Commission totals remain: "
            f"{commission_total_mismatches[:10]}"
        )

    update_result: Dict[str, object] = {"ok": True, "skipped": True}
    if update_workbook:
        snapshot = _build_snapshot(
            repaired,
            source.get("cashflow_ledger")
            if isinstance(source.get("cashflow_ledger"), dict)
            else {},
        )
        snapshot["diagnostics"]["canonical_oanda_rows"] = len(canonical)
        update_result = update_master_journal_workbook_data_only(
            workbook_path,
            snapshot,
            expected_survivor_row_ids=expected_ids,
            preserve_existing_layout=True,
        )
        candidate_path = Path(str(update_result.get("candidate_path") or ""))
        if not candidate_path.exists():
            raise RuntimeError(
                f"Workbook update candidate was not created: {candidate_path}"
            )
        candidate_path.replace(workbook_path)

    state_payload = {
        "items": sorted(repaired, key=master_service._row_sort_dt, reverse=True),
        "updated_at": master_service._utc_now_iso(),
    }
    journal_state_path.parent.mkdir(parents=True, exist_ok=True)
    master_service.write_json_file(
        journal_state_path,
        state_payload,
        sort_keys=False,
        ensure_ascii=False,
    )

    backup = json.loads(state_backup_path.read_text(encoding="utf-8"))
    backup["trading_journal"] = state_payload
    backup["savedAt"] = datetime.now(timezone.utc).isoformat()
    state_backup_path.write_text(
        json.dumps(backup, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "rows_before": len(existing),
        "rows_after": len(repaired),
        "canonical_rows": len(canonical),
        "stale_ids_removed": sorted(stale_ids),
        "oanda_demo_rows_after": len(demo_rows),
        "oanda_demo_nonzero_commissions_after": sum(
            1
            for row in demo_rows
            if abs(master_service._to_float(row.get("commission")) or 0.0) > 1e-12
        ),
        "oanda_demo_commission_total_mismatches_after": len(commission_total_mismatches),
        "warnings": parsed.get("warnings") or [],
        "workbook_update": update_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("journal/Trading Journal.xlsx"),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=_default_demo_csv_path(),
    )
    parser.add_argument(
        "--journal-state",
        type=Path,
        default=Path("render/data/trading_journal.json"),
    )
    parser.add_argument(
        "--state-backup",
        type=Path,
        default=Path("state_backup.json"),
    )
    parser.add_argument(
        "--state-only",
        action="store_true",
        help="Repair persisted JSON state without rewriting the workbook.",
    )
    args = parser.parse_args()
    result = repair(
        workbook_path=args.workbook,
        csv_path=args.csv,
        journal_state_path=args.journal_state,
        state_backup_path=args.state_backup,
        update_workbook=not args.state_only,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
