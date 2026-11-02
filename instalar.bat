@echo off
rem Instala el gestor de almacenamiento (se puede repetir sin romper nada).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
pause
