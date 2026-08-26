@echo off
title SeveralUDO Clock Sync Self-Test
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0SeveralUDOClockRelay.ps1" -SelfTest
echo.
if errorlevel 1 (
  echo Clock Sync needs attention. Open self_test_result.json in this folder for details.
) else (
  echo Clock Sync is installed correctly and the tracker accepted the private link.
)
echo.
pause
