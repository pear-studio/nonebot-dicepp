@echo off
chcp 65001 >nul
title DicePP Update

set "ROOT=%~dp0"
set "UPDATE_PS=%ROOT%scripts\deploy\windows\update.ps1"

if not exist "%UPDATE_PS%" (
    echo [Error] update.ps1 not found:
    echo   %UPDATE_PS%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%UPDATE_PS%" %*
set "RET=%ERRORLEVEL%"

pause
exit /b %RET%
