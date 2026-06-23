@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "TARGET=%SCRIPT_DIR%pepperstone_spreads_latest.json"

if not "%~1"=="" (
  set "MT5_DATA=%~1"
) else (
  set "MT5_DATA=%MT5_DATA_PATH%"
)

if "%MT5_DATA%"=="" (
  echo Usage: copy_pepperstone_spreads_latest.bat "C:\Users\...\MetaQuotes\Terminal\TERMINAL_ID"
  echo Or set MT5_DATA_PATH to the MT5 terminal data folder first.
  exit /b 2
)

set "SOURCE=%MT5_DATA%\MQL5\Files\pepperstone_spreads_latest.json"
if not exist "%SOURCE%" (
  echo Pepperstone spread export not found: "%SOURCE%"
  exit /b 1
)

copy /Y "%SOURCE%" "%TARGET%" >nul
if errorlevel 1 exit /b 1

echo Copied "%SOURCE%" to "%TARGET%".
