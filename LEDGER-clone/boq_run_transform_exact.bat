
@echo off
REM Ensure Python is available in PATH, or activate environment here if needed

echo Running exact-format BOQ CSV transformation script...
cd /d "%~dp0"
python transform_boq_csv_exact.py

echo.
pause
