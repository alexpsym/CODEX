@echo off
setlocal enabledelayedexpansion

:: Get the current folder name as repo name
for %%I in ("%cd%") do set "reponame=%%~nxI"

:: Confirm GitHub CLI is available
where gh >nul 2>&1
if errorlevel 1 (
    echo GitHub CLI not found. Please install it from https://cli.github.com/
    pause
    exit /b
)

:: Confirm Git is available
where git >nul 2>&1
if errorlevel 1 (
    echo Git not found. Please install Git from https://git-scm.com/
    pause
    exit /b
)

:: Initialize repo
git init
git add .
git commit -m "Initial commit"

:: Create new repo on GitHub
gh repo create "!reponame!" --private --source=. --remote=origin --push

echo Done. GitHub repo created and pushed as "!reponame!".
pause
