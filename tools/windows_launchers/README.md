# Windows launcher executables

These `.exe` files are **launchers only**. They do not replace the existing batch startup logic.

## What gets built

This creates:

- `dist/windows-launchers/Local Trading Tools.exe` → launches `run_local_master_control.bat`
- `dist/windows-launchers/Trading Journal.exe` → launches `run_trading_journal_local.bat`

## Build commands

Recommended:

```bat
.\build_windows_launchers.bat
```

PowerShell alternative from repo root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\tools\windows_launchers\build_windows_launchers.ps1"
```

Do not run:

- `ExecutionPolicy Bypass -File tools/windows_launchers/build_windows_launchers.ps1`
- `powershell/powershell.exe Bypass -File tools/windows_launchers/build_windows_launchers.ps1`
- `-powershell/powershell.exe Bypass -File tools/windows_launchers/build_windows_launchers.ps1`

If no C# compiler is installed, the script uses IExpress when available. IExpress-built launchers store the current repo path, so rebuild the launchers after moving the repo.

## Important behavior

- The `.bat` files remain the source of truth for startup behavior.
- Do **not** delete the `.bat` files.
- Keep launcher `.exe` files beside the repo, or only copy them where the target `.bat` files are still reachable.
- Do **not** move the `.exe` files away from the repo unless launcher path resolution to the target `.bat` still works.

## Recommended pin-to-Start flow

1. Build the launchers.
2. Optionally create Windows shortcuts to the generated `.exe` files.
3. Pin each shortcut (or each `.exe`) to Start.

## Failure behavior

If a target `.bat` file cannot be found, the launcher prints a clear error and exits non-zero.
