@echo off
chcp 65001 >nul
:: 创建符号链接：docs/agent/ -> .trae/
:: 需要管理员权限运行

cd /d %~dp0\..\..

:: 检查源目录是否存在
if not exist "docs\agent\rules" (
    echo 错误: 源目录 docs\agent\rules 不存在
    pause
    exit /b 1
)
if not exist "docs\agent\skills" (
    echo 错误: 源目录 docs\agent\skills 不存在
    pause
    exit /b 1
)

echo 正在创建符号链接...

:: 确保 .trae 目录存在
if not exist ".trae" mkdir ".trae"

:: 删除旧目录/链接
if exist ".trae\rules" (
    rmdir /s /q ".trae\rules" 2>nul
    del ".trae\rules" 2>nul
    echo 已删除 .trae\rules
)
if exist ".trae\skills" (
    rmdir /s /q ".trae\skills" 2>nul
    del ".trae\skills" 2>nul
    echo 已删除 .trae\skills
)

:: 创建符号链接
mklink /D ".trae\rules" "docs\agent\rules"
mklink /D ".trae\skills" "docs\agent\skills"

echo.
echo 符号链接创建完成:
echo   .trae\rules  -^> docs\agent\rules
echo   .trae\skills -^> docs\agent\skills
pause