@echo off
setlocal

REM Runs the deploy script located in the same folder as this BAT.
REM Uses the default MT5 data folder set inside the Python script.
REM If you ever need to override target folder, pass it as an argument:
REM   deploy_to_mt5.bat "C:\path\to\Terminal\YOUR_TERMINAL_ID"

set SCRIPT_DIR=%~dp0
set PY=%SCRIPT_DIR%deploy_to_mt5.py

REM Prefer py launcher if available (Windows)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%PY%" %*
  goto :done
)

REM Fallback to python
where python >nul 2>nul
if %errorlevel%==0 (
  python "%PY%" %*
  goto :done
)

echo ERROR: Could not find "py" or "python" on PATH.
echo Install Python 3, or add it to PATH, then try again.
exit /b 1

:done
echo.
pause
endlocal
