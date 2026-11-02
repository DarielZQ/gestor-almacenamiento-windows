# Gestor de almacenamiento de Windows

Repositorio: `gestor-almacenamiento-windows`
- ES: Herramienta local para liberar espacio en Windows moviendo a otro disco solo lo que apruebas, con historial y deshacer.
- EN: Local tool to free up Windows disk space by moving only what you approve to another drive, with history and undo.

Herramienta local para liberar espacio en el disco del sistema (C:) moviendo archivos personales a un
segundo disco (D:) **solo cuando tú lo apruebas**. Escanea, clasifica cada elemento como **Migrable**,
**Intocable** o **Revisar**, propone un destino dentro de una taxonomía de carpetas, ejecuta los movimientos
aprobados con un motor PowerShell que nunca sobrescribe ni borra, y guarda un historial que permite deshacer.

- Interfaz web local en español, servida solo en `127.0.0.1`.
- Nunca elimina archivos: no hay ningún botón ni operación de borrado.
- Ninguna clasificación automática se convierte sola en un movimiento.
- Detección de archivos nuevos en Descargas y Escritorio (cola de organización).
- Posibles duplicados por tamaño y hash (solo informa).

## Arquitectura

```
navegador ──HTTP (127.0.0.1 + token)──> gestor (Python, stdlib) ──JSON──> motor\Motor-Almacenamiento.ps1 (PowerShell 5.1)
                                            │                                   │
                                        SQLite (datos\)            CSV de registro (D:\Registro_Migracion_DiscoC)
                                            └──────── lee config\reglas.json ───┘
```

| Pieza | Qué hace |
|---|---|
| `motor\Motor-Almacenamiento.ps1` | Única implementación de la clasificación, la protección (intocables) y los movimientos. Modos nuevos: `-Analizar`, `-PlanAprobado`, `-ListarReferencias`, `-SalidaJson`. Mantiene todos sus parámetros y fases originales. `motor\Liberar-DiscoC.ps1` es un envoltorio con el nombre anterior que reenvía todos los parámetros. |
| `config\reglas.json` | Datos de las reglas (taxonomía, extensiones, patrones, intocables, cachés) que leen el motor y el gestor. `config\reglas.local.json` (no versionado) se fusiona encima con lo propio del equipo. |
| `gestor\` | Backend en Python 3.11+ sin dependencias: escáner, servicio (flujo y seguridad), SQLite, servidor HTTP. |
| `web\` | Interfaz estática (HTML, CSS y JS sin librerías externas). |
| `tests\` | Pruebas pytest con un "equipo de mentira" en directorios temporales. |

Flujo estricto: **Escanear → Analizar → Proponer → Revisar → Aprobar → Validar → Ejecutar → Registrar**.
Más detalle y motivos en [`docs/DECISIONES.md`](docs/DECISIONES.md).

## Requisitos

- Windows 10/11 con Windows PowerShell 5.1 (`powershell.exe`).
- Python 3.11 o superior (solo biblioteca estándar).
- Disco destino NTFS (por defecto `D:\`).

## Instalación y ejecución

```powershell
git clone <url-del-repo> GestorAlmacenamientoWindows
cd GestorAlmacenamientoWindows
.\iniciar.ps1          # o doble clic en iniciar.bat
```

`iniciar.ps1` busca Python (`.venv`, luego `py`, luego `python`), lanza el servidor **sin ventana**
(`pythonw.exe`) en `http://127.0.0.1:8742/` (o el siguiente puerto libre) y abre el navegador. Cerrar la
consola no lo detiene. Si el gestor ya está abierto, solo abre el navegador.

- Detenerlo: botón **Detener servidor** (pestaña Ajustes) o `.\detener.ps1`.
- Diagnóstico: `.\iniciar.ps1 -Consola` (primer plano) y `datos\servidor.log`.
- Si el servidor se cierra con la página abierta, la interfaz muestra un aviso, desactiva las acciones y se
  reconecta sola cuando vuelve (si se reinició, recarga la página para obtener la nueva sesión).

