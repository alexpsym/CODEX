@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "EXIT_CODE=0"
set "DID_PUSHD=0"

pushd "%SCRIPT_DIR%" >nul 2>&1
if errorlevel 1 (
    echo Failed to change directory to %SCRIPT_DIR%.
    set "EXIT_CODE=1"
    goto :pause_exit
)
set "DID_PUSHD=1"

set "PAYSLIP="
for /f "delims=" %%f in ('dir /b /a:-d *.pdf 2^>nul') do (
    if defined PAYSLIP (
        echo More than one payslip PDF was found in %CD%.
        echo Please leave only the single payslip PDF you wish to audit.
        set "EXIT_CODE=1"
        goto :pause_exit
    )
    set "PAYSLIP=%CD%\%%f"
)

if not defined PAYSLIP (
    echo No payslip PDF found alongside %~nx0.
    set "EXIT_CODE=1"
    goto :pause_exit
)

set "TIMESHEET_COUNT=0"
for %%f in (*.jpg *.jpeg *.png) do (
    if exist "%%f" (
        set /a TIMESHEET_COUNT+=1 >nul
        if !TIMESHEET_COUNT! GTR 4 (
            echo Found more than 4 timesheet images in %CD%.
            echo Leave only the 2-4 screenshots that belong to this payslip.
            set "EXIT_CODE=1"
            goto :pause_exit
        )
        set "TIMESHEET_FILE!TIMESHEET_COUNT!=%%~ff"
    )
)

if !TIMESHEET_COUNT! LSS 2 (
    echo Expected between 2 and 4 timesheet images (.jpg/.jpeg/.png) but found !TIMESHEET_COUNT!.
    set "EXIT_CODE=1"
    goto :pause_exit
)

set "TIMESHEET_LIST="
for /l %%i in (1,1,!TIMESHEET_COUNT!) do (
    set "CURRENT=!TIMESHEET_FILE%%i!"
    if defined TIMESHEET_LIST (
        set "TIMESHEET_LIST=!TIMESHEET_LIST! \"!CURRENT!\""
    ) else (
        set "TIMESHEET_LIST=\"!CURRENT!\""
    )
)

echo Running audit with:
echo   Payslip   : %PAYSLIP%
echo   Timesheets:
for /l %%i in (1,1,!TIMESHEET_COUNT!) do (
    echo       %%i: !TIMESHEET_FILE%%i!
)
echo.

call python "%SCRIPT_DIR%payslip_timesheet_audit.py" --payslip "%PAYSLIP%" --timesheet !TIMESHEET_LIST! --output "%SCRIPT_DIR%audit_report.pdf"
set "EXIT_CODE=%ERRORLEVEL%"
echo.

if "%EXIT_CODE%"=="0" (
    echo Audit completed successfully.
) else (
    echo Audit failed with exit code %EXIT_CODE%.
)

:pause_exit
if "%DID_PUSHD%"=="1" popd >nul 2>&1
echo.
echo Press any key to close this window...
pause >nul
endlocal & exit /b %EXIT_CODE%
