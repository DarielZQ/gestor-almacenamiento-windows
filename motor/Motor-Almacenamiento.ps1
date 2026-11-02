<#
.SYNOPSIS
    Libera espacio en C: clasificando archivos personales hacia D: (nunca borra archivos personales).

.DESCRIPTION
    Por defecto corre en SIMULACION: no mueve ni borra nada, solo muestra y registra lo que haría.
    Agrega -Ejecutar para aplicar los cambios. Cada movimiento queda en un CSV que permite deshacerlo.

    Las reglas de clasificación y protección viven en config\reglas.json (compartido con el gestor
    visual). Si existe config\reglas.local.json se fusiona encima con los valores propios del equipo.

    Fases:
      1. Archivos sueltos de Descargas y Escritorio  -> taxonomía en D:   (siempre)
      2. Subcarpetas de Descargas                    -> D:               (-IncluirCarpetasDescargas)
      3. Cachés pesadas regenerables                 -> junction a D:    (-ReubicarCaches; no borra nada)
      4. Carpetas pesadas de apps (enlaces_app)      -> junction a D:    (-EnlazarVMApp, con la app cerrada)
      5. Carpetas y archivos sueltos en la raíz de D: -> taxonomía en D: (-ReorganizarRaizD)

    Modos para el gestor visual (no ejecutan las fases anteriores):
      -Analizar <json>      Clasifica una lista de rutas y dice si son intocables. Solo lectura.
      -ListarReferencias    Lista rutas usadas por programas instalados, accesos directos y PATH. Solo lectura.
      -PlanAprobado <json>  Mueve SOLO las operaciones origen->destino aprobadas. Sin -Ejecutar solo valida.
      -SalidaJson <ruta>    Archivo donde se escribe el resultado legible por máquina (también con -Deshacer).

.EXAMPLE
    .\Motor-Almacenamiento.ps1
    .\Motor-Almacenamiento.ps1 -Ejecutar
    .\Motor-Almacenamiento.ps1 -Ejecutar -IncluirCarpetasDescargas
    .\Motor-Almacenamiento.ps1 -Ejecutar -ReubicarCaches
    .\Motor-Almacenamiento.ps1 -Ejecutar -EnlazarVMApp
    .\Motor-Almacenamiento.ps1 -ReorganizarRaizD
    .\Motor-Almacenamiento.ps1 -Ejecutar -Deshacer "D:\Registro_Migracion_DiscoC\migracion_2026-11-01_1530.csv"
    .\Motor-Almacenamiento.ps1 -PlanAprobado plan.json -SalidaJson resultado.json -Ejecutar
#>
[CmdletBinding()]
param(
    [switch]$Ejecutar,
    [switch]$IncluirCarpetasDescargas,
    [switch]$ReubicarCaches,
    [switch]$EnlazarVMApp,       # carpetas de apps de reglas.json -> enlaces_app (habilitadas)
    [switch]$ReorganizarRaizD,
    [switch]$OmitirSueltos,      # no procesar la fase 1 (útil para correr solo otra fase)
    [string]$Deshacer,
    [string]$RaizDestino,        # sin valor fijo: sale de -Config (config\config.json) si no se pasa
    [string]$Config,             # configuración del equipo generada por la configuración inicial
    [int]$OmitirRecientesHoras = 24,
    # --- Gestor visual ---
    [string]$PlanAprobado,
    [string]$Analizar,
    [switch]$ListarReferencias,
    [string]$SalidaJson,
    [string]$DirRegistro,
    [string]$Reglas
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$modo = if ($Ejecutar) { 'EJECUCION' } else { 'SIMULACION' }
$marca = Get-Date -Format 'yyyy-MM-dd_HHmm'

# ---------------------------------------------------------------------------
# 0. Reglas compartidas (config\reglas.json + config\reglas.local.json)
# ---------------------------------------------------------------------------
function Merge-Reglas($base, $extra) {
    # Objetos: se combinan clave a clave. Listas y textos: se reemplazan completos.
    foreach ($p in $extra.PSObject.Properties) {
        $actual = $base.PSObject.Properties[$p.Name]
        if ($actual -and $actual.Value -is [pscustomobject] -and $p.Value -is [pscustomobject]) { Merge-Reglas $actual.Value $p.Value }
        elseif ($actual) { $actual.Value = $p.Value }
        else { $base | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value }
    }
}

function Get-Prop($objeto, [string]$nombre, $defecto = $null) {
    if ($null -ne $objeto -and $objeto.PSObject.Properties[$nombre]) { return $objeto.PSObject.Properties[$nombre].Value }
    return $defecto
}

function Read-Json([string]$ruta) { return Get-Content -LiteralPath $ruta -Raw -Encoding UTF8 | ConvertFrom-Json }

if (-not $Reglas) {
    $Reglas = @((Join-Path $PSScriptRoot '..\config\reglas.json'), (Join-Path $PSScriptRoot 'config\reglas.json')) |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Reglas -or -not (Test-Path -LiteralPath $Reglas)) { throw 'No se encuentra config\reglas.json (pásalo con -Reglas).' }
$Reglas = (Resolve-Path -LiteralPath $Reglas).ProviderPath
$dirConfig = Split-Path -Parent $Reglas

# Configuración del equipo (capas: valores del repo -> config.json del equipo -> reglas.local.json personal).
if (-not $Config -and $env:GESTOR_CONFIG) { $Config = $env:GESTOR_CONFIG }
if (-not $Config) { $candidata = Join-Path $dirConfig 'config.json'; if (Test-Path -LiteralPath $candidata) { $Config = $candidata } }
$C = $null
if ($Config) {
    if (-not (Test-Path -LiteralPath $Config)) { throw "No existe la configuración $Config" }
    $C = Read-Json $Config
}

$R = Read-Json $Reglas
# Taxonomía en otro idioma (config.taxonomia = 'en'...), encima de la de reglas.json.
$idioma = [string](Get-Prop $C 'taxonomia' 'es')
$taxIdioma = Join-Path $dirConfig "taxonomias\$idioma.json"
if ($idioma -and $idioma -ne 'es' -and (Test-Path -LiteralPath $taxIdioma)) { Merge-Reglas $R (Read-Json $taxIdioma) }
$reglasLocales = [IO.Path]::ChangeExtension($Reglas, '.local.json')
if (Test-Path -LiteralPath $reglasLocales) { Merge-Reglas $R (Read-Json $reglasLocales) }

# ---------------------------------------------------------------------------
# 0. Verificaciones previas
# ---------------------------------------------------------------------------
if (-not $RaizDestino) { $RaizDestino = [string](Get-Prop $C 'raiz_destino' '') }
if (-not $RaizDestino) {
    throw 'No hay destino configurado. Ejecuta instalar.ps1 (configuración inicial) o pasa -RaizDestino.'
}
if (-not (Test-Path -LiteralPath $RaizDestino)) {
    throw "No se encuentra $RaizDestino. ¿Está conectado el disco de destino?"
}
$RaizDestino = [IO.Path]::GetFullPath($RaizDestino)
if (-not $RaizDestino.EndsWith('\')) { $RaizDestino += '\' }
$letraDestino = $RaizDestino.Substring(0, 1)
# DriveInfo en lugar de Get-Volume: misma información y sin cargar el módulo Storage (más de 1 s).
$volDestino = New-Object IO.DriveInfo $letraDestino
# Junctions: solo en NTFS y si la configuración no las desactiva (por ejemplo, destino USB).
$permitirJunctions = [bool](Get-Prop $C 'permitir_junctions' $true)
if ($volDestino.DriveFormat -ne 'NTFS') {
    if ($ReubicarCaches -or $EnlazarVMApp) { throw "$RaizDestino no es NTFS; las junctions no funcionarían." }
    $permitirJunctions = $false
}

$perfil = $env:USERPROFILE
$claveCarpetas = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
$descargas = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty $claveCarpetas).'{374DE290-123F-4565-9164-39C4925E467B}')
$escritorio = [Environment]::GetFolderPath('Desktop')
$documentos = [Environment]::GetFolderPath('MyDocuments')
$raizOneDrive = $env:OneDrive

# Dónde se escribe el CSV. Los modos de solo lectura (-Analizar, -ListarReferencias) no escriben nada.
if (-not $DirRegistro -and $Ejecutar -and (Get-Prop $C 'dir_registro')) { $DirRegistro = [string](Get-Prop $C 'dir_registro') }
if (-not $DirRegistro) { $DirRegistro = if ($Ejecutar) { Join-Path $RaizDestino 'Registro_Migracion_DiscoC' } else { $PSScriptRoot } }
if ($Deshacer) { $prefijo = 'deshacer'; $marca = Get-Date -Format 'yyyy-MM-dd_HHmmss' }
elseif ($PlanAprobado) { $prefijo = if ($Ejecutar) { 'migracion_plan' } else { 'simulacion_plan' }; $marca = Get-Date -Format 'yyyy-MM-dd_HHmmss' }
else { $prefijo = if ($Ejecutar) { 'migracion' } else { 'simulacion' } }
$registro = Join-Path $DirRegistro "${prefijo}_$marca.csv"

if (-not ($Analizar -or $ListarReferencias)) {
    Write-Host "=== Motor-Almacenamiento | Modo: $modo ===" -ForegroundColor Cyan
    Write-Host "Descargas : $descargas"
    Write-Host "Escritorio: $escritorio"
    Write-Host "Destino   : $RaizDestino  (libre: $([math]::Round($volDestino.AvailableFreeSpace/1GB,1)) GB)"
    Write-Host "Reglas    : $Reglas"
    Write-Host "Registro  : $registro`n"
}

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
$reservadas = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
$resumen = @{}
$script:bytesDesdeC = [long]0

function Get-RutaLibre([string]$dir, [string]$nombre) {
    # Nunca sobrescribe: si el nombre existe (o ya está reservado en esta corrida) agrega " (2)", " (3)"...
    $base = [IO.Path]::GetFileNameWithoutExtension($nombre)
    $ext = [IO.Path]::GetExtension($nombre)
    $candidata = Join-Path $dir $nombre
    $n = 2
    while ((Test-Path -LiteralPath $candidata) -or $reservadas.Contains($candidata)) {
        $candidata = Join-Path $dir ("{0} ({1}){2}" -f $base, $n, $ext)
        $n++
    }
    [void]$reservadas.Add($candidata)
    return $candidata
}

function Write-Registro([string]$tipo, [string]$origen, [string]$destino, [string]$categoria, [long]$bytes, [string]$estado) {
    $dirReg = Split-Path -Parent $registro
    if (-not (Test-Path -LiteralPath $dirReg)) { [void][IO.Directory]::CreateDirectory($dirReg) }
    [pscustomobject]@{
        Fecha = (Get-Date -Format s); Tipo = $tipo; Categoria = $categoria
        MB = [math]::Round($bytes / 1MB, 2); Estado = $estado; Origen = $origen; Destino = $destino
    } | Export-Csv -LiteralPath $registro -Append -NoTypeInformation -Encoding UTF8
    if (-not $resumen.ContainsKey($categoria)) { $resumen[$categoria] = [pscustomobject]@{ N = 0; Bytes = [long]0 } }
    $resumen[$categoria].N++
    $resumen[$categoria].Bytes += $bytes
    if ($origen -like 'C:\*' -and $estado -notlike 'ERROR*' -and $estado -notlike 'OMITIDO*' -and $estado -notlike 'VERIFICAR*') { $script:bytesDesdeC += $bytes }
    $color = if ($estado -like 'ERROR*' -or $estado -like 'VERIFICAR*') { 'Yellow' } else { 'Gray' }
    Write-Host ("[{0}] {1} -> {2}" -f $estado, $origen, $destino) -ForegroundColor $color
}

function Write-SalidaJson($objeto) {
    if (-not $SalidaJson) { return }
    $json = ConvertTo-Json -InputObject $objeto -Depth 6
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($SalidaJson), $json, (New-Object System.Text.UTF8Encoding $false))
}

