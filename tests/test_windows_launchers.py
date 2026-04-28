from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_DIR = ROOT / "tools" / "windows_launchers"


def test_windows_launcher_files_exist() -> None:
    assert (ROOT / "build_windows_launchers.bat").exists()
    assert (LAUNCHER_DIR / "build_windows_launchers.ps1").exists()
    assert (LAUNCHER_DIR / "TradingToolsLauncher.cs").exists()
    assert (LAUNCHER_DIR / "README.md").exists()


def test_root_bat_invokes_expected_powershell_command() -> None:
    script = (ROOT / "build_windows_launchers.bat").read_text(encoding="utf-8")
    assert "powershell.exe" in script
    assert "-NoProfile" in script
    assert "-ExecutionPolicy Bypass" in script
    assert "tools\\windows_launchers\\build_windows_launchers.ps1" in script


def test_build_script_searches_csc_beyond_path_and_supports_add_type() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert "Get-Command csc.exe" in script
    assert "Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe" in script
    assert "Microsoft.NET\\Framework\\v4.0.30319\\csc.exe" in script
    assert "Microsoft.NET\\Framework64\\*\\csc.exe" in script
    assert "Microsoft.NET\\Framework\\*\\csc.exe" in script
    assert "Add-Type" in script


def test_build_script_defines_expected_launcher_targets() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert "Local Trading Tools.exe" in script
    assert "run_local_master_control.bat" in script
    assert "Trading Journal.exe" in script
    assert "run_trading_journal_local.bat" in script


def test_build_script_validates_required_checkout_and_hardens_iexpress() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert "ERROR: This script must be run from a valid CODEX-master checkout or via build_windows_launchers.bat." in script
    assert "iexpress.exe" in script
    assert "IExpress debug files preserved at:" in script
    assert "--- IExpress stdout ---" in script
    assert "--- IExpress stderr ---" in script


def test_build_script_uses_lnk_fallback_but_requires_exe_for_success() -> None:
    script = (LAUNCHER_DIR / "build_windows_launchers.ps1").read_text(encoding="utf-8")
    assert ".lnk" in script
    assert "Could not create .exe launchers. Created .lnk shortcuts instead." in script
    assert "Build did not produce all required .exe launchers" in script


def test_launcher_source_has_required_runtime_safety_behavior() -> None:
    source = (LAUNCHER_DIR / "TradingToolsLauncher.cs").read_text(encoding="utf-8")
    assert "File.Exists" in source
    assert "WaitForExit" in source
    assert "return exitCode;" in source


def test_launcher_source_does_not_redefine_batch_runtime_profile_logic() -> None:
    source = (LAUNCHER_DIR / "TradingToolsLauncher.cs").read_text(encoding="utf-8")
    assert "APP_PROFILE" not in source
    assert "AUTOSTART_SCRIPTS" not in source


def test_readme_documents_build_order_and_fallback_behavior() -> None:
    readme = (LAUNCHER_DIR / "README.md").read_text(encoding="utf-8")
    assert ".\\build_windows_launchers.bat" in readme
    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \".\\tools\\windows_launchers\\build_windows_launchers.ps1\"" in readme
    assert "1. `csc.exe`" in readme
    assert "2. `Add-Type` C# compilation" in readme
    assert "3. `iexpress.exe`" in readme
    assert "4. `.lnk` shortcut fallback if `.exe` generation fails" in readme
    assert "IExpress-built launchers contain the current repo path" in readme
    assert "does **not** count as successful `.exe` generation" in readme
