@echo off
title Reltix
cd /d "%~dp0"
rem One click: starts the app + the phone link, opens the dashboard. Close this window to stop.
where py >NUL 2>NUL && (py launch.py %* & goto end)
if exist "%USERPROFILE%\anaconda3\python.exe" ("%USERPROFILE%\anaconda3\python.exe" launch.py %* & goto end)
where python >NUL 2>NUL && (python launch.py %* & goto end)
echo Python was not found. Install it from https://www.python.org/downloads/ and try again.
:end
pause
