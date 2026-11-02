<#
.SYNOPSIS
    Nombre anterior del motor. Envoltorio de compatibilidad: reenvía todos los parámetros a
    Motor-Almacenamiento.ps1, para que sigan funcionando los comandos antiguos y -Deshacer sobre CSV antiguos.

.EXAMPLE
    .\Liberar-DiscoC.ps1 -Ejecutar -Deshacer "D:\Registro_Migracion_DiscoC\migracion_2026-11-01_1530.csv"
#>
& (Join-Path $PSScriptRoot 'Motor-Almacenamiento.ps1') @args
exit $LASTEXITCODE
