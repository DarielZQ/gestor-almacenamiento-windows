<#
.SYNOPSIS
    Lista los volúmenes con letra de unidad y su tipo de bus (USB, SATA, NVMe...). Solo lectura, salida JSON.

.DESCRIPTION
    Lo usa gestor\deteccion para saber qué disco es USB o extraíble. Primero usa el módulo Storage
    (Get-Volume, Get-Partition, Get-Disk); si no está disponible, WMI (Win32_LogicalDisk y Win32_DiskDrive).
    No modifica nada. Escribe en la salida estándar un objeto JSON:
      { "fuente": "storage|wmi", "volumenes": [ { letra, etiqueta, sistema_archivos, total, libre,
        tipo_unidad, bus, disco, disco_sistema, disco_arranque } ], "errores": [ ... ] }

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\detectar-equipo.ps1
#>
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

$volumenes = New-Object System.Collections.ArrayList
$errores = New-Object System.Collections.ArrayList
$fuente = 'storage'

try {
    $particiones = @{}
    foreach ($p in @(Get-Partition -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter })) {
        $particiones[[string]$p.DriveLetter] = $p
    }
    $discos = @{}
    foreach ($d in @(Get-Disk -ErrorAction SilentlyContinue)) { $discos[[int]$d.Number] = $d }
    foreach ($v in @(Get-Volume -ErrorAction Stop | Where-Object { $_.DriveLetter })) {
        $letra = [string]$v.DriveLetter
        $p = $particiones[$letra]
        $d = $null
        if ($p) { $d = $discos[[int]$p.DiskNumber] }
        [void]$volumenes.Add([ordered]@{
            letra            = $letra
            etiqueta         = [string]$v.FileSystemLabel
            sistema_archivos = [string]$v.FileSystem
            total            = [int64]$v.Size
            libre            = [int64]$v.SizeRemaining
            tipo_unidad      = [string]$v.DriveType
            bus              = $(if ($d) { [string]$d.BusType } else { '' })
            disco            = $(if ($p) { [int]$p.DiskNumber } else { -1 })
            disco_sistema    = $(if ($d) { [bool]$d.IsSystem } else { $false })
            disco_arranque   = $(if ($d) { [bool]$d.IsBoot } else { $false })
        })
    }
} catch {
    [void]$errores.Add("Storage: $($_.Exception.Message)")
    $volumenes.Clear()
    $fuente = 'wmi'
    try {
        $tipos = @{ 2 = 'Removable'; 3 = 'Fixed'; 4 = 'Network'; 5 = 'CD-ROM'; 6 = 'Ram' }
        foreach ($ld in @(Get-CimInstance -ClassName Win32_LogicalDisk -ErrorAction Stop)) {
            $bus = ''
            $numero = -1
            if ([int]$ld.DriveType -ne 4) {
                try {
                    $part = @(Get-CimAssociatedInstance -InputObject $ld -ResultClassName Win32_DiskPartition -ErrorAction Stop) | Select-Object -First 1
                    if ($part) {
                        $dd = @(Get-CimAssociatedInstance -InputObject $part -ResultClassName Win32_DiskDrive -ErrorAction Stop) | Select-Object -First 1
                        if ($dd) { $bus = [string]$dd.InterfaceType; $numero = [int]$dd.Index }
                    }
                } catch { }
            }
            [void]$volumenes.Add([ordered]@{
                letra            = ([string]$ld.DeviceID).TrimEnd(':')
                etiqueta         = [string]$ld.VolumeName
                sistema_archivos = [string]$ld.FileSystem
                total            = [int64]$ld.Size
                libre            = [int64]$ld.FreeSpace
                tipo_unidad      = $(if ($tipos.ContainsKey([int]$ld.DriveType)) { $tipos[[int]$ld.DriveType] } else { 'Unknown' })
                bus              = $bus
                disco            = $numero
                disco_sistema    = $false
                disco_arranque   = $false
            })
        }
    } catch {
        [void]$errores.Add("WMI: $($_.Exception.Message)")
    }
}

$salida = [ordered]@{ fuente = $fuente; volumenes = @($volumenes); errores = @($errores) }
ConvertTo-Json -InputObject $salida -Depth 4 -Compress
