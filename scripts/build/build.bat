@echo off
REM ============================================================
REM DicePP Build Script
REM Package DicePP as a Windows EXE with PyInstaller.
REM ============================================================

setlocal enabledelayedexpansion

REM Switch to the project root from scripts/build.
cd /d "%~dp0..\.."

echo ============================================================
echo DicePP Build Script
echo ============================================================
echo.

REM Check that uv is available.
where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv not found. Please install uv first:
    echo   https://docs.astral.sh/uv/getting-started/installation/
    exit /b 1
)

REM Sync dependencies, including PyInstaller from dev dependencies.
echo [INFO] Syncing dependencies with uv...
uv sync --dev
if errorlevel 1 (
    echo [ERROR] Failed to sync dependencies
    exit /b 1
)

echo [INFO] PyInstaller version:
uv run pyinstaller --version
echo.

REM Clean the old dist directory.
echo [INFO] Cleaning old dist artifacts...
if exist "dist" (
    rmdir /s /q "dist"
    if errorlevel 1 (
        echo [ERROR] Failed to remove old dist directory.
        echo [ERROR] Please close any running DicePP.exe / DicePP-Runtime.exe processes or tools holding files under dist, then retry.
        exit /b 1
    )
)
if exist "dist" (
    echo [ERROR] Failed to remove old dist directory: dist still exists.
    echo [ERROR] Please close any running DicePP.exe / DicePP-Runtime.exe processes or tools holding files under dist, then retry.
    exit /b 1
)
echo [INFO] Clean complete
echo.

REM Build the runtime executable.
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

echo [INFO] Building DicePP Windows launcher...
uv run pyinstaller scripts\build\dashboard.spec --clean --noconfirm
if errorlevel 1 (
    echo [ERROR] Windows launcher build failed!
    exit /b 1
)

echo.
echo [INFO] Preparing user-accessible files...
set "DIST_DIR=dist\DicePP"

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\build\assemble_windows_package.ps1" -DistDir "%DIST_DIR%" -LauncherSource "dist\DicePP.exe"
if errorlevel 1 (
    echo [ERROR] Failed to assemble Windows package
    exit /b 1
)

REM pyproject.toml can stay in _internal; users do not need direct access.

REM Clean the build cache directory.
echo [INFO] Cleaning build cache...
if exist "build" rmdir /s /q "build"

REM Verify the assembled Portable package through its normal launcher path.
echo.
echo [INFO] Verifying Portable package...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\build\verify_windows_package.ps1" -DistDir "%DIST_DIR%"
if errorlevel 1 (
    echo [ERROR] Portable verification failed! See output above.
    exit /b 1
)
echo [INFO] Portable verification passed

set "PORTABLE_ZIP=dist\DicePP-Portable.zip"
if exist "%PORTABLE_ZIP%" del /q "%PORTABLE_ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path '%DIST_DIR%' -DestinationPath '%PORTABLE_ZIP%'"
if errorlevel 1 (
    echo [ERROR] Failed to create Portable ZIP
    exit /b 1
)
echo [INFO] Portable ZIP: %PORTABLE_ZIP%

echo.
echo ============================================================
echo [SUCCESS] Build complete!
echo ============================================================
echo.
echo Output location: dist\DicePP\
echo Portable ZIP: dist\DicePP-Portable.zip
echo.
echo Contents:
dir /b "dist\DicePP\"
echo.
echo To run: dist\DicePP\DicePP.exe
echo Runtime: dist\DicePP\DicePP-Runtime.exe
echo ============================================================

endlocal
