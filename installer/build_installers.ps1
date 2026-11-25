# Builds everything that goes on a GitHub release, into dist\:
#   MetaHuman-for-Blender-<version>.zip          the extension (install from disk in Blender)
#   MetaHuman-for-Blender-<version>-Setup.exe    Windows installer (Inno Setup)
#   MetaHuman-for-Blender-<version>-macOS.zip    macOS installer (double-click .command scripts)
# The version comes from addon\mfb\blender_manifest.toml.
# Usage: powershell -File installer\build_installers.ps1
# Needs Blender 4.2+ and Inno Setup 6 (winget install JRSoftware.InnoSetup). Set BLENDER or ISCC
# to point at them if they are not in the usual place.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force $dist | Out-Null

$blender = if ($env:BLENDER) { $env:BLENDER } else {
    (Get-ChildItem "C:\Program Files\Blender Foundation\*\blender.exe" | Sort-Object FullName | Select-Object -Last 1).FullName }
$iscc = if ($env:ISCC) { $env:ISCC } else {
    @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1 }
if (-not $blender) { throw "Blender not found; set BLENDER to blender.exe" }
if (-not $iscc) { throw "Inno Setup not found; install it with: winget install JRSoftware.InnoSetup" }

$manifest = Get-Content (Join-Path $root "addon\mfb\blender_manifest.toml")
$version = ($manifest | Where-Object { $_ -match '^version\s*=' }) -replace '^version\s*=\s*"([^"]+)".*', '$1'
"Version $version"

# 1. Extension zip, built from a copy of the add-on without caches
$stage = Join-Path $env:TEMP "mfb_build_$PID"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
robocopy (Join-Path $root "addon\mfb") (Join-Path $stage "mfb") /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
& $blender --command extension build --source-dir (Join-Path $stage "mfb") --output-dir $stage 2>&1 | Select-String "created|error"
$built = Join-Path $stage "mfb-$version.zip"
if (-not (Test-Path $built)) { throw "Blender did not build $built" }
& $blender --command extension validate $built 2>&1 | Select-String "Success|error"
$extZip = Join-Path $dist "MetaHuman-for-Blender-$version.zip"
Copy-Item $built $extZip -Force
Copy-Item $built (Join-Path $dist "mfb-$version.zip") -Force

# 2. Windows installer
& $iscc /Q "/DAppVersion=$version" "/DExtensionZip=$built" "/O$dist" (Join-Path $root "installer\windows\MetaHumanForBlender.iss")
if ($LASTEXITCODE) { throw "Inno Setup failed" }

# 3. macOS installer
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py).Source }
& $py (Join-Path $root "installer\package_macos.py") $version $built (Join-Path $dist "MetaHuman-for-Blender-$version-macOS.zip")

Remove-Item -Recurse -Force $stage
Get-ChildItem $dist -Filter "MetaHuman-for-Blender-$version*" | Select-Object Name, Length
