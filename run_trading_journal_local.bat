@echo off
setlocal
set "ROOT=%~dp0"
set "JOURNAL=%ROOT%journal\Trading Journal.xlsx"
if not exist "%JOURNAL%" (
  echo [journal-local] Trading Journal not found: %JOURNAL%
  echo [journal-local] Run Local Trading Tools and click Sync Journal.
  exit /b 1
)
start "" "%JOURNAL%"
echo [journal-local] Opened %JOURNAL%
exit /b 0
