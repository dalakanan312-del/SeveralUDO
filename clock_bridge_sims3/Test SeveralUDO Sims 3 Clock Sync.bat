@echo off
title SeveralUDO Sims 3 Clock Sync Self-Test
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0SeveralUDOClockRelay.ps1" -SelfTest
if errorlevel 1 (
  echo Clock Sync needs attention. Open self_test_result.json in this folder for details.
) else (
  echo Sims 3 Clock Sync is ready. Start the relay, then open a Sims 3 save for automatic clock reports.
)
pause
