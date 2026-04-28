# Windows launcher executables

These `.exe` files are **launchers only**. They do not replace the existing batch startup logic.

## Build commands

Recommended build command from repo root:

```bat
.\build_windows_launchers.bat
```

PowerShell alternative:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\tools\windows_launchers\build_windows_launchers.ps1"
```

Do not run:

- `ExecutionPolicy Bypass -File tools/windows_launchers/build_windows_launchers.ps1`
- `powershell/powershell.exe Bypass -File tools/windows_launchers/build_windows_launchers.ps1`
- `-powershell/powershell.exe Bypass -File tools/windows_launchers/build_windows_launchers.ps1`

## What gets built

- `.\Local Trading Tools.exe` -> launches `run_local_master_control.bat`
- `.\Trading Journal.exe` -> launches `run_trading_journal_local.bat`

## Build order and fallback behavior

The script builds in this order:

1. `csc.exe` (PATH + Microsoft.NET Framework locations)
2. `Add-Type` C# compilation
3. `iexpress.exe`
4. `.lnk` shortcut fallback as a last resort

## Important

- The `.bat` files remain the source of truth.
- Do not delete `run_local_master_control.bat` or `run_trading_journal_local.bat`.
- The final `.exe` launchers are placed at repo root so the external `ExtractLatestCodexMaster.bat` can verify them.
- The external `ExtractLatestCodexMaster.bat` is outside this repo and should call `.\build_windows_launchers.bat` after extraction.
- If IExpress is used, launcher executables may contain the current repo path and should be rebuilt if the repo folder is moved.
- `.lnk` fallback does **not** count as successful `.exe` generation.
- If both repo-root `.exe` files are not present, the build exits non-zero.
