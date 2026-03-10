@echo off
chcp 65001 >nul
REM ============================================================
REM DicePP Integration Test Runner
REM 运行 Bot 集成测试（模拟 OneBot 消息）
REM ============================================================

setlocal

REM 切换到项目根目录
cd /d "%~dp0..\.."

echo ============================================================
echo DicePP Integration Test
echo ============================================================
echo.

REM 检查 uv 是否可用
where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv not found in PATH
    echo [INFO] Please install uv: https://docs.astral.sh/uv/
    goto :END
)

echo [INFO] 请确保 Bot 已启动（开发环境或 EXE）
echo [INFO] 默认端口: 8080
echo.

REM 检查是否有参数
if "%1"=="-i" goto :INTERACTIVE
if "%1"=="--interactive" goto :INTERACTIVE

REM 自动测试模式
echo [INFO] Running integration tests...
echo.
uv run python scripts\test\test_bot.py %*
goto :RESULT

:INTERACTIVE
echo [INFO] Entering interactive mode...
echo.
uv run python scripts\test\test_bot.py -i
goto :END

:RESULT
set TEST_RESULT=%errorlevel%

echo.
echo ============================================================

if %TEST_RESULT% equ 0 (
    echo [SUCCESS] 集成测试通过!
) else (
    echo [FAILED] 部分测试失败 (exit code: %TEST_RESULT%)
)

echo ============================================================

:END
echo.
echo 按任意键退出...
pause >nul

endlocal
exit /b %TEST_RESULT%
