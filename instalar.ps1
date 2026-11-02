<#
.SYNOPSIS
    Instala el gestor de almacenamiento en este equipo (se puede repetir sin romper nada).

.DESCRIPTION
    1. Comprueba Windows 10/11, PowerShell 5.1 o 7 y Python 3.10 o superior. No instala Python por su cuenta:
       si falta, explica cómo instalarlo.
    2. Crea el entorno virtual .venv (si no existe) e instala requirements.txt (y requirements-dev.txt con -ConPruebas).
    3. Activa los hooks de privacidad del repositorio (si es un clon de git).
    4. Ejecuta la configuración inicial por consola (detecta el equipo y propone; tú confirmas).
       Si ya hay config\config.json, pregunta antes de repetirla.
    5. Pregunta si se crea el acceso directo en el Escritorio.

.EXAMPLE
    .\instalar.ps1
    .\instalar.ps1 -Desatendido -AccesoDirecto        # acepta la propuesta y crea el acceso directo
    .\instalar.ps1 -SinConfiguracion -SinAccesoDirecto # solo comprueba e instala el entorno
#>
param(
    [switch]$Desatendido,        # no pregunta: acepta la propuesta de la configuración inicial
    [switch]$SinConfiguracion,   # no ejecuta la configuración inicial
    [switch]$AccesoDirecto,      # crea el acceso directo sin preguntar
    [switch]$SinAccesoDirecto,   # no crea el acceso directo
    [switch]$ConPruebas,         # instala también las dependencias de las pruebas
    [string]$ConfigSalida        # ruta alternativa para config.json (pruebas)
)
$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
function Paso([string]$texto) { Write-Host "`n== $texto" -ForegroundColor Cyan }
function Preguntar([string]$texto, [bool]$porDefecto) {
    if ($Desatendido) { return $porDefecto }
    $opciones = if ($porDefecto) { '[S/n]' } else { '[s/N]' }
    $r = (Read-Host "$texto $opciones").Trim().ToLower()
    if (-not $r) { return $porDefecto }
    return $r.StartsWith('s')
}

# ------------------------------------------------------------------ 1. requisitos
Paso 'Comprobando requisitos'
$os = [Environment]::OSVersion.Version
if ($os.Major -ne 10 -or $os.Build -lt 17763) { throw "Se necesita Windows 10 (1809 o posterior) u 11. Este equipo: $os" }
Write-Host "Windows $($os.Major).0 compilación $($os.Build): correcto."
$ps = $PSVersionTable.PSVersion
if ($ps.Major -lt 5 -or ($ps.Major -eq 5 -and $ps.Minor -lt 1)) { throw "Se necesita PowerShell 5.1 o 7. Este equipo: $ps" }
Write-Host "PowerShell ${ps}: correcto (el motor usa powershell.exe 5.1, incluido en Windows)."

function Test-Python([string]$exe, [string[]]$extra) {
    try {
        $v = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.version_info >= (3, 10))" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$($v[-1])".Trim() -eq 'True') { return "$($v[0])".Trim() }
    } catch { }
    return $null
}
$python = $null
foreach ($c in @(@('py', @('-3')), @('python', @()))) {
    if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
        $ver = Test-Python $c[0] $c[1]
        if ($ver) { $python = $c; Write-Host "Python $ver encontrado ($($c[0]))."; break }
    }
}
$venvPython = Join-Path $raiz '.venv\Scripts\python.exe'
if (-not $python -and -not (Test-Path -LiteralPath $venvPython)) {
    Write-Host @'
No se encontró Python 3.10 o superior. Instálalo y vuelve a ejecutar este script:
  - Con winget:   winget install --id Python.Python.3.12 -e
  - O desde https://www.python.org/downloads/ (marca "Add python.exe to PATH").
'@ -ForegroundColor Yellow
    exit 1
}

# ------------------------------------------------------------------ 2. entorno virtual
Paso 'Entorno virtual (.venv)'
if ((Test-Path -LiteralPath $venvPython) -and (Test-Python $venvPython @())) {
    Write-Host 'Ya existe y funciona: se reutiliza.'
} else {
    & $python[0] @($python[1]) -m venv (Join-Path $raiz '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
    Write-Host 'Creado.'
}
$requisitos = @('requirements.txt') + $(if ($ConPruebas) { @('requirements-dev.txt') } else { @() })
foreach ($r in $requisitos) {
    $ruta = Join-Path $raiz $r
    $paquetes = @(Get-Content -LiteralPath $ruta | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') })
    if ($paquetes.Count) {
        & $venvPython -m pip install --disable-pip-version-check -q -r $ruta
        if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias de $r." }
        Write-Host "Dependencias de $r instaladas."
    } else { Write-Host "$r no tiene dependencias (solo biblioteca estándar)." }
}

# ------------------------------------------------------------------ 3. hooks de privacidad
if ((Test-Path -LiteralPath (Join-Path $raiz '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    Paso 'Hooks de privacidad del repositorio'
    & git -C $raiz config core.hooksPath .githooks
    Write-Host 'core.hooksPath = .githooks (solo en este repositorio).'
}

# ------------------------------------------------------------------ 4. configuración inicial
$config = if ($ConfigSalida) { $ConfigSalida } elseif ($env:GESTOR_CONFIG) { $env:GESTOR_CONFIG } else { Join-Path $raiz 'config\config.json' }
if (-not $SinConfiguracion) {
    Paso 'Configuración inicial'
    $hacer = $true
    if (Test-Path -LiteralPath $config) { $hacer = Preguntar "Ya hay una configuración ($config). ¿Repetir la configuración inicial?" $false }
    if ($hacer) {
        $argsCI = @('-m', 'gestor.configuracion_inicial', '--salida', $config)
        if ($Desatendido) { $argsCI += '--si' }
        if (Test-Path -LiteralPath $config) { $argsCI += '--sobrescribir' }   # ya confirmado arriba
        Push-Location $raiz
        try { & $venvPython @argsCI } finally { Pop-Location }
        if ($LASTEXITCODE -gt 1) { throw 'La configuración inicial no terminó bien.' }
    } else { Write-Host 'Se conserva la configuración actual.' }
}

# ------------------------------------------------------------------ 5. acceso directo
if (-not $SinAccesoDirecto) {
    if ($AccesoDirecto -or (Preguntar '¿Crear el acceso directo "Gestor de Almacenamiento" en el Escritorio?' $false)) {
        Paso 'Acceso directo'
        & (Join-Path $raiz 'scripts\crear-acceso-directo.ps1')
    }
}

Paso 'Listo'
Write-Host 'Abre el gestor con el acceso directo o con .\iniciar.ps1. La primera vez que lo abras sin configuración, la'
Write-Host 'pestaña Equipo te guía. Guía completa: docs\ADAPTAR-A-TU-EQUIPO.md'