function Get-TamanoCarpeta([string]$ruta) {
    # Suma manual: Measure-Object sin entrada no expone .Sum y falla con StrictMode (carpetas vacías).
    $s = [long]0
    foreach ($a in @(Get-ChildItem -LiteralPath $ruta -Recurse -File -Force -ErrorAction SilentlyContinue)) { $s += $a.Length }
    return $s
}

function Move-Carpeta([string]$origen, [string]$destino) {
    # Move-Item de PowerShell 5.1 no mueve carpetas entre volúmenes; robocopy /MOVE copia, verifica y borra el origen.
    $null = robocopy $origen $destino /E /MOVE /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ /NFL /NDL /NP /NJH /NJS
    $codigo = $LASTEXITCODE
    if ($codigo -ge 8) { return "ERROR robocopy $codigo" }
    if (Test-Path -LiteralPath $origen) {
        $restantes = @(Get-ChildItem -LiteralPath $origen -Recurse -Force -File -ErrorAction SilentlyContinue).Count
        if ($restantes -gt 0) { return "VERIFICAR: $restantes archivos quedaron en origen (en uso o solo lectura)" }
        Remove-Item -LiteralPath $origen -Recurse -Force   # solo quedan carpetas vacías
    }
    return 'MOVIDO'
}

function Move-ConJunction([string]$origen, [string]$destino) {
    # Mueve una carpeta a D: y deja en su lugar una junction: la app sigue viendo la misma ruta.
    if ((Get-Item -LiteralPath $origen -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { return 'YA ENLAZADO' }
    if (Test-Path -LiteralPath $destino) { return "ERROR: ya existe $destino" }
    # Renombrar primero sirve de prueba de bloqueo: si una app tiene archivos abiertos, falla y no se toca nada.
    $temporal = "$origen.__migrando"
    try { Rename-Item -LiteralPath $origen -NewName (Split-Path -Leaf $temporal) -ErrorAction Stop }
    catch { return 'OMITIDO: carpeta en uso (cierra la app que la usa y repite)' }
    $estado = Move-Carpeta $temporal $destino
    if ($estado -ne 'MOVIDO') {
        if (Test-Path -LiteralPath $destino) { $null = Move-Carpeta $destino $temporal }
        if (-not (Test-Path -LiteralPath $origen)) { Rename-Item -LiteralPath $temporal -NewName (Split-Path -Leaf $origen) }
        return "ERROR, se restauró el original: $estado"
    }
    if (Test-Path -LiteralPath $origen) { return "VERIFICAR: la app recreó $origen mientras se movía; los datos están en $destino" }
    cmd /c mklink /J "$origen" "$destino" | Out-Null
    if ((Test-Path -LiteralPath $origen) -and ((Get-Item -LiteralPath $origen -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { return 'ENLAZADO' }
    return "ERROR: no se creó la junction; los datos están en $destino"
}

function Test-Dentro([string]$ruta, [string]$base) {
    # ¿$ruta es $base o está dentro de $base? Comparación literal, sin comodines.
    $rN = $ruta.TrimEnd('\'); $b = $base.TrimEnd('\')
    return ($rN -ieq $b) -or $rN.StartsWith("$b\", [StringComparison]::OrdinalIgnoreCase)
}

function Test-BajoPatron([string]$ruta, [string]$patron) {
    # Igual que Test-Dentro, pero cada segmento del patrón admite comodines (-like).
    $a = $ruta.TrimEnd('\').Split('\'); $b = $patron.TrimEnd('\').Split('\')
    if ($a.Count -lt $b.Count) { return $false }
    for ($i = 0; $i -lt $b.Count; $i++) { if ($a[$i] -notlike $b[$i]) { return $false } }
    return $true
}

# ---------------------------------------------------------------------------
# 1. Taxonomía y reglas de clasificación (los datos están en config\reglas.json)
# ---------------------------------------------------------------------------
$T = @{}
# Una categoría con valor null (desde reglas.local.json) queda quitada.
foreach ($p in $R.taxonomia.PSObject.Properties) { if ($p.Name -notlike '_*' -and $null -ne $p.Value) { $T[$p.Name] = [string]$p.Value } }
$grupos = @{}
foreach ($p in $R.extensiones.PSObject.Properties) { $grupos[$p.Name] = @($p.Value | ForEach-Object { ([string]$_).ToLower() }) }
$patrones = @{}
foreach ($p in $R.patrones.PSObject.Properties) { $patrones[$p.Name] = [string]$p.Value }

function Resolve-Categoria([string]$clave) { if ($T.ContainsKey($clave)) { return $T[$clave] } return $clave }

# Nunca se mueven: accesos directos, configuración y archivos temporales o descargas a medias.
$extIgnorar = $grupos[[string]$R.archivos.ignorar_grupo]
# Las reglas del usuario (archivos.reglas_usuario, en reglas.local.json) van antes que las genéricas.
$reglasArchivos = @(foreach ($regla in (@(Get-Prop $R.archivos 'reglas_usuario' @()) + @($R.archivos.reglas))) {
    $exts = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($g in @(Get-Prop $regla 'grupos' @())) { foreach ($e in $grupos[[string]$g]) { [void]$exts.Add($e) } }
    foreach ($e in @(Get-Prop $regla 'extensiones' @())) { [void]$exts.Add(([string]$e).ToLower()) }
    $clavePatron = Get-Prop $regla 'patron'
    [pscustomobject]@{
        Exts = $exts
        Patron = $(if ($clavePatron) { $patrones[[string]$clavePatron] } else { $null })
        Categoria = Resolve-Categoria ([string]$regla.categoria)
    }
})

function Get-CategoriaNombre([string]$nombre) {
    # Primera regla que coincide gana (orden de reglas.json: archivos.reglas).
    $ext = [IO.Path]::GetExtension($nombre).ToLower()
    $n = [IO.Path]::GetFileNameWithoutExtension($nombre)
    if ($extIgnorar -contains $ext) { return $null }
    foreach ($patron in @($R.archivos.ignorar_nombres_como)) { if ($nombre -like $patron) { return $null } }
    foreach ($regla in $reglasArchivos) {
        if (-not $regla.Exts.Contains($ext)) { continue }
        if ($regla.Patron -and $n -notmatch $regla.Patron) { continue }
        return $regla.Categoria
    }
    $etiqueta = if ($ext) { $ext.TrimStart('.').ToUpper() } else { [string]$R.archivos.respaldo_sin_extension }
    return ([string]$R.archivos.respaldo).Replace('{EXT}', $etiqueta)
}

function Get-Categoria([IO.FileInfo]$f) { return Get-CategoriaNombre $f.Name }

function Test-EnOneDrive([string]$ruta) {
    return ($raizOneDrive -and $ruta.StartsWith($raizOneDrive, [StringComparison]::OrdinalIgnoreCase))
}

# ---------------------------------------------------------------------------
# 1b. Protección: intocables y rutas referenciadas por programas, accesos directos o el PATH
# ---------------------------------------------------------------------------
$rutasIntocables = @(foreach ($rutaRegla in (@($R.intocables.rutas) + @(Get-Prop $R.intocables 'rutas_extra' @()))) {
    $e = [Environment]::ExpandEnvironmentVariables([string]$rutaRegla)
    if ($e -and $e -notmatch '%') { $e.TrimEnd('\') }
})
$nombresIntocables = @(@($R.intocables.nombres) + @(Get-Prop $R.intocables 'proyectos_usuario' @()) | Where-Object { $_ })
$extIntocables = @($R.intocables.extensiones | ForEach-Object { ([string]$_).ToLower() })
$nombresNoBD = @(Get-Prop $R.intocables 'nombres_no_bd' @())
$marcadoresRepo = @($R.intocables.marcadores_repo)
# Marcadores de bibliotecas de juegos o apps (Steam, Epic, Xbox...): detección por contenido, no por nombre.
$marcadoresBiblioteca = @(Get-Prop $R.intocables 'marcadores_biblioteca' @())
$script:cacheRepo = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::OrdinalIgnoreCase)
$script:referencias = $null

# Rutas detectadas en este equipo por la configuración inicial (nube, bibliotecas de juegos, launchers...).
$intocablesDetectados = @(foreach ($d in @(Get-Prop $C 'intocables_detectados' @())) {
    $rutaDet = if ($d -is [string]) { $d } else { [string](Get-Prop $d 'ruta' '') }
    if ($rutaDet) {
        $motivoDet = if ($d -is [string]) { 'detectada en el equipo' } else { [string](Get-Prop $d 'motivo' 'detectada en el equipo') }
        [pscustomobject]@{ Ruta = [Environment]::ExpandEnvironmentVariables($rutaDet).TrimEnd('\'); Motivo = $motivoDet }
    }
})

# Cachés: la lista del equipo (config.json, con 'habilitada') sustituye a la genérica de reglas.json.
$listaCaches = @(if ($null -ne (Get-Prop $C 'caches')) { @(Get-Prop $C 'caches' @()) | Where-Object { [bool](Get-Prop $_ 'habilitada' $true) } } else { @($R.caches.lista) })

function ConvertTo-RutaLimpia([string]$s) {
    if (-not $s) { return $null }
    $s = $s.Trim()
    if ($s.StartsWith('"')) { $i = $s.IndexOf('"', 1); $s = if ($i -gt 0) { $s.Substring(1, $i - 1) } else { $s.Trim('"') } }
    $s = $s -replace ',\s*-?\d+$', ''
    if ($s -match '^(?<r>[A-Za-z]:\\.*?\.(exe|dll|ico|bat|cmd|msi|com))(\s|$)') { $s = $matches['r'] }
    $s = [Environment]::ExpandEnvironmentVariables($s).TrimEnd('\')
    if ($s -notmatch '^[A-Za-z]:\\[^\\]') { return $null }   # las raíces de unidad no cuentan
    return $s
}

function Get-Referencias {
    if ($null -ne $script:referencias) { return $script:referencias }
    $lista = New-Object System.Collections.ArrayList
    foreach ($k in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall') {
        foreach ($clave in @(Get-ChildItem $k -ErrorAction SilentlyContinue)) {
            foreach ($v in 'InstallLocation', 'DisplayIcon', 'UninstallString') {
                $ruta = ConvertTo-RutaLimpia "$($clave.GetValue($v))"
                if ($ruta) { [void]$lista.Add([pscustomobject]@{ fuente = 'Programa instalado'; campo = $v; ruta = $ruta }) }
            }
        }
    }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $dirsAccesos = @("$env:USERPROFILE\Desktop", $escritorio, "$env:PUBLIC\Desktop", "$env:APPDATA\Microsoft\Windows\Start Menu", "$env:ProgramData\Microsoft\Windows\Start Menu", $RaizDestino) |
            Where-Object { $_ -match '^[A-Za-z]:\\' -and (Test-Path -LiteralPath $_) } | Sort-Object -Unique
        foreach ($lnk in @(Get-ChildItem -LiteralPath $dirsAccesos -Filter *.lnk -Recurse -Depth 3 -Force -ErrorAction SilentlyContinue)) {
            try { $ruta = ConvertTo-RutaLimpia "$($shell.CreateShortcut($lnk.FullName).TargetPath)" } catch { $ruta = $null }
            if ($ruta) { [void]$lista.Add([pscustomobject]@{ fuente = 'Acceso directo'; campo = $lnk.Name; ruta = $ruta }) }
        }
    } catch { Write-Warning "No se pudieron leer los accesos directos: $($_.Exception.Message)" }
    foreach ($p in ([Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')).Split(';')) {
        $ruta = ConvertTo-RutaLimpia $p
        if ($ruta) { [void]$lista.Add([pscustomobject]@{ fuente = 'PATH'; campo = 'Path'; ruta = $ruta }) }
    }
    # Servicios de Windows: su ejecutable no puede cambiar de sitio.
    foreach ($clave in @(Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Services' -ErrorAction SilentlyContinue)) {
        try { $ruta = ConvertTo-RutaLimpia "$($clave.GetValue('ImagePath'))" } catch { $ruta = $null }
        if ($ruta) { [void]$lista.Add([pscustomobject]@{ fuente = 'Servicio'; campo = $clave.PSChildName; ruta = $ruta }) }
    }

    # Índices: todo ancestro de una ruta referenciada (mover ese ancestro rompería la referencia)
    # y las carpetas de instalación (mover algo de dentro rompería el programa).
    $script:ancestrosRef = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::OrdinalIgnoreCase)
    $script:instalaciones = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([StringComparer]::OrdinalIgnoreCase)
    $amplias = @($perfil, $descargas, $escritorio, $documentos, $env:LOCALAPPDATA, $env:APPDATA, $env:TEMP, $RaizDestino) | Where-Object { $_ }
    foreach ($ref in $lista) {
        $p = $ref.ruta
        while ($p -and $p.Length -gt 3) {
            if (-not $script:ancestrosRef.ContainsKey($p)) { $script:ancestrosRef[$p] = "$($ref.fuente): $($ref.ruta)" }
            $p = [IO.Path]::GetDirectoryName($p)
        }
        if ($ref.campo -eq 'InstallLocation') {
            $esAmplia = $false
            foreach ($a in $amplias) { if (Test-Dentro $a $ref.ruta) { $esAmplia = $true; break } }
            if (-not $esAmplia) { $script:instalaciones[$ref.ruta] = $ref.ruta }
        }
    }
    $script:referencias = $lista
    return $lista
}

function Get-ReferenciaAfectada([string]$ruta) {
    $null = Get-Referencias
    if ($script:ancestrosRef.ContainsKey($ruta)) { return "la usa un programa, acceso directo o el PATH ($($script:ancestrosRef[$ruta]))" }
    $p = $ruta
    while ($p -and $p.Length -gt 3) {
        if ($script:instalaciones.ContainsKey($p)) { return "está dentro de la carpeta de un programa instalado ($p)" }
        $p = [IO.Path]::GetDirectoryName($p)
    }
    return $null
}

function Get-MarcadorEn([string]$dir) {
    # Motivo si $dir es un repositorio git o una biblioteca de juegos/apps; '' si no.
    foreach ($m in $marcadoresRepo) { if (Test-Path -LiteralPath (Join-Path $dir $m)) { return 'repositorio git' } }
    foreach ($m in $marcadoresBiblioteca) {
        if (Get-ChildItem -LiteralPath $dir -Filter $m -Force -ErrorAction SilentlyContinue | Select-Object -First 1) { return "biblioteca de juegos o apps (contiene $m)" }
    }
    return ''
}

function Get-MarcadorAncestro([string]$dir) {
    # ¿Algún ancestro (incluido $dir) es un repositorio git o una biblioteca? Con caché por carpeta.
    $p = $dir
    $vistos = @()
    while ($p -and $p.Length -gt 3) {
        if ($script:cacheRepo.ContainsKey($p)) { $res = $script:cacheRepo[$p]; foreach ($v in $vistos) { $script:cacheRepo[$v] = $res }; return $res }
        $vistos += $p
        $res = Get-MarcadorEn $p
        if ($res) { foreach ($v in $vistos) { $script:cacheRepo[$v] = $res }; return $res }
        $p = [IO.Path]::GetDirectoryName($p)
    }
    foreach ($v in $vistos) { $script:cacheRepo[$v] = '' }
    return ''
}

function Get-MotivoIntocableSinControl([string]$ruta, [bool]$esCarpeta) {
    $ruta = [IO.Path]::GetFullPath($ruta).TrimEnd('\')
    foreach ($p in $rutasIntocables) { if (Test-BajoPatron $ruta $p) { return "ruta protegida ($p)" } }
    foreach ($d in $intocablesDetectados) {
        if (Test-Dentro $ruta $d.Ruta) { return "$($d.Motivo) ($($d.Ruta))" }
        if (Test-Dentro $d.Ruta $ruta) { return "contiene una ruta protegida: $($d.Motivo) ($($d.Ruta))" }
    }
    $segs = $ruta.Split('\')
    for ($i = 1; $i -lt $segs.Count; $i++) {
        foreach ($n in $nombresIntocables) { if ($segs[$i] -like $n) { return "nombre protegido: $($segs[$i])" } }
    }
    $nombre = $segs[$segs.Count - 1]
    if (-not $esCarpeta -and $extIntocables -contains [IO.Path]::GetExtension($ruta).ToLower() -and $nombresNoBD -notcontains $nombre) {
        return 'base de datos o archivo de datos de una aplicación'
    }
    if (Test-Path -LiteralPath $ruta) {
        $it = Get-Item -LiteralPath $ruta -Force
        if ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) { return 'es un enlace simbólico o junction' }
        if ($esCarpeta) {
            $marca = Get-MarcadorEn $ruta
            if ($marca) { return $marca }
            $bd = @(Get-ChildItem -LiteralPath $ruta -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $extIntocables -contains $_.Extension.ToLower() -and $nombresNoBD -notcontains $_.Name } | Select-Object -First 1)
            if ($bd.Count) { return "contiene bases de datos ($($bd[0].Name))" }
        }
    }
    $padre = [IO.Path]::GetDirectoryName($ruta)
    if ($padre) { $marca = Get-MarcadorAncestro $padre; if ($marca) { return "está dentro de: $marca" } }
    return Get-ReferenciaAfectada $ruta
}

function Get-MotivoIntocable([string]$ruta, [bool]$esCarpeta) {
    # Devuelve por qué una ruta NUNCA debe moverse, o $null si no hay motivo. Ante la duda, protege.
    try { return Get-MotivoIntocableSinControl $ruta $esCarpeta }
    catch { return "no se pudo verificar: $($_.Exception.Message)" }
}

# ---------------------------------------------------------------------------
# 1c. Decisiones por contexto (las usan las fases del motor y el modo -Analizar)
#     Codigo: OK | IGNORAR | CONSERVAR | PROPIA | INTOCABLE | REVISAR | NO_EXISTE
# ---------------------------------------------------------------------------
function New-Decision([string]$codigo, [string]$motivo, [string]$categoria) {
    [pscustomobject]@{ Codigo = $codigo; Motivo = $(if ($motivo) { $motivo } else { $null }); Categoria = $(if ($categoria) { $categoria } else { $null }) }
}

function Get-DecisionArchivo($f) {
    if ($f.PSIsContainer) { return New-Decision 'REVISAR' 'se esperaba un archivo' $null }
    if ($f.Attributes -band [IO.FileAttributes]::System) { return New-Decision 'IGNORAR' 'archivo de sistema' $null }
    $categoria = Get-Categoria $f
    if (-not $categoria) { return New-Decision 'IGNORAR' 'acceso directo, configuración, temporal o descarga incompleta' $null }
    return New-Decision 'OK' $null $categoria
}

function Get-DecisionCarpetaUsuario($d) {
    # Subcarpetas de Descargas (y del Escritorio en el gestor).
    $cu = $R.carpetas_usuario
    foreach ($patron in @($cu.excluir)) { if ($d.Name -like $patron) { return New-Decision 'CONSERVAR' 'excluida por las reglas: algo depende de su ruta' $null } }
    if ($d.Attributes -band [IO.FileAttributes]::ReparsePoint) { return New-Decision 'CONSERVAR' 'es un enlace o junction' $null }
    $esCodigo = $false
    foreach ($m in @($cu.marcadores_codigo)) { if (Get-ChildItem -LiteralPath $d.FullName -Filter $m -Force -ErrorAction SilentlyContinue | Select-Object -First 1) { $esCodigo = $true; break } }
    $clave = if ($esCodigo) { $cu.categoria_codigo } elseif (@($cu.mensajeria) -contains $d.Name) { $cu.categoria_mensajeria } else { $cu.categoria_defecto }
    return New-Decision 'OK' $null (Resolve-Categoria ([string]$clave))
}

$script:refRaiz = $null
function Get-ReferenciadasRaiz {
    # Nombres de primer nivel bajo la raíz de destino que usa algún programa, acceso directo o el PATH.
    if ($null -ne $script:refRaiz) { return ,$script:refRaiz }
    $script:refRaiz = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $raiz = $RaizDestino.TrimEnd('\')
    foreach ($ref in (Get-Referencias)) {
        if ((Test-Dentro $ref.ruta $raiz) -and $ref.ruta.Length -gt $raiz.Length + 1) {
            [void]$script:refRaiz.Add($ref.ruta.Substring($raiz.Length + 1).Split('\')[0])
        }
    }
    return ,$script:refRaiz
}

$propias = @(@($T.Values | ForEach-Object { $_.Split('\')[0] }) + @($R.raiz_destino.propias_extra) | Sort-Object -Unique)

function Get-DecisionCarpetaRaizD($d) {
    $rd = $R.raiz_destino
    if ($d.Attributes -band ([IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::System -bor [IO.FileAttributes]::ReparsePoint)) { return New-Decision 'CONSERVAR' 'oculta, de sistema o enlace' $null }
    if ($propias -contains $d.Name) { return New-Decision 'PROPIA' 'carpeta de la taxonomía' $null }
    foreach ($patron in @($rd.protegidas_por_nombre)) { if ($d.Name -like $patron) { return New-Decision 'CONSERVAR' 'protegida por nombre' $null } }
    if ((Get-ReferenciadasRaiz).Contains($d.Name)) { return New-Decision 'CONSERVAR' 'la usa un programa, acceso directo o el PATH' $null }
    if (Get-ChildItem -LiteralPath $d.FullName -File -Filter *.exe -ErrorAction SilentlyContinue | Select-Object -First 1) { return New-Decision 'CONSERVAR' 'contiene un ejecutable en su raíz' $null }
    if ($d.Name -match $patrones[[string]$rd.patron_nombre_tecnico]) { return New-Decision 'CONSERVAR' 'nombre técnico (temporal de instalador)' $null }
    # Clasificación de carpetas por nombre; el orden importa.
    $categoria = Resolve-Categoria ([string]$rd.categoria_defecto)
    foreach ($regla in @($rd.reglas_carpetas)) { if ($d.Name -match $patrones[[string]$regla.patron]) { $categoria = Resolve-Categoria ([string]$regla.categoria); break } }
    return New-Decision 'OK' $null $categoria
}

function Get-DecisionArchivoRaizD($f) {
    if ($f.Attributes -band ([IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::System)) { return New-Decision 'CONSERVAR' 'oculto o de sistema' $null }
    foreach ($patron in @($R.raiz_destino.archivos_conservar)) { if ($f.Name -like $patron) { return New-Decision 'CONSERVAR' 'acompaña a un programa portable' $null } }
    return Get-DecisionArchivo $f
}

function Get-CacheConocida([string]$ruta) {
    foreach ($c in $listaCaches) {
        if ([Environment]::ExpandEnvironmentVariables([string]$c.ruta).TrimEnd('\') -ieq $ruta.TrimEnd('\')) { return $c }
    }
    return $null
}

function Get-DestinoCache($c) { return Join-Path (Join-Path $RaizDestino ([string]$R.caches.carpeta_destino)) ([string]$c.nombre) }

function Get-DecisionCache($item) {
    $c = Get-CacheConocida $item.FullName
    if (-not $c) { return New-Decision 'REVISAR' 'no es una caché conocida de reglas.json' $null }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return New-Decision 'CONSERVAR' 'ya enlazada (junction)' $null }
    if (-not $permitirJunctions) { return New-Decision 'REVISAR' 'junctions desactivadas en la configuración (destino USB o no NTFS)' "Junction: $($c.nombre)" }
    $abiertos = @(@($c.procesos) | Where-Object { $_ } | ForEach-Object { Get-Process -Name $_ -ErrorAction SilentlyContinue })
    if ($abiertos.Count -gt 0) { return New-Decision 'REVISAR' "cierra $(@($c.procesos) -join '/') antes de reubicarla" "Junction: $($c.nombre)" }
    if (Test-Path -LiteralPath (Get-DestinoCache $c)) { return New-Decision 'REVISAR' "ya existe $(Get-DestinoCache $c)" "Junction: $($c.nombre)" }
    return New-Decision 'OK' $null "Junction: $($c.nombre)"
}

function Get-Decision([string]$ruta, [string]$contexto) {
    # Decisión completa para el gestor: reglas del contexto + intocables + destino libre propuesto.
    if (-not (Test-Path -LiteralPath $ruta)) { return [pscustomobject]@{ Codigo = 'NO_EXISTE'; Motivo = 'la ruta ya no existe'; Categoria = $null; Destino = $null } }
    $item = Get-Item -LiteralPath $ruta -Force
    switch ($contexto) {
        'cache'           { $d = Get-DecisionCache $item }
        'carpeta_raiz_d'  { $d = Get-DecisionCarpetaRaizD $item }
        'archivo_raiz_d'  { $d = Get-DecisionArchivoRaizD $item }
        'carpeta_usuario' { $d = Get-DecisionCarpetaUsuario $item }
        default           { $d = Get-DecisionArchivo $item }
    }
    $destino = $null
    if ($d.Codigo -eq 'OK' -or $d.Codigo -eq 'REVISAR') {
        $motivo = Get-MotivoIntocable $item.FullName ([bool]$item.PSIsContainer)
        if ($motivo) { return [pscustomobject]@{ Codigo = 'INTOCABLE'; Motivo = $motivo; Categoria = $d.Categoria; Destino = $null } }
        if ($contexto -eq 'cache') { $c = Get-CacheConocida $item.FullName; if ($c) { $destino = Get-DestinoCache $c } }
        elseif ($d.Categoria) { $destino = Get-RutaLibre (Join-Path $RaizDestino $d.Categoria) $item.Name }
    }
    return [pscustomobject]@{ Codigo = $d.Codigo; Motivo = $d.Motivo; Categoria = $d.Categoria; Destino = $destino }
}

# ---------------------------------------------------------------------------
# Modo deshacer
# ---------------------------------------------------------------------------
if ($Deshacer) {
    if (-not (Test-Path -LiteralPath $Deshacer)) { throw "No existe el registro $Deshacer" }
    $resultados = New-Object System.Collections.ArrayList
    $filas = @(Import-Csv -LiteralPath $Deshacer | Where-Object { $_.Estado -eq 'MOVIDO' })
    [array]::Reverse($filas)
    foreach ($f in $filas) {
        $estado = $null
        if (-not (Test-Path -LiteralPath $f.Destino)) { $estado = "OMITIDO: ya no existe $($f.Destino)" }
        elseif (Test-Path -LiteralPath $f.Origen) { $estado = "OMITIDO: ocupado, no se sobrescribe $($f.Origen)" }
        if ($estado) { Write-Warning $estado }
        else {
            Write-Host "[$modo] $($f.Destino) -> $($f.Origen)"
            $estado = 'SIMULADO'
            if ($Ejecutar) {
                try {
                    $padre = Split-Path -LiteralPath $f.Origen
                    if (-not (Test-Path -LiteralPath $padre)) { [void][IO.Directory]::CreateDirectory($padre) }
                    $mismoVolumen = $f.Destino.Substring(0, 2) -eq $f.Origen.Substring(0, 2)
                    if ($f.Tipo -eq 'Archivo' -or $mismoVolumen) { Move-Item -LiteralPath $f.Destino -Destination $f.Origen -ErrorAction Stop; $estado = 'MOVIDO' }
                    else { $estado = Move-Carpeta $f.Destino $f.Origen; Write-Host $estado }
                    if ($estado -eq 'MOVIDO') {
                        $estado = if ((Test-Path -LiteralPath $f.Origen) -and -not (Test-Path -LiteralPath $f.Destino)) { 'RESTAURADO' } else { 'VERIFICAR: origen o destino no quedaron como se esperaba' }
                    }
                } catch { $estado = "ERROR: $($_.Exception.Message)" }
                $mb = 0.0; [void][double]::TryParse([string]$f.MB, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$mb)
                Write-Registro $f.Tipo $f.Destino $f.Origen $f.Categoria ([long]($mb * 1MB)) $estado
            }
        }
        [void]$resultados.Add([pscustomobject]@{ tipo = $f.Tipo; origen = $f.Origen; destino = $f.Destino; estado = $estado; ok = ($estado -eq 'RESTAURADO' -or $estado -eq 'SIMULADO') })
    }
    $hayRegistro = $Ejecutar -and (Test-Path -LiteralPath $registro)
    Write-SalidaJson @{ operacion = 'deshacer'; modo = $modo; registro = $(if ($hayRegistro) { $registro } else { $null }); resultados = $resultados }
    return
}

# ---------------------------------------------------------------------------
# Modo -ListarReferencias (solo lectura)
# ---------------------------------------------------------------------------
if ($ListarReferencias) {
    $refs = New-Object System.Collections.ArrayList
    foreach ($unaRef in (Get-Referencias)) { [void]$refs.Add($unaRef) }
    Write-SalidaJson @{ operacion = 'referencias'; referencias = $refs }
    if (-not $SalidaJson) { $refs | Format-Table -AutoSize }
    return
}

# ---------------------------------------------------------------------------
# Modo -Analizar (solo lectura): {"elementos":[{"id":..,"ruta":..,"contexto":..}]}
#   contexto: archivo | carpeta_usuario | carpeta_raiz_d | archivo_raiz_d | cache
# ---------------------------------------------------------------------------
if ($Analizar) {
    $entrada = Get-Content -LiteralPath $Analizar -Raw -Encoding UTF8 | ConvertFrom-Json
    $resultados = New-Object System.Collections.ArrayList
    foreach ($e in @($entrada.elementos)) {
        $ruta = [string](Get-Prop $e 'ruta' '')
        try { $d = Get-Decision $ruta ([string](Get-Prop $e 'contexto' 'archivo')) }
        catch { $d = [pscustomobject]@{ Codigo = 'REVISAR'; Motivo = "error al analizar: $($_.Exception.Message)"; Categoria = $null; Destino = $null } }
        [void]$resultados.Add([pscustomobject]@{ id = (Get-Prop $e 'id'); ruta = $ruta; codigo = $d.Codigo; motivo = $d.Motivo; categoria = $d.Categoria; destino = $d.Destino })
    }
    Write-SalidaJson @{ operacion = 'analizar'; raiz_destino = $RaizDestino; resultados = $resultados }
    return
}

# ---------------------------------------------------------------------------
# Modo -PlanAprobado: {"operaciones":[{"id":..,"tipo":"Archivo|Carpeta|Junction","origen":..,"destino":..,"categoria":..,"bytes":..}]}
#   Revalida cada operación justo antes de ejecutarla. Nunca sobrescribe; si algo no cuadra, la omite.
# ---------------------------------------------------------------------------
function Test-OperacionPlan($op, [ref]$bytesRef) {
    $tipo = [string](Get-Prop $op 'tipo' ''); $origen = [string](Get-Prop $op 'origen' ''); $destino = [string](Get-Prop $op 'destino' '')
    if ('Archivo', 'Carpeta', 'Junction' -notcontains $tipo) { return "OMITIDO: tipo desconocido '$tipo'" }
    if (-not $origen -or -not $destino) { return 'OMITIDO: faltan origen o destino' }
    foreach ($rutaOp in $origen, $destino) {
        if ($rutaOp -notmatch '^[A-Za-z]:\\[^\\]') { return "OMITIDO: ruta no absoluta: $rutaOp" }
        if ([IO.Path]::GetFullPath($rutaOp).TrimEnd('\') -ne $rutaOp.TrimEnd('\')) { return "OMITIDO: ruta no normalizada: $rutaOp" }
    }
    if (-not (Test-Path -LiteralPath $origen)) { return 'OMITIDO: el origen ya no existe' }
    $item = Get-Item -LiteralPath $origen -Force
    if ($tipo -eq 'Archivo' -and $item.PSIsContainer) { return 'OMITIDO: se esperaba un archivo y es una carpeta' }
    if ($tipo -ne 'Archivo' -and -not $item.PSIsContainer) { return 'OMITIDO: se esperaba una carpeta y es un archivo' }
    $raiz = $RaizDestino.TrimEnd('\')
    if (-not (Test-Dentro $destino $raiz) -or ($destino.TrimEnd('\') -ieq $raiz)) { return "OMITIDO: el destino está fuera de $RaizDestino" }
    if (Test-Path -LiteralPath $destino) { return 'OMITIDO: el destino ya existe (no se sobrescribe)' }
    if ($reservadas.Contains($destino)) { return 'OMITIDO: destino repetido en el plan' }
    if (Test-Dentro $destino $origen) { return 'OMITIDO: el destino está dentro del origen' }
    $motivo = Get-MotivoIntocable $origen ([bool]$item.PSIsContainer)
    if ($motivo) { return "OMITIDO: intocable ($motivo)" }
    $motivo = Get-MotivoIntocable $destino ([bool]$item.PSIsContainer)
    if ($motivo) { return "OMITIDO: destino protegido ($motivo)" }
    $bytesPlan = Get-Prop $op 'bytes'
    if ($tipo -eq 'Archivo') {
        $bytesRef.Value = [long]$item.Length
        if ($null -ne $bytesPlan -and [long]$bytesPlan -ne [long]$item.Length) { return 'OMITIDO: el archivo cambió desde el análisis (tamaño distinto)' }
        if ($item.LastWriteTime -gt $limiteReciente) { return 'OMITIDO reciente' }
    } else {
        $bytesRef.Value = if ($null -ne $bytesPlan) { [long]$bytesPlan } else { Get-TamanoCarpeta $origen }
    }
    if ($tipo -eq 'Junction') {
        if (-not $permitirJunctions) { return 'OMITIDO: junctions desactivadas en la configuración (destino USB o no NTFS)' }
        $c = Get-CacheConocida $origen
        if (-not $c) { return 'OMITIDO: solo se enlazan las cachés conocidas de reglas.json' }
        $abiertos = @(@($c.procesos) | Where-Object { $_ } | ForEach-Object { Get-Process -Name $_ -ErrorAction SilentlyContinue })
        if ($abiertos.Count -gt 0) { return "OMITIDO: cierra $(@($c.procesos) -join '/')" }
    }
    return $null
}

function Invoke-OperacionPlan([string]$tipo, [string]$origen, [string]$destino, [long]$bytes) {
    $padre = [IO.Path]::GetDirectoryName($destino)
    if (-not (Test-Path -LiteralPath $padre)) { [void][IO.Directory]::CreateDirectory($padre) }
    if ($tipo -eq 'Junction') { return Move-ConJunction $origen $destino }
    $mismoVolumen = $origen.Substring(0, 2) -ieq $destino.Substring(0, 2)
    if ($tipo -eq 'Archivo') {
        [IO.File]::Move($origen, $destino)   # falla si el destino existe: nunca sobrescribe
        $ok = (Test-Path -LiteralPath $destino) -and ((Get-Item -LiteralPath $destino -Force).Length -eq $bytes) -and -not (Test-Path -LiteralPath $origen)
        return $(if ($ok) { 'MOVIDO' } else { 'VERIFICAR: tamaño u origen no coincide' })
    }
    if ($mismoVolumen) { [IO.Directory]::Move($origen, $destino); $estado = 'MOVIDO' }
    else { $estado = Move-Carpeta $origen $destino }
    if ($estado -eq 'MOVIDO' -and -not ((Test-Path -LiteralPath $destino) -and -not (Test-Path -LiteralPath $origen))) { $estado = 'VERIFICAR: origen o destino no quedaron como se esperaba' }
    return $estado
}

if ($PlanAprobado) {
    if (-not (Test-Path -LiteralPath $PlanAprobado)) { throw "No existe el plan $PlanAprobado" }
    $plan = Get-Content -LiteralPath $PlanAprobado -Raw -Encoding UTF8 | ConvertFrom-Json
    $limiteReciente = (Get-Date).AddHours(-$OmitirRecientesHoras)
    $resultados = New-Object System.Collections.ArrayList
    foreach ($op in @($plan.operaciones)) {
        $tipo = [string](Get-Prop $op 'tipo' ''); $origen = [string](Get-Prop $op 'origen' ''); $destino = [string](Get-Prop $op 'destino' '')
        $categoria = [string](Get-Prop $op 'categoria' 'Plan aprobado')
        $bytes = [long]0
        try { $estado = Test-OperacionPlan $op ([ref]$bytes) } catch { $estado = "OMITIDO: error al validar: $($_.Exception.Message)" }
        if (-not $estado) {
            [void]$reservadas.Add($destino)
            $estado = 'SIMULADO'
            if ($Ejecutar) {
                try { $estado = Invoke-OperacionPlan $tipo $origen $destino $bytes } catch { $estado = "ERROR: $($_.Exception.Message)" }
            }
        }
        Write-Registro $tipo $origen $destino $categoria $bytes $estado
        [void]$resultados.Add([pscustomobject]@{
            id = (Get-Prop $op 'id'); tipo = $tipo; origen = $origen; destino = $destino; categoria = $categoria
            bytes = $bytes; estado = $estado; ok = ('MOVIDO', 'ENLAZADO', 'SIMULADO' -contains $estado)
        })
    }
    $hayRegistro = Test-Path -LiteralPath $registro
    Write-SalidaJson @{ operacion = 'plan'; modo = $modo; registro = $(if ($hayRegistro) { $registro } else { $null }); resultados = $resultados }
    Write-Host ("Plan: {0} operaciones, {1} correctas. Registro: {2}" -f $resultados.Count, @($resultados | Where-Object { $_.ok }).Count, $registro)
    return
}

# ---------------------------------------------------------------------------
# 2. Fase 1: archivos sueltos de Descargas y Escritorio
# ---------------------------------------------------------------------------
$limiteReciente = (Get-Date).AddHours(-$OmitirRecientesHoras)

foreach ($origenRaiz in $(if ($OmitirSueltos) { @() } else { @($descargas, $escritorio) })) {
    if (-not (Test-Path -LiteralPath $origenRaiz)) { Write-Warning "No existe $origenRaiz"; continue }
    if (Test-EnOneDrive $origenRaiz) { Write-Warning "Omitido (sincronizado por OneDrive): $origenRaiz"; continue }

    Write-Host "`n--- Archivos sueltos en $origenRaiz ---" -ForegroundColor Cyan
    Get-ChildItem -LiteralPath $origenRaiz -File -Force | Where-Object {
        -not ($_.Attributes -band [IO.FileAttributes]::System)
    } | ForEach-Object {
        $f = $_
        $categoria = Get-Categoria $f
        if (-not $categoria) { return }
        if ($f.LastWriteTime -gt $limiteReciente) { Write-Host "[OMITIDO reciente] $($f.FullName)" -ForegroundColor DarkGray; return }
        $motivo = Get-MotivoIntocable $f.FullName $false
        if ($motivo) { Write-Host "[CONSERVADO: $motivo] $($f.FullName)" -ForegroundColor DarkGray; return }

        $dirDestino = Join-Path $RaizDestino $categoria
        $destino = Get-RutaLibre $dirDestino $f.Name
        $estado = 'SIMULADO'
        if ($Ejecutar) {
            try {
                if (-not (Test-Path -LiteralPath $dirDestino)) { New-Item -ItemType Directory -Path $dirDestino -Force | Out-Null }
                $bytes = $f.Length
                Move-Item -LiteralPath $f.FullName -Destination $destino -ErrorAction Stop
                $verificado = (Test-Path -LiteralPath $destino) -and
                              ((Get-Item -LiteralPath $destino -Force).Length -eq $bytes) -and
                              -not (Test-Path -LiteralPath $f.FullName)
                $estado = if ($verificado) { 'MOVIDO' } else { 'VERIFICAR: tamaño u origen no coincide' }
            } catch { $estado = "ERROR: $($_.Exception.Message)" }
        }
        Write-Registro 'Archivo' $f.FullName $destino $categoria $f.Length $estado
    }
}

# ---------------------------------------------------------------------------
# 3. Fase 2 (opcional): subcarpetas de Descargas
# ---------------------------------------------------------------------------
if ($IncluirCarpetasDescargas -and -not (Test-EnOneDrive $descargas)) {
    Write-Host "`n--- Subcarpetas de $descargas ---" -ForegroundColor Cyan
    # Las excluidas (reglas.json: carpetas_usuario.excluir) se quedan en su sitio porque algo depende de su ruta.
    Get-ChildItem -LiteralPath $descargas -Directory -Force | ForEach-Object {
        $d = $_
        $decision = Get-DecisionCarpetaUsuario $d
        if ($decision.Codigo -ne 'OK') { Write-Host "[CONSERVADO: $($decision.Motivo)] $($d.FullName)" -ForegroundColor DarkGray; return }
        $motivo = Get-MotivoIntocable $d.FullName $true
        if ($motivo) { Write-Host "[CONSERVADO: $motivo] $($d.FullName)" -ForegroundColor DarkGray; return }
        $categoria = $decision.Categoria

        $destino = Get-RutaLibre (Join-Path $RaizDestino $categoria) $d.Name
        $bytes = Get-TamanoCarpeta $d.FullName
        $estado = 'SIMULADO'
        if ($Ejecutar) {
            try { $estado = Move-Carpeta $d.FullName $destino } catch { $estado = "ERROR: $($_.Exception.Message)" }
        }
        Write-Registro 'Carpeta' $d.FullName $destino $categoria $bytes $estado
    }
}

# ---------------------------------------------------------------------------
# 4. Fase 3 (opcional): cachés pesadas -> junction en D:. No se borra nada.
# ---------------------------------------------------------------------------
# La lista está en reglas.json (caches.lista). Quedan fuera a propósito:
#   - las carpetas de datos de apps marcadas como intocables en reglas.json;
#   - apps MSIX (dentro de Packages): Windows no soporta bien junctions ahí;
#   - uv: su caché usa enlaces duros hacia los entornos virtuales.
if ($ReubicarCaches -and -not $permitirJunctions) {
    Write-Warning 'Reubicación de cachés omitida: las junctions están desactivadas en la configuración (destino USB o no NTFS).'
} elseif ($ReubicarCaches) {
    Write-Host "`n--- Cachés pesadas -> junction en D: ---" -ForegroundColor Cyan
    $raizCaches = Join-Path $RaizDestino ([string]$R.caches.carpeta_destino)
    foreach ($c in $listaCaches) {
        $ruta = [Environment]::ExpandEnvironmentVariables([string]$c.ruta)
        if (-not (Test-Path -LiteralPath $ruta)) { continue }
        $procesos = @(@($c.procesos) | Where-Object { $_ })
        $abiertos = @($procesos | ForEach-Object { Get-Process -Name $_ -ErrorAction SilentlyContinue })
        $destino = Join-Path $raizCaches ([string]$c.nombre)
        $bytes = Get-TamanoCarpeta $ruta
        $estado = 'SIMULADO'
        if ($abiertos.Count -gt 0) { $estado = "OMITIDO: cierra $($procesos -join '/')" }
        elseif ($Ejecutar) {
            if (-not (Test-Path -LiteralPath $raizCaches)) { New-Item -ItemType Directory -Path $raizCaches -Force | Out-Null }
            try { $estado = Move-ConJunction $ruta $destino } catch { $estado = "ERROR: $($_.Exception.Message)" }
        }
        Write-Registro 'Junction' $ruta $destino "Junction: $($c.nombre)" $bytes $estado
    }
}

# ---------------------------------------------------------------------------
# 5. Fase 4 (opcional): carpetas pesadas de apps de escritorio -> junction en D:
#    La lista está en reglas.json (enlaces_app.lista); la app debe estar cerrada.
# ---------------------------------------------------------------------------
if ($EnlazarVMApp -and -not $permitirJunctions) {
    Write-Warning 'Reubicación de carpetas de apps omitida: las junctions están desactivadas en la configuración.'
} elseif ($EnlazarVMApp) {
    $enlaces = @(@(Get-Prop (Get-Prop $R 'enlaces_app') 'lista' @()) | Where-Object { $_ -and [bool](Get-Prop $_ 'habilitada' $true) })
    if (-not $enlaces.Count) { Write-Warning 'No hay carpetas de apps habilitadas en enlaces_app (reglas.json o reglas.local.json).' }
    foreach ($ea in $enlaces) {
        Write-Host "`n--- $($ea.nombre) ---" -ForegroundColor Cyan
        $base = [Environment]::ExpandEnvironmentVariables([string]$ea.base)
        $sub = [string](Get-Prop $ea 'subcarpeta' '')
        $dirApp = if ($sub) { Get-ChildItem -LiteralPath $base -Directory -Filter $sub -ErrorAction SilentlyContinue | Select-Object -First 1 } elseif (Test-Path -LiteralPath $base) { Get-Item -LiteralPath $base } else { $null }
        $origen = if ($dirApp) { Join-Path $dirApp.FullName ([string]$ea.carpeta) } else { $null }
        $procesos = @(@(Get-Prop $ea 'procesos' @()) | Where-Object { $_ })
        if (-not $origen -or -not (Test-Path -LiteralPath $origen)) {
            Write-Warning "No se encontró $($ea.carpeta) de $($ea.nombre)."
        } elseif ((Get-Item -LiteralPath $origen -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Write-Host "[YA ENLAZADO] $origen" -ForegroundColor DarkGray
        } elseif (@($procesos | ForEach-Object { Get-Process -Name $_ -ErrorAction SilentlyContinue }).Count) {
            Write-Warning "$($ea.nombre) está abierta. Ciérrala por completo (también desde la bandeja) y ejecuta esta fase desde una PowerShell aparte."
        } else {
            $destino = Join-Path (Join-Path $RaizDestino ([string]$R.caches.carpeta_destino)) ([string]$ea.destino)
            if (Test-Path -LiteralPath $destino) { throw "Ya existe $destino; revísalo antes de continuar." }
            $bytes = Get-TamanoCarpeta $origen
            $estado = 'SIMULADO'
            if ($Ejecutar) { $estado = Move-ConJunction $origen $destino }
            Write-Registro 'Junction' $origen $destino "Junction: $($ea.destino)" $bytes $estado
        }
    }
}

# ---------------------------------------------------------------------------
# 6. Fase 5 (opcional): reorganizar la raíz de D:. Mismo volumen: Move-Item es un renombrado instantáneo.
# ---------------------------------------------------------------------------
if ($ReorganizarRaizD) {
    Write-Host "`n--- Raíz de $RaizDestino ---" -ForegroundColor Cyan

    # La taxonomía propia nunca se reclasifica; lo protegido por nombre, lo referenciado por programas,
    # accesos directos o el PATH, y lo intocable se queda donde está (ver Get-DecisionCarpetaRaizD).
    foreach ($d in @(Get-ChildItem -LiteralPath $RaizDestino -Directory -Force)) {
        $decision = Get-DecisionCarpetaRaizD $d
        if ($decision.Codigo -eq 'PROPIA') { continue }
        if ($decision.Codigo -ne 'OK') {
            if ($decision.Motivo -ne 'oculta, de sistema o enlace') { Write-Host "[CONSERVADO: $($decision.Motivo)] $($d.FullName)" -ForegroundColor DarkGray }
            continue
        }
        $motivo = Get-MotivoIntocable $d.FullName $true
        if ($motivo) { Write-Host "[CONSERVADO: $motivo] $($d.FullName)" -ForegroundColor DarkGray; continue }
        $categoria = $decision.Categoria

        $dirDestino = Join-Path $RaizDestino $categoria
        $destino = Get-RutaLibre $dirDestino $d.Name
        $bytes = Get-TamanoCarpeta $d.FullName
        $estado = 'SIMULADO'
        if ($Ejecutar) {
            try {
                if (-not (Test-Path -LiteralPath $dirDestino)) { New-Item -ItemType Directory -Path $dirDestino -Force | Out-Null }
                Move-Item -LiteralPath $d.FullName -Destination $destino -ErrorAction Stop
                $estado = if ((Test-Path -LiteralPath $destino) -and -not (Test-Path -LiteralPath $d.FullName)) { 'MOVIDO' } else { 'VERIFICAR' }
            } catch { $estado = "ERROR: $($_.Exception.Message)" }
        }
        Write-Registro 'Carpeta' $d.FullName $destino $categoria $bytes $estado
    }

    foreach ($f in @(Get-ChildItem -LiteralPath $RaizDestino -File -Force)) {
        $decision = Get-DecisionArchivoRaizD $f
        if ($decision.Codigo -ne 'OK') { continue }
        $motivo = Get-MotivoIntocable $f.FullName $false
        if ($motivo) { Write-Host "[CONSERVADO: $motivo] $($f.FullName)" -ForegroundColor DarkGray; continue }
        $categoria = $decision.Categoria
        $dirDestino = Join-Path $RaizDestino $categoria
        $destino = Get-RutaLibre $dirDestino $f.Name
        $estado = 'SIMULADO'
        if ($Ejecutar) {
            try {
                if (-not (Test-Path -LiteralPath $dirDestino)) { New-Item -ItemType Directory -Path $dirDestino -Force | Out-Null }
                Move-Item -LiteralPath $f.FullName -Destination $destino -ErrorAction Stop
                $estado = if ((Test-Path -LiteralPath $destino) -and -not (Test-Path -LiteralPath $f.FullName)) { 'MOVIDO' } else { 'VERIFICAR' }
            } catch { $estado = "ERROR: $($_.Exception.Message)" }
        }
        Write-Registro 'Archivo' $f.FullName $destino $categoria $f.Length $estado
    }
}

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
Write-Host "`n=== Resumen ($modo) ===" -ForegroundColor Cyan
$resumen.GetEnumerator() | Sort-Object { $_.Value.Bytes } -Descending | ForEach-Object {
    [pscustomobject]@{ Categoria = $_.Key; Elementos = $_.Value.N; GB = [math]::Round($_.Value.Bytes / 1GB, 2) }
} | Format-Table -AutoSize
Write-Host ("Total que sale de C: {0:N2} GB" -f ($script:bytesDesdeC / 1GB)) -ForegroundColor Green
if (-not $Ejecutar) { Write-Host "Simulación: no se movió nada. Repite con -Ejecutar para aplicar." -ForegroundColor Yellow }
Write-Host "Registro: $registro"
