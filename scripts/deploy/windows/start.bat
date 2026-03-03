@echo off
chcp 65001 >nul
setlocal

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%..\.."

cd /d "%PROJECT_DIR%"

echo ===== DicePP Windows 启动脚本 =====
echo.

if not exist "bot.py" (
    echo 错误: 未找到 bot.py 文件
    pause
    exit /b 1
)

echo 正在启动 DicePP...
echo.

python bot.py

pause
