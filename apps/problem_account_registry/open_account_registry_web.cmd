@echo off
set SCRIPT_DIR=%~dp0
start "problem-account-registry" powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_account_registry_web.ps1"
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8776
