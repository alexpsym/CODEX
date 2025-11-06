@echo off
setlocal

:: Set source (current directory) and destination ("backup" subfolder)
set "source=%cd%"
set "backup=%cd%\backup"

:: Create backup folder if it doesn't exist
if not exist "%backup%" (
    mkdir "%backup%"
)

:: Copy only folders ending in -clone, excluding the "backup" folder itself
for /D %%F in ("%source%\*-clone") do (
    if /I not "%%~nxF"=="backup" (
        robocopy "%%F" "%backup%\%%~nxF" /E /NFL /NDL /NJH /NJS /NC /NS
    )
)

echo Backup complete.
pause
