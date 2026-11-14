@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0.."
if errorlevel 1 exit /b 1
call "scripts\find_python.bat"
if not defined COACH_PYTHON (
  echo Python 3.12 or later is required. Use the Python included with the app.
  pause
  popd
  exit /b 1
)
if defined COACH_PY_BUNDLED (
  set "PYTHONHOME="
  set "PYTHONPATH="
  set "PYTHONSTARTUP="
  set "PYTHONNOUSERSITE=1"
)
call %COACH_PYTHON% -X utf8 -B "scripts\review_short_radio.py" --open
popd
endlocal
