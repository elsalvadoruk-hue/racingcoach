@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto folder_error

call "scripts\find_python.bat"
if not defined COACH_PYTHON goto no_python
call %COACH_PYTHON% "scripts\launch_app.py" %*
goto finished

:no_python
echo Python 3.10 or later was not found.
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
