#Requires -Version 5.0
<#
.SYNOPSIS
启动 DicePP WebUI 管理后台并自动打开浏览器
#>

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path
$UvExe       = Join-Path $ProjectRoot "bin\uv.exe"

# 复用环境变量（如果是直接调用本脚本）
$env:UV_PROJECT_ENVIRONMENT = Join-Path $ProjectRoot ".venv"
$env:UV_PYTHON_INSTALL_DIR  = Join-Path $ProjectRoot ".python"

$AdminPort = if ($env:DPP_ADMIN_PORT) { $env:DPP_ADMIN_PORT } else { "2333" }
$AdminUrl  = "http://127.0.0.1:$AdminPort/admin"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  WebUI 启动中"                                                 -ForegroundColor Cyan
Write-Host "  地址：$AdminUrl"                                               -ForegroundColor Cyan
Write-Host "  按 Ctrl+C 可关闭本服务"                                       -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 异步等待端口起来，再打开浏览器
$openJob = Start-Job -ScriptBlock {
    param($Url, $Port)
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -lt 500) {
                Start-Process $Url
                return
            }
        }
        catch {}
        Start-Sleep -Milliseconds 800
    }
} -ArgumentList $AdminUrl, $AdminPort

Push-Location $ProjectRoot
$adminExit = 0
try {
    # 前台运行后端（用户 Ctrl+C 停止；启动失败立即返回非 0）
    & $UvExe run python -m dicepp_admin
    $adminExit = $LASTEXITCODE
}
finally {
    Pop-Location
    Stop-Job $openJob -ErrorAction SilentlyContinue
    Remove-Job $openJob -ErrorAction SilentlyContinue
}

if ($adminExit -ne 0) {
    Write-Host ""
    Write-Host "[错误] WebUI 后端异常退出 (exit code $adminExit)" -ForegroundColor Red
    Write-Host "常见原因：" -ForegroundColor Yellow
    Write-Host "  - Python 依赖缺失（首次启动失败请重跑「启动骰娘.bat」让 uv sync 重装）" -ForegroundColor Yellow
    Write-Host "  - 端口 $AdminPort 被占用（设环境变量 DPP_ADMIN_PORT 改端口）" -ForegroundColor Yellow
    Write-Host "  - 配置损坏（删除 data\admin\ 让其重新初始化）" -ForegroundColor Yellow
    throw "admin 启动失败 (exit code $adminExit)"
}
