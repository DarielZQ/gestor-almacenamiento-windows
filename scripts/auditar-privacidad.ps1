<#
.SYNOPSIS
    Audita el repositorio para que no contenga datos personales, rutas reales, secretos ni archivos prohibidos.

.DESCRIPTION
    Revisa el contenido versionado (o el preparado para commit con -Preparado) y, salvo -SinHistorial, todo el
    historial de git (diffs, mensajes e identidades de autor y committer). Termina con código 1 si encuentra algo.

    Este script solo contiene patrones genéricos:
      - correos personales por dominio, rutas de perfil C:\Users\<nombre>, rutas de otras unidades fuera de la
        taxonomía, tokens y claves, contraseñas escritas, números de cuenta y de identificación;
      - el usuario de Windows, el perfil, el nombre del equipo y la identidad global de git, leídos en el momento;
      - archivos que no deben versionarse.
    Los términos privados se definen localmente en config\privacidad.local.txt (no versionado; ver el .example):
    un término por línea (sin distinguir mayúsculas) o, si la línea empieza por "re:", una expresión regular.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\auditar-privacidad.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\auditar-privacidad.ps1 -Preparado -SinHistorial
#>
param(
    [switch]$Preparado,      # revisar solo lo que está en el índice (hook pre-commit)
    [switch]$SinHistorial,   # no revisar el historial
    [string]$MensajeCommit   # revisar solo este archivo de mensaje (hook commit-msg)
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding

$raiz = (& git rev-parse --show-toplevel 2>$null)
if (-not $raiz) { throw 'Ejecuta este script dentro del repositorio.' }
Set-Location -LiteralPath $raiz
$hallazgos = New-Object System.Collections.ArrayList
function Add-Hallazgo([string]$donde, [string]$motivo) { [void]$hallazgos.Add("$donde -> $motivo") }

# ---------------------------------------------------------------- términos privados (nunca escritos aquí)
$terminos = New-Object System.Collections.Generic.List[string]     # literales, sin distinguir mayúsculas
$expresiones = New-Object System.Collections.Generic.List[string]  # líneas "re:" de la lista local
function Add-Termino([string]$t, [int]$minimo = 3) {
    if ($t -and $t.Trim().Length -ge $minimo -and -not $terminos.Contains($t.Trim())) { $terminos.Add($t.Trim()) }
}
Add-Termino $env:USERNAME
Add-Termino $env:COMPUTERNAME
Add-Termino $env:USERPROFILE
$nombreGlobal = (& git config --global user.name 2>$null)
$correoGlobal = (& git config --global user.email 2>$null)
if ($nombreGlobal) { Add-Termino $nombreGlobal; foreach ($p in ($nombreGlobal -split '\s+')) { if ($p.Length -ge 4) { Add-Termino $p } } }
if ($correoGlobal -and $correoGlobal -notmatch 'noreply') { Add-Termino $correoGlobal; Add-Termino ($correoGlobal -split '@')[0] }
$listaLocal = Join-Path $raiz 'config\privacidad.local.txt'
if (Test-Path -LiteralPath $listaLocal) {
    foreach ($l in Get-Content -LiteralPath $listaLocal -Encoding UTF8) {
        $l = $l.Trim()
        if (-not $l -or $l.StartsWith('#')) { continue }
        if ($l.StartsWith('re:')) { $expresiones.Add($l.Substring(3)) } else { Add-Termino $l 2 }
    }
} else {
    Write-Host 'Aviso: no existe config\privacidad.local.txt; solo se aplican los patrones genéricos.' -ForegroundColor Yellow
}

function New-ReTerminos($lista) {
    $partes = @($lista | ForEach-Object { [regex]::Escape($_) })
    if (-not $partes.Count) { return $null }
    return '(?i)(' + ($partes -join '|') + ')'
}
$reTerminos = New-ReTerminos $terminos
# En las identidades de los commits se admite exactamente el nombre de autor configurado en este repo.
$autorPermitido = (& git config --local user.name 2>$null)
$reIdentidad = New-ReTerminos @($terminos | Where-Object { $_ -ne $autorPermitido })

# ---------------------------------------------------------------- patrones genéricos
$permitidosUnidad = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
try {
    $reglas = Get-Content -LiteralPath (Join-Path $raiz 'config\reglas.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $reglas.taxonomia.PSObject.Properties) { if ($p.Name -notlike '_*') { [void]$permitidosUnidad.Add(([string]$p.Value).Split('\')[0]) } }
    foreach ($x in @($reglas.raiz_destino.propias_extra)) { [void]$permitidosUnidad.Add([string]$x) }
} catch { }
$patrones = [ordered]@{
    'correo personal'              = '(?i)[A-Za-z0-9._%+-]+@(gmail|googlemail|hotmail|outlook|live|yahoo|icloud|proton|protonmail)\.[a-z.]+'
    'ruta de perfil real'          = '(?i)\b[A-Za-z]:(\\{1,2}|/)Users(\\{1,2}|/)(?!<|Public\b|Default\b|Usuario\b|\$|%|\{)[A-Za-z0-9._-]+'
    'token de GitHub'              = '\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'
    'clave de API'                 = '\b(sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})'
    'clave privada'                = '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    'contraseña o secreto escrito' = '(?i)\b(password|passwd|contrase(n|ñ)a|api[_-]?key|secret)\b\s*[:=]\s*[''"][^''"\s]{6,}[''"]'
    'número de cuenta (IBAN)'      = '\b[A-Z]{2}\d{2} ?(\d{4} ?){4,7}\d{0,4}\b'
    'número de identificación'     = '\b\d-\d{4}-\d{4}\b'
}
$reUnidad = '(?i)\b([D-Z]):(\\{1,2})([^\\/:*?"<>|\r\n''`,;)\]]+)'

function Test-Texto([string]$donde, [string]$texto, $reVetados = $reTerminos) {
    $n = 0
    foreach ($linea in ($texto -split "`n")) {
        $n++
        if ($reVetados -and $linea -match $reVetados) { Add-Hallazgo "${donde}:$n" 'término privado'; continue }
        $privada = $false
        foreach ($e in $expresiones) { if ($linea -cmatch $e) { $privada = $true; break } }
        if ($privada) { Add-Hallazgo "${donde}:$n" 'término privado'; continue }
        foreach ($k in $patrones.Keys) { if ($linea -match $patrones[$k]) { Add-Hallazgo "${donde}:$n" $k } }
        foreach ($m in [regex]::Matches($linea, $reUnidad)) {
            $segmento = $m.Groups[3].Value.Trim()
            if ($segmento -and -not $permitidosUnidad.Contains($segmento) -and $segmento -notmatch '^(<|\$|%|\{)') {
                Add-Hallazgo "${donde}:$n" "ruta de unidad fuera de la taxonomía genérica ($($m.Groups[1].Value):\$segmento)"
            }
        }
    }
}

# ---------------------------------------------------------------- solo el mensaje de commit (hook commit-msg)
if ($MensajeCommit) {
    $texto = (Get-Content -LiteralPath $MensajeCommit -Encoding UTF8 | Where-Object { -not $_.StartsWith('#') }) -join "`n"
    Test-Texto 'mensaje de commit' $texto
    if ($hallazgos.Count) { $hallazgos | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }; Write-Host 'Mensaje de commit rechazado por la auditoría de privacidad.' -ForegroundColor Red; exit 1 }
    exit 0
}

# ---------------------------------------------------------------- archivos versionados
$archivos = if ($Preparado) { @(& git diff --cached --name-only --diff-filter=ACMR) } else { @(& git ls-files) }
$todos = @(& git ls-files)
$prohibidos = '(?i)(\.(db|sqlite3?|csv|log|env|lnk|pyc|pst|ost)$|^(\.venv|venv|__pycache__|node_modules|datos)/|/(__pycache__|node_modules)/|^config/(config\.json|reglas\.local\.json|privacidad\.local\.txt)$)'
foreach ($f in $todos) { if ($f -match $prohibidos) { Add-Hallazgo $f 'archivo que no debe versionarse' } }
foreach ($f in $archivos) {
    if (-not $f) { continue }
    if ($f -match '(?i)\.(png|jpe?g|gif|ico|webp|bmp)$') { Add-Hallazgo $f 'imagen: revísala a mano (no se admiten capturas con datos reales)'; continue }
    $contenido = (& git show ":$f" 2>$null) -join "`n"
    Test-Texto $f $contenido
}

# ---------------------------------------------------------------- identidad del próximo commit
if ($Preparado) {
    foreach ($v in 'GIT_AUTHOR_IDENT', 'GIT_COMMITTER_IDENT') {
        $ident = (& git var $v 2>$null) -replace '\s+\d+\s+[+-]\d{4}$', ''
        Test-Texto "identidad $v" $ident $reIdentidad
    }
}

# ---------------------------------------------------------------- historial completo
if (-not $SinHistorial -and (& git rev-parse --verify --quiet HEAD 2>$null)) {
    $log = (& git log --all -p --no-color --format='commit %H%n%B') -join "`n"
    Test-Texto 'historial' $log
    Test-Texto 'mensajes' ((& git log --all --format='%B') -join "`n")
    $ids = (& git log --all --format='%an <%ae>|%cn <%ce>') -join "`n"
    Test-Texto 'identidades' $ids $reIdentidad
}

# ---------------------------------------------------------------- resultado
if ($hallazgos.Count) {
    Write-Host "Auditoría de privacidad: $($hallazgos.Count) hallazgos" -ForegroundColor Red
    $hallazgos | Select-Object -First 200 | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    exit 1
}
$alcance = if ($Preparado) { 'cambios preparados' } else { "$($archivos.Count) archivos versionados" }
if (-not $SinHistorial) { $alcance += ' + historial completo' }
Write-Host "Auditoría de privacidad: limpia ($alcance; $($terminos.Count + $expresiones.Count) términos privados y $($patrones.Count + 1) patrones genéricos)." -ForegroundColor Green
exit 0
