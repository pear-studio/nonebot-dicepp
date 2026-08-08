param(
    [string]$DistDir = "dist/DicePP",
    [string]$Source = "scripts/build/windows_launcher_shim.cpp"
)

$ErrorActionPreference = "Stop"

$distribution = [System.IO.Path]::GetFullPath($DistDir)
$sourcePath = [System.IO.Path]::GetFullPath($Source)
$frozenApplication = Join-Path $distribution "DicePP.exe"
$application = Join-Path $distribution "DicePP-App.exe"
$shim = Join-Path $distribution "DicePP.exe"
$object = Join-Path $distribution "windows_launcher_shim.obj"

if (-not (Test-Path -LiteralPath $frozenApplication -PathType Leaf)) {
    throw "Frozen Dashboard executable is missing: $frozenApplication"
}
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Windows launcher shim source is missing: $sourcePath"
}
if (Test-Path -LiteralPath $application) {
    throw "Windows launcher target already exists: $application"
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
    throw "Visual Studio locator is unavailable: $vswhere"
}
$visualStudio = (& $vswhere -latest -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath).Trim()
$developerCommand = Join-Path $visualStudio "Common7\Tools\VsDevCmd.bat"
if (-not $visualStudio -or -not (Test-Path -LiteralPath $developerCommand -PathType Leaf)) {
    throw "MSVC x64 build tools are unavailable"
}

Move-Item -LiteralPath $frozenApplication -Destination $application
try {
    $compileCommand = '"{0}" -no_logo -arch=x64 -host_arch=x64 && cl.exe /nologo /O2 /MT /EHsc /DUNICODE /D_UNICODE "{1}" /Fo:"{2}" /link /SUBSYSTEM:WINDOWS /OUT:"{3}" shell32.lib' -f `
        $developerCommand, $sourcePath, $object, $shim
    & $env:COMSPEC /d /s /c $compileCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Windows launcher shim compilation failed with exit code $LASTEXITCODE"
    }
} finally {
    if (Test-Path -LiteralPath $object -PathType Leaf) {
        Remove-Item -LiteralPath $object -Force
    }
}
if (-not (Test-Path -LiteralPath $shim -PathType Leaf)) {
    throw "Windows launcher shim was not created: $shim"
}

Write-Host "Windows launcher shim assembled at $shim"
