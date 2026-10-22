@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto folder_error

call "scripts\find_python.bat"
if not defined COACH_PYTHON goto no_python
if defined COACH_PY_BUNDLED (
  set "PYTHONHOME="
  set "PYTHONPATH="
  set "PYTHONSTARTUP="
  set "PYTHONNOUSERSITE=1"
)
call %COACH_PYTHON% "scripts\launch_app.py" %* & goto finished
rem An update may rewrite this file while the app runs. "& goto finished" above then finds the way back
rem by the label, wherever the lines moved, so keep the ":finished" label.
goto finished

:no_python
echo Python 3.10 or later was not found.
echo Download racingcoach-setup-2.0.3.zip from the latest GitHub Releases page.
echo It includes Python and the screen. Extract it and run its installer.
echo For a source installation instead:
echo Install Python 3.12, run the installer bat file once, then try again.
echo   - With the Python install manager:  py install 3.12
echo   - Or download Python 3.12 from python.org and check "Add Python to PATH".
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
