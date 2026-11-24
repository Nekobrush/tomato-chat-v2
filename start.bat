@echo off
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto usepython

py -3 launcher.py %*
goto finished

:usepython
python launcher.py %*

:finished
if errorlevel 1 (
  echo.
  echo 启动失败，请把上面的报错发给开发者。
  pause
)
