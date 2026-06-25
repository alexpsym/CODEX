@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "TARGET=%SCRIPT_DIR%pepperstone_spreads_latest.json"
set "EXPORT_NAME=pepperstone_spreads_latest.json"

if not "%~1"=="" (
  set "MT5_DATA=%~1"
) else (
  set "MT5_DATA=%MT5_DATA_PATH%"
)

if not "%MT5_DATA%"=="" (
  set "SOURCE=%MT5_DATA%\MQL5\Files\%EXPORT_NAME%"
) else (
  set "SOURCE="
  for /f "usebackq delims=" %%F in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$roots=@($env:APPDATA,$env:LOCALAPPDATA)|Where-Object{$_}; $matches=@(); foreach($root in $roots){ $base=Join-Path $root 'MetaQuotes\Terminal'; if(Test-Path -LiteralPath $base){ $matches += Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'MQL5\Files\pepperstone_spreads_latest.json' } | Where-Object { Test-Path -LiteralPath $_ } | Get-Item } }; $matches | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName"`) do (
    set "SOURCE=%%F"
  )
)

echo Pepperstone spread export source: "%SOURCE%"
echo Pepperstone spread import target: "%TARGET%"

if "%SOURCE%"=="" (
  echo Could not auto-detect "%EXPORT_NAME%".
  echo Usage: copy_pepperstone_spreads_latest.bat "C:\Users\...\MetaQuotes\Terminal\TERMINAL_ID"
  echo Or set MT5_DATA_PATH to the MT5 terminal data folder.
  exit /b 2
)

if not exist "%SOURCE%" (
  echo Pepperstone spread export not found: "%SOURCE%"
  echo Expected fallback folder shape: "%%APPDATA%%\MetaQuotes\Terminal\TERMINAL_ID\MQL5\Files\%EXPORT_NAME%"
  exit /b 1
)

copy /Y "%SOURCE%" "%TARGET%" >nul
if errorlevel 1 (
  echo Copy failed.
  exit /b 1
)

echo Copied "%SOURCE%" to "%TARGET%".
