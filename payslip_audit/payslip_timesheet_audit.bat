@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import pdfplumber,pytesseract,PIL,reportlab" >nul 2>nul
    if errorlevel 1 (
        echo Missing Python dependency. Run install_requirements.bat first.
        pause
        exit /b 1
    )
    py -3 "payslip_timesheet_audit.py" %*
) else (
    python -c "import pdfplumber,pytesseract,PIL,reportlab" >nul 2>nul
    if errorlevel 1 (
        echo Missing Python dependency. Run install_requirements.bat first.
        pause
        exit /b 1
    )
    python "payslip_timesheet_audit.py" %*
)

set EXIT_CODE=%errorlevel%
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Audit failed with exit code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
