@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "EXIT_CODE=0"
set "DID_PUSHD=0"

pushd "%SCRIPT_DIR%" >nul 2>&1
if errorlevel 1 (
    echo Failed to change directory to "%SCRIPT_DIR%".
    set "EXIT_CODE=1"
    goto :finalise
)
set "DID_PUSHD=1"

call :discover_payslip
if errorlevel 1 goto :finalise

call :discover_timesheets
if errorlevel 1 goto :finalise

call :resolve_python
if errorlevel 1 goto :finalise

echo Running audit with:
echo   Payslip   : %PAYSLIP%
echo   Timesheets:
for %%i in (!TIMESHEET_INDEXES!) do (
    echo       %%i: !TIMESHEET_FILE%%i!
)
echo.

echo Launching Python audit...
echo.
"%PYTHON_CMD%" "%SCRIPT_DIR%payslip_timesheet_audit.py" --payslip "%PAYSLIP%" --timesheet !TIMESHEET_ARGS! --output "%SCRIPT_DIR%audit_report.pdf"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo Audit completed successfully.
) else (
    echo Audit failed with exit code %EXIT_CODE%.
)

goto :finalise

:discover_payslip
set "PAYSLIP="
set "PAYSLIP_COUNT=0"
for /f "usebackq delims=" %%f in (`dir /b /a:-d *.pdf 2^>nul`) do (
    if /I "%%f"=="File Not Found" (
        rem Skip the placeholder message when no files are present.
    ) else (
        set /a PAYSLIP_COUNT+=1 >nul
        if !PAYSLIP_COUNT! gtr 1 (
            echo More than one payslip PDF was found in %CD%.
            echo Leave only the single payslip PDF you wish to audit.
            set "EXIT_CODE=1"
            exit /b 1
        )
        set "PAYSLIP=%%~ff"
    )
)
if not defined PAYSLIP (
    echo No payslip PDF found alongside %~nx0.
    set "EXIT_CODE=1"
    exit /b 1
)
exit /b 0

:discover_timesheets
set "TIMESHEET_COUNT=0"
set "TIMESHEET_ARGS="
set "TIMESHEET_INDEXES="
for /f "usebackq delims=" %%f in (`dir /b /a:-d *.jpg *.jpeg *.png 2^>nul`) do (
    if /I "%%f"=="File Not Found" (
        rem Skip the placeholder message when no files are present.
    ) else (
        set /a TIMESHEET_COUNT+=1 >nul
        if !TIMESHEET_COUNT! gtr 4 (
            echo Found more than 4 timesheet images in %CD%.
            echo Leave only the 2-4 screenshots that belong to this payslip.
            set "EXIT_CODE=1"
            exit /b 1
        )
        if not defined TIMESHEET_ARGS (
            set "TIMESHEET_ARGS="%%~ff""
        ) else (
            set "TIMESHEET_ARGS=!TIMESHEET_ARGS! "%%~ff""
        )
        set "TIMESHEET_FILE!TIMESHEET_COUNT!=%%~ff"
        set "TIMESHEET_INDEXES=!TIMESHEET_INDEXES! !TIMESHEET_COUNT!"
    )
)
if !TIMESHEET_COUNT! lss 2 (
    echo Expected between 2 and 4 timesheet images (.jpg/.jpeg/.png) but found !TIMESHEET_COUNT!.
    set "EXIT_CODE=1"
    exit /b 1
)
exit /b 0

:resolve_python
set "PYTHON_CMD="
for %%p in (python.exe python3.exe py.exe) do (
    for /f "usebackq tokens=*" %%q in (`where %%p 2^>nul`) do (
        if not defined PYTHON_CMD set "PYTHON_CMD=%%q"
    )
    if defined PYTHON_CMD goto :found_python
)
echo Python interpreter not found on PATH.
set "EXIT_CODE=1"
exit /b 1
:found_python
exit /b 0

:finalise
if "%DID_PUSHD%"=="1" popd >nul 2>&1
echo.
pause
echo.
endlocal & exit /b %EXIT_CODE%
