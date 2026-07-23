param(
    [string]$DistDir = "dist/DicePP",
    [string]$LauncherSource = "dist/DicePP.exe",
    [string]$UpdateGuardSource = "dist/DicePP-UpdateGuard.exe"
)

$ErrorActionPreference = "Stop"

function Copy-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required file does not exist: $Source"
    }
    $parent = Split-Path -Parent $Destination
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

if (-not (Test-Path -LiteralPath $DistDir -PathType Container)) {
    throw "Distribution directory does not exist: $DistDir"
}

if (Test-Path -LiteralPath $LauncherSource -PathType Leaf) {
    Copy-RequiredFile -Source $LauncherSource -Destination (Join-Path $DistDir "DicePP.exe")
    if (-not (Test-Path -LiteralPath (Join-Path $DistDir "DicePP.exe") -PathType Leaf)) {
        throw "Copy failed: $(Join-Path $DistDir 'DicePP.exe') does not exist"
    }
    Remove-Item -LiteralPath $LauncherSource -Force
    if (Test-Path -LiteralPath $LauncherSource) {
        throw "Temporary launcher still exists: $LauncherSource"
    }
}

Copy-RequiredFile `
    -Source $UpdateGuardSource `
    -Destination (Join-Path $DistDir "DicePP-UpdateGuard.exe")

Copy-RequiredFile -Source "config/global.json" -Destination (Join-Path $DistDir "config/global.json")
Copy-RequiredFile -Source "config/bots/_template.json" -Destination (Join-Path $DistDir "config/bots/_template.json")

$localizedReadmeName = ([char]0x4f7f) + ([char]0x7528) + ([char]0x8bf4) + ([char]0x660e) + ".md"
Copy-RequiredFile -Source "docs/windows-package-readme.md" -Destination (Join-Path $DistDir $localizedReadmeName)

$docFiles = @(
    "windows.md",
    "configuration.md",
    "persona.md",
    "persona-character-card.md"
)

foreach ($doc in $docFiles) {
    Copy-RequiredFile -Source (Join-Path "docs" $doc) -Destination (Join-Path (Join-Path $DistDir "docs") $doc)
}

Write-Host "Windows package assembled at $DistDir"
