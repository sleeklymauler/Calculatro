# Builds the player release, dist\Calculatro-<version>-windows.zip (not
# Calculatro-<version>.zip, the name of the "Source code" zip GitHub adds to
# every release): the mod with its own Python (the official embeddable package) and the app in its python
# folder, which the mod runs when there's no launch.txt. Players unzip it into
# %AppData%\Balatro\Mods; no Python install needed.
#
# Runs on Windows PowerShell 5.1 and PowerShell 7. -Tag (e.g. v0.1.0, as the
# release workflow passes) must match the version, which must be the same in
# pyproject.toml, the mod's lovely.toml and Calculatro.json, and calculatro\__init__.py.
param(
    [string]$Tag
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest's progress bar slows 5.1's downloads to a crawl

# The bundled Python. To update, take the 64-bit embeddable package's SHA-256
# from python.org (its release page, or
# https://www.python.org/api/v2/downloads/release_file/?release=<id>).
$PythonVersion = "3.14.8"
$PythonSha256 = "a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dist = Join-Path $root "dist"

function Get-Version([string]$file) {
    $found = Select-String -Path (Join-Path $root $file) -Pattern '^(?:__)?version(?:__)?\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $found) { throw "No version in $file" }
    $found.Matches[0].Groups[1].Value
}
$version = Get-Version "pyproject.toml"
$modVersion = Get-Version "mod\Calculatro\lovely.toml"
if ($version -ne $modVersion) {
    throw "pyproject.toml's version ($version) differs from lovely.toml's ($modVersion)"
}
$steamoddedVersion = (Get-Content -Raw (Join-Path $root "mod\Calculatro\Calculatro.json") | ConvertFrom-Json).version
if ($version -ne $steamoddedVersion) {
    throw "pyproject.toml's version ($version) differs from Calculatro.json's ($steamoddedVersion)"
}
$appVersion = Get-Version "calculatro\__init__.py"
if ($version -ne $appVersion) {
    throw "pyproject.toml's version ($version) differs from calculatro\__init__.py's ($appVersion)"
}
if ($Tag -and $Tag -ne "v$version") {
    throw "Tag $Tag doesn't match the version ($version); expected v$version"
}

function Get-Sha256([string]$path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try { -join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString("x2") }) }
    finally { $stream.Dispose(); $sha.Dispose() }
}

# Download the embeddable package once, checking it against the pinned hash.
$cache = Join-Path $dist "cache"
New-Item -ItemType Directory -Force $cache | Out-Null
$pythonZip = Join-Path $cache "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $pythonZip) -or (Get-Sha256 $pythonZip) -ne $PythonSha256) {
    $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Write-Host "Downloading $url"
    Invoke-WebRequest $url -OutFile $pythonZip -UseBasicParsing
    $hash = (Get-Sha256 $pythonZip)
    if ($hash -ne $PythonSha256) {
        Remove-Item $pythonZip
        throw "$url has SHA-256 $hash, expected $PythonSha256"
    }
}

# Lay out the mod folder as players will have it.
$stage = Join-Path $dist "stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
$mod = Join-Path $stage "Calculatro"
New-Item -ItemType Directory -Force $mod | Out-Null
Copy-Item (Join-Path $root "mod\Calculatro\*") $mod -Exclude "launch.txt" -Recurse
Copy-Item (Join-Path $root "LICENSE") $mod
# The repository's lovely.toml has dump_lua = true, for development: Lovely then writes out
# every patched game file on each launch. Players don't need that.
$manifest = Join-Path $mod "lovely.toml"
$toml = [System.IO.File]::ReadAllText($manifest)
$toml = [regex]::Replace($toml, '(?m)^dump_lua\s*=\s*true\b', 'dump_lua = false')
if ($toml -notmatch '(?m)^dump_lua = false\b') { throw "No dump_lua setting found in lovely.toml's manifest" }
[System.IO.File]::WriteAllText($manifest, $toml, (New-Object System.Text.UTF8Encoding $false))
$python = Join-Path $mod "python"
Expand-Archive $pythonZip $python
# The package goes next to pythonw.exe: the embeddable package's ._pth file
# puts its own folder (".") on sys.path, so -m calculatro finds it.
$app = Join-Path $python "calculatro"
# Every .py file of the package and its subpackages (calculatro\engine), without __pycache__.
$package = Join-Path $root "calculatro"
Get-ChildItem $package -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch '\\__pycache__\\' } | ForEach-Object {
    $dest = Join-Path $app $_.FullName.Substring($package.Length + 1)
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Copy-Item $_.FullName $dest
}

# Check the bundled Python can import and run the app.
& (Join-Path $python "python.exe") -m calculatro --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "The bundled Python couldn't run the app" }
# And that it can start worker processes ("Cores used" above 1) and work a state's odds out in them: the
# embeddable package has to bring multiprocessing, and a worker has to import the app again. The odds of a
# Pair with a 9 held and the other three unseen, among 8 cards to draw 4 from: `odds --cores` fails if the
# workers don't start.
$schema = & (Join-Path $python "python.exe") -c "from calculatro.state import SCHEMA; print(SCHEMA)"
$check = Join-Path $stage "check-state.json"
$cards = { param($suit, $ranks) ($ranks | ForEach-Object { "{`"rank_id`": $_, `"suit`": `"$suit`"}" }) -join ", " }
$state = "{`"schema`": $schema, `"in_run`": true, `"state`": `"SELECTING_HAND`", `"hand_size`": 5, " +
    "`"hands_left`": 2, `"discards_left`": 2, `"hand`": [$(& $cards 'Hearts' @(2, 3, 4, 5, 9))], " +
    "`"unseen`": [$(& $cards 'Spades' @(6, 7, 8, 9, 10, 11, 12, 13))], `"tracked`": {`"hands`": [`"Pair`"]}}"
[System.IO.File]::WriteAllText($check, $state, (New-Object System.Text.UTF8Encoding $false))
$odds = & (Join-Path $python "python.exe") -m calculatro odds $check --cores 2 | Out-String
if ($LASTEXITCODE -ne 0 -or $odds -notmatch 'Pair\s+0\.00%') {
    throw "The bundled Python couldn't work out odds in worker processes:`n$odds"
}
Remove-Item $check
Get-ChildItem $app -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

$zip = Join-Path $dist "Calculatro-$version-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($mod, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
Write-Host "Built $zip ($([math]::Round((Get-Item $zip).Length / 1MB, 1)) MB, Python $PythonVersion)"
