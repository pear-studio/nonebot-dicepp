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
$repositoryRootPath = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$portableName = "DicePP-${Tag}-win64-Portable.zip"
$setupName = "DicePP-${Tag}-win64-Setup.exe"
$bundleName = "velopack.win-x64.zip"
$expectedNames = @($portableName, $setupName, $bundleName)
$actualNames = @(
    Get-ChildItem -LiteralPath $artifactRootPath -File |
        Where-Object {
            $_.Name -like "DicePP-*-win64-Portable.zip" -or
            $_.Name -like "DicePP-*-win64-Setup.exe" -or
            $_.Name -eq $bundleName
        } |
        ForEach-Object { $_.Name } |
        Sort-Object
)
if (Compare-Object ($expectedNames | Sort-Object) $actualNames) {
    throw "Final Windows asset set is incomplete, renamed, or ambiguous"
}
foreach ($name in $expectedNames) {
    $path = Join-Path $artifactRootPath $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing final Windows asset: $name"
    }
    if ((Get-Item -LiteralPath $path).Length -lt 1) {
        throw "Final Windows asset is empty: $name"
    }
}

function Invoke-DetachedLaunchSmoke {
    param([Parameter(Mandatory = $true)][string]$DashboardPath)
    $env:DICEPP_WINDOWS_PACKAGE_SMOKE = "1"
    $env:DICEPP_DASHBOARD_EXE = $DashboardPath
    $testPath = Join-Path $repositoryRootPath `
        "tests\system\package\windows\test_windows_package_detached_launch.py"
    uv run --frozen pytest $testPath -n0 --tb=short -q
    if ($LASTEXITCODE -ne 0) {
        throw "Final Windows detached launch smoke failed"
    }
}

function Write-ManualMigrationSentinels {
    param([Parameter(Mandatory = $true)][string]$Root)
    $sentinels = @(
        "config\user-preserved.json",
        "content\user-preserved.txt",
        "data\user-preserved.txt",
        "dashboard\data\user-preserved.txt",
        "manager\control\user-preserved.txt",
        "manager\state\user-preserved.txt",
        "manager\backups\user-preserved.zip"
    )
    foreach ($relative in $sentinels) {
        $path = Join-Path $Root $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
        [System.IO.File]::WriteAllText(
            $path,
            "preserve:$relative",
            [System.Text.UTF8Encoding]::new($false)
        )
    }
}

function Assert-ManualMigrationSentinels {
    param([Parameter(Mandatory = $true)][string]$Root)
    $sentinels = @(
        "config\user-preserved.json",
        "content\user-preserved.txt",
        "data\user-preserved.txt",
        "dashboard\data\user-preserved.txt",
        "manager\control\user-preserved.txt",
        "manager\state\user-preserved.txt",
        "manager\backups\user-preserved.zip"
    )
    foreach ($relative in $sentinels) {
        $path = Join-Path $Root $relative
        if (
            -not (Test-Path -LiteralPath $path -PathType Leaf) -or
            [System.IO.File]::ReadAllText($path) -ne "preserve:$relative"
        ) {
            throw "Manual migration did not preserve instance data: $relative"
        }
    }
}

$portablePath = Join-Path $artifactRootPath $portableName
$extractRoot = Join-Path $env:RUNNER_TEMP "dicepp-portable-$([guid]::NewGuid())"
try {
    Write-ManualMigrationSentinels $extractRoot
    Expand-Archive -LiteralPath $portablePath -DestinationPath $extractRoot
    Assert-ManualMigrationSentinels $extractRoot
    $runtimeMatches = @(
        Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "DicePP-Runtime.exe"
    )
    if ($runtimeMatches.Count -ne 1) {
        throw "Portable must contain exactly one DicePP-Runtime.exe"
    }
    $programRoot = $runtimeMatches[0].Directory.FullName
    $runtime = Join-Path $programRoot "DicePP-Runtime.exe"
    $dashboard = Join-Path $programRoot "DicePP.exe"
    $forbiddenGuard = @(
        Get-ChildItem -LiteralPath $extractRoot -Recurse -File |
            Where-Object { $_.Name -eq "DicePP-UpdateGuard.exe" }
    )
    if ($forbiddenGuard.Count -ne 0) {
        throw "Portable must not contain DicePP-UpdateGuard.exe"
    }
    $expectedRuntime = "DicePP v${Version}"
    if ((Invoke-DicePPProcess -FilePath $runtime -Arguments @("--version") `
        -Scenario "final-portable-runtime-version" `
        -DiagnosticsRoot $ProcessDiagnosticsRoot) -ne $expectedRuntime) {
        throw "Portable Runtime version mismatch"
    }
    Invoke-DicePPProcess -FilePath $runtime -Arguments @("--smoke-check") `
        -Scenario "final-portable-runtime-smoke" `
        -DiagnosticsRoot $ProcessDiagnosticsRoot | Out-Null
    if ((Invoke-DicePPProcess -FilePath $dashboard -Arguments @("--version") `
        -Scenario "final-portable-dashboard-version" `
        -DiagnosticsRoot $ProcessDiagnosticsRoot) -ne "DicePP Dashboard v${Version}") {
        throw "Portable Dashboard version mismatch"
    }
    Invoke-DicePPProcess -FilePath $dashboard -Arguments @("--smoke-check") `
        -Scenario "final-portable-dashboard-smoke" `
        -DiagnosticsRoot $ProcessDiagnosticsRoot | Out-Null
    $stableDashboard = Join-Path $extractRoot "DicePP.exe"
    if (-not (Test-Path -LiteralPath $stableDashboard -PathType Leaf)) {
        throw "Portable stable DicePP.exe is missing"
    }
    Invoke-DetachedLaunchSmoke $stableDashboard
} finally {
    if (Test-Path -LiteralPath $extractRoot) {
        Remove-Item -LiteralPath $extractRoot -Recurse -Force
    }
}

$setupPath = Join-Path $artifactRootPath $setupName
$installRoot = Join-Path $env:RUNNER_TEMP "dicepp-setup-$([guid]::NewGuid())"
try {
    Write-ManualMigrationSentinels $installRoot
    Invoke-DicePPProcess `
        -FilePath $setupPath `
        -Arguments @("--silent", "--installto", $installRoot) `
        -TimeoutSeconds 20 `
        -Scenario "final-setup-install" `
        -DiagnosticsRoot $ProcessDiagnosticsRoot | Out-Null
    Assert-ManualMigrationSentinels $installRoot
    $stableDashboard = Join-Path $installRoot "DicePP.exe"
    $payloadDashboard = Join-Path $installRoot "current\DicePP.exe"
    foreach ($path in @($stableDashboard, $payloadDashboard)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Setup installation is incomplete: $path"
        }
    }
    $forbiddenGuard = @(
        Get-ChildItem -LiteralPath $installRoot -Recurse -File |
            Where-Object { $_.Name -eq "DicePP-UpdateGuard.exe" }
    )
    if ($forbiddenGuard.Count -ne 0) {
        throw "Setup must not install DicePP-UpdateGuard.exe"
    }
    Invoke-DetachedLaunchSmoke $stableDashboard
} finally {
    if (Test-Path -LiteralPath $installRoot) {
        Remove-Item -LiteralPath $installRoot -Recurse -Force
    }
}

