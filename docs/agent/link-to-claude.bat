@echo off
chcp 65001 >nul
:: 创建符号链接：docs/agent/ -> .claude/
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

:: 确保 .claude 目录存在
if not exist ".claude" mkdir ".claude"

:: 删除旧目录/链接
if exist ".claude\rules" (
    rmdir /s /q ".claude\rules" 2>nul
    del ".claude\rules" 2>nul
    echo 已删除 .claude\rules
)
if exist ".claude\skills" (
    rmdir /s /q ".claude\skills" 2>nul
    del ".claude\skills" 2>nul
    echo 已删除 .claude\skills
)

:: 创建符号链接（使用绝对路径）
mklink /D ".claude\rules" "%cd%\docs\agent\rules"
mklink /D ".claude\skills" "%cd%\docs\agent\skills"

echo.
echo 符号链接创建完成:
echo   .claude\rules  -^> docs\agent\rules
echo   .claude\skills -^> docs\agent\skills
pause
