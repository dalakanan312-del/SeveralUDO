@echo off
title Install or Update SeveralUDO Clock Sync
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install or Update SeveralUDO Clock Sync.ps1"
echo.
if errorlevel 1 echo Installation did not finish. Read the error above; no Sims save was changed.
pause
