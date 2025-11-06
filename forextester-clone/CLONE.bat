@echo off
setlocal enabledelayedexpansion

:: Check if inside a Git repo
if not exist ".git" (
    echo This folder is not a Git repository.
    pause
    exit /b
)

:: Confirm Git is available
where git >nul 2>&1
if errorlevel 1 (
    echo Git is not installed or not in PATH.
    pause
    exit /b
)

:: Get remote URL
for /f "delims=" %%A in ('git config --get remote.origin.url') do set "remote_url=%%A"

:: Check if URL is empty
if not defined remote_url (
    echo No origin remote found in this Git repo.
    pause
    exit /b
)

:: Extract repo name (e.g., https://github.com/user/repo.git → repo)
for %%I in ("!remote_url!") do set "basename=%%~nI"

:: Set clone destination folder (e.g., repo-clone)
set "clone_folder=%cd%\!basename!-clone"

:: Clone the repo
echo Cloning !remote_url! into !clone_folder!
git clone "!remote_url!" "!clone_folder!"

echo Done.
pause
