@echo off
rem Picks the Python that installs and runs the coach; the install and start bat files both call this
rem so the packages land in the same interpreter that later starts the app.
rem   COACH_PYTHON      command to run, empty when no Python 3.10 or later was found
rem   COACH_PY_VERSION  version of the picked Python
rem   COACH_PY_NEW      1 when the picked Python is 3.14 or later (newer than the tested 3.12 and 3.13)
rem Any Python 3.10 or later is tried, but 3.12 and 3.13 come first even when a newer one is the default:
rem a brand-new Python can lack ready-made wheels for some packages for a while.
rem stdin comes from nul so a launcher that offers to install a missing version cannot wait for input.
set "COACH_PYTHON="
set "COACH_PY_VERSION="
set "COACH_PY_NEW="
set "COACH_PY_BUNDLED="

rem Ignore other Python settings even during the bundled interpreter's version probe.
call :probe 10 99 runtime\python\python.exe -E -s
if defined COACH_PYTHON (
  set "COACH_PY_BUNDLED=1"
  goto picked
)

rem A virtual environment the user set up comes first.
rem (No quotes: a quoted program inside for /f gets its quotes stripped by cmd.)
call :probe 10 99 .venv\Scripts\python.exe
if defined COACH_PYTHON goto picked
call :probe 10 99 iRacing_Backend\.venv\Scripts\python.exe
if defined COACH_PYTHON goto picked

rem The tested versions.
call :probe 12 13 py -3.12
if defined COACH_PYTHON goto picked
call :probe 12 13 py -3.13
if defined COACH_PYTHON goto picked
call :probe 12 13 python
if defined COACH_PYTHON goto picked

rem Older versions still work.
call :probe 10 11 python
if defined COACH_PYTHON goto picked
call :probe 10 11 py -3.11
if defined COACH_PYTHON goto picked
call :probe 10 11 py -3.10
if defined COACH_PYTHON goto picked

rem Newer versions are tried last.
call :probe 14 99 python
if defined COACH_PYTHON goto picked
call :probe 14 99 py -3
if defined COACH_PYTHON goto picked
exit /b 0

:picked
for /f "tokens=1,2 delims=." %%a in ("%COACH_PY_VERSION%") do if %%b GEQ 14 set "COACH_PY_NEW=1"
exit /b 0

:probe
rem %1 %2 = lowest and highest accepted minor version of Python 3, the rest = the command.
set "COACH_LOW=%~1"
set "COACH_HIGH=%~2"
shift
shift
set "COACH_CMD="
:probe_args
if "%~1"=="" goto probe_run
if defined COACH_CMD (set "COACH_CMD=%COACH_CMD% %1") else (set "COACH_CMD=%1")
shift
goto probe_args
:probe_run
if not defined COACH_CMD exit /b 0
set "COACH_FOUND="
for /f "usebackq delims=" %%v in (`%COACH_CMD% -c "import sys; v = sys.version_info; print('%%d.%%d.%%d' %% v[:3] if v[0] == 3 and %COACH_LOW% <= v[1] <= %COACH_HIGH% else '')" ^<nul 2^>nul`) do set "COACH_FOUND=%%v"
if defined COACH_FOUND (
  set "COACH_PYTHON=%COACH_CMD%"
  set "COACH_PY_VERSION=%COACH_FOUND%"
)
exit /b 0
