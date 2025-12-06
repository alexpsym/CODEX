@echo off
rem Push entries.xlsx to the Excel repository
rem Go to the folder where this script lives
cd /d "%~dp0"

rem Name of the workbook to upload
set "FILE=entries.xlsx"

rem Address of the online repository
set "REPO=https://github.com/alexx1202/Excel"

if not exist "%FILE%" (
  echo %FILE% not found in this folder.
  pause
  exit /b
)

rem Update local copy and send the workbook to GitHub
"git" pull --rebase "%REPO%" master
"git" add "%FILE%"
"git" commit -m "Update %FILE%"
"git" pull --rebase "%REPO%" master
"git" push "%REPO%" master

echo Done.
pause
