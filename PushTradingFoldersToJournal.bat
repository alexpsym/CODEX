@echo off
setlocal EnableExtensions

set "FOREX_SRC=C:\Users\User\Documents\TRADING\FOREX"
set "CRYPTO_SRC=C:\Users\User\Documents\TRADING\CRYPTO"

set "FOREX_DEST=journal\Forex"
set "CRYPTO_DEST=journal\Crypto"

set "COMMIT_MSG=Add Forex and Crypto journal folders"

cd /d "%~dp0" || (
  echo ERROR: Could not enter script directory.
  pause
  exit /b 1
)

if not exist ".git" (
  echo ERROR: This script must be run from the repo root containing .git
  echo Current folder: %CD%
  pause
  exit /b 1
)

where git >nul 2>&1 || (
  echo ERROR: git is not installed or not on PATH.
  pause
  exit /b 1
)

where robocopy >nul 2>&1 || (
  echo ERROR: robocopy is not available.
  pause
  exit /b 1
)

if not exist "%FOREX_SRC%" (
  echo ERROR: Forex source folder not found:
  echo %FOREX_SRC%
  pause
  exit /b 1
)

if not exist "%CRYPTO_SRC%" (
  echo ERROR: Crypto source folder not found:
  echo %CRYPTO_SRC%
  pause
  exit /b 1
)

for %%Y in (2018 2019 2020 2021 2022 2023 2024 2025) do (
  if not exist "%FOREX_SRC%\%%Y" (
    echo ERROR: Missing Forex source folder: %FOREX_SRC%\%%Y
    pause
    exit /b 1
  )
)

for %%Y in (2020 2021 2022 2023 2024 2025) do (
  if not exist "%CRYPTO_SRC%\%%Y" (
    echo ERROR: Missing Crypto source folder: %CRYPTO_SRC%\%%Y
    pause
    exit /b 1
  )
)

for /f "delims=" %%B in ('git branch --show-current') do set "BRANCH=%%B"

if "%BRANCH%"=="" (
  echo ERROR: Could not detect current Git branch. Are you in detached HEAD?
  pause
  exit /b 1
)

echo Pulling latest changes from origin/%BRANCH%...
git pull --rebase --autostash origin "%BRANCH%"
if errorlevel 1 (
  echo ERROR: git pull failed. Resolve the issue before pushing.
  pause
  exit /b 1
)

if not exist "journal" mkdir "journal"

if not exist "%FOREX_DEST%" mkdir "%FOREX_DEST%"
if not exist "%CRYPTO_DEST%" mkdir "%CRYPTO_DEST%"

echo.
echo Copying Forex year folders into %FOREX_DEST%...

for %%Y in (2018 2019 2020 2021 2022 2023 2024 2025) do (
  echo Copying Forex %%Y...
  robocopy "%FOREX_SRC%\%%Y" "%FOREX_DEST%\%%Y" /E /R:2 /W:2 /NFL /NDL /NP
  if errorlevel 8 (
    echo ERROR: robocopy failed for Forex %%Y.
    pause
    exit /b 1
  )
)

echo.
echo Copying Crypto year folders into %CRYPTO_DEST%...

for %%Y in (2020 2021 2022 2023 2024 2025) do (
  echo Copying Crypto %%Y...
  robocopy "%CRYPTO_SRC%\%%Y" "%CRYPTO_DEST%\%%Y" /E /R:2 /W:2 /NFL /NDL /NP
  if errorlevel 8 (
    echo ERROR: robocopy failed for Crypto %%Y.
    pause
    exit /b 1
  )
)

echo.
echo Staging copied folders...
git add -- "%FOREX_DEST%" "%CRYPTO_DEST%"

git diff --cached --quiet
if not errorlevel 1 (
  echo No changes to commit. The GitHub repo may already be up to date.
  pause
  exit /b 0
)

echo.
echo Committing changes...
git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
  echo ERROR: git commit failed.
  pause
  exit /b 1
)

echo.
echo Pushing to GitHub origin/%BRANCH%...
git push origin "%BRANCH%"
if errorlevel 1 (
  echo ERROR: git push failed.
  pause
  exit /b 1
)

echo.
echo Done.
echo Forex folders pushed under: %FOREX_DEST%
echo Crypto folders pushed under: %CRYPTO_DEST%
pause
exit /b 0