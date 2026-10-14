@echo off
rem The front door of /dapilot:quickstart on Windows.
rem
rem   quickstart.bat [file or folder]
rem
rem quickstart.py is Python, so something has to find a Python before it can run. This does that
rem and nothing else: it looks for an interpreter the pipeline's packages support (3.10 to 3.13),
rem installs one with winget when there is none, and hands over to quickstart.py with the same
rem arguments. The venv, Blender, Chrome, ffmpeg and the accounts are quickstart.py's job.
rem
rem Set DAPILOT_BOOTSTRAP_ONLY=1 to print the interpreter it would use and stop: a check, not a run.
setlocal EnableExtensions
set "HERE=%~dp0"
set "PY="
set "CHECK=import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)"

call :find
if defined PY goto :run

where winget >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 to 3.13 is needed and winget is not available. Install Python 3.12 from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run quickstart again.
  exit /b 2
)
echo Installing Python 3.12 with winget. Windows may ask for permission ^(a minute or two^)...
winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
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
