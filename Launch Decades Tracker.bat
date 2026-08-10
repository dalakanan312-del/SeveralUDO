@echo off
setlocal
cd /d "%~dp0"
title Decades Tracker
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch Decades Tracker.ps1"
if errorlevel 1 (
  echo.
  echo Decades Tracker could not start.
  echo Please check your internet connection on first launch and try again.
  echo.
  pause
)
