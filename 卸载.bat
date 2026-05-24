@echo off
chcp 65001 >nul
title DicePP Uninstall

set "ROOT=%~dp0"
set "UNINSTALL_PS=%ROOT%scripts\deploy\windows\uninstall.ps1"

if not exist "%UNINSTALL_PS%" (
    echo [Error] uninstall.ps1 not found:
    echo   %UNINSTALL_PS%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%UNINSTALL_PS%" %*

pause
