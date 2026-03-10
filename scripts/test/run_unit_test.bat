@echo off
chcp 65001 >nul
REM ============================================================
REM DicePP Pytest Runner
REM 运行所有 pytest 单元测试
REM ============================================================

setlocal

REM 切换到项目根目录
cd /d "%~dp0..\.."

echo ============================================================
echo DicePP Pytest Runner
echo ============================================================
echo.

REM 检查 uv 是否可用
where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv not found in PATH
    echo [INFO] Please install uv: https://docs.astral.sh/uv/
    goto :END
)

echo [INFO] Running pytest...
echo.

REM 运行 pytest
REM   -v: 详细输出
REM   --tb=short: 简短的错误追踪
REM   -x: 遇到第一个失败就停止 (可选，去掉则运行全部)
REM   --ignore: 忽略集成测试脚本

uv run pytest tests/ -v --tb=short --ignore=scripts/

set TEST_RESULT=%errorlevel%

echo.
echo ============================================================

if %TEST_RESULT% equ 0 (
    echo [SUCCESS] 所有测试通过!
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
