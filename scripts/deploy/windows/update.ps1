#Requires -Version 5.0
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path
$UvExe       = Join-Path $ProjectRoot "bin\uv.exe"

Push-Location $ProjectRoot
try {
    Write-Host "▶ 拉取最新代码..." -ForegroundColor Cyan
    if (Test-Path (Join-Path $ProjectRoot ".git")) {
        git pull --ff-only
        if ($LASTEXITCODE -ne 0) {
            Write-Host "git pull 失败，可能有本地修改" -ForegroundColor Yellow
            exit 1
        }
    }
    else {
        Write-Host "  非 git 仓库，跳过 git pull" -ForegroundColor Yellow
    }

    Write-Host "▶ 更新依赖..." -ForegroundColor Cyan
    if (Test-Path $UvExe) {
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $ProjectRoot ".venv"
        $env:UV_PYTHON_INSTALL_DIR  = Join-Path $ProjectRoot ".python"
        & $UvExe sync --no-dev
    }

    Write-Host ""
    Write-Host "✓ 更新完成" -ForegroundColor Green
}
finally {
    Pop-Location
}
