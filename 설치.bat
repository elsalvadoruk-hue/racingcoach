@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto folder_error

set "COACH_PYTHON="
if exist ".venv\Scripts\python.exe" set "COACH_PYTHON=.venv\Scripts\python.exe"
if not defined COACH_PYTHON (
  where python >nul 2>nul
  if not errorlevel 1 set "COACH_PYTHON=python"
)
if not defined COACH_PYTHON (
  where py >nul 2>nul
  if not errorlevel 1 set "COACH_PYTHON=py -3"
)
if not defined COACH_PYTHON (
  echo Python was not found. Install Python 3.10 or later ^(check "Add Python to PATH"^) and run this again.
  popd
  pause
  exit /b 1
)

echo Installing the analysis and live coach packages...
%COACH_PYTHON% -m pip install -r "iRacing_Backend\requirements_core.txt" -r "iRacing_Backend\requirements_voice.txt"
if errorlevel 1 (
  echo.
  echo Installation failed. Check the internet connection and the messages above.
  popd
  pause
  exit /b 1
)
echo.
echo Done. Start the app with the launcher next to this file.
popd
pause
exit /b 0

:folder_error
echo Unable to open the project folder.
pause
exit /b 1
