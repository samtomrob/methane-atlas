# Rebuild the Methane Atlas desktop app.
#
# The site is baked into the executable, so the exe is a snapshot: refresh the
# data and it keeps showing yesterday's map until this is run again. Order
# matters — pipeline data, then the static export, then the package.
#
#     powershell -ExecutionPolicy Bypass -File desktop\build.ps1

$ErrorActionPreference = "Stop"
$proj = Split-Path -Parent $PSScriptRoot
Set-Location $proj

Write-Host "1/3  building the static site" -ForegroundColor Cyan
Set-Location (Join-Path $proj "web")
npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }

Set-Location $proj
$mb = [math]::Round((Get-ChildItem web\out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "     site is $mb MB" -ForegroundColor DarkGray

Write-Host "2/3  refreshing the icon" -ForegroundColor Cyan
& "C:\Python313\python.exe" desktop\make_icon.py

Write-Host "3/3  packaging the executable" -ForegroundColor Cyan
# A running instance holds a lock on the exe and the build fails late and
# confusingly. Close it first.
Get-Process "Methane Atlas" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400
& "C:\Python313\python.exe" -m PyInstaller MethaneAtlas.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Get-Item "dist\Methane Atlas.exe"
Write-Host ""
Write-Host "done: $($exe.FullName)" -ForegroundColor Green
Write-Host "      $([math]::Round($exe.Length/1MB,1)) MB, built $($exe.LastWriteTime)" -ForegroundColor DarkGray
Write-Host "      the desktop shortcut points here already; no need to recreate it" -ForegroundColor DarkGray
