@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "EXIT_CODE=0"
set "DID_PUSHD="

pushd "%SCRIPT_DIR%" >nul 2>&1
if errorlevel 1 (
    echo Failed to change directory to %SCRIPT_DIR%.
    set "EXIT_CODE=1"
    goto :cleanup
)
set "DID_PUSHD=1"

set "PAYSLIP="
for %%f in (*.pdf) do (
    set "PAYSLIP=%%~ff"
    goto :have_payslip
)

echo No payslip PDF found alongside %~nx0.
set "EXIT_CODE=1"
goto :cleanup

:have_payslip
set "TIMESHEET_ARGS="
set "TIMESHEET_COUNT=0"
for %%f in (*.jpg *.jpeg *.png) do (
    set /a TIMESHEET_COUNT+=1 >nul
    set "TIMESHEET_ARGS=!TIMESHEET_ARGS! ^"%%~ff^""
)

if not defined TIMESHEET_ARGS (
    echo No timesheet images (.jpg, .jpeg, .png) found in %SCRIPT_DIR%.
    set "EXIT_CODE=1"
    goto :cleanup
)

if %TIMESHEET_COUNT% LSS 2 (
    echo Expected at least 2 timesheet images but found %TIMESHEET_COUNT%.
    set "EXIT_CODE=1"
    goto :cleanup
)

if %TIMESHEET_COUNT% GTR 4 (
    echo Expected no more than 4 timesheet images but found %TIMESHEET_COUNT%.
    set "EXIT_CODE=1"
    goto :cleanup
)

set "TIMESHEET_ECHO=!TIMESHEET_ARGS!"
set "TIMESHEET_ECHO=!TIMESHEET_ECHO:^"="!"
set "TIMESHEET_ECHO=!TIMESHEET_ECHO:~1!"

echo Running audit with:
echo   Payslip   : %PAYSLIP%
echo   Timesheets: !TIMESHEET_ECHO!
echo.

python "%SCRIPT_DIR%payslip_timesheet_audit.py" --payslip "%PAYSLIP%" --timesheet!TIMESHEET_ARGS! --output "%SCRIPT_DIR%audit_report.pdf"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Audit completed successfully.
) else (
    echo Audit failed with exit code %EXIT_CODE%.
)

:cleanup
if defined DID_PUSHD popd >nul 2>&1
echo.
pause
endlocal & exit /b %EXIT_CODE%
