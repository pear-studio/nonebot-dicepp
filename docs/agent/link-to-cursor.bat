@echo off
chcp 65001 >nul
goto :link_main

::: Rules: docs/agent/rules/*.md 硬链接到 .cursor/rules/*.mdc
::: Skills: 将 docs/agent/skills/* 以目录符号链接方式接到 .cursor/skills/
:::
::: 说明：
::: - 本项目当前 .cursor/skills 里已有其它技能（例如 openspec-*），因此这里不会删除 .cursor/skills，
:::   只会对 docs/agent/skills 中缺失的目录创建链接，避免破坏现有配置。

:link_main
cd /d %~dp0\..\..
set "REPO_ROOT=%CD%"

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

if not exist ".cursor" mkdir ".cursor"

echo 正在链接 Cursor 规则目录 .cursor\rules ...
if not exist ".cursor\rules" mkdir ".cursor\rules"
del /q ".cursor\rules\*.mdc" 2>nul

for %%f in (docs\agent\rules\*.md) do (
    echo 硬链接: %%~nf.md -^> .cursor\rules\%%~nf.mdc
    mklink /H ".cursor\rules\%%~nf.mdc" "%REPO_ROOT%\docs\agent\rules\%%~nf.md"
)

echo.
echo 正在链接 Cursor skills 目录...
if not exist ".cursor\skills" mkdir ".cursor\skills"

for /d %%d in (docs\agent\skills\*) do (
    if not exist ".cursor\skills\%%~nxd" (
        echo 符号链接: %%~nxd -^> .cursor\skills\%%~nxd
        mklink /D ".cursor\skills\%%~nxd" "%REPO_ROOT%\docs\agent\skills\%%~nxd" >nul 2>nul
    ) else (
        echo 已存在 .cursor\skills\%%~nxd, 跳过
    )
)

echo.
echo 完成. Cursor 将加载:
echo   .cursor\rules\*.mdc  -^> docs\agent\rules\*.md
echo   .cursor\skills       -^> %REPO_ROOT%\docs\agent\skills

echo.
echo 当前 .cursor\skills:
dir /b ".cursor\skills"

pause

