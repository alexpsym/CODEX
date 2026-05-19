from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def test_run_local_master_loads_env_before_render_url_check() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'call :load_master_env_vars' in script
    assert script.find('call :load_master_env_vars') < script.find('if defined RENDER_CALCULATOR_BASE_URL')
    assert 'RENDER_CALCULATOR_BASE_URL=missing' in script


def test_run_local_master_uses_external_env_parser_helper() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'parse_master_env.ps1' in script
    assert '-File "%ENV_PARSE_HELPER%"' in script
    assert 'for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_PARSE_OUTPUT%") do (' in script
    assert 'if exist "%ENV_PARSE_OUTPUT%" del /q "%ENV_PARSE_OUTPUT%"' in script


def test_parse_master_env_helper_exists_and_filters_allowed_keys() -> None:
    helper = (ROOT / 'tools' / 'windows_launchers' / 'parse_master_env.ps1').read_text(encoding='utf-8')
    assert "'RENDER_CALCULATOR_BASE_URL'" in helper
    assert "$k -like 'DROPBOX_*' -or $allow -contains $k" in helper
    assert "Set-Content -LiteralPath $OutputPath" in helper


def test_windows_launchers_use_repo_journal_dir_and_preflight() -> None:
    journal = (ROOT / 'run_trading_journal_local.bat').read_text(encoding='utf-8')
    master = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'set "TRADING_JOURNAL_LOCAL_DIR=%ROOT%journal"' in master
    assert 'C:\\Users\\User\\Documents\\TRADING' not in journal
    assert 'Trading Journal.xlsx' in journal
    assert 'Sync Journal' in journal
    assert '[local-master] TRADING_JOURNAL_LOCAL_DIR=%TRADING_JOURNAL_LOCAL_DIR%' in master


def test_run_local_master_requires_master_journal_not_legacy_cashflow_workbook() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'account_cashflows.xlsx' not in script
    assert 'Trading Journal.xlsx' in script
    assert 'required workbook missing: %CANONICAL_JOURNAL%' in script


def test_run_local_master_control_uses_master_journal_source() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'set "TRADING_JOURNAL_SOURCE=master_journal"' in script
    assert 'set "TRADING_JOURNAL_SOURCE=local"' not in script
    assert 'set "TRADING_JOURNAL_MASTER_JOURNAL_AUTHORITATIVE=1"' in script
    assert 'set "TRADING_JOURNAL_ENABLE_LOCAL_IMPORT=0"' in script


def test_run_local_master_control_protects_master_journal_env() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'TRADING_JOURNAL_SOURCE' in script
    assert 'TRADING_JOURNAL_MASTER_JOURNAL_AUTHORITATIVE' in script
    assert 'TRADING_JOURNAL_LOCAL_DIR' in script
    assert 'TRADING_JOURNAL_ENABLE_LOCAL_IMPORT' in script
    assert 'TRADING_JOURNAL_BROKER_REFRESH_ENABLED' in script


def test_run_local_master_control_disables_fill_polls() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'set "ENABLE_BYBIT_FILL_POLL=0"' in script
    assert 'set "ENABLE_OANDA_FILL_POLL=0"' in script


def test_run_local_master_control_protects_fill_poll_env() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'ENABLE_BYBIT_FILL_POLL' in script
    assert 'ENABLE_OANDA_FILL_POLL' in script


def test_launcher_builder_only_targets_local_trading_tools() -> None:
    ps1 = (ROOT / 'tools' / 'windows_launchers' / 'build_windows_launchers.ps1').read_text(encoding='utf-8')
    assert 'Local Trading Tools.exe' in ps1
    assert 'Trading Journal.exe' not in ps1


def test_local_uvicorn_log_config_exists_and_has_timestamped_access() -> None:
    cfg_path = ROOT / 'render' / 'local_uvicorn_log_config.json'
    assert cfg_path.exists()
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    fmt = cfg.get('formatters', {}).get('access', {}).get('fmt', '')
    assert '%(asctime)s' in fmt
    assert '%(client_addr)s' in fmt
    assert '%(request_line)s' in fmt
    assert '%(status_code)s' in fmt


