@echo off
rem The front door of /dapilot:quickstart on Windows.
rem
rem   quickstart.bat [file or folder]
rem
rem quickstart.py is Python, so something has to find a Python before it can run. This does that
rem and nothing else: it looks for an interpreter the pipeline's packages support (3.10 to 3.13),
rem installs one when there is none (winget, else the official standalone CPython build unpacked
rem under the store, no admin rights needed), and hands over to quickstart.py with the same
rem arguments. The venv, Blender, Chrome, ffmpeg and the accounts are quickstart.py's job.
rem
rem Set DAPILOT_BOOTSTRAP_ONLY=1 to print the interpreter it would use and stop: a check, not a run.
setlocal EnableExtensions
set "HERE=%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PY="
set "CHECK=import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)"

call :find
if defined PY goto :run

where winget >nul 2>nul
if errorlevel 1 (
  rem winget missing: Windows LTSC, Server, or a machine where the Store app was removed. Microsoft ships
  rem it as the App Installer package, which installs per user without admin rights. Two documented ways,
  rem the second for machines without the PowerShell Gallery: the WinGet client module's repair command,
  rem then the package bundle and its two dependencies from Microsoft's own links.
  echo Setting up the Windows package manager ^(winget^), a minute or two...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference='SilentlyContinue'; $ok=$false;" ^
    "try { Install-PackageProvider -Name NuGet -Force -Scope CurrentUser -ErrorAction Stop | Out-Null; Install-Module Microsoft.WinGet.Client -Force -Scope CurrentUser -Repository PSGallery -ErrorAction Stop; Repair-WinGetPackageManager -ErrorAction Stop; $ok=$true } catch { }" ^
    "if (-not $ok) { try { $t=Join-Path $env:TEMP 'dapilot-winget'; New-Item -ItemType Directory -Force -Path $t | Out-Null;" ^
    "  Invoke-WebRequest -Uri 'https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx' -OutFile (Join-Path $t 'vclibs.appx') -UseBasicParsing;" ^
    "  Invoke-WebRequest -Uri 'https://github.com/microsoft/microsoft-ui-xaml/releases/download/v2.8.6/Microsoft.UI.Xaml.2.8.x64.appx' -OutFile (Join-Path $t 'xaml.appx') -UseBasicParsing;" ^
    "  Invoke-WebRequest -Uri 'https://aka.ms/getwinget' -OutFile (Join-Path $t 'winget.msixbundle') -UseBasicParsing;" ^
    "  Add-AppxPackage (Join-Path $t 'vclibs.appx'); Add-AppxPackage (Join-Path $t 'xaml.appx'); Add-AppxPackage (Join-Path $t 'winget.msixbundle'); $ok=$true } catch { Write-Host ('winget could not be set up: ' + $_.Exception.Message) } }" ^
    "if ($ok) { Write-Host 'winget is ready.' }"
  rem the new winget lives under WindowsApps, which this shell's PATH does not have yet
  if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe" set "PATH=%LOCALAPPDATA%\Microsoft\WindowsApps;%PATH%"
)
where winget >nul 2>nul
if not errorlevel 1 (
  echo Installing Python 3.12 with winget. Windows may ask for permission ^(a minute or two^)...
  winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
  call :find
  if defined PY goto :run
)
rem No winget, or it did not deliver: the official standalone CPython build, unpacked under the store.
rem No admin rights needed. Verified against the release's SHA256SUMS before it is unpacked.
if defined DAPILOT_STORE_DIR (set "PY_HOME=%DAPILOT_STORE_DIR%\python") else (set "PY_HOME=%USERPROFILE%\.dapilot\python")
echo Downloading Python 3.12 ^(about 30 MB^)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' };" ^
  "$rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest' -Headers @{ 'User-Agent' = 'dapilot' };" ^
  "$asset = $rel.assets | Where-Object { $_.name -match ('^cpython-3\.12\.\d+\+\d+-' + $arch + '-pc-windows-msvc-install_only\.tar\.gz$') } | Select-Object -First 1;" ^
  "if (-not $asset) { throw 'no Python 3.12 build for ' + $arch };" ^
  "$tmp = Join-Path $env:TEMP ('dapilot-py-' + [guid]::NewGuid());" ^
  "New-Item -ItemType Directory -Path $tmp | Out-Null;" ^
  "$file = Join-Path $tmp $asset.name;" ^
  "Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $file -UseBasicParsing;" ^
  "$sums = $rel.assets | Where-Object { $_.name -eq 'SHA256SUMS' } | Select-Object -First 1;" ^
  "if ($sums) { $want = ((Invoke-WebRequest -Uri $sums.browser_download_url -UseBasicParsing).Content -split \"`n\" | Where-Object { $_ -match ('\s' + [regex]::Escape($asset.name) + '$') } | Select-Object -First 1) -split '\s+' | Select-Object -First 1; $have = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower(); if (-not $want -or $want -ne $have) { throw 'checksum mismatch' } };" ^
  "$dest = '%PY_HOME%';" ^
  "if (Test-Path ($dest + '.new')) { Remove-Item ($dest + '.new') -Recurse -Force };" ^
  "New-Item -ItemType Directory -Path ($dest + '.new') -Force | Out-Null;" ^
  "tar -xzf $file -C ($dest + '.new');" ^
  "if (Test-Path $dest) { Remove-Item $dest -Recurse -Force };" ^
  "New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null;" ^
  "Move-Item (Join-Path ($dest + '.new') 'python') $dest;" ^
  "Remove-Item ($dest + '.new') -Recurse -Force; Remove-Item $tmp -Recurse -Force;" ^
  "Write-Host ('Python installed under ' + $dest)"
