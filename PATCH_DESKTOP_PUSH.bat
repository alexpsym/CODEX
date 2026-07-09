@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Auto Local Commit Push

set "DEFAULT_REPO=C:\GPT\CODEX-master"
set "DEFAULT_LOG=C:\GPT\PATCH_DESKTOP_PUSH-latest.log"

if defined PATCH_DESKTOP_PUSH_REPO (
  set "REPO=%PATCH_DESKTOP_PUSH_REPO%"
) else (
  set "REPO=%DEFAULT_REPO%"
)

if defined PATCH_DESKTOP_PUSH_LOG (
  set "LOG=%PATCH_DESKTOP_PUSH_LOG%"
) else (
  set "LOG=%DEFAULT_LOG%"
)

for %%I in ("%LOG%") do if not exist "%%~dpI" mkdir "%%~dpI" >nul 2>nul

echo.
echo AUTO MODE: committing local changes and pushing origin/master.
echo Repo: %REPO%
echo Log: %LOG%
echo.

call :Main > "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

type "%LOG%"
echo.
echo Log: %LOG%
if "%RC%"=="0" (
  echo COMPLETE.
) else (
  echo FAILED with exit code %RC%.
)
echo.
echo Press any key to close this window.
pause >nul
exit /b %RC%

:Main
setlocal EnableExtensions EnableDelayedExpansion
set "COMMIT_MSG=Auto commit local changes %DATE% %TIME:~0,5%"

echo Log: "%LOG%"
echo Started: %DATE% %TIME%
echo Repo: "%REPO%"
echo.
echo Note: unreachable loose-object warnings are git housekeeping warnings, not push failures.
echo This script does not run git prune automatically.
echo.

cd /d "%REPO%" || (
  echo FAILED: Could not open repo "%REPO%".
  exit /b 1
)

if not exist "%REPO%\.git" (
  echo FAILED: .git was not found at "%REPO%\.git".
  echo This is not a Git checkout. Do not run git init blindly; restore or reopen the moved repo with its original .git folder.
  exit /b 1
)

echo Git version:
call git --version
if errorlevel 1 exit /b 1

echo.
echo Status before staging, including untracked files:
call git status --short --branch
if errorlevel 1 exit /b 1

echo.
echo Untracked files before staging:
call git status --short --untracked-files=all
if errorlevel 1 exit /b 1

echo.
echo Staging all repo changes: new files, modified files, and deletions.
call git add -A -- .
if errorlevel 1 exit /b 1

echo.
echo Excluding generated temp/log/cache/runtime files from the staged commit.
for %%P in (
  ".pytest_cache"
  ".pytest_tmp*"
  ".pytest_*.log"
  "__pycache__"
  "*.pyc"
  "PATCH_DESKTOP_PUSH-latest.log"
  "INSTALL-latest.log"
  ".env"
  ".env.*"
  "*.env"
  "render/data"
  "bybit_monitor/runtime_status.json"
  "bybit_monitor/state.json"
  "oanda_monitor/runtime_status.json"
  "state_backup.json"
) do (
  call git reset -q -- "%%~P" >nul 2>nul
)

echo.
echo Status after staging:
call git status --short --branch
if errorlevel 1 exit /b 1

echo.
echo Staged diff summary:
call git diff --cached --stat
if errorlevel 1 exit /b 1

call git diff --cached --quiet
set "DIFF_RC=!ERRORLEVEL!"
if "!DIFF_RC!"=="0" (
  echo.
  echo Nothing staged after git add -A -- . No new commit will be created.
  goto :SyncAndPush
)
if not "!DIFF_RC!"=="1" (
  echo FAILED: git diff --cached --quiet returned !DIFF_RC!.
  exit /b !DIFF_RC!
)

echo.
echo Committing staged changes...
call git commit -m "!COMMIT_MSG!"
if errorlevel 1 exit /b 1

:SyncAndPush
echo.
echo Pull/rebase origin/master...
call git pull --rebase --autostash origin master
if errorlevel 1 exit /b 1

echo.
echo Push local HEAD to origin/master...
call git push origin HEAD:master
if errorlevel 1 exit /b 1

echo.
echo Fetch origin/master for verification...
call git fetch origin master
if errorlevel 1 exit /b 1

for /f "usebackq delims=" %%H in (`call git rev-parse HEAD`) do set "LOCAL_HEAD=%%H"
if errorlevel 1 exit /b 1
for /f "usebackq delims=" %%H in (`call git rev-parse origin/master`) do set "REMOTE_HEAD=%%H"
if errorlevel 1 exit /b 1

echo Local HEAD:  !LOCAL_HEAD!
echo Origin HEAD: !REMOTE_HEAD!
if not "!LOCAL_HEAD!"=="!REMOTE_HEAD!" (
  echo FAILED: local HEAD does not match origin/master after push.
  exit /b 1
)

echo.
echo Push verified: local HEAD matches origin/master.
exit /b 0