### Acceso directo en el Escritorio

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\crear-acceso-directo.ps1
```

Crea `Gestor de Almacenamiento.lnk` en el Escritorio del usuario actual (fuera del repositorio). Ejecuta
`iniciar.ps1` en una ventana minimizada; con un doble clic se abre la interfaz.

### Configuración

Copia `config\config.example.json` como `config\config.json` (no versionado) para cambiar valores:

| Clave | Por defecto | Significado |
|---|---|---|
| `puerto` | 8742 | Puerto local (si está ocupado se prueban los 19 siguientes). |
| `raiz_destino` | `D:\` | Raíz del disco destino. |
| `dir_registro` | `<raiz_destino>\Registro_Migracion_DiscoC` | Dónde escribe y lee el motor sus CSV. |
| `horas_recientes` | 2 | Archivos modificados hace menos se marcan Revisar y el motor no los mueve. |
| `umbral_grande_mb` | 500 | Tamaño a partir del cual un archivo del perfil se lista como "grande". |
| `vigilancia_minutos` | 10 | Re-escaneo rápido de Descargas y Escritorio mientras la app está abierta (0 = desactivado). |
| `presupuesto_segundos_*` | 20 / 180 | Tiempo máximo para medir una carpeta / una zona. |
| `duplicados` | ver archivo | Tamaño mínimo, hash completo y tiempo máximo. |
| `rutas` | autodetectadas | Perfil, Descargas, Escritorio, Documentos y raíz del sistema (registro de Windows). |

Las reglas propias del equipo van en `config\reglas.local.json` (mira `reglas.local.example.json`): proyectos que
deben quedar intocables (`intocables.proyectos_usuario`), carpetas de datos de apps (`intocables.rutas_extra`),
palabras clave de tus cursos (`patrones`) y carpetas de apps reubicables con junction (`enlaces_app`).

### Respaldo de la configuración personal

Cada vez que cambian `config\config.json` o `config\reglas.local.json` (se comprueba al arrancar), el gestor copia
ambos a `<dir_registro>\respaldo_config\<fecha>\` y conserva las últimas 20 copias. La rotación solo quita carpetas que
creó el propio gestor. En **Ajustes** puedes respaldar a mano o restaurar una copia (antes se guarda la actual). La base
SQLite no se respalda.

## Uso

1. **Resumen** → *Escaneo completo* (1–2 minutos). Muestra discos, espacio liberable y dónde está el espacio.
2. **Elementos** → filtra, revisa motivo y destino, y aprueba o rechaza (uno a uno o en lote).
3. **Ejecutar** → *Simular* valida sin mover; *Ejecutar aprobados* mueve con el motor y registra en CSV.
4. **Historial** → movimientos (incluye los CSV previos del motor, en solo lectura) y botón *Deshacer*.
5. **Cola de nuevos** → lo que apareció en Descargas o Escritorio desde el escaneo anterior.
6. **Duplicados** → grupos de archivos idénticos. Solo informa.

El motor también se puede usar solo, como antes:

```powershell
.\motor\Motor-Almacenamiento.ps1                         # simulación de la fase 1
.\motor\Motor-Almacenamiento.ps1 -PlanAprobado plan.json -SalidaJson resultado.json            # valida
.\motor\Motor-Almacenamiento.ps1 -PlanAprobado plan.json -SalidaJson resultado.json -Ejecutar  # mueve
.\motor\Motor-Almacenamiento.ps1 -Deshacer "D:\Registro_Migracion_DiscoC\migracion_plan_<fecha>.csv" -Ejecutar
```

## Pruebas

```powershell
uv venv --python 3.11 .venv            # o: python -m venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

Las pruebas crean un equipo ficticio en un directorio temporal (perfil, Descargas, Escritorio, AppData,
disco destino) y llaman al motor PowerShell real con `-RaizDestino`, `-DirRegistro` y `-Reglas` apuntando
ahí. Nunca tocan archivos reales. `-m "not motor"` omite las que necesitan PowerShell.

## Privacidad del repositorio

El repositorio no debe contener datos del equipo: ni nombres, ni rutas reales, ni registros, ni bases de datos.

```powershell
git config core.hooksPath .githooks       # activa el hook pre-commit (una vez por clon)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\auditar-privacidad.ps1   # árbol + historial
```

`scripts\auditar-privacidad.ps1` solo contiene patrones genéricos. Falla si encuentra, en el contenido, los mensajes
de commit o las identidades:
- el usuario de Windows, el perfil, el nombre del equipo o la identidad global de git (leídos en el momento);
- correos personales, rutas `C:\Users\<nombre>`, rutas de otra unidad fuera de la taxonomía, tokens y claves,
  contraseñas escritas, números de cuenta o identificación;
- archivos prohibidos (`.db`, `.sqlite`, `.csv`, `.log`, `.env`, `.lnk`, `.venv`, `datos\`, configuración local);
- los **términos privados definidos localmente** en `config\privacidad.local.txt` (no versionado). Copia
  `config\privacidad.local.example.txt`: un término por línea, o `re:<expresión>` para expresiones regulares.
  Si el archivo no existe, el script avisa y aplica solo los patrones genéricos.

Los hooks `.githooks\pre-commit` (sobre lo preparado) y `.githooks\commit-msg` (sobre el mensaje) usan la misma
lista local cuando existe. Las protecciones de carpetas de apps concretas de un equipo van en
`config\reglas.local.json`, que tampoco se versiona.
