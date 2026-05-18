from pathlib import Path


def classify_status(status_text: str, allowed_root_generated_files=None):
    allowed_root_generated_files = allowed_root_generated_files or []
    result = {"IsOnlyAllowed": True, "DisallowedLines": []}
    if status_text is None or status_text.strip() == "":
        return result

    lines = [ln for ln in status_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    for line_raw in lines:
        if len(line_raw) < 4:
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)
            continue

        xy = line_raw[:2]
        path_raw = line_raw[3:]

        if path_raw.strip() == "":
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)
            continue

        if xy not in {"??", " M"}:
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)
            continue

        path = path_raw.strip()
        if path.startswith('"') and path.endswith('"') and len(path) >= 2:
            path = path[1:-1]
        p = path.replace("\\", "/")

        if p.strip() == "":
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)
            continue

        is_allowed_generated_root_file = ("/" not in p) and (p in allowed_root_generated_files)
        allowed = (
            p == ".env"
            or p == "env.env"
            or p in {"watchlist.json", "state_manifest.json", "stateManifest.json", "state_backup.json"}
            or p in {"journal", "journal/"}
            or p.startswith("journal/")
            or (p.startswith("bybit_monitor/") and p.endswith(".json"))
            or (p.startswith("oanda_monitor/") and p.endswith(".json"))
            or p in {"render/data", "render/data/"}
            or p.startswith("render/data/")
            or p in {"render/uploads", "render/uploads/"}
            or p.startswith("render/uploads/")
            or is_allowed_generated_root_file
        )

        if "__pycache__" in p or not allowed:
            result["IsOnlyAllowed"] = False
            result["DisallowedLines"].append(line_raw)

    return result


def test_classifier_source_preserves_leading_xy_columns():
    text = Path("ExtractLatestCodexMaster.bat").read_text(encoding="utf-8")
    assert "$lineRaw.Substring(0,2)" in text
    assert "$line = $lineRaw.Trim()" not in text


def test_allows_empty_status():
    assert classify_status("")["IsOnlyAllowed"] is True


def test_allows_required_runtime_entries_including_leading_space_m():
    status = "\n".join(
        [
            "?? bybit_monitor/runtime_status.json",
            "?? oanda_monitor/custom_alerts.json",
            "?? render/uploads/",
            "?? journal/Bybit-UM-USDTPerp-TradeHistory-template.csv",
            " M render/data/monthly_aud_revaluation.json",
            " M render/data/trade_contexts.json",
            ' M "journal/OANDA DEMO.xlsx"',
        ]
    )
    result = classify_status(status)
    assert result["IsOnlyAllowed"] is True


def test_rejects_unsafe_statuses_and_paths():
    cases = [
        " M render/master_service.py",
        "M  render/data/monthly_aud_revaluation.json",
        " D render/data/monthly_aud_revaluation.json",
        "R  old -> new",
        "?? render/master_service.py",
        "?? render/data/__pycache__/x.pyc",
        "??",
        "M",
    ]
    for case in cases:
        result = classify_status(case)
        assert result["IsOnlyAllowed"] is False, case


def test_allowed_generated_root_file_example():
    result = classify_status("?? Local Trading Tools.exe", ["Local Trading Tools.exe"])
    assert result["IsOnlyAllowed"] is True


def test_allows_relocated_state_root_files_only():
    ok = classify_status("\n".join(["?? watchlist.json", " M state_manifest.json", "?? state_backup.json"]))
    assert ok["IsOnlyAllowed"] is True
    bad = classify_status("?? random.json")
    assert bad["IsOnlyAllowed"] is False


def test_preserve_local_files_from_backup_includes_relocated_root_state_files():
    text = Path("ExtractLatestCodexMaster.bat").read_text(encoding="utf-8")
    assert "function Preserve-LocalFilesFromBackup" in text
    for file_name in ("watchlist.json", "state_manifest.json", "stateManifest.json", "state_backup.json"):
        assert file_name in text


def test_invoke_git_text_uses_process_start_info_and_separate_stderr():
    text = Path("ExtractLatestCodexMaster.bat").read_text(encoding="utf-8")
    assert "function Invoke-GitText" in text
    assert "System.Diagnostics.ProcessStartInfo" in text
    assert "$psi.RedirectStandardError = $true" in text
    assert "& $GitExe @Arguments 2>&1" not in text


def test_backup_diagnostics_use_best_effort_helper():
    text = Path("ExtractLatestCodexMaster.bat").read_text(encoding="utf-8")
    assert "function Write-GitDiagnosticFile" in text
    assert "WARNING: Could not write backup diagnostic" in text
    assert "git-status-before-reset.txt" in text
    assert "git-log-local-ahead.txt" in text
    assert "local-changes.patch" in text
    assert "local-staged-changes.patch" in text


def test_classifier_allows_modified_state_backup_file():
    result = classify_status(" M state_backup.json")
    assert result["IsOnlyAllowed"] is True


def test_classifier_rejects_modified_render_master_service():
    result = classify_status(" M render/master_service.py")
    assert result["IsOnlyAllowed"] is False
