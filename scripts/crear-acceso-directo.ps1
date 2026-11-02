<#
.SYNOPSIS
    Crea (o regenera) el acceso directo "Gestor de Almacenamiento.lnk" en el Escritorio del usuario actual.

.DESCRIPTION
    El acceso directo ejecuta iniciar.ps1 en una ventana minimizada. iniciar.ps1 arranca el servidor local
    (solo 127.0.0.1) y abre el navegador; si el gestor ya está abierto, solo abre el navegador.
    Las rutas se resuelven en el momento: no hay rutas del equipo escritas en este script.
    El .lnk vive fuera del repositorio y no se versiona.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\crear-acceso-directo.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\crear-acceso-directo.ps1 -Destino "$env:TEMP"
#>
param(
    [string]$Destino = [Environment]::GetFolderPath('Desktop'),
    [string]$Nombre = 'Gestor de Almacenamiento'
)
$ErrorActionPreference = 'Stop'

$raiz = Split-Path -Parent $PSScriptRoot
$iniciar = Join-Path $raiz 'iniciar.ps1'
if (-not (Test-Path -LiteralPath $iniciar)) { throw "No se encuentra $iniciar" }
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$ruta = Join-Path $Destino "$Nombre.lnk"

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($ruta)
$lnk.TargetPath = $powershell
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$iniciar`""
$lnk.WorkingDirectory = $raiz
$lnk.WindowStyle = 7   # minimizada
$lnk.Description = 'Gestor de almacenamiento: libera C: moviendo a D: solo lo que apruebes'
$lnk.IconLocation = (Join-Path $env:SystemRoot 'System32\imageres.dll') + ',27'
$lnk.Save()
Write-Host "Acceso directo creado: $ruta"
