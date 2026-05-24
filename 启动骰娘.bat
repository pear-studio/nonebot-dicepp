@echo off
chcp 65001 >nul
title DicePP Launcher

set "ROOT=%~dp0"
set "BOOTSTRAP=%ROOT%scripts\deploy\windows\bootstrap.ps1"

if not exist "%BOOTSTRAP%" (
    echo [Error] bootstrap.ps1 not found:
    echo   %BOOTSTRAP%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%BOOTSTRAP%" %*
set "RET=%ERRORLEVEL%"

if not "%RET%"=="0" (
    echo.
    echo [Warning] exit code: %RET%
    pause
)

exit /b %RET%
