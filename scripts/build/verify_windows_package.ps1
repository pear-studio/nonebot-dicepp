param(
    [Parameter(Mandatory = $true)]
    [string]$DistDir,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$resolvedDist = (Resolve-Path -LiteralPath $DistDir).Path
$sourceRuntime = Join-Path $resolvedDist "DicePP-Runtime.exe"
$sourceLauncher = Join-Path $resolvedDist "DicePP.exe"
if (-not (Test-Path -LiteralPath $sourceRuntime) -or -not (Test-Path -LiteralPath $sourceLauncher)) {
    throw "Portable directory must contain DicePP.exe and DicePP-Runtime.exe"
}

if (-not $Version) {
    $projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $versionLine = Select-String -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"$' | Select-Object -First 1
    if (-not $versionLine) { throw "Cannot read project version from pyproject.toml" }
    $Version = $versionLine.Matches[0].Groups[1].Value
}

function Get-ListeningPids([int]$Port) {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

$tempRoot = (Resolve-Path -LiteralPath ([System.IO.Path]::GetTempPath())).Path
$verifyDist = Join-Path $tempRoot ("DicePP-verify-" + [guid]::NewGuid().ToString("N"))
$process = $null
$ports = @(4090, 8080)
try {
    New-Item -ItemType Directory -Path $verifyDist -Force | Out-Null
    Get-ChildItem -LiteralPath $resolvedDist -Force | Copy-Item -Destination $verifyDist -Recurse -Force

    $runtime = Join-Path $verifyDist "DicePP-Runtime.exe"
    $launcher = Join-Path $verifyDist "DicePP.exe"
    $runtimeVersion = (& $runtime --version | Out-String).Trim()
    if ($runtimeVersion -ne "DicePP v$Version") {
        throw "Runtime version mismatch: expected DicePP v$Version, got $runtimeVersion"
    }

    foreach ($port in $ports) {
        if (@(Get-ListeningPids $port).Count -gt 0) {
            throw "Port $port is already in use"
        }
    }

    $process = Start-Process -FilePath $launcher -ArgumentList @("--background") `
        -WorkingDirectory $verifyDist -WindowStyle Hidden -PassThru
    $ready = $false
    for ($i = 0; $i -lt 45; $i++) {
        $health = (& curl.exe --silent --output NUL --write-out "%{http_code}" --max-time 2 `
            "http://127.0.0.1:4090/api/health").Trim()
        $onebot = (& curl.exe --silent --output NUL --write-out "%{http_code}" --max-time 2 `
            "http://127.0.0.1:8080/").Trim()
        if ($health -eq "200" -and $onebot -ne "000") {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Portable background runtime did not expose Dashboard and Bot ports" }
}
finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    foreach ($port in $ports) {
        $listenerPids = @(Get-ListeningPids $port)
        foreach ($processId in $listenerPids) {
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
    }
    if ((Split-Path -Parent $verifyDist) -ne $tempRoot) {
        throw "Refusing to remove verification directory outside the system temp directory"
    }
    Remove-Item -LiteralPath $verifyDist -Recurse -Force -ErrorAction SilentlyContinue
}