call :find
if defined PY goto :run

rem Nothing in the supported range. Any Python 3 can still run quickstart.py, which then reports
rem the version problem in its own words; otherwise stop here.
for %%C in (py python python3) do (
  if not defined PY (
    %%C -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul && set "PY=%%C"
  )
)
if not defined PY (
  echo No Python 3 was found, so setup cannot start. Install Python 3.12 from https://www.python.org/downloads/ and run quickstart again.
  exit /b 2
)

:run
if "%DAPILOT_BOOTSTRAP_ONLY%"=="1" (
  %PY% -c "import sys; print(sys.executable, sys.version.split()[0])"
  exit /b 0
)
%PY% "%HERE%quickstart.py" %*
exit /b %errorlevel%

:find
rem Order: a pinned DAPILOT_PYTHON, the py launcher by version, then python on PATH, then the
rem folders winget and the python.org installer use (a fresh install is not on this shell's PATH yet).
rem Each candidate must pass CHECK, which also skips the Microsoft Store placeholder python.exe.
if defined DAPILOT_PYTHON (
  "%DAPILOT_PYTHON%" -c "%CHECK%" >nul 2>nul && set PY="%DAPILOT_PYTHON%"
  if defined PY exit /b 0
)
rem a Python this script installed earlier, under the store
if defined DAPILOT_STORE_DIR (set "STORE_PY=%DAPILOT_STORE_DIR%\python\python.exe") else (set "STORE_PY=%USERPROFILE%\.dapilot\python\python.exe")
if exist "%STORE_PY%" (
  "%STORE_PY%" -c "%CHECK%" >nul 2>nul && set PY="%STORE_PY%"
  if defined PY exit /b 0
)
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "%CHECK%" >nul 2>nul && set "PY=py -%%V"
  )
)
if defined PY exit /b 0
python -c "%CHECK%" >nul 2>nul && set "PY=python" && exit /b 0
for %%V in (313 312 311 310) do (
  if not defined PY (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
      "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" -c "%CHECK%" >nul 2>nul && set PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    )
    if not defined PY if exist "%ProgramFiles%\Python%%V\python.exe" (
      "%ProgramFiles%\Python%%V\python.exe" -c "%CHECK%" >nul 2>nul && set PY="%ProgramFiles%\Python%%V\python.exe"
    )
  )
)
exit /b 0
