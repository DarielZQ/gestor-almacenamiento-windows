<#
.SYNOPSIS
    Arranca el gestor de almacenamiento (servidor local en 127.0.0.1) y abre el navegador.

.DESCRIPTION
    No instala nada ni cambia la configuración del sistema.
    - Si el gestor ya está corriendo (responde a /salud con su firma), solo abre el navegador.
    - Si no, lo lanza DESACOPLADO con pythonw.exe (sin ventana): cerrar esta consola no lo detiene.
      Para detenerlo: botón "Detener servidor" en Ajustes o .\detener.ps1.
    Python se busca por este orden: .venv del proyecto, lanzador "py" (3.10+), "python" del PATH.

.EXAMPLE
    .\iniciar.ps1
    .\iniciar.ps1 -Consola          # en primer plano, con la salida en esta ventana (diagnóstico)
    .\iniciar.ps1 -Puerto 8800 -NoAbrir
#>
param(
    [int]$Puerto = 0,
    [switch]$NoAbrir,
    [switch]$Consola
)
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
$firma = 'gestor-almacenamiento-windows'

function Get-PuertoActivo {
    $candidatos = @()
    $info = Join-Path $raiz 'datos\servidor.json'
    if (Test-Path -LiteralPath $info) { try { $candidatos += [int](Get-Content -LiteralPath $info -Raw | ConvertFrom-Json).puerto } catch { } }
    if ($Puerto) { $candidatos += $Puerto } else { $candidatos += 8742..8761 }
    foreach ($p in ($candidatos | Select-Object -Unique)) {
        try {
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:$p/salud" -TimeoutSec 1 -UseBasicParsing
            if ($r.app -eq $firma) { return $p }
        } catch { }
    }
    return $null
}

$activo = Get-PuertoActivo
if ($activo) {
    Write-Host "El gestor ya está abierto en http://127.0.0.1:$activo/"
    if (-not $NoAbrir) { Start-Process "http://127.0.0.1:$activo/" }
    return
}

function Test-Python([string]$exe, [string[]]$extra) {
    try {
        $v = & $exe @extra -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq 'True')
    } catch { return $false }
}

# Candidatos: (python de consola, python sin ventana, argumentos extra)
$candidatos = @()
$venv = Join-Path $raiz '.venv\Scripts'
if (Test-Path -LiteralPath (Join-Path $venv 'python.exe')) { $candidatos += , @((Join-Path $venv 'python.exe'), (Join-Path $venv 'pythonw.exe'), @()) }
if (Get-Command py -ErrorAction SilentlyContinue) { $candidatos += , @('py', 'pyw', @('-3')) }
$sistema = Get-Command python -ErrorAction SilentlyContinue
if ($sistema) { $candidatos += , @($sistema.Source, (Join-Path (Split-Path $sistema.Source) 'pythonw.exe'), @()) }

$elegido = $null
foreach ($c in $candidatos) { if (Test-Python $c[0] $c[2]) { $elegido = $c; break } }
if (-not $elegido) { throw 'No se encontró Python 3.10 o superior. Ejecuta instalar.ps1 o instala Python desde python.org.' }

$argumentos = @($elegido[2]) + @('-m', 'gestor')
if ($Puerto) { $argumentos += @('--puerto', "$Puerto") }
if ($NoAbrir) { $argumentos += '--no-abrir' }

if ($Consola) {
    Push-Location $raiz
    try { & $elegido[0] @argumentos } finally { Pop-Location }
    return
}

$sinVentana = $elegido[1]
if (-not (Get-Command $sinVentana -ErrorAction SilentlyContinue)) { $sinVentana = $elegido[0] }
Start-Process -FilePath $sinVentana -ArgumentList $argumentos -WorkingDirectory $raiz -WindowStyle Hidden
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    $activo = Get-PuertoActivo
    if ($activo) { Write-Host "Gestor iniciado en http://127.0.0.1:$activo/ (sin ventana). Para detenerlo: .\detener.ps1"; return }
}
Write-Warning "El gestor no respondió a tiempo. Revisa datos\servidor.log o prueba con: .\iniciar.ps1 -Consola"
