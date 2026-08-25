param(
    [Parameter(Mandatory = $true)]
    [string]$DistDir,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$resolvedDist = (Resolve-Path -LiteralPath $DistDir).Path
$runtime = Join-Path $resolvedDist "DicePP-Runtime.exe"
$launcher = Join-Path $resolvedDist "DicePP.exe"
if (-not (Test-Path -LiteralPath $runtime) -or -not (Test-Path -LiteralPath $launcher)) {
    throw "Portable directory must contain DicePP.exe and DicePP-Runtime.exe"
}

if (-not $Version) {
    $projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $versionLine = Select-String -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"$' | Select-Object -First 1
    if (-not $versionLine) { throw "Cannot read project version from pyproject.toml" }
    $Version = $versionLine.Matches[0].Groups[1].Value
}

$runtimeVersion = (& $runtime --version | Out-String).Trim()
if ($runtimeVersion -ne "DicePP v$Version") {
    throw "Runtime version mismatch: expected DicePP v$Version, got $runtimeVersion"
}

function Get-ListeningPids([int]$Port) {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

$ports = @(4090, 8080)
foreach ($port in $ports) {
    if (@(Get-ListeningPids $port).Count -gt 0) {
        throw "Port $port is already in use"
    }
}

$process = $null
try {
    $process = Start-Process -FilePath $launcher -ArgumentList @("--background") `
        -WorkingDirectory $resolvedDist -WindowStyle Hidden -PassThru
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
}
