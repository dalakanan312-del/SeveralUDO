@echo off
start "SeveralUDO Clock Relay" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0SeveralUDOClockRelay.ps1"
exit /b 0
