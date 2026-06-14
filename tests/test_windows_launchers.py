from pathlib import Path
import json
import re
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _installer_script_path() -> Path:
    modern = ROOT / 'INSTALL.bat'
    legacy = ROOT / 'ExtractLatestCodexMaster.bat'
    return modern if modern.exists() else legacy


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


def test_run_local_master_exit_wiring_and_ordering() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'set "LOCAL_MASTER_EXIT_REQUEST=%TEMP%\\LocalTradingToolsExit-%LOCAL_LAUNCH_TS%.flag"' in script
    assert 'set "LOCAL_MASTER_WINDOW_TITLE=Local Master Control - %LOCAL_LAUNCH_TS%"' in script
    assert 'set "LOCAL_MASTER_EDGE_DEBUG_PORT=' in script
    assert 'start "%LOCAL_MASTER_WINDOW_TITLE%" /D "%ROOT%" cmd /d /v:on /c ""%~f0" __worker"' in script
    assert 'if defined LOCAL_MASTER_WINDOW_TITLE title !LOCAL_MASTER_WINDOW_TITLE!' in script
    assert '/k ""%~f0" __worker"' not in script
    assert 'call "%ROOT%tools\\open_edge_url.bat" "%MASTER_BROWSER_URL%" "%LOCAL_MASTER_EDGE_DEBUG_PORT%" "%LOCAL_MASTER_EDGE_PROFILE_DIR%"' in script
    assert 'if defined LOCAL_MASTER_EXIT_REQUEST if exist "!LOCAL_MASTER_EXIT_REQUEST!" (' in script
    assert 'goto restart_master' in script
    worker_idx = script.find('start "%LOCAL_MASTER_WINDOW_TITLE%"')
    health_idx = script.find(':wait_for_master_ready')
    open_idx = script.find('call "%ROOT%tools\\open_edge_url.bat"')
    assert worker_idx != -1 and health_idx != -1 and open_idx != -1
    assert worker_idx < health_idx < open_idx
    exit_branch_idx = script.find('if defined LOCAL_MASTER_EXIT_REQUEST if exist "!LOCAL_MASTER_EXIT_REQUEST!" (')
    restart_idx = script.find('goto restart_master')
    assert exit_branch_idx != -1 and restart_idx != -1 and exit_branch_idx < restart_idx
    assert '\n  exit /b 0\n)' not in script
    assert 'LOCAL_MASTER_SHUTDOWN_PS1=%TEMP%\\local_master_shutdown_!RANDOM!_!RANDOM!.ps1' in script
    assert 'start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "!LOCAL_MASTER_SHUTDOWN_PS1!"' in script
    assert 'powershell -NoProfile -WindowStyle Hidden -Command' not in script
    assert "$_.MainWindowTitle -eq $title" in script
    assert "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue" in script
    assert "$allow = @^('WindowsTerminal','wt','OpenConsole','conhost','cmd'^)" in script
    assert "Stop-Process -Id $_.Id -Force -ErrorAction Stop" in script
    assert "Stop-Process -Name WindowsTerminal" not in script
    assert '\n  exit 0\n)' in script


def test_open_edge_url_supports_optional_debugging_profile_args() -> None:
    script = (ROOT / 'tools' / 'open_edge_url.bat').read_text(encoding='utf-8')
    assert 'where msedge.exe' in script
    assert '--remote-debugging-port=%DEBUG_PORT%' in script
    assert '--user-data-dir="%PROFILE_DIR%"' in script
    assert 'chrome' not in script.lower()
    assert 'brave' not in script.lower()
    assert 'start "" "%TARGET_URL%"' not in script


def test_run_local_master_migrates_legacy_master_journal_name() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert "LEGACY_JOURNAL=%TRADING_JOURNAL_LOCAL_DIR%\\Master Journal.xlsx" in script
    assert 'move /Y "%LEGACY_JOURNAL%" "%CANONICAL_JOURNAL%"' in script
    assert "ambiguous workbook names found" in script
    assert "Keep only journal\\Trading Journal.xlsx in the journal folder." in script
    assert "Move backups outside journal\\ or rename them so they do not end in .xlsx/.xls/.xlsm." in script


