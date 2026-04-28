from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_DIR = ROOT / "tools" / "windows_launchers"


def test_root_build_bat_exists() -> None:
    assert (ROOT / "build_windows_launchers.bat").exists()


def test_root_build_bat_invokes_expected_powershell_command() -> None:
    script = (ROOT / "build_windows_launchers.bat").read_text(encoding="utf-8")
    assert "powershell.exe" in script
    assert "-NoProfile" in script
    assert "-ExecutionPolicy Bypass" in script
    assert "tools\\windows_launchers\\build_windows_launchers.ps1" in script


def test_build_script_outputs_launchers_to_repo_root() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert 'Join-Path $RepoRoot "Local Trading Tools.exe"' in script
    assert 'Join-Path $RepoRoot "Trading Journal.exe"' in script


def test_build_script_defines_expected_launcher_target_mappings() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert 'ExeName = "Local Trading Tools.exe"' in script
    assert 'TargetBat = "run_local_master_control.bat"' in script
    assert 'ExeName = "Trading Journal.exe"' in script
    assert 'TargetBat = "run_trading_journal_local.bat"' in script


def test_build_script_searches_framework64_and_framework_csc_locations() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert "Get-Command csc.exe" in script
    assert "Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe" in script
    assert "Microsoft.NET\\Framework\\v4.0.30319\\csc.exe" in script
    assert "Microsoft.NET\\Framework64\\*\\csc.exe" in script
    assert "Microsoft.NET\\Framework\\*\\csc.exe" in script


def test_build_script_supports_add_type_and_iexpress_fallbacks() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert "Add-Type" in script
    assert "Trying Add-Type CSharp compilation fallback." in script
    assert "iexpress.exe" in script
    assert "WARNING: No C# compiler found. Falling back to IExpress launcher generation." in script


def test_build_script_uses_safe_temp_names_and_csc_call_operator() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert "-replace '\\\\s+', '_'" not in script
    assert "[^A-Za-z0-9_.-]" in script
    assert "tempOutputPath" in script
    assert "$Target.OutputPath" in script
    assert "& $CompilerPath @compilerArgs" in script


def test_build_script_does_not_use_start_process_for_csc_compilation() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    csc_section = script.split("function Build-WithCsc")[1].split("function Build-WithAddType")[0]
    assert "Start-Process -FilePath $CompilerPath" not in csc_section


def test_build_with_csc_recoverable_failures_do_not_use_write_error() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    csc_section = script.split("function Build-WithCsc")[1].split("function Build-WithAddType")[0]
    assert "Write-Error" not in csc_section


def test_build_script_requires_both_repo_root_exes_for_success() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert "Build did not produce all required .exe launchers at repo root" in script
    assert "Successfully created launcher executables" in script
    assert "Local Trading Tools.exe" in script
    assert "Trading Journal.exe" in script
    assert "dist\\windows-launchers" not in script


def test_build_script_validates_repo_layout() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert "ERROR: This script must be run from a valid CODEX-master checkout or via build_windows_launchers.bat." in script


def test_launcher_source_uses_old_csharp_compatible_patterns() -> None:
    source = (LAUNCHER_DIR / "TradingToolsLauncher.cs").read_text(encoding="utf-8")
    assert '$"' not in source
    assert "string?" not in source
    assert "Process?" not in source
    assert "AppDomain.CurrentDomain.BaseDirectory" in source
    assert "WaitForExit" in source
    assert "process.ExitCode" in source


def test_launcher_source_does_not_duplicate_profile_logic() -> None:
    source = (LAUNCHER_DIR / "TradingToolsLauncher.cs").read_text(encoding="utf-8")
    assert "APP_PROFILE" not in source
    assert "AUTOSTART_SCRIPTS" not in source


def test_readme_documents_repo_root_output_and_external_flow() -> None:
    readme = (LAUNCHER_DIR / "README.md").read_text(encoding="utf-8")
    assert ".\\build_windows_launchers.bat" in readme
    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \".\\tools\\windows_launchers\\build_windows_launchers.ps1\"" in readme
    assert ".\\Local Trading Tools.exe" in readme
    assert ".\\Trading Journal.exe" in readme
    assert "outside this repo" in readme
    assert "ExtractLatestCodexMaster.bat" in readme


def test_run_trading_journal_local_launcher_enforces_local_source_mode() -> None:
    launcher = (ROOT / "run_trading_journal_local.bat").read_text(encoding="utf-8")
    assert 'set "TRADING_JOURNAL_SOURCE=local"' in launcher
    assert 'set "TRADING_JOURNAL_ENABLE_LOCAL_IMPORT=1"' in launcher
    assert 'set "TRADING_JOURNAL_LOCAL_DIR=C:\\Users\\User\\Documents\\TRADING"' in launcher
    assert 'set "DROPBOX_SYNC_ENABLED=0"' in launcher
    assert 'set "LOCAL_STATE_ONLY=1"' in launcher
    assert "MASTER_ENV_PROTECTED_KEYS" in launcher
    assert "TRADING_JOURNAL_SOURCE" in launcher
    assert "DROPBOX_SYNC_ENABLED" in launcher


def test_run_local_master_control_keeps_dropbox_sync_configurable() -> None:
    launcher = (ROOT / "run_local_master_control.bat").read_text(encoding="utf-8")
    assert 'if not defined DROPBOX_SYNC_ENABLED set "DROPBOX_SYNC_ENABLED=1"' in launcher
    assert 'set "DROPBOX_SYNC_ENABLED=0"' not in launcher
