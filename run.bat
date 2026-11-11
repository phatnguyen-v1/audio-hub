@echo off
rem Start Audio Hub without a console window. Pass --browser to use the browser instead.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m audiohub %*
