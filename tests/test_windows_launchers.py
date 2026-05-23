from pathlib import Path
import json
import re
import shutil
import subprocess
import pytest

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
    ff_helper_idx = script.find('Move-CheckoutBlockingUntrackedFilesBeforeGitUpdate -GitExe $GitExe -RepoDir $RepoDir -BackupDir $ffTempBackupDir')
    ff_publish_idx = script.find('Publish-SingleExtractLatestBackup -DestinationRoot $DestinationRoot -TempBackupPath $ffTempBackupDir')
    ff_checkout_idx = script.find("Invoke-GitCommand -GitExe $GitExe -Arguments @('checkout', $Branch)")
    ff_merge_idx = script.find("Invoke-GitCommand -GitExe $GitExe -Arguments @('merge', '--ff-only', \"origin/$Branch\")")
    ff_restore_idx = script.find('Preserve-LocalFilesFromBackup -BackupDir $ffRestoreRoot -NewRepoDir $RepoDir')
    assert ff_helper_idx != -1
    assert ff_publish_idx != -1
    assert ff_checkout_idx != -1
    assert ff_merge_idx != -1
    assert ff_restore_idx != -1
    assert ff_helper_idx < ff_publish_idx < ff_checkout_idx < ff_merge_idx < ff_restore_idx
    assert 'CODEX-master-backup.tmp-' in script
    assert 'CODEX-master-fastforward-blockers-' not in script
    assert 'Move-Item -LiteralPath $fullPath -Destination $destPath -Force -ErrorAction Stop' in script


def test_extract_latest_fast_forward_restores_preserved_workbook_and_resolves_collision() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    assert 'if ($ffMovedBlockers -gt 0) {' in script
    assert "$ffRestoreRoot = Join-Path $publishedBackupDir 'checkout-blockers'" in script
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
    assert "ExtractLatestCodexMaster-{0}.log" not in script
    assert '$timestampedLogPath' not in script
    assert 'Start-Transcript -LiteralPath $latestLogPath -Force' in script
    assert 'Stop-Transcript' in script
    assert 'Full log written to: $latestLogPath' in script


def test_extract_latest_has_no_invalid_variable_scope_tokens_in_double_quoted_strings() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    double_quoted_strings = re.findall(r'"(?:[^"\\]|\\.)*"', script)
    invalid_hits: list[str] = []
    for s in double_quoted_strings:
        for m in re.finditer(r'\$([A-Za-z_][A-Za-z0-9_]*)\:', s):
            if m.group(1).lower() not in {'env', 'script', 'global', 'local', 'private'}:
                invalid_hits.append(s)
    assert not invalid_hits, f"Invalid $var: token(s) found in double-quoted strings: {invalid_hits}"


def test_extract_latest_embedded_powershell_parses_without_errors() -> None:
    ps_exe = shutil.which('pwsh') or shutil.which('powershell')
    if not ps_exe:
        pytest.skip('PowerShell executable not available in test environment')

    script_path = ROOT / 'ExtractLatestCodexMaster.bat'
    parse_cmd = (
        "$content = Get-Content -LiteralPath '" + str(script_path).replace("'", "''") + "';"
        "$ps = ($content | Select-Object -Skip 7) -join \"`n\";"
        "$tokens = $null; $errors = $null;"
        "[System.Management.Automation.Language.Parser]::ParseInput($ps,[ref]$tokens,[ref]$errors) | Out-Null;"
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
    )
    result = subprocess.run([ps_exe, "-NoProfile", "-Command", parse_cmd], capture_output=True, text=True)
    assert result.returncode == 0, f"PowerShell parse errors:\n{result.stdout}\n{result.stderr}"


def test_extract_latest_static_retention_regressions() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    for forbidden in [
        'CODEX-master-git-backup-',
        'CODEX-master-zip-backup-',
        'CODEX-master-fastforward-blockers-',
        'ExtractLatestCodexMaster-{0}.log',
        '$timestampedLogPath',
    ]:
        assert forbidden not in script

    for required in [
        'CODEX-master-backup',
        'CODEX-master-backup.tmp-',
        'CODEX-master-backup.rollback-',
        'Remove-ExtractLatestLegacyBackupFolders',
        'Remove-ExtractLatestLegacyLogFiles',
        'Publish-SingleExtractLatestBackup',
        'Assert-ExtractLatestRetentionState',
    ]:
        assert required in script


