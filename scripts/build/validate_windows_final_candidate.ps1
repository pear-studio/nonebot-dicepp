param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$ArtifactRoot = ".",
    [string]$RepositoryRoot = (Join-Path $PSScriptRoot "..\..")
)

$ErrorActionPreference = "Stop"
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

function Invoke-PackagedExe {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$Arguments = @()
    )
    $stdout = New-TemporaryFile
    $stderr = New-TemporaryFile
    try {
        $process = Start-Process `
            -FilePath (Resolve-Path -LiteralPath $Path) `
            -ArgumentList $Arguments `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr `
            -WindowStyle Hidden
        $out = Get-Content $stdout -Raw -ErrorAction SilentlyContinue
        $err = Get-Content $stderr -Raw -ErrorAction SilentlyContinue
        $out = if ($null -eq $out) { "" } else { $out.Trim() }
        $err = if ($null -eq $err) { "" } else { $err.Trim() }
        if ($process.ExitCode -ne 0) {
            throw "$Path $($Arguments -join ' ') exited $($process.ExitCode). stdout: '$out' stderr: '$err'"
        }
        return $out
    } finally {
        Remove-Item -LiteralPath $stdout, $stderr -ErrorAction SilentlyContinue
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

$portablePath = Join-Path $artifactRootPath $portableName
$extractRoot = Join-Path $env:RUNNER_TEMP "dicepp-portable-$([guid]::NewGuid())"
try {
    Expand-Archive -LiteralPath $portablePath -DestinationPath $extractRoot
    $runtimeMatches = @(
        Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "DicePP-Runtime.exe"
    )
    if ($runtimeMatches.Count -ne 1) {
        throw "Portable must contain exactly one DicePP-Runtime.exe"
    }
    $programRoot = $runtimeMatches[0].Directory.FullName
    $runtime = Join-Path $programRoot "DicePP-Runtime.exe"
    $dashboard = Join-Path $programRoot "DicePP.exe"
    $updateGuard = Join-Path $programRoot "DicePP-UpdateGuard.exe"
    $expectedRuntime = "DicePP v${Version}"
    if ((Invoke-PackagedExe $runtime @("--version")) -ne $expectedRuntime) {
        throw "Portable Runtime version mismatch"
    }
    Invoke-PackagedExe $runtime @("--smoke-check") | Out-Null
    if ((Invoke-PackagedExe $dashboard @("--version")) -ne "DicePP Dashboard v${Version}") {
        throw "Portable Dashboard version mismatch"
    }
    Invoke-PackagedExe $dashboard @("--smoke-check") | Out-Null
    if ((Invoke-PackagedExe $updateGuard @("--version")) -ne $Version) {
        throw "Portable UpdateGuard version mismatch"
    }
    Invoke-PackagedExe $updateGuard @("--smoke-check") | Out-Null
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
$installer = $null
try {
    $installer = Start-Process `
        -FilePath (Resolve-Path -LiteralPath $setupPath) `
        -ArgumentList @("--silent", "--installto", $installRoot) `
        -PassThru `
        -WindowStyle Hidden
    if (-not $installer.WaitForExit(20000)) {
        taskkill /PID $installer.Id /T /F | Out-Null
        throw "Setup did not finish within 20 seconds; lifecycle hook may be blocked"
    }
    if ($installer.ExitCode -ne 0) {
        throw "Setup exited with code $($installer.ExitCode)"
    }
    $stableDashboard = Join-Path $installRoot "DicePP.exe"
    $payloadDashboard = Join-Path $installRoot "current\DicePP.exe"
    foreach ($path in @($stableDashboard, $payloadDashboard)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Setup installation is incomplete: $path"
        }
    }
    Invoke-DetachedLaunchSmoke $stableDashboard
} finally {
    if ($null -ne $installer -and -not $installer.HasExited) {
        taskkill /PID $installer.Id /T /F | Out-Null
    }
    if (Test-Path -LiteralPath $installRoot) {
        Remove-Item -LiteralPath $installRoot -Recurse -Force
    }
}
