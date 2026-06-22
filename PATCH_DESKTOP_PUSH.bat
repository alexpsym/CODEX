@echo off
setlocal EnableExtensions
title Auto Local Commit Push

set "REPO=C:\Users\User\Documents\GPT\CODEX-master"
set "COMMIT_MSG=Auto commit local changes %DATE% %TIME:~0,5%"

echo.
echo AUTO MODE: committing tracked local changes and pushing.
echo Repo: %REPO%
echo.

cd /d "%REPO%" || (
  echo FAILED: Could not open repo.
  pause
  exit /b 1
)

echo Checking status...
git status --short --branch -uno

echo.
echo Staging tracked changes only...
git add -u
if errorlevel 1 goto :Fail

git diff --cached --quiet
if "%ERRORLEVEL%"=="0" (
  echo.
  echo Nothing staged. Pushing current branch only...
  goto :Push
)

echo.
echo Committing...
git commit -m "%COMMIT_MSG%"
if errorlevel 1 goto :Fail

:Push
echo.
echo Pull/rebase...
git pull --rebase --autostash
if errorlevel 1 goto :Fail

echo.
echo Push...
git push
if errorlevel 1 goto :Fail

echo.
echo COMPLETE.
timeout /t 5 /nobreak >nul
exit /b 0

:Fail
echo.
echo FAILED. See output above.
pause
exit /b 1