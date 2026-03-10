@echo off
REM ============================================================
REM DicePP Build Script
REM 使用 PyInstaller 打包 DicePP 为 Windows EXE
REM ============================================================

setlocal enabledelayedexpansion

REM 切换到项目根目录 (从 scripts/build/ 向上两级)
cd /d "%~dp0..\.."

echo ============================================================
echo DicePP Build Script
echo ============================================================
echo.

REM 检查 uv 是否可用
where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv not found. Please install uv first:
    echo   https://docs.astral.sh/uv/getting-started/installation/
    exit /b 1
)

REM 同步依赖（包括 dev 依赖中的 pyinstaller）
echo [INFO] Syncing dependencies with uv...
uv sync --dev
if errorlevel 1 (
    echo [ERROR] Failed to sync dependencies
    exit /b 1
)

echo [INFO] PyInstaller version:
uv run pyinstaller --version
echo.

REM 清理旧的 dist 目录
echo [INFO] Cleaning old dist artifacts...
if exist "dist" rmdir /s /q "dist"
echo [INFO] Clean complete
echo.

REM 执行打包
echo [INFO] Building DicePP...
echo [INFO] This may take several minutes...
echo.

uv run pyinstaller scripts\build\dicepp.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo ============================================================
    echo [ERROR] Build failed!
    echo ============================================================
    exit /b 1
)

echo.
echo [INFO] Relocating user-accessible files...
REM 将用户需要访问的文件从 _internal 移动到 EXE 同级目录
set "DIST_DIR=dist\DicePP"
set "INTERNAL_DIR=%DIST_DIR%\_internal"

REM 移动 .env（用户配置文件）
if exist "%INTERNAL_DIR%\.env" (
    move "%INTERNAL_DIR%\.env" "%DIST_DIR%\" >nul
    echo [INFO] Moved .env to application root
)

REM 移动 Data 目录（用户数据）
if exist "%INTERNAL_DIR%\Data" (
    move "%INTERNAL_DIR%\Data" "%DIST_DIR%\" >nul
    echo [INFO] Moved Data directory to application root
)

REM pyproject.toml 可以留在 _internal，不需要用户访问

REM 清理 build 缓存目录
echo [INFO] Cleaning build cache...
if exist "build" rmdir /s /q "build"

echo.
echo ============================================================
echo [SUCCESS] Build complete!
echo ============================================================
echo.
echo Output location: dist\DicePP\
echo.
echo Contents:
dir /b "dist\DicePP\"
echo.
echo To run: dist\DicePP\DicePP.exe
echo ============================================================

endlocal
