param([string]$Python = 'python', [string]$InstallDir = (Join-Path $PSScriptRoot '..\.venv'), [switch]$Warmup)
$ErrorActionPreference = 'Stop'
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
& $Python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14), 'Use Python 3.10-3.13 (recommended: 3.12)'"
if ($LASTEXITCODE -ne 0) { throw 'Please install Python 3.12 first.' }
& $Python -m venv $InstallDir
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
$BabelPython = Join-Path $InstallDir 'Scripts\python.exe'
& $BabelPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not install pip.' }
& $BabelPython -m pip install -r (Join-Path $PSScriptRoot '..\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install BabelDOC. Check the network connection.' }
if ($Warmup) {
    & (Join-Path $InstallDir 'Scripts\babeldoc.exe') --warmup
    if ($LASTEXITCODE -ne 0) { throw 'Could not download BabelDOC assets. Retry with -Warmup.' }
}
Write-Host "`nPaste this Python path into Zotero > Tools > BabelDOC > Settings:`n$BabelPython" -ForegroundColor Green
