@echo off
setlocal EnableExtensions

set "TARGET_URL=%~1"
if not defined TARGET_URL (
  echo [open-edge-url] ERROR: missing URL argument. Microsoft Edge launch requires a URL.
  exit /b 1
)

set "EDGE_EXE="

if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "EDGE_EXE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined EDGE_EXE if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "EDGE_EXE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not defined EDGE_EXE if exist "%LocalAppData%\Microsoft\Edge\Application\msedge.exe" set "EDGE_EXE=%LocalAppData%\Microsoft\Edge\Application\msedge.exe"

if not defined EDGE_EXE (
  for /f "delims=" %%I in ('where msedge.exe 2^>nul') do (
    set "EDGE_EXE=%%~fI"
    goto edge_found
  )
)

:edge_found
if not defined EDGE_EXE (
  echo [open-edge-url] ERROR: Microsoft Edge is required but msedge.exe was not found.
  echo [open-edge-url] ERROR: Checked standard install paths and PATH lookup via "where msedge.exe".
  exit /b 1
)

start "Microsoft Edge" "%EDGE_EXE%" "%TARGET_URL%"
if errorlevel 1 (
  echo [open-edge-url] ERROR: failed to launch Microsoft Edge using "%EDGE_EXE%".
  exit /b 1
)

exit /b 0