def test_extract_latest_moves_checkout_blocking_untracked_journal_workbooks_before_checkout() -> None:
    script = _installer_script_path().read_text(encoding='utf-8')
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
    script = _installer_script_path().read_text(encoding='utf-8')
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
    script = _installer_script_path().read_text(encoding='utf-8')
    assert 'if ($ffMovedBlockers -gt 0) {' in script
    assert "$ffRestoreRoot = Join-Path $ffBlockerBackupDir 'checkout-blockers'" in script
    assert 'Preserve-LocalFilesFromBackup -BackupDir $ffRestoreRoot -NewRepoDir $RepoDir' in script
    assert 'Resolve-JournalWorkbookCollision -JournalDir $newJournal -Context "backup journal preservation"' in script


def test_extract_latest_git_diagnostic_writes_quiet_files_and_logs_only_summary() -> None:
    script = _installer_script_path().read_text(encoding='utf-8')
    assert 'Invoke-GitText -GitExe $GitExe -Arguments $Arguments -WorkingDirectory $WorkingDirectory -AllowFailure -Quiet' in script
    assert 'Wrote diagnostic: $DestinationPath' in script
    assert "Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('diff', '--binary')" in script
    assert "Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('diff', '--cached', '--binary')" in script


def test_extract_latest_transcript_logging_paths_and_messages_present() -> None:
    script = _installer_script_path().read_text(encoding='utf-8')
    assert "$scriptLogStem = 'INSTALL'" in script
    assert "[IO.Path]::GetFileNameWithoutExtension($env:__BATFILE)" in script
    assert '"{0}-latest.log" -f $scriptLogStem' in script
    assert '"{0}-{1}.log" -f $scriptLogStem' not in script
    assert "ExtractLatestCodexMaster-latest.log" not in script
    assert "ExtractLatestCodexMaster-{0}.log" not in script
    assert 'Start-Transcript -LiteralPath $latestLogPath -Force' in script
    assert 'Stop-Transcript' in script
    assert 'Copy-Item -LiteralPath $timestampedLogPath -Destination $latestLogPath' not in script
    assert 'Full log written to: $latestLogPath' in script


def test_extract_latest_has_no_invalid_variable_scope_tokens_in_double_quoted_strings() -> None:
    script = _installer_script_path().read_text(encoding='utf-8')
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

    script_path = _installer_script_path()
    parse_cmd = (
        "$content = Get-Content -LiteralPath '" + str(script_path).replace("'", "''") + "';"
        "$ps = ($content | Select-Object -Skip 7) -join \"`n\";"
        "$tokens = $null; $errors = $null;"
        "[System.Management.Automation.Language.Parser]::ParseInput($ps,[ref]$tokens,[ref]$errors) | Out-Null;"
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
    )
    result = subprocess.run([ps_exe, "-NoProfile", "-Command", parse_cmd], capture_output=True, text=True)
    assert result.returncode == 0, f"PowerShell parse errors:\n{result.stdout}\n{result.stderr}"


def test_extract_latest_cleanup_helpers_and_calls_present() -> None:
    script = _installer_script_path().read_text(encoding='utf-8')
    assert 'function Remove-OldInstallLogs' in script
    assert 'function Remove-OldVersionedDirectories' in script
    assert 'function Remove-OldCodexBackupDirectories' in script
    assert "CODEX-master-git-backup-" in script
    assert "CODEX-master-zip-backup-" in script
    assert "CODEX-master-fastforward-blockers-" in script
    assert "Remove-OldInstallLogs -DestinationRoot $dest -ScriptLogStem $scriptLogStem -KeepPath $latestLogPath" in script
    assert "Remove-OldVersionedDirectories -DestinationRoot $dest -Prefix 'CODEX-master-fastforward-blockers-'" in script
    assert "Remove-OldVersionedDirectories -DestinationRoot $dest -Prefix 'CODEX-master-fastforward-blockers-' -KeepNewestWhenNoKeepPath" not in script
    assert "Remove-OldCodexBackupDirectories -DestinationRoot $dest" in script
    git_backup_idx = script.find("Write-GitDiagnosticFile -GitExe $GitExe -Arguments @('diff', '--cached', '--binary')")
    git_cleanup_idx = script.find("Remove-OldCodexBackupDirectories -DestinationRoot $DestinationRoot -KeepPath $backupDir")
    assert git_backup_idx != -1 and git_cleanup_idx != -1 and git_backup_idx < git_cleanup_idx
    zip_preserve_idx = script.rfind('Preserve-LocalFilesFromBackup -BackupDir $backupDir -NewRepoDir $RepoDir')
    zip_cleanup_idx = script.rfind("Remove-OldCodexBackupDirectories -DestinationRoot $DestinationRoot -KeepPath $backupDir")
    assert zip_preserve_idx != -1 and zip_cleanup_idx != -1 and zip_preserve_idx < zip_cleanup_idx
    ff_restore_idx = script.find("Preserve-LocalFilesFromBackup -BackupDir $ffRestoreRoot -NewRepoDir $RepoDir")
    ff_delete_idx = script.find("Remove-Item -LiteralPath $ffBlockerBackupDir -Recurse -Force -ErrorAction Stop")
    assert ff_restore_idx != -1 and ff_delete_idx != -1 and ff_restore_idx < ff_delete_idx
    assert "if ($name -ieq 'CODEX-master') { return $false }" in script
    assert 'CODEX-master*' not in script


