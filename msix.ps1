# Packs dist\Shiftdial (run build.ps1 first) into dist\Shiftdial-<version>.msix for the Microsoft Store.
# The Store signs the package, so it is left unsigned. Defaults are the reserved identity from Partner Center
# (Product identity page); override them only for a different reservation:
#   powershell -ExecutionPolicy Bypass -File msix.ps1 -Name 12345Shiftdial.Shiftdial -Publisher "CN=..." -PublisherDisplayName "..."
param(
    [string]$Name = "Shiftdial.Shiftdial",
    [string]$Publisher = "CN=B7392B70-F658-464F-9B22-D0F9A5E4FEAD",
    [string]$PublisherDisplayName = "Shiftdial",
    [string]$Version = "1.0.0.0"  # the Store needs the last part to be 0
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path dist\Shiftdial\Shiftdial.exe)) { throw "Run build.ps1 first." }

$makeappx = $env:MAKEAPPX
if (-not $makeappx) {
    $makeappx = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe", "build\sdk\bin\*\x64\makeappx.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName | Select-Object -Last 1 -ExpandProperty FullName
}
if (-not $makeappx) { throw "makeappx.exe not found: install the Windows SDK, or set MAKEAPPX to its path." }

$stage = "build\msix"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item dist\Shiftdial $stage -Recurse
New-Item -ItemType Directory -Force "$stage\Assets" | Out-Null

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -c @"
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pathlib import Path
from shiftdial.main import make_icon
for name, size in (('Square44x44Logo', 44), ('Square150x150Logo', 150), ('StoreLogo', 50)):
    make_icon(size).pixmap(size, size).save(str(Path(r'$stage', 'Assets', name + '.png')))
"@
if ($LASTEXITCODE -ne 0) { throw "icon render failed" }

$manifest = (Get-Content packaging\AppxManifest.xml -Raw) `
    -replace "\{\{Name\}\}", $Name -replace "\{\{Publisher\}\}", [Security.SecurityElement]::Escape($Publisher) `
    -replace "\{\{PublisherDisplayName\}\}", [Security.SecurityElement]::Escape($PublisherDisplayName) `
    -replace "\{\{Version\}\}", $Version
Set-Content "$stage\AppxManifest.xml" $manifest -Encoding utf8

$out = "dist\Shiftdial-$Version.msix"
& $makeappx pack /d $stage /p $out /o
if ($LASTEXITCODE -ne 0) { throw "makeappx failed" }
Write-Host "Built $out"
