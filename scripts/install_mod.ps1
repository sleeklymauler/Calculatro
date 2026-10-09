# Installs the Calculatro Lovely mod into %AppData%\Balatro\Mods.
# The mod starts the Python app with the game (the venv's pythonw.exe, named in
# the mod's launch.txt) unless -NoAutoStart is given.
param(
    [string]$GameDir = "C:\Program Files (x86)\Steam\steamapps\common\Balatro",
    [switch]$NoAutoStart
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

if (-not $NoAutoStart) {
    $pythonw = Join-Path $PSScriptRoot "..\.venv\Scripts\pythonw.exe"
    if (Test-Path $pythonw) {
        $pythonw = (Resolve-Path $pythonw).Path
        Set-Content -Encoding utf8NoBOM (Join-Path $dest "launch.txt") $pythonw
        Write-Host "The game will start the app with $pythonw"
    } else {
        Write-Warning "No venv at $(Split-Path $pythonw); the app won't start with the game. Set up the venv and re-run."
    }
}
