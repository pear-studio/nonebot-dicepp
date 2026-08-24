param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$ArtifactRoot = ".",
    [string]$RepositoryRoot = (Join-Path $PSScriptRoot "..\.."),
    [string]$ValidatedSummaryPath = "",
    [string]$ProcessDiagnosticsRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows_process_runner.ps1")
$artifactRootPath = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$portableName = "DicePP-${Tag}-win64-Portable.zip"
$portablePath = Join-Path $artifactRootPath $portableName
if (-not (Test-Path -LiteralPath $portablePath -PathType Leaf)) {
    throw "Missing Windows Portable asset: $portableName"
}

$extractRoot = Join-Path $env:RUNNER_TEMP "dicepp-portable-$([guid]::NewGuid())"
try {
    [System.IO.Compression.ZipFile]::ExtractToDirectory($portablePath, $extractRoot, $false)
    $runtimeMatches = @(Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "DicePP-Runtime.exe")
    $dashboardMatches = @(Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "DicePP.exe")
    if ($runtimeMatches.Count -ne 1 -or $dashboardMatches.Count -ne 1) {
        throw "Portable must contain exactly one DicePP.exe and one DicePP-Runtime.exe"
    }
    $programRoot = $runtimeMatches[0].Directory.FullName
    if ($dashboardMatches[0].Directory.FullName -ne $programRoot) {
        throw "Portable executables must share one directory"
    }
    if (Test-Path -LiteralPath (Join-Path $programRoot "config/global.json")) {
        throw "Portable payload must not contain config/global.json"
    }
    $executables = @(Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "*.exe")
    if ($executables.Count -ne 2) {
        throw "Portable must contain only its Dashboard and Bot executables"
    }

    $runtime = Join-Path $programRoot "DicePP-Runtime.exe"
    $dashboard = Join-Path $programRoot "DicePP.exe"
    if ((Invoke-DicePPProcess -FilePath $runtime -Arguments @("--version") `
        -Scenario "portable-runtime-version" -DiagnosticsRoot $ProcessDiagnosticsRoot) -ne "DicePP v${Version}") {
        throw "Portable Runtime version mismatch"
    }
    Invoke-DicePPProcess -FilePath $runtime -Arguments @("--smoke-check") `
        -Scenario "portable-runtime-smoke" -DiagnosticsRoot $ProcessDiagnosticsRoot | Out-Null
    if ((Invoke-DicePPProcess -FilePath $dashboard -Arguments @("--version") `
        -Scenario "portable-dashboard-version" -DiagnosticsRoot $ProcessDiagnosticsRoot) -ne "DicePP Dashboard v${Version}") {
        throw "Portable Dashboard version mismatch"
    }
    Invoke-DicePPProcess -FilePath $dashboard -Arguments @("--smoke-check") `
        -Scenario "portable-dashboard-smoke" -DiagnosticsRoot $ProcessDiagnosticsRoot | Out-Null
} finally {
    if (Test-Path -LiteralPath $extractRoot) {
        Remove-Item -LiteralPath $extractRoot -Recurse -Force
    }
}

if ($ValidatedSummaryPath) {
    $summaryPath = [System.IO.Path]::GetFullPath($ValidatedSummaryPath)
    New-Item -ItemType Directory -Path (Split-Path -Parent $summaryPath) -Force | Out-Null
    $item = Get-Item -LiteralPath $portablePath
    $summary = [ordered]@{
        contract_version = 1
        artifacts = @([ordered]@{
            filename = $portableName
            size = $item.Length
            sha256 = (Get-FileHash -LiteralPath $portablePath -Algorithm SHA256).Hash.ToLowerInvariant()
        })
    }
    $summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $summaryPath -Encoding utf8
}
