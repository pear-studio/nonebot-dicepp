@echo off
chcp 65001 >nul
:: 创建符号链接：docs/agent/ -> .codex/
:: 需要管理员权限运行
::
:: 注意：.codex/agents 是手工维护的 .toml 文件，本脚本不动它

cd /d %~dp0\..\..
set "REPO_ROOT=%CD%"

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

:: 确保 .codex 目录存在
if not exist ".codex" mkdir ".codex"

:: 删除旧 skills 链接/目录
if exist ".codex\skills" (
    rmdir /s /q ".codex\skills" 2>nul
    del ".codex\skills" 2>nul
    echo 已删除 .codex\skills
)

:: 删除旧的指令文件链接
if exist ".codex\AGENTS.md" (
    del /q ".codex\AGENTS.md" 2>nul
    echo 已删除 .codex\AGENTS.md
)
if exist ".codex\CODEX.md" (
    del /q ".codex\CODEX.md" 2>nul
    echo 已删除 .codex\CODEX.md
)

:: 创建 skills 符号链接
mklink /D ".codex\skills" "%REPO_ROOT%\docs\agent\skills"

:: 创建顶层指令文件硬链接 -> docs\agent\rules\CLAUDE.md
echo Linking instructions: docs\agent\rules\CLAUDE.md -^> .codex\AGENTS.md
mklink /H ".codex\AGENTS.md" "%REPO_ROOT%\docs\agent\rules\CLAUDE.md" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Failed to create hardlink, trying symlink...
    mklink ".codex\AGENTS.md" "%REPO_ROOT%\docs\agent\rules\CLAUDE.md" >nul 2>nul
)

echo Linking instructions: docs\agent\rules\CLAUDE.md -^> .codex\CODEX.md
mklink /H ".codex\CODEX.md" "%REPO_ROOT%\docs\agent\rules\CLAUDE.md" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Failed to create hardlink, trying symlink...
    mklink ".codex\CODEX.md" "%REPO_ROOT%\docs\agent\rules\CLAUDE.md" >nul 2>nul
)

echo.
echo 符号链接创建完成:
echo   .codex\skills    -^> docs\agent\skills
echo   .codex\AGENTS.md -^> docs\agent\rules\CLAUDE.md
echo   .codex\CODEX.md  -^> docs\agent\rules\CLAUDE.md
echo.
echo 注意: .codex\agents (toml) 由手工维护，本脚本不动
echo.
echo Skills:
for /d %%d in (docs\agent\skills\*) do (
    echo   - %%~nd
)
pause