# The declaration is intentionally the last write: every listed byte has passed
# the complete Portable, Setup, hook and detached-launch validation above.
if ($ValidatedSummaryPath) {
    $summaryPath = [System.IO.Path]::GetFullPath($ValidatedSummaryPath)
    $summaryParent = Split-Path -Parent $summaryPath
    if (-not $summaryParent) {
        throw "Validated summary path must have a parent directory"
    }
    New-Item -ItemType Directory -Path $summaryParent -Force | Out-Null
    $records = @(
        foreach ($name in ($expectedNames | Sort-Object)) {
            $path = Join-Path $artifactRootPath $name
            $item = Get-Item -LiteralPath $path
            [ordered]@{
                filename = $name
                size = $item.Length
                sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
    )
    $summary = [ordered]@{
        contract_version = 1
        artifacts = $records
    }
    $temporarySummary = Join-Path $summaryParent ".$(Split-Path -Leaf $summaryPath).$([guid]::NewGuid()).tmp"
    try {
        $json = $summary | ConvertTo-Json -Depth 4
        [System.IO.File]::WriteAllText(
            $temporarySummary,
            "$json`n",
            [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporarySummary -Destination $summaryPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporarySummary -PathType Leaf) {
            Remove-Item -LiteralPath $temporarySummary -Force
        }
    }
}