def test_extract_latest_fast_forward_publish_not_only_post_merge() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    ff_start = script.find("if ((-not $needsBackupRecovery) -or ($behindCount -gt 0 -and $aheadCount -eq 0 -and $statusClassification.IsOnlyAllowed)) {")
    assert ff_start != -1
    ff_end = script.find("Write-Host 'Git checkout updated successfully by fast-forward merge.'", ff_start)
    assert ff_end != -1
    ff_block = script[ff_start:ff_end]

    publish_idx = ff_block.find('Publish-SingleExtractLatestBackup -DestinationRoot $DestinationRoot -TempBackupPath $ffTempBackupDir')
    checkout_idx = ff_block.find("Invoke-GitCommand -GitExe $GitExe -Arguments @('checkout', $Branch)")
    merge_idx = ff_block.find("Invoke-GitCommand -GitExe $GitExe -Arguments @('merge', '--ff-only', \"origin/$Branch\")")

    assert publish_idx != -1
    assert checkout_idx != -1
    assert merge_idx != -1
    assert publish_idx < checkout_idx < merge_idx


def test_extract_latest_fast_forward_stage_publish_is_guarded_and_restores_on_failure() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    ff_start = script.find("if ((-not $needsBackupRecovery) -or ($behindCount -gt 0 -and $aheadCount -eq 0 -and $statusClassification.IsOnlyAllowed)) {")
    assert ff_start != -1
    ff_end = script.find("Write-Host 'Git checkout updated successfully by fast-forward merge.'", ff_start)
    assert ff_end != -1
    ff_block = script[ff_start:ff_end]

    assert 'try {' in ff_block
    assert '} catch {' in ff_block
    assert "Move-CheckoutBlockingUntrackedFilesBeforeGitUpdate -GitExe $GitExe -RepoDir $RepoDir -BackupDir $ffTempBackupDir" in ff_block
    assert 'Publish-SingleExtractLatestBackup -DestinationRoot $DestinationRoot -TempBackupPath $ffTempBackupDir' in ff_block
    assert "$ffTempRestoreRoot = Join-Path $ffTempBackupDir 'checkout-blockers'" in ff_block
    assert "$ffPublishedRestoreRoot = Join-Path (Join-Path $DestinationRoot 'CODEX-master-backup') 'checkout-blockers'" in ff_block
    assert 'Preserve-LocalFilesFromBackup -BackupDir $ffTempRestoreRoot -NewRepoDir $RepoDir' in ff_block
    assert 'Preserve-LocalFilesFromBackup -BackupDir $ffPublishedRestoreRoot -NewRepoDir $RepoDir' in ff_block
    assert 'throw' in ff_block

    publish_idx = ff_block.find('Publish-SingleExtractLatestBackup -DestinationRoot $DestinationRoot -TempBackupPath $ffTempBackupDir')
    checkout_idx = ff_block.find("Invoke-GitCommand -GitExe $GitExe -Arguments @('checkout', $Branch)")
    assert publish_idx != -1 and checkout_idx != -1
    assert publish_idx < checkout_idx


def test_extract_latest_publish_backup_is_rollback_safe() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    assert 'CODEX-master-backup.rollback-' in script
    assert 'Move-Item -LiteralPath $finalBackupPath -Destination $rollbackBackupPath -ErrorAction Stop' in script
    assert 'Move-Item -LiteralPath $TempBackupPath -Destination $finalBackupPath -ErrorAction Stop' in script
    assert "if ((Test-Path -LiteralPath $rollbackBackupPath -PathType Container) -and -not (Test-Path -LiteralPath $finalBackupPath))" in script
    assert 'Move-Item -LiteralPath $rollbackBackupPath -Destination $finalBackupPath -ErrorAction Stop' in script
    assert "Remove-Item -LiteralPath $finalBackupPath -Recurse -Force -ErrorAction Stop" not in script


def test_extract_latest_startup_repairs_interrupted_backup_publish_before_cleanup() -> None:
    script = (ROOT / 'ExtractLatestCodexMaster.bat').read_text(encoding='utf-8')
    assert 'function Resolve-ExtractLatestInterruptedBackupPublish' in script
    assert "'CODEX-master-backup.rollback-*'" in script
    assert "'CODEX-master-backup.tmp-*'" in script
    assert "'CODEX-master-backup'" in script
    assert 'Move-Item -LiteralPath $rollbackToPromote.FullName -Destination $finalBackupPath -ErrorAction Stop' in script
    assert 'Interrupted backup publish detected: CODEX-master-backup is missing, rollback backups are missing, and temp backup folder(s) remain' in script

    resolve_call_idx = script.find('Resolve-ExtractLatestInterruptedBackupPublish -DestinationRoot $dest')
    cleanup_call_idx = script.find('Remove-ExtractLatestLegacyBackupFolders -DestinationRoot $dest')
    assert resolve_call_idx != -1
    assert cleanup_call_idx != -1
    assert resolve_call_idx < cleanup_call_idx
