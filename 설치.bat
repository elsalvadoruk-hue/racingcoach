@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto folder_error

call "scripts\find_python.bat"
if not defined COACH_PYTHON goto no_python
echo Using Python %COACH_PY_VERSION% ^(%COACH_PYTHON%^)
if defined COACH_PY_NEW (
  echo This Python is newer than the tested 3.12 and 3.13. Trying it anyway.
)

rem PyYAML works without its optional C part; this skips compiling it when no ready-made wheel exists
rem for this Python, so no C++ compiler is needed. A ready-made wheel, when there is one, is still used.
set "PYYAML_FORCE_LIBYAML=0"

echo Installing the analysis and live coach packages...
rem "call" lets this file continue when python is itself a .bat or .cmd (pyenv-win shims, for example).
call %COACH_PYTHON% -m pip install --prefer-binary -r "iRacing_Backend\requirements_core.txt" -r "iRacing_Backend\requirements_voice.txt"
if errorlevel 1 goto install_failed
echo.
echo Preparing the screen from the GitHub source...
call %COACH_PYTHON% "scripts\install_ui.py"
if errorlevel 1 goto ui_install_failed
echo.
echo Done. Start the app with the launcher next to this file.
popd
pause
exit /b 0

:ui_install_failed
echo.
echo Screen setup failed. Check the messages above and run this installer again.
popd
pause
exit /b 1

:install_failed
echo.
if defined COACH_PY_NEW (
  echo Installation failed on Python %COACH_PY_VERSION%.
  echo Some packages may not be ready for this new Python yet.
  echo Install Python 3.12 and run this again. You do not need to remove the other Python.
  echo   - With the Python install manager, open a command window and run:  py install 3.12
  echo   - Or download Python 3.12 from python.org and check "Add Python to PATH".
) else (
  echo Installation failed. Check the internet connection and the messages above.
)
popd
pause
exit /b 1

:no_python
echo.
echo Python 3.10 or later was not found.
echo Install Python 3.12 and run this again.
echo   - With the Python install manager, open a command window and run:  py install 3.12
echo   - Or download Python 3.12 from python.org and check "Add Python to PATH".
popd
pause
exit /b 1

:folder_error
echo Unable to open the project folder.
pause
exit /b 1
