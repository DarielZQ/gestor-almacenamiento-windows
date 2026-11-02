@echo off
rem Arranca el gestor de almacenamiento y abre el navegador.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar.ps1" %*
