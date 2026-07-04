@echo off
setlocal
title Local Transaction Extractor v7 no venv
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=py -3
) else (
    set PYTHON_CMD=python
)

echo Using normal Python command: %PYTHON_CMD%
echo Checking required Python packages...
%PYTHON_CMD% -c "import requests, openpyxl, PIL, pytesseract, numpy; import fitz" >nul 2>nul
if errorlevel 1 (
    echo Some required Python packages are missing.
    echo Installing missing/required packages into your normal user Python, not a virtual environment...
    %PYTHON_CMD% -m pip install --user -r requirements.txt
    if errorlevel 1 goto fail
) else (
    echo Required Python packages found.
)

echo.
%PYTHON_CMD% extract_transactions_local.py
set EXITCODE=%ERRORLEVEL%
echo.
pause
exit /b %EXITCODE%

:fail
echo.
echo FAILED before extraction started.
echo Check that Python is installed and pip is available for the Python command above.
pause
exit /b 1
