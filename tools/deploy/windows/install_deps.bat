@echo off
chcp 65001 >nul
setlocal

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%..\.."

cd /d "%PROJECT_DIR%"

echo ===== 安装 DicePP 依赖 =====
echo.

pip install .

echo.
echo ===== 依赖安装完成 =====

pause
