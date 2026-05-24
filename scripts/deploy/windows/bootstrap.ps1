#Requires -Version 5.0
<#
.SYNOPSIS
DicePP 一键启动引导脚本。
首次运行自动下载 uv / Python / 依赖，之后直接启动 WebUI 后端。
#>

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ─── 路径定位 ───────────────────────────────────────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path

$BinDir       = Join-Path $ProjectRoot "bin"
$UvExe        = Join-Path $BinDir "uv.exe"
$PythonDir    = Join-Path $ProjectRoot ".python"
$VenvDir      = Join-Path $ProjectRoot ".venv"
$StartScript  = Join-Path $ScriptDir "start_admin.ps1"

# 把项目内 Python/uv 路径写到环境变量，让 uv 知道把东西装哪
$env:UV_PYTHON_INSTALL_DIR = $PythonDir
$env:UV_PROJECT_ENVIRONMENT = $VenvDir
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".cache\uv"

# 国内镜像加速
if (-not $env:UV_PYTHON_INSTALL_MIRROR) {
    $env:UV_PYTHON_INSTALL_MIRROR = "https://gh-proxy.com/https://github.com/astral-sh/python-build-standalone/releases/download"
}
if (-not $env:UV_DEFAULT_INDEX) {
    $env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
}

function Write-Step([string]$Msg) {
    Write-Host ""
    Write-Host "▶ $Msg" -ForegroundColor Cyan
}
function Write-OK([string]$Msg)   { Write-Host "  ✓ $Msg" -ForegroundColor Green }
function Write-Warn2([string]$Msg) { Write-Host "  ⚠ $Msg" -ForegroundColor Yellow }
function Write-Err2([string]$Msg) { Write-Host "  ✗ $Msg" -ForegroundColor Red }

# ─── Step 1: 确保 uv 存在 ────────────────────────────────────────────────
function Ensure-Uv {
    Write-Step "检查 uv 工具"
    if (Test-Path $UvExe) {
        Write-OK "uv 已就绪：$UvExe"
        return
    }

    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

    # uv 官方下载 URL（Windows x64）
    $arch = if ([Environment]::Is64BitOperatingSystem) { "x86_64" } else { "i686" }
    $uvVersion = "0.5.11"
    $uvZip = Join-Path $BinDir "uv.zip"

    # 国内代理优先，失败回退官方
    $urls = @(
        "https://gh-proxy.com/https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-$arch-pc-windows-msvc.zip",
        "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-$arch-pc-windows-msvc.zip"
    )

    $downloaded = $false
    foreach ($url in $urls) {
        try {
            Write-Host "  下载: $url"
            Invoke-WebRequest -Uri $url -OutFile $uvZip -UseBasicParsing -TimeoutSec 60
            $downloaded = $true
            break
        }
        catch {
            Write-Warn2 "下载失败，尝试备用源..."
        }
    }

    if (-not $downloaded) {
        Write-Err2 "uv 下载失败，请检查网络或手动放置 uv.exe 到 $BinDir"
        throw "uv download failed"
    }

    # 解压
    Expand-Archive -Path $uvZip -DestinationPath $BinDir -Force
    Remove-Item $uvZip -Force
    # uv 解压后是 uv-x86_64-pc-windows-msvc/uv.exe，提取到 bin 根
    $nested = Get-ChildItem -Path $BinDir -Directory | Where-Object { $_.Name -like "uv-*" } | Select-Object -First 1
    if ($nested) {
        Move-Item -Path (Join-Path $nested.FullName "uv.exe") -Destination $UvExe -Force
        if (Test-Path (Join-Path $nested.FullName "uvx.exe")) {
            Move-Item -Path (Join-Path $nested.FullName "uvx.exe") -Destination (Join-Path $BinDir "uvx.exe") -Force
        }
        Remove-Item $nested.FullName -Recurse -Force
    }

    if (-not (Test-Path $UvExe)) {
        Write-Err2 "uv 解压后未找到 uv.exe"
        throw "uv extract failed"
    }
    Write-OK "uv 安装完成"
}

# ─── Step 2: 确保 Python 存在 ────────────────────────────────────────────
function Ensure-Python {
    Write-Step "检查 Python 3.10 解释器"
    $pythonReady = $false
    if (Test-Path $PythonDir) {
        $pythonExes = Get-ChildItem -Path $PythonDir -Recurse -Filter "python.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pythonExes) {
            Write-OK "Python 已就绪"
            $pythonReady = $true
        }
    }
    if (-not $pythonReady) {
        Write-Host "  下载独立 Python 到 $PythonDir（约 50MB）..."
        & $UvExe python install 3.10
        if ($LASTEXITCODE -ne 0) {
            throw "Python install failed"
        }
        Write-OK "Python 安装完成"
    }
}

# ─── Step 3: uv sync 装依赖 ──────────────────────────────────────────────
function Ensure-Deps {
    Write-Step "同步项目依赖（首次较慢）"
    Push-Location $ProjectRoot
    try {
        & $UvExe sync --no-dev
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed"
        }
    }
    finally {
        Pop-Location
    }
    Write-OK "依赖已就绪"
}

# ─── Step 4: 启动 WebUI 后端 ─────────────────────────────────────────────
function Start-Admin {
    Write-Step "启动 WebUI 后端"
    if (Test-Path $StartScript) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $StartScript
    }
    else {
        Write-Err2 "未找到 start_admin.ps1"
        throw "start script missing"
    }
}

# ─── 主流程 ─────────────────────────────────────────────────────────────
try {
    Ensure-Uv
    Ensure-Python
    Ensure-Deps
    Start-Admin
    exit 0
}
catch {
    Write-Err2 $_.Exception.Message
    Write-Host ""
    Write-Host "如反复失败，请到群里求助并附上上方完整日志" -ForegroundColor Yellow
    exit 1
}
