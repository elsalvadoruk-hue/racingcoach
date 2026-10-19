@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto folder_error

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\launch_app.py" %*
  goto finished
)
if exist "iRacing_Backend\.venv\Scripts\python.exe" (
  "iRacing_Backend\.venv\Scripts\python.exe" "scripts\launch_app.py" %*
  goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
  python "scripts\launch_app.py" %*
  goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "scripts\launch_app.py" %*
  goto finished
)
echo Python was not found. Install Python 3.10 or later and try again.
popd
pause
exit /b 1

:finished
set "TUNER_EXIT_CODE=%errorlevel%"
popd
if not "%TUNER_EXIT_CODE%"=="0" pause
exit /b %TUNER_EXIT_CODE%

:folder_error
echo Unable to open the project folder.
pause
exit /b 1
