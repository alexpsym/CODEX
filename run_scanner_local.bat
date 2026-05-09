@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "ROOT=%~dp0"
set "MASTER_ENV_FILE=C:\Users\User\Documents\GPT\env.env"
if not exist "%MASTER_ENV_FILE%" (
  echo [scanner-local] ERROR: MASTER_ENV_FILE not found at %MASTER_ENV_FILE%
  exit /b 1
)
echo scanner launcher placeholder
