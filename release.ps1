# Baut den Release (self-contained, win-x64) nach artifacts\ und packt ihn samt skripte\ als Zip.
# Aufruf: pwsh ./release.ps1 [-Version 0.1.0]
param([string]$Version = "0.1.0")
$ErrorActionPreference = "Stop"
$wurzel = $PSScriptRoot
$name = "DiscDigitalisierung-v$Version-win-x64"
$ziel = Join-Path $wurzel "artifacts\$name"
$zip = Join-Path $wurzel "artifacts\$name.zip"

if (Test-Path $ziel) { Remove-Item $ziel -Recurse -Force }
if (Test-Path $zip) { Remove-Item $zip -Force }

dotnet publish (Join-Path $wurzel "src\DiscDigitalisierung.App\DiscDigitalisierung.App.csproj") `
    -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:DebugType=none -p:Version=$Version -o $ziel
if ($LASTEXITCODE -ne 0) { throw "dotnet publish fehlgeschlagen" }

$skripte = Join-Path $ziel "skripte"
New-Item -ItemType Directory -Force $skripte | Out-Null
Get-ChildItem (Join-Path $wurzel "skripte") -File |
    Where-Object { $_.Extension -in ".py", ".ini", ".md" } |
    Copy-Item -Destination $skripte
Copy-Item (Join-Path $wurzel "README.md"), (Join-Path $wurzel "LICENSE") $ziel
Copy-Item (Join-Path $wurzel "docs") $ziel -Recurse

Compress-Archive -Path $ziel -DestinationPath $zip
Get-Item $zip | Select-Object Name, @{ n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
