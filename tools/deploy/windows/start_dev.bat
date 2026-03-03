@echo off
chcp 65001 >nul
setlocal

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%..\.."

cd /d "%PROJECT_DIR%"

echo ===== DicePP Windows 开发模式启动脚本 =====
echo.
echo 使用 nb-cli 启动机器人...
echo.

call pip install -r requirements.txt

nb run

pause
