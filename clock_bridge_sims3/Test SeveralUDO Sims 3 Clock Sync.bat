@echo off
title SeveralUDO Sims 3 Clock Sync Self-Test
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0SeveralUDOClockRelay.ps1" -SelfTest
if errorlevel 1 (
  echo Clock Sync needs attention. Open self_test_result.json in this folder for details.
) else (
  echo Sims 3 Clock Sync is ready. Run Report Sims 3 Clock Now.bat after noting the current in-game day and time.
)
pause
