@echo off
chcp 65001 >nul
title tomato-chat
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto usepython

py -3 launcher.py %*
set "CODE=%errorlevel%"
goto finished

:usepython
python launcher.py %*
set "CODE=%errorlevel%"

:finished
if "%CODE%"=="0" goto end
echo.
if "%CODE%"=="2" goto running
echo [ERROR] startup failed with exit code %CODE% - see the log above.
goto pauseit

:running
echo [INFO] tomato-chat is already running; this window can be closed.

:pauseit
pause

:end