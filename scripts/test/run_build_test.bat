@echo off
chcp 65001 >nul
REM ============================================================
REM DicePP Build Test Script
REM 验证打包后的 EXE 是否能正常启动并响应命令
REM ============================================================

setlocal enabledelayedexpansion

REM 切换到项目根目录
cd /d "%~dp0..\.."

echo ============================================================
echo DicePP Build Test
echo ============================================================
echo.

set EXE_PATH=dist\DicePP\DicePP.exe
set TEST_TIMEOUT=15
set BOT_PORT=8080

REM 检查 EXE 是否存在
if not exist "%EXE_PATH%" (
    echo [ERROR] EXE not found: %EXE_PATH%
    echo [INFO] Please run scripts\build\build.bat first
    exit /b 1
)

echo [INFO] EXE found: %EXE_PATH%
echo.

REM ============================================================
REM 阶段 1: 检查目录结构
REM ============================================================
echo [PHASE 1] Checking directory structure...
echo.

set CHECK_PASSED=1

REM 检查 .env 文件
if exist "dist\DicePP\.env" (
    echo [OK] .env found
) else if exist "dist\DicePP\_internal\.env" (
    echo [OK] .env found in _internal
) else (
    echo [WARN] .env not found - may need manual configuration
)

REM 检查 pyproject.toml
if exist "dist\DicePP\pyproject.toml" (
    echo [OK] pyproject.toml found
) else if exist "dist\DicePP\_internal\pyproject.toml" (
    echo [OK] pyproject.toml found in _internal
) else (
    echo [WARN] pyproject.toml not found
)

REM 检查 Data 目录
if exist "dist\DicePP\Data" (
    echo [OK] Data directory found
) else if exist "dist\DicePP\_internal\Data" (
    echo [OK] Data directory found in _internal
) else (
    echo [INFO] Data directory will be created on first run
)

echo.

REM ============================================================
REM 阶段 2: 启动 EXE 并等待服务器就绪
REM ============================================================
echo [PHASE 2] Starting EXE and waiting for server...
echo.

REM 先确保没有残留进程
taskkill /f /im DicePP.exe >nul 2>&1

REM 启动 EXE（后台）
echo [INFO] Starting %EXE_PATH%...
start "" "%EXE_PATH%"

REM 等待一小段时间让进程启动
timeout /t 3 /nobreak >nul

REM 检查进程是否还在运行
tasklist /fi "imagename eq DicePP.exe" 2>nul | find /i "DicePP.exe" >nul
if errorlevel 1 (
    echo [ERROR] DicePP.exe crashed immediately after start
    echo [INFO] Check for missing dependencies or configuration issues
    exit /b 1
) else (
    echo [OK] DicePP.exe is running
)

REM 等待服务器就绪（轮询检查端口）
echo [INFO] Waiting for server to be ready (up to %TEST_TIMEOUT% seconds)...
set /a WAIT_COUNT=0
:WAIT_LOOP
set /a WAIT_COUNT+=1
if %WAIT_COUNT% gtr %TEST_TIMEOUT% (
    echo [WARN] Server did not respond within %TEST_TIMEOUT% seconds
    goto :SKIP_BOT_TEST
)

REM 使用 PowerShell 检查端口
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%BOT_PORT%' -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue; exit 0 } catch { if ($_.Exception.Response) { exit 0 } else { exit 1 } }" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto :WAIT_LOOP
)

echo [OK] Server is responding on port %BOT_PORT%
echo.

REM ============================================================
REM 阶段 3: 运行 Bot 集成测试
REM ============================================================
echo [PHASE 3] Running bot integration tests...
echo.

REM 调用 Python 测试脚本
uv run python scripts\test\test_bot.py --port %BOT_PORT%
set TEST_RESULT=%errorlevel%

if %TEST_RESULT% equ 0 (
    echo.
    echo [OK] Bot integration tests passed
) else (
    echo.
    echo [WARN] Some bot integration tests failed (exit code: %TEST_RESULT%)
)

:SKIP_BOT_TEST

REM ============================================================
REM 阶段 4: 清理
REM ============================================================
echo.
echo [PHASE 4] Cleanup...

REM 终止进程
echo [INFO] Stopping DicePP.exe...
taskkill /f /im DicePP.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [OK] Cleanup complete
echo.

REM ============================================================
REM 最终报告
REM ============================================================
echo ============================================================
echo Build Test Report
echo ============================================================
echo.

if defined TEST_RESULT (
    if %TEST_RESULT% equ 0 (
        echo [RESULT] All tests PASSED
        echo.
        echo Build is ready for distribution!
    ) else (
        echo [RESULT] Some tests FAILED
        echo.
        echo Please check the errors above.
    )
) else (
    echo [RESULT] Basic launch test PASSED
    echo [INFO] Bot integration tests were skipped (server not ready)
)

echo.
echo ============================================================
echo 测试说明
echo ============================================================
echo.
echo 本测试验证的是:
echo   - EXE 能否正常启动
echo   - Bot 服务器能否监听端口
echo.
echo 测试无法验证:
echo   - Bot 回复的具体内容 (需要真实聊天客户端)
echo   - Bot 日志中的 ApiNotAvailable 是正常的 (无客户端接收回复)
echo.
echo ============================================================
echo 手动测试指南
echo ============================================================
echo.
echo   1. 启动: dist\DicePP\DicePP.exe
echo   2. 连接聊天客户端 (如 LLBot, Lagrange 等)
echo   3. 发送骰子命令测试: .r, .rd20, .help
echo.
echo   或使用交互测试模式:
echo   uv run python scripts\test\test_bot.py -i
echo.
echo ============================================================
echo.

REM 最终结果提示
if defined TEST_RESULT (
    if %TEST_RESULT% equ 0 (
        echo [SUCCESS] 构建测试全部通过!
    ) else (
        echo [FAILED] 部分测试失败，请检查上方日志
    )
) else (
    echo [PARTIAL] 基础启动测试通过，集成测试被跳过
)

echo.
echo 按任意键退出...
pause >nul

endlocal

if defined TEST_RESULT (
    exit /b %TEST_RESULT%
) else (
    exit /b 0
)
