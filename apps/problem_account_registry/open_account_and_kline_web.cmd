@echo off
set SCRIPT_DIR=%~dp0
start "account-and-kline" powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_account_and_kline_web.ps1"