def test_trading_tools_launcher_runs_hidden_and_shows_clear_errors() -> None:
    launcher = (ROOT / 'tools' / 'windows_launchers' / 'TradingToolsLauncher.cs').read_text(encoding='utf-8')
    assert 'using System.Windows.Forms;' in launcher
    assert 'startInfo.CreateNoWindow = true;' in launcher
    assert 'startInfo.WindowStyle = ProcessWindowStyle.Hidden;' in launcher
    assert 'MessageBox.Show' in launcher
    assert 'BuildForwardedArgString(args)' in launcher


def test_windows_launcher_builder_uses_windows_subsystem_output() -> None:
    ps1 = (ROOT / 'tools' / 'windows_launchers' / 'build_windows_launchers.ps1').read_text(encoding='utf-8')
    assert '/target:winexe' in ps1
    assert '/target:exe' not in ps1
    assert '-OutputType WindowsApplication' in ps1
    assert '-OutputType ConsoleApplication' not in ps1


def test_windows_launcher_builder_references_windows_forms_for_message_box() -> None:
    ps1 = (ROOT / 'tools' / 'windows_launchers' / 'build_windows_launchers.ps1').read_text(encoding='utf-8')
    assert '/reference:System.Windows.Forms.dll' in ps1
    assert "-ReferencedAssemblies @('System.Windows.Forms.dll', 'System.dll')" in ps1


def test_windows_launcher_builder_embeds_repo_icon() -> None:
    ps1 = (ROOT / 'tools' / 'windows_launchers' / 'build_windows_launchers.ps1').read_text(encoding='utf-8')
    assert '$IconPath = Join-Path $ScriptDir "TT.ico"' in ps1
    assert '$requiredPaths = @($LocalMasterBat, $TemplatePath, $IconPath)' in ps1
    assert '"/win32icon:{0}" -f $Icon' in ps1
    assert '-CompilerOptions $compilerOptions' in ps1
    assert 'IExpress fallback is disabled because it cannot embed the required TT.ico launcher icon.' in ps1
    assert (ROOT / 'tools' / 'windows_launchers' / 'TT.ico').is_file()


def test_run_local_master_parent_logs_are_condensed_and_worker_logs_are_detailed() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'echo [local-master] launcher starting.' in script
    assert 'echo [local-master] waiting for %MASTER_HEALTH_URL% ...' in script
    assert 'echo [local-master] worker started at !DATE! !TIME!' in script
    parent_idx = script.find('echo [local-master] launcher starting.')
    worker_idx = script.find(':worker')
    assert parent_idx != -1 and worker_idx != -1 and parent_idx < worker_idx


def test_iexpress_fallback_runs_single_launcher_cmd_invocation() -> None:
    ps1 = (ROOT / 'tools' / 'windows_launchers' / 'build_windows_launchers.ps1').read_text(encoding='utf-8')
    assert 'AppLaunched=cmd /d /c launcher.cmd' in ps1
    assert 'ShowInstallProgramWindow=1' in ps1
