from __future__ import annotations

from pathlib import Path

from tools import repair_oanda_demo_journal, repair_oanda_journal


def test_repair_snapshot_keeps_period_reports(monkeypatch) -> None:
    expected_stats = {
        "totals": {"trades": 0},
        "period_reports": {"years": {}, "months": {}},
    }
    calls = []

    monkeypatch.setattr(
        repair_oanda_journal.master_service,
        "_build_journal_balance_timelines",
        lambda rows, _cashflows, _balances: {"rows": rows, "balances": []},
    )
    monkeypatch.setattr(
        repair_oanda_journal.master_service,
        "_enrich_trade_row_metrics",
        lambda rows: rows,
    )

    def fake_stats(rows, balances):
        calls.append((rows, balances))
        return expected_stats

    monkeypatch.setattr(
        repair_oanda_journal.master_service,
        "_compute_journal_stats_with_period_reports",
        fake_stats,
    )

    snapshot = repair_oanda_journal._build_snapshot(
        [],
        {},
        account_label="OANDA DEMO",
        canonical_count=0,
    )

    assert snapshot["stats"] == expected_stats
    assert calls == [([], [])]


def test_repair_updates_workbook_in_layout_preservation_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workbook = tmp_path / "Trading Journal.xlsx"
    workbook.write_bytes(b"original")
    csv_path = tmp_path / "oanda_demo.csv"
    csv_path.write_text("placeholder\n", encoding="utf-8")
    journal_state = tmp_path / "trading_journal.json"
    state_backup = tmp_path / "state_backup.json"
    state_backup.write_text("{}", encoding="utf-8")
    row = {
        "id": "oanda_export:demo:652:664",
        "row_type": "trade",
        "source": "oanda_transaction_export",
        "account": "OANDA DEMO",
        "account_label": "OANDA DEMO",
        "symbol": "EURUSD",
        "commission": 0.9608,
        "fees": 0.9608,
        "swap": -0.9608,
        "net_profit": 17.4233,
        "metrics": {
            "oanda_displayed_commission_total": 0.9608,
            "oanda_financing_commission_total": 0.9608,
            "oanda_commission_includes_financing": True,
        },
    }
    captured = {}

    monkeypatch.setattr(
        repair_oanda_journal,
        "read_master_journal_source",
        lambda _path: {"items": [], "cashflow_ledger": {}},
    )
    monkeypatch.setattr(repair_oanda_journal.pd, "read_csv", lambda *_a, **_k: object())
    monkeypatch.setattr(
        repair_oanda_journal.master_service,
        "_journal_rows_from_oanda_transaction_history_frame",
        lambda *_a, **_k: {"rows": [dict(row)], "warnings": []},
    )
    monkeypatch.setattr(
        repair_oanda_journal.master_service,
        "_prepare_oanda_canonical_replacements",
        lambda _existing, canonical: (list(canonical), set()),
    )
    monkeypatch.setattr(
        repair_oanda_journal.master_service,
        "_sanitize_oanda_commission_fields",
        lambda rows, **_kwargs: [dict(item) for item in rows],
    )
    monkeypatch.setattr(
        repair_oanda_journal,
        "_build_snapshot",
        lambda items, _cashflows, **_kwargs: {
            "items": list(items),
            "balances": [],
            "stats": {"period_reports": {"years": {}, "months": {}}},
        },
    )

    def fake_update(path, snapshot, expected_survivor_row_ids=None, **kwargs):
        captured.update(
            {
                "path": path,
                "snapshot": snapshot,
                "expected": expected_survivor_row_ids,
                "kwargs": kwargs,
            }
        )
        candidate = path.with_suffix(".update-candidate.tmp.xlsx")
        candidate.write_bytes(b"repaired")
        return {"ok": True, "candidate_path": str(candidate), "diagnostics": {}}

    monkeypatch.setattr(
        repair_oanda_journal,
        "update_master_journal_workbook_data_only",
        fake_update,
    )

    result = repair_oanda_journal.repair(
        account="demo",
        workbook_path=workbook,
        csv_path=csv_path,
        journal_state_path=journal_state,
        state_backup_path=state_backup,
        update_workbook=True,
    )

    assert result["workbook_update"]["ok"] is True
    assert captured["kwargs"]["preserve_existing_layout"] is True
    assert captured["expected"] == [row["id"]]
    assert workbook.read_bytes() == b"repaired"


def test_legacy_demo_repair_also_requests_layout_preservation() -> None:
    source = Path(repair_oanda_demo_journal.__file__).read_text(encoding="utf-8")
    assert "preserve_existing_layout=True" in source
    assert "_compute_journal_stats_with_period_reports" in source
