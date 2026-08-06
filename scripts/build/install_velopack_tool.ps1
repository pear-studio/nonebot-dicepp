param(
    [string]$VersionFile = (Join-Path $PSScriptRoot "velopack-tool-version.txt")
)

$ErrorActionPreference = "Stop"
$versionPath = (Resolve-Path -LiteralPath $VersionFile).Path
$version = (Get-Content -LiteralPath $versionPath -Raw).Trim()
if ($version -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$') {
    throw "Velopack tool version file must contain an exact SemVer version"
}

& dotnet tool install -g vpk --version $version
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install vpk $version"
}
if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_OUTPUT)) {
    "velopack_version=$version" >> $env:GITHUB_OUTPUT
}