def test_run_local_master_uses_uvicorn_log_config_and_access_log_enabled() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert '--log-config "%ROOT%render\\local_uvicorn_log_config.json"' in script
    assert '--no-access-log' not in script


def test_run_local_master_migrates_legacy_master_journal_name() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert "LEGACY_JOURNAL=%TRADING_JOURNAL_LOCAL_DIR%\\Master Journal.xlsx" in script
    assert 'move /Y "%LEGACY_JOURNAL%" "%CANONICAL_JOURNAL%"' in script
    assert "ambiguous workbook names found" in script
    assert "Keep only journal\\Trading Journal.xlsx in the journal folder." in script
    assert "Move backups outside journal\\ or rename them so they do not end in .xlsx/.xls/.xlsm." in script


def test_extract_latest_moves_checkout_blocking_untracked_journal_workbooks_before_checkout() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    assert 'function Move-CheckoutBlockingUntrackedFilesBeforeGitUpdate' in script
    assert "'journal/Trading Journal.xlsx'" in script
    assert "'journal/Master Journal.xlsx'" in script
    assert "@('ls-files', '--others', '--exclude-standard', '--', $relativePath)" in script
    assert "checkout-blockers" in script
    assert 'Move-Item -LiteralPath $fullPath -Destination $destPath -Force -ErrorAction Stop' in script
    assert 'Moved checkout-blocking untracked file to backup before recovery' in script
    helper_idx = script.find('Move-CheckoutBlockingUntrackedFilesBeforeGitUpdate -GitExe $GitExe -RepoDir $RepoDir -BackupDir $backupDir')
    checkout_idx = script.find("Invoke-GitCommand -GitExe $GitExe -Arguments @('checkout', '-B', $Branch, \"origin/$Branch\")")
    assert helper_idx != -1
    assert checkout_idx != -1
    assert helper_idx < checkout_idx


def test_extract_latest_calls_blocker_helper_before_fast_forward_checkout_merge() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    ff_helper_idx = script.find('Move-CheckoutBlockingUntrackedFilesBeforeGitUpdate -GitExe $GitExe -RepoDir $RepoDir -BackupDir $ffBlockerBackupDir')
    ff_checkout_idx = script.find("Invoke-GitCommand -GitExe $GitExe -Arguments @('checkout', $Branch)")
    ff_merge_idx = script.find("Invoke-GitCommand -GitExe $GitExe -Arguments @('merge', '--ff-only', \"origin/$Branch\")")
    assert ff_helper_idx != -1
    assert ff_checkout_idx != -1
    assert ff_merge_idx != -1
    assert ff_helper_idx < ff_checkout_idx < ff_merge_idx
    assert 'CODEX-master-fastforward-blockers-' in script
    assert 'Move-Item -LiteralPath $fullPath -Destination $destPath -Force -ErrorAction Stop' in script


def test_extract_latest_fast_forward_restores_preserved_workbook_and_resolves_collision() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    assert 'if ($ffMovedBlockers -gt 0) {' in script
    assert "$ffRestoreRoot = Join-Path $ffBlockerBackupDir 'checkout-blockers'" in script
    assert 'Preserve-LocalFilesFromBackup -BackupDir $ffRestoreRoot -NewRepoDir $RepoDir' in script
    assert 'Resolve-JournalWorkbookCollision -JournalDir $newJournal -Context "backup journal preservation"' in script


def test_extract_latest_git_diagnostic_writes_quiet_files_and_logs_only_summary() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    assert 'Invoke-GitText -GitExe $GitExe -Arguments $Arguments -WorkingDirectory $WorkingDirectory -AllowFailure -Quiet' in script
    assert 'Wrote diagnostic: $DestinationPath' in script
    assert "Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('diff', '--binary')" in script
    assert "Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('diff', '--cached', '--binary')" in script


def test_extract_latest_transcript_logging_paths_and_messages_present() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    assert "ExtractLatestCodexMaster-latest.log" in script
    assert "ExtractLatestCodexMaster-{0}.log" in script
    assert 'Start-Transcript -LiteralPath $timestampedLogPath -Force' in script
    assert 'Stop-Transcript' in script
    assert 'Full log written to: $timestampedLogPath' in script
