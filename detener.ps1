<#
.SYNOPSIS
    Detiene el servidor local del gestor de almacenamiento (solo ese proceso).

.DESCRIPTION
    1. Pide la parada por HTTP (POST /api/detener con el token de la sesión, como la interfaz).
    2. Si no responde, usa el PID de datos\servidor.pid, pero solo si ese proceso es Python ejecutando
       "-m gestor" desde esta carpeta. Nunca detiene otros procesos.
#>
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
$datos = Join-Path $raiz 'datos'
$info = Join-Path $datos 'servidor.json'
if (-not (Test-Path -LiteralPath $info)) { Write-Host 'No hay datos de un servidor en marcha.'; return }
$puerto = [int](Get-Content -LiteralPath $info -Raw | ConvertFrom-Json).puerto
$base = "http://127.0.0.1:$puerto"

try {
    $salud = Invoke-RestMethod -Uri "$base/salud" -TimeoutSec 2 -UseBasicParsing
    if ($salud.app -eq 'gestor-almacenamiento-windows') {
        $html = (Invoke-WebRequest -Uri "$base/" -TimeoutSec 5 -UseBasicParsing).Content
        $token = [regex]::Match($html, 'name="gestor-token" content="([^"]+)"').Groups[1].Value
        Invoke-RestMethod -Uri "$base/api/detener" -Method Post -Body '{}' -ContentType 'application/json' `
            -Headers @{ 'X-Gestor-Token' = $token } -TimeoutSec 5 -UseBasicParsing | Out-Null
        Write-Host "Servidor detenido ($base)."
        return
    }
} catch { }

$archivoPid = Join-Path $datos 'servidor.pid'
if (-not (Test-Path -LiteralPath $archivoPid)) { Write-Host 'El servidor no responde y no hay PID guardado.'; return }
$id = [int](Get-Content -LiteralPath $archivoPid -Raw)
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
if ($proc -and $proc.Name -match '^pythonw?\.exe$' -and $proc.CommandLine -match '-m gestor') {
    Stop-Process -Id $id
    Write-Host "Servidor detenido (PID $id)."
} else {
    Write-Host 'El servidor no está en marcha.'
}
