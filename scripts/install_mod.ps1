# Installs the Calculatro Lovely mod into %AppData%\Balatro\Mods.
param(
    [string]$GameDir = "C:\Program Files (x86)\Steam\steamapps\common\Balatro"
)
$ErrorActionPreference = "Stop"

$src = Join-Path $PSScriptRoot "..\mod\Calculatro"
$modsDir = Join-Path $env:APPDATA "Balatro\Mods"
$dest = Join-Path $modsDir "Calculatro"

# Newer Lovely releases ship winmm.dll; older ones shipped version.dll.
$lovely = @("winmm.dll", "version.dll") | Where-Object { Test-Path (Join-Path $GameDir $_) }
if (-not $lovely) {
    Write-Warning "Lovely injector not found in $GameDir (expected winmm.dll or version.dll)."
    Write-Warning "Download it from https://github.com/ethangreen-dev/lovely-injector/releases and place the DLL next to Balatro.exe."
}

New-Item -ItemType Directory -Force $modsDir | Out-Null
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Copy-Item -Recurse $src $dest
Write-Host "Installed Calculatro mod to $dest"
