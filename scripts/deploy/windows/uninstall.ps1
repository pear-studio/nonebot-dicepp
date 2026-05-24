#Requires -Version 5.0
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..\..")).Path

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  DicePP 卸载工具"                                              -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "将删除以下目录："
Write-Host "  - $ProjectRoot\bin\        （uv 工具）"
Write-Host "  - $ProjectRoot\.python\    （独立 Python）"
Write-Host "  - $ProjectRoot\.venv\      （Python 依赖）"
Write-Host ""
Write-Host "注意：data\ 目录（你的骰娘数据）不会被删除" -ForegroundColor Yellow
Write-Host "      如要彻底清理，请手动删除 data\ 目录"   -ForegroundColor Yellow
Write-Host ""

$confirm = Read-Host "确认卸载？输入 y 继续"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "已取消" -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "正在删除..." -ForegroundColor Cyan
foreach ($d in @("bin", ".python", ".venv", ".cache")) {
    $full = Join-Path $ProjectRoot $d
    if (Test-Path $full) {
        try {
            Remove-Item -Recurse -Force $full
            Write-Host "  ✓ 已删除 $d" -ForegroundColor Green
        }
        catch {
            Write-Host "  ✗ 删除 $d 失败: $_" -ForegroundColor Red
        }
    }
}

Write-Host ""
Write-Host "✓ 已清理运行环境" -ForegroundColor Green
Write-Host "如需重新使用，双击「启动骰娘.bat」即可重新部署"
