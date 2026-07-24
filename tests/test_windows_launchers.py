from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _windows_console_safe_creationflags() -> int:
    if os.name != 'nt':
        return 0

    flags = getattr(subprocess, 'DETACHED_PROCESS', 0)
    flags |= getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    if flags == 0:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    return flags


def _installer_script_path() -> Path:
    modern = ROOT / 'INSTALL.bat'
    legacy = ROOT / 'ExtractLatestCodexMaster.bat'
    return modern if modern.exists() else legacy


def _powershell_exe() -> str:
    powershell = shutil.which('powershell.exe') or shutil.which('powershell')
    if not powershell:
        pytest.skip('PowerShell is required for Windows launcher tests')
    return powershell


def _cmd_exe() -> str:
    cmd = shutil.which('cmd.exe')
    if not cmd:
        pytest.skip('cmd.exe is required for Windows BAT smoke test')
    return cmd


def _normal_marker_payload(reason: str, action: str) -> str:
    return json.dumps(
        {
            'reason': reason,
            'timestamp': '2026-07-12T00:00:00Z',
            'server_pid': 12345,
            'requesting_action': action,
        },
        sort_keys=True,
    )


def _run_normal_marker_helper(marker: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell_exe(),
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            str(ROOT / 'tools' / 'windows_launchers' / 'write_local_master_normal_exit_marker.ps1'),
            '-MarkerPath',
            str(marker),
            '-Reason',
            'batch_exit_request',
            '-RequestingAction',
            'batch_post_uvicorn',
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_worker_console_smoke(smoke_dir: Path, normal_marker_text: str | None = None) -> tuple[subprocess.CompletedProcess[str], str, str, Path]:
    cmd_exe = _cmd_exe()
    smoke_dir.mkdir(exist_ok=True)
    worker_log = smoke_dir / 'worker-console-smoke.log'
    exit_request = smoke_dir / 'exit-smoke.flag'
    normal_exit = smoke_dir / 'normal-smoke.flag'
    failed_marker = smoke_dir / 'failed-smoke.flag'
    for path in (worker_log, exit_request, normal_exit, failed_marker):
        if path.exists():
            path.unlink()
    exit_request.write_text('exit requested\n', encoding='utf-8')
    if normal_marker_text is not None:
        normal_exit.write_text(normal_marker_text, encoding='utf-8')

    env = os.environ.copy()
    env.update(
        {
            'SPREAD_MONITOR_SKIP_REQUIREMENTS_INSTALL': '1',
            'PYTHON': cmd_exe,
            'LOCAL_MASTER_WINDOW_TITLE': f'Codex BAT Smoke Test {os.getpid()}',
            'LOCAL_MASTER_SUPPRESS_WINDOW_CLOSE': '1',
            'LOCAL_MASTER_WORKER_LOG': str(worker_log),
            'LOCAL_MASTER_EXIT_REQUEST': str(exit_request),
            'LOCAL_MASTER_NORMAL_EXIT_FILE': str(normal_exit),
            'LOCAL_MASTER_WORKER_FAILED_FILE': str(failed_marker),
        }
    )
    result = subprocess.run(
        [cmd_exe, '/d', '/c', 'call', str(ROOT / 'tools' / 'windows_launchers' / 'local_master_worker_console.bat')],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        creationflags=_windows_console_safe_creationflags(),
        timeout=45,
    )
    combined_output = f"{result.stdout}\n{result.stderr}"
    log_text = worker_log.read_text(encoding='utf-8') if worker_log.exists() else ''
    return result, combined_output, log_text, normal_exit


def _run_streamer_with_fake_worker(tmp_path: Path, worker_lines: list[str], exit_code: int = 1, normal_marker: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd_exe = _cmd_exe()
    fake_root = tmp_path / 'fake-root'
    fake_root.mkdir()
    worker_log = tmp_path / 'stream-worker.log'
    batch = fake_root / 'run_local_master_control.bat'
    batch_lines = ['@echo off']
    for line in worker_lines:
        batch_lines.append(f'echo {line}')
    batch_lines.append(f'exit /b {exit_code}')
    batch.write_text('\n'.join(batch_lines) + '\n', encoding='utf-8')

    env = os.environ.copy()
    env['COMSPEC'] = cmd_exe
    if normal_marker is not None:
        marker_path = tmp_path / 'normal-exit.json'
        marker_path.write_text(normal_marker, encoding='utf-8')
        env['LOCAL_MASTER_NORMAL_EXIT_FILE'] = str(marker_path)
    else:
        env.pop('LOCAL_MASTER_NORMAL_EXIT_FILE', None)

    return subprocess.run(
        [
            _powershell_exe(),
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            str(ROOT / 'tools' / 'windows_launchers' / 'stream_local_master_worker.ps1'),
            '-Root',
            str(fake_root),
            '-WorkerLog',
            str(worker_log),
            '-MasterReadyTimeoutSeconds',
            '2',
            '-ScannerReadyTimeoutSeconds',
            '2',
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )


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
    assert 'set "LOCAL_MASTER_NORMAL_EXIT_FILE=%TEMP%\\LocalTradingToolsExit-%LOCAL_LAUNCH_TS%.normal"' in script
    assert 'set "LOCAL_MASTER_WORKER_FAILED_FILE=%TEMP%\\LocalTradingToolsExit-%LOCAL_LAUNCH_TS%.failed"' in script
    assert 'set "LOCAL_MASTER_WINDOW_TITLE=Local Master Control - %LOCAL_LAUNCH_TS%"' in script
    assert 'set "LOCAL_MASTER_EDGE_DEBUG_PORT=' in script
    assert 'start "%LOCAL_MASTER_WINDOW_TITLE%" /D "%ROOT%" "%ROOT%tools\\windows_launchers\\local_master_worker_console.bat"' in script
    assert '__worker_console' not in script
    assert 'if defined LOCAL_MASTER_WINDOW_TITLE (' in script
    assert 'if /I not "!LOCAL_MASTER_SUPPRESS_WINDOW_CLOSE!"=="1" title !LOCAL_MASTER_WINDOW_TITLE!' in script
    assert 'cmd /d /v:on /c "call ""%~f0"" __worker > ""%LOCAL_MASTER_WORKER_LOG%"" 2>&1"' not in script
    assert 'cmd /d /v:on /k "call ""%~f0""' not in script
    assert 'call "%ROOT%tools\\open_edge_url.bat" "%MASTER_BROWSER_URL%" "%LOCAL_MASTER_EDGE_DEBUG_PORT%" "%LOCAL_MASTER_EDGE_PROFILE_DIR%"' in script
    assert 'if defined LOCAL_MASTER_EXIT_REQUEST (\n  if exist "!LOCAL_MASTER_EXIT_REQUEST!" (' in script
    assert 'goto restart_master' in script
    worker_idx = script.find('start "%LOCAL_MASTER_WINDOW_TITLE%"')
    health_idx = script.find(':wait_for_master_ready')
    open_idx = script.find('call "%ROOT%tools\\open_edge_url.bat"')
    assert worker_idx != -1 and health_idx != -1 and open_idx != -1
    assert worker_idx < health_idx < open_idx
    exit_branch_idx = script.find('if defined LOCAL_MASTER_EXIT_REQUEST (\n  if exist "!LOCAL_MASTER_EXIT_REQUEST!" (')
    restart_idx = script.find('goto restart_master')
    assert exit_branch_idx != -1 and restart_idx != -1 and exit_branch_idx < restart_idx
    assert '\n  exit /b 0\n)' not in script
    assert 'LOCAL_MASTER_SHUTDOWN_PS1=%TEMP%\\local_master_shutdown_!RANDOM!_!RANDOM!.ps1' in script
    assert 'if /I "!LOCAL_MASTER_SUPPRESS_WINDOW_CLOSE!"=="1" (' in script
    assert 'smoke/test mode: not closing shared console window.' in script
    assert 'start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "!LOCAL_MASTER_SHUTDOWN_PS1!"' in script
    assert 'powershell -NoProfile -WindowStyle Hidden -Command' not in script
    assert "$_.MainWindowTitle -eq $title" in script
    assert "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue" in script
    assert "$allow = @^('WindowsTerminal','wt','OpenConsole','conhost','cmd'^)" in script
    assert "Stop-Process -Id $_.Id -Force -ErrorAction Stop" in script
    assert "Stop-Process -Name WindowsTerminal" not in script
    assert "taskkill" not in script.lower()
    assert '\n    exit 0\n  )\n)' in script


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
    assert 'startInfo.RedirectStandardOutput = true;' in launcher
    assert 'startInfo.RedirectStandardError = true;' in launcher
    assert 'MessageBox.Show' in launcher
    assert 'BuildForwardedArgString(args)' in launcher
    assert 'LocalTradingTools-launch-latest.log' in launcher
    assert 'LocalTradingTools-worker-latest.log' in launcher
    assert 'Last useful launch log lines:' in launcher


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
    assert 'function Resolve-LauncherIconPath' in ps1
    tool_icon_idx = ps1.find('$toolIconPath = Join-Path $ScriptDir "TT.ico"')
    root_icon_idx = ps1.find('$rootIconPath = Join-Path $RepoRoot "TT.ico"')
    assert tool_icon_idx != -1
    assert root_icon_idx != -1
    assert tool_icon_idx < root_icon_idx
    assert '$rootLowercaseIconPath = Join-Path $RepoRoot "tradingtools.ico"' in ps1
    assert '$rootTitlecaseIconPath = Join-Path $RepoRoot "TradingTools.ico"' in ps1
    assert 'Resolved launcher icon path: $IconPath' in ps1
    assert 'Required launcher icon was not found. Checked paths:' in ps1
    assert '$checkedPaths | ForEach-Object { " - $_" }' in ps1
    assert '"/win32icon:{0}" -f $Icon' in ps1
    assert '-CompilerOptions $compilerOptions' in ps1
    assert 'IExpress fallback is disabled because it cannot embed the required TT.ico launcher icon.' in ps1
    icon_candidates = (
        ROOT / 'tools' / 'windows_launchers' / 'TT.ico',
        ROOT / 'TT.ico',
        ROOT / 'tradingtools.ico',
        ROOT / 'TradingTools.ico',
    )
    assert any(path.is_file() for path in icon_candidates)


def test_installer_captures_launcher_output_and_disables_nested_pause() -> None:
    installer = _installer_script_path().read_text(encoding='utf-8')
    launcher_batch = (ROOT / 'build_windows_launchers.bat').read_text(encoding='utf-8')

    assert 'function Invoke-CapturedLauncherBuild' in installer
    assert '$psi.RedirectStandardOutput = $true' in installer
    assert '$psi.RedirectStandardError = $true' in installer
    assert '/d /s /c set CODEX_INSTALLER_NONINTERACTIVE=1&& call' in installer
    assert "$psi.EnvironmentVariables['CODEX_INSTALLER_NONINTERACTIVE']" not in installer
    assert 'Builder script path: $BuildScript' in installer
    assert '--- launcher build stdout ---' in installer
    assert '--- launcher build stderr ---' in installer
    assert '$launcherBuildExit = Invoke-CapturedLauncherBuild -BuildScript $buildLaunchersBat -WorkingDirectory $codexDir' in installer
    assert '& $buildLaunchersBat' not in installer
    assert 'if "%RESULT%"=="0" pause' in installer.splitlines()[:7]
    assert 'if not defined CODEX_INSTALLER_NONINTERACTIVE pause' in launcher_batch


def test_run_local_master_parent_logs_are_condensed_and_worker_logs_are_detailed() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    wrapper = (ROOT / 'tools' / 'windows_launchers' / 'local_master_worker_console.bat').read_text(encoding='utf-8')
    preflight = (ROOT / 'tools' / 'windows_launchers' / 'ensure_local_master_server.ps1').read_text(encoding='utf-8')
    assert 'set "LOG_DIR=%ROOT%logs"' in script
    assert 'set "LOCAL_MASTER_WORKER_LOG=%LOG_DIR%\\LocalTradingTools-worker-latest.log"' in script
    assert 'ensure_local_master_server.ps1' not in script
    assert 'stale_master_not_stopped' not in script
    assert 'checking for an existing dashboard server on port 8000' in wrapper
    assert 'ensure_local_master_server.ps1' in wrapper
    assert '-DecisionPath "!LOCAL_MASTER_PREFLIGHT_DECISION!"' in wrapper
    assert wrapper.index('ensure_local_master_server.ps1') < wrapper.index('stream_local_master_worker.ps1')
    assert '/api/local-build-info' in preflight
    assert '/api/local-shutdown' in preflight
    assert '/api/local-exit' in preflight
    assert 'function Test-PortListening' in preflight
    assert '(-not (Test-PortListening))' in preflight
    assert 'netstat.exe -ano -p tcp' in preflight
    assert 'Get-CimInstance Win32_Process' in preflight
    assert 'run_local_master_control.bat' in preflight
    assert '__worker' in preflight
    assert 'curl.exe' in preflight
    assert 'Stop-Process -Id $processId -Force' in preflight
    assert 'existing dashboard server has no build-info endpoint; treating it as stale' in preflight
    assert 'echo [local-master] worker log: %LOCAL_MASTER_WORKER_LOG%' in script
    assert 'waiting for launcher preflight to finish' in script
    assert ':wait_for_launcher_preflight' in script
    assert 'if exist "%LOCAL_MASTER_PREFLIGHT_DECISION%" goto launcher_preflight_ready' in script
    assert 'if /I not "!PREFLIGHT_DECISION!"=="start" goto launcher_preflight_not_ready' in script
    assert 'cmd /d /s /v:on /c ""%~f0" __worker"' not in script
    assert 'timeout /t 1 /nobreak' not in script
    assert 'echo [local-master] launcher starting.' in script
    assert 'set "MASTER_READINESS_URL=http://127.0.0.1:8000/api/startup-readiness"' in script
    assert 'echo [local-master] waiting for complete startup readiness via %MASTER_READINESS_URL% ...' in script
    assert 'Invoke-RestMethod -Uri \'%MASTER_READINESS_URL%\'' in script
    assert ':master_readiness_failed' in script
    assert 'echo [local-master] worker started at !DATE! !TIME!' in script
    assert 'echo [local-master] uvicorn restart generation !LOCAL_MASTER_UVICORN_GENERATION!' in script
    assert 'write_local_master_normal_exit_marker.ps1' in script
    assert 'Check worker startup log: %LOCAL_MASTER_WORKER_LOG%' in script
    parent_idx = script.find('echo [local-master] launcher starting.')
    worker_idx = script.find(':worker')
    assert parent_idx != -1 and worker_idx != -1 and parent_idx < worker_idx


def test_run_local_master_worker_console_stays_visible_on_abnormal_failure() -> None:
    master = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    wrapper = (ROOT / 'tools' / 'windows_launchers' / 'local_master_worker_console.bat')
    script = wrapper.read_text(encoding='utf-8')
    streamer = (ROOT / 'tools' / 'windows_launchers' / 'stream_local_master_worker.ps1').read_text(encoding='utf-8')
    assert wrapper.exists()
    assert 'if /I "%~1"=="__worker_console" goto worker_console' not in master
    assert 'cmd /d /v:on /k "call ""%~f0"" __worker_console"' not in master
    assert ':worker_console' not in master
    assert 'stream_local_master_worker.ps1' in script
    assert 'ensure_local_master_server.ps1' in script
    assert 'smoke/test mode: not changing shared console title.' in script
    assert 'Launcher preflight failed before the dashboard worker started.' in script
    assert 'This window is intentionally left open so the failure stays readable.' in script
    assert 'worker output will print live below and is also being written to:' in script
    assert 'Startup progress follows dashboard health and required core state; background services report separately.' in script
    assert 'call "%ROOT%run_local_master_control.bat" __worker > "%LOCAL_MASTER_WORKER_LOG%" 2>&1' not in script
    assert 'cmd /d /s /v:on /c ""%~f0" __worker"' not in script
    assert 'cmd /d /v:on /k "call ""%~f0""' not in script
    assert 'Controlled replacement by a new launcher completed.' in script
    assert 'Controlled Exit-button shutdown completed.' in script
    assert 'Unexpected runtime worker exit with exit code !WORKER_EXIT_CODE!.' in script
    assert 'Startup failure: worker failed with exit code !WORKER_EXIT_CODE!.' in script
    assert 'Get-Content -LiteralPath $env:LOCAL_MASTER_WORKER_LOG -Tail 40' in script
    assert 'Runtime exit log:' in script
    assert 'Startup error log:' in script
    assert 'runtime exits stay readable.' in script
    assert 'This window is intentionally left open so startup errors stay readable.' in script
    assert 'pause >nul' in script
    assert 'if /I "!LOCAL_MASTER_SUPPRESS_WINDOW_CLOSE!"=="1" exit /b 0' in script
    assert 'if defined LOCAL_MASTER_NORMAL_EXIT_FILE (\n  if exist "!LOCAL_MASTER_NORMAL_EXIT_FILE!" (' in script
    assert 'Write-WorkerLogTail' in streamer
    assert 'RedirectStandardOutput = $false' in streamer
    assert '> "{1}" 2>&1' in streamer
    assert 'Write-StartupProgress -Phase "starting worker process"' in streamer
    assert 'checking dashboard health at $HealthUrl' in streamer
    assert 'Get-Command curl.exe -ErrorAction SilentlyContinue' in streamer
    assert '& $curl.Path -s --noproxy "*" -m 2 $HealthUrl' in streamer
    assert 'Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2' in streamer
    assert '[string] $ReadinessUrl = "http://127.0.0.1:8000/api/startup-readiness"' in streamer
    assert 'Invoke-RestMethod -Uri $ReadinessUrl -TimeoutSec 2' in streamer
    assert 'checking core startup readiness at $ReadinessUrl' in streamer
    assert 'restart detected: dashboard health became unavailable while worker process stayed alive.' in streamer
    assert 'dashboard recovered after worker restart.' in streamer
    assert 'configured autostart targets recovered:' in streamer
    assert 'autostart readiness lost after startup:' in streamer
    assert 'startup progress remains complete' in streamer
    assert 'worker process ended: worker_pid={0} uvicorn_pid=unknown uvicorn_generation={1}' in streamer
    assert 'latest_uvicorn_exit_code={4} worker_exit_code={5}' in streamer
    assert 'process disappeared before clean Uvicorn exit logging for generation {0}' in streamer
    assert 'Select-String -LiteralPath $script:WorkerLogPath -Pattern "uvicorn exited with"' not in streamer
    assert 'startup complete. Live server log remains open below.' in streamer


def test_run_local_master_worker_dead_fail_fast_before_health_timeout() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    wrapper = (ROOT / 'tools' / 'windows_launchers' / 'local_master_worker_console.bat').read_text(encoding='utf-8')
    worker_dead_check = 'if defined LOCAL_MASTER_WORKER_FAILED_FILE (\n  if exist "%LOCAL_MASTER_WORKER_FAILED_FILE%" goto worker_failed_before_ready\n)'
    assert worker_dead_check in script
    assert ':worker_failed_before_ready' in script
    assert 'ERROR: Worker exited before dashboard became ready.' in script
    assert 'Worker exited before dashboard became ready with exit code !WORKER_EXIT_CODE!' in wrapper
    assert script.index(worker_dead_check) < script.index('if !READY_WAITED! GEQ %MASTER_READY_TIMEOUT_SECONDS% goto master_not_ready')
    assert 'Browser was not opened because the worker is no longer running.' in script


def test_batch_normal_exit_marker_fallback_preserves_valid_api_markers(tmp_path: Path) -> None:
    for reason, action in (
        ('exit_button', 'local_exit'),
        ('launcher_preflight', 'local_shutdown'),
    ):
        marker = tmp_path / f'{reason}.json'
        original = _normal_marker_payload(reason, action)
        marker.write_text(original, encoding='utf-8')

        result = _run_normal_marker_helper(marker)

        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(marker.read_text(encoding='utf-8')) == json.loads(original)
        assert 'preserving existing normal-exit marker' in result.stdout
        assert reason in result.stdout


def test_batch_normal_exit_marker_fallback_writes_when_missing(tmp_path: Path) -> None:
    marker = tmp_path / 'missing.json'

    result = _run_normal_marker_helper(marker)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(marker.read_text(encoding='utf-8-sig'))
    assert payload['reason'] == 'batch_exit_request'
    assert payload['requesting_action'] == 'batch_post_uvicorn'
    assert payload['timestamp']
    assert 'wrote fallback normal-exit marker' in result.stdout


@pytest.mark.parametrize('initial_text', ['', '{not valid json'])
def test_batch_normal_exit_marker_fallback_replaces_invalid_marker_with_diagnostic(tmp_path: Path, initial_text: str) -> None:
    marker = tmp_path / 'corrupt.json'
    marker.write_text(initial_text, encoding='utf-8')

    result = _run_normal_marker_helper(marker)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(marker.read_text(encoding='utf-8-sig'))
    assert payload['reason'] == 'batch_exit_request'
    assert payload['requesting_action'] == 'batch_post_uvicorn'
    assert 'invalid existing normal-exit marker' in result.stdout
    assert list(tmp_path.glob('corrupt.json.invalid.*'))


@pytest.mark.parametrize(
    ('reason', 'action', 'expected'),
    [
        ('exit_button', 'local_exit', 'Controlled Exit-button shutdown completed.'),
        ('launcher_preflight', 'local_shutdown', 'Controlled replacement by a new launcher completed.'),
    ],
)
def test_worker_console_preserves_api_marker_until_controlled_classification(
    tmp_path: Path,
    reason: str,
    action: str,
    expected: str,
) -> None:
    result, combined_output, log_text, normal_exit = _run_worker_console_smoke(
        tmp_path,
        _normal_marker_payload(reason, action),
    )

    assert result.returncode == 0, combined_output + '\n' + log_text
    assert expected in combined_output
    assert 'Normal-exit marker:' in combined_output
    assert reason in combined_output
    assert 'Startup failure:' not in combined_output
    assert 'Unexpected runtime worker exit' not in combined_output
    assert 'preserving existing normal-exit marker' in log_text
    assert not normal_exit.exists()


def test_streamer_uses_latest_uvicorn_generation_exit_code(tmp_path: Path) -> None:
    result = _run_streamer_with_fake_worker(
        tmp_path,
        [
            '[local-master] starting uvicorn at old time',
            '[local-master] uvicorn restart generation 1',
            '[local-master] uvicorn exited with -1 at old time',
            '[local-master] starting uvicorn at new time',
            '[local-master] uvicorn restart generation 2',
            '[local-master] uvicorn exited with 7 at new time',
        ],
        exit_code=7,
    )

    assert result.returncode == 7, result.stdout + result.stderr
    assert 'uvicorn_generation=2' in result.stdout
    assert 'latest_uvicorn_exit_logged=True' in result.stdout
    assert 'latest_uvicorn_exit_code=7' in result.stdout
    assert 'latest uvicorn generation 2 exited with 7.' in result.stdout
    assert 'process disappeared before clean Uvicorn exit logging' not in result.stdout


def test_streamer_previous_uvicorn_exit_does_not_satisfy_latest_generation(tmp_path: Path) -> None:
    result = _run_streamer_with_fake_worker(
        tmp_path,
        [
            '[local-master] starting uvicorn at old time',
            '[local-master] uvicorn restart generation 1',
            '[local-master] uvicorn exited with -1 at old time',
            '[local-master] starting uvicorn at new time',
            '[local-master] uvicorn restart generation 2',
            'INFO:     Application startup complete.',
            '[local-master] dashboard recovered after worker restart.',
        ],
        exit_code=1,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert 'uvicorn_generation=2' in result.stdout
    assert 'latest_uvicorn_exit_logged=False' in result.stdout
    assert 'latest_uvicorn_exit_code=none' in result.stdout
    assert 'process disappeared before clean Uvicorn exit logging for generation 2' in result.stdout
    assert 'external/forced termination' in result.stdout


def test_streamer_normal_marker_suppresses_external_termination_diagnosis(tmp_path: Path) -> None:
    result = _run_streamer_with_fake_worker(
        tmp_path,
        [
            '[local-master] starting uvicorn at old time',
            '[local-master] uvicorn restart generation 1',
            '[local-master] uvicorn exited with -1 at old time',
            '[local-master] starting uvicorn at new time',
            '[local-master] uvicorn restart generation 2',
        ],
        exit_code=0,
        normal_marker=_normal_marker_payload('exit_button', 'local_exit'),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'uvicorn_generation=2' in result.stdout
    assert 'normal_marker_exists=True' in result.stdout
    assert 'process disappeared before clean Uvicorn exit logging' not in result.stdout


def test_run_local_master_worker_console_smoke_has_no_cmd_syntax_error() -> None:
    smoke_dir = ROOT / '.pytest_tmp_launcher_smoke'
    result, combined_output, log_text, _normal_exit = _run_worker_console_smoke(smoke_dir)

    assert result.returncode == 0, combined_output + '\n' + log_text
    assert 'The syntax of the command is incorrect.' not in combined_output
    assert 'The syntax of the command is incorrect.' not in log_text
    assert 'smoke/test mode: not changing shared console title.' in combined_output
    assert 'worker started at' in log_text
    assert 'local exit requested; closing worker window.' in log_text
    assert 'smoke/test mode: not closing shared console window.' in log_text
    assert 'closing Local Master Control command prompt.' not in log_text


def test_run_local_master_spread_requirements_are_probed_before_conditional_install() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'if not /I "!SPREAD_MONITOR_SKIP_REQUIREMENTS_INSTALL!"=="1"' not in script
    assert 'if not /I' not in script
    assert 'if /I "!SPREAD_MONITOR_SKIP_REQUIREMENTS_INSTALL!"=="1" (' in script
    assert 'dependency probe and requirements install skipped by SPREAD_MONITOR_SKIP_REQUIREMENTS_INSTALL' in script
    assert 'if exist "!ROOT!spreads-clone\\requirements.txt" (' in script
    assert "required=['flask','openpyxl','requests']+(['MetaTrader5'] if os.name=='nt' else [])" in script
    assert 'importlib.util.find_spec(name)' in script
    assert "Spread Monitor dependency probe result: '+('missing '+', '.join(missing) if missing else 'ready')" in script
    probe = '"!PYTHON_EXE!" -c "!SPREAD_MONITOR_DEPENDENCY_PROBE!"'
    install = '"!PYTHON_EXE!" -m pip install -r "!ROOT!spreads-clone\\requirements.txt"'
    assert script.count(probe) == 2
    assert script.count(install) == 1
    first_probe_idx = script.index(probe)
    install_idx = script.index(install)
    second_probe_idx = script.index(probe, first_probe_idx + len(probe))
    assert first_probe_idx < install_idx < second_probe_idx
    assert 'ERROR: Spread Monitor requirements installation failed.' in script
    assert 'ERROR: Spread Monitor dependencies are still missing after requirements installation.' in script
    assert 'requirements installation and dependency presence verification complete at !DATE! !TIME!.' in script
    assert 'requirements already installed, skipping pip at !DATE! !TIME!.' in script


def test_streamer_uses_core_readiness_as_the_only_startup_completion_gate() -> None:
    streamer = (ROOT / 'tools' / 'windows_launchers' / 'stream_local_master_worker.ps1').read_text(encoding='utf-8')
    runtime = streamer[streamer.index('$startedAt = Get-Date'):]
    completion = (
        'Write-StartupProgress -Phase "dashboard health and core state are ready; browser should open now" '
        '-ElapsedSeconds $elapsed -TotalSeconds $MasterReadyTimeoutSeconds -Complete:$true'
    )

    assert 'function Get-CoreStartupReadiness' in streamer
    assert '$coreReadyProperty = $response.PSObject.Properties["core_ready"]' in streamer
    assert 'Available = $true' in streamer
    assert 'Available = $false' in streamer
    assert completion in runtime
    assert runtime.index('if ($readinessStatus.Ready) {') < runtime.index(completion)
    assert runtime.index(completion) < runtime.index('$autostartStatus = Test-AutostartTargetsReady')
    assert '$scannerReady' not in streamer
    assert 'startup step: waiting for configured autostart targets' not in streamer


def test_streamer_keeps_background_failures_nonblocking_and_reports_recovery() -> None:
    streamer = (ROOT / 'tools' / 'windows_launchers' / 'stream_local_master_worker.ps1').read_text(encoding='utf-8')
    background_function = streamer[
        streamer.index('function Update-BackgroundReadiness'):
        streamer.index('$rootPath = [IO.Path]::GetFullPath($Root)')
    ]

    assert 'background startup warning (nonblocking):' in background_function
    assert 'autostart readiness lost after startup:' in background_function
    assert '(nonblocking; startup progress remains complete).' in background_function
    assert 'background services remain supervised and will continue retrying.' in background_function
    assert 'configured autostart targets recovered:' in background_function
    assert 'Write-StartupProgress' not in background_function
    assert '$coreReady = $false' not in background_function


def test_streamer_uses_server_canonical_autostart_targets_for_background_checks() -> None:
    streamer = (ROOT / 'tools' / 'windows_launchers' / 'stream_local_master_worker.ps1').read_text(encoding='utf-8')

    assert '$response.PSObject.Properties["autostart_targets"]' in streamer
    assert '$script:CanonicalAutostartTargetsKnown = $true' in streamer
    assert 'if ($script:CanonicalAutostartTargetsKnown)' in streamer
    assert '@($script:CanonicalAutostartTargets)' in streamer
    assert '([string] $env:AUTOSTART_EXCLUDE).Split(",")' in streamer
    assert 'Where-Object { $_ -notin $excluded }' in streamer


def test_streamer_dashboard_health_loss_reenters_core_recovery() -> None:
    streamer = (ROOT / 'tools' / 'windows_launchers' / 'stream_local_master_worker.ps1').read_text(encoding='utf-8')
    health_loss_idx = streamer.index('if (-not $dashboardHealthOk) {')
    core_check_idx = streamer.index('if (-not $coreReady) {', health_loss_idx)
    health_loss_branch = streamer[health_loss_idx:core_check_idx]

    assert '$consecutiveHealthFailures += 1' in health_loss_branch
    assert '$consecutiveHealthFailures -lt [Math]::Max(1, $HealthFailureThreshold)' in health_loss_branch
    assert health_loss_branch.index('$consecutiveHealthFailures -lt [Math]::Max(1, $HealthFailureThreshold)') < health_loss_branch.index('$coreReady = $false')
    assert 'restart detected: dashboard health became unavailable while worker process stayed alive.' in health_loss_branch
    assert 'waiting for recovery: dashboard health and core startup readiness will be rechecked.' in health_loss_branch
    assert '$dashboardReady = $false' in health_loss_branch
    assert '$coreReady = $false' in health_loss_branch
    assert '$restartRecoveryActive = $true' in health_loss_branch


def test_streamer_single_health_probe_miss_keeps_completed_progress_sticky() -> None:
    streamer = (ROOT / 'tools' / 'windows_launchers' / 'stream_local_master_worker.ps1').read_text(encoding='utf-8')
    health_loss_idx = streamer.index('if (-not $dashboardHealthOk) {')
    core_check_idx = streamer.index('if (-not $coreReady) {', health_loss_idx)
    health_loss_branch = streamer[health_loss_idx:core_check_idx]
    debounce_branch = health_loss_branch[
        health_loss_branch.index('if ($consecutiveHealthFailures -lt'):
        health_loss_branch.index('if (-not $restartRecoveryActive)')
    ]

    assert 'dashboard health probe missed once; confirming before entering recovery.' in debounce_branch
    assert 'continue' in debounce_branch
    assert '$coreReady = $false' not in debounce_branch
    assert 'dashboard health probe recovered before the recovery threshold.' in health_loss_branch


def test_streamer_transient_readiness_query_failure_does_not_reset_completed_progress() -> None:
    streamer = (ROOT / 'tools' / 'windows_launchers' / 'stream_local_master_worker.ps1').read_text(encoding='utf-8')
    runtime = streamer[streamer.index('$startedAt = Get-Date'):]
    steady_idx = runtime.index('if (($now - $steadyCheckAt).TotalSeconds -ge 5) {')
    steady = runtime[steady_idx:]
    unavailable_idx = steady.index('if (-not $readinessStatus.Available) {')
    explicit_not_ready_idx = steady.index('if (-not $readinessStatus.Ready) {', unavailable_idx)
    unavailable_branch = steady[unavailable_idx:explicit_not_ready_idx]

    assert 'startup readiness query warning after completion:' in unavailable_branch
    assert '(nonblocking; startup progress remains complete).' in unavailable_branch
    assert 'Update-BackgroundReadiness -CoreStatus $null -AutostartStatus $autostartStatus' in unavailable_branch
    assert 'continue' in unavailable_branch
    assert '$coreReady = $false' not in unavailable_branch
    assert 'Write-StartupProgress' not in unavailable_branch

    explicit_not_ready_branch = steady[explicit_not_ready_idx:]
    assert 'core readiness lost after startup:' in explicit_not_ready_branch
    assert '$coreReady = $false' in explicit_not_ready_branch


def test_launcher_logs_are_ignored_by_git() -> None:
    ignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
    assert 'logs/' in ignore
    assert 'fxweekend-clone/*.log' in ignore


def test_trading_tools_launcher_failure_message_includes_log_path_and_tail() -> None:
    launcher = (ROOT / 'tools' / 'windows_launchers' / 'TradingToolsLauncher.cs').read_text(encoding='utf-8')
    assert '"Log: " + launchLogPath' in launcher
    assert 'BuildFailureMessage(exitCode, launchLogPath, logTail.ToString(), workerLogPath)' in launcher
    assert '"\\n\\nWorker log: " + workerLogPath' in launcher
    assert '"Last useful worker log lines:\\n" + workerLogTail' in launcher
    assert 'ReadUsefulLogTail(workerLogPath, 18)' in launcher
    assert '(no worker log output captured)' in launcher
    assert 'private sealed class LogTail' in launcher
    assert 'File.AppendAllText(logPath' in launcher


def test_iexpress_fallback_runs_single_launcher_cmd_invocation() -> None:
    ps1 = (ROOT / 'tools' / 'windows_launchers' / 'build_windows_launchers.ps1').read_text(encoding='utf-8')
    assert 'AppLaunched=cmd /d /c launcher.cmd' in ps1
    assert 'ShowInstallProgramWindow=1' in ps1
