# Gestor de almacenamiento de Windows

Repositorio: `gestor-almacenamiento-windows`
- ES: Herramienta local para liberar espacio en Windows moviendo a otro disco solo lo que apruebas, con historial y deshacer.
- EN: Local tool to free up Windows disk space by moving only what you approve to another drive, with history and undo.

Herramienta local para liberar espacio en el disco del sistema moviendo archivos personales a otro disco
**solo cuando tú lo apruebas**. Se adapta sola a cada equipo (discos, nube, bibliotecas de juegos, cachés) sin
editar código. Escanea, clasifica cada elemento como **Migrable**,
**Intocable** o **Revisar**, propone un destino dentro de una taxonomía de carpetas, ejecuta los movimientos
aprobados con un motor PowerShell que nunca sobrescribe ni borra, y guarda un historial que permite deshacer.

- Interfaz web local en español, servida solo en `127.0.0.1`.
- Nunca elimina archivos: no hay ningún botón ni operación de borrado.
- Ninguna clasificación automática se convierte sola en un movimiento.
- Detección de archivos nuevos en Descargas y Escritorio (cola de organización).
- Posibles duplicados por tamaño y hash (solo informa).
- Configuración inicial guiada, taxonomía editable (español o inglés) y perfiles exportables.

**Alcance:** solo Windows 10/11, porque depende de PowerShell, NTFS y las *junctions* de Windows. macOS y Linux
quedan fuera de alcance por ahora (posible trabajo futuro).

## Instalación rápida

```powershell
git clone <url-del-repositorio> gestor-almacenamiento-windows
cd gestor-almacenamiento-windows
.\instalar.ps1        # o doble clic en instalar.bat
```

El instalador comprueba Windows, PowerShell y Python 3.10+, crea `.venv`, lanza la configuración inicial y ofrece el
acceso directo. Después abre el gestor con el acceso directo o `.\iniciar.ps1`. Guía completa, desde clonar hasta la
primera migración segura: [`docs/ADAPTAR-A-TU-EQUIPO.md`](docs/ADAPTAR-A-TU-EQUIPO.md).

## Arquitectura

```
navegador ──HTTP (127.0.0.1 + token)──> gestor (Python, stdlib) ──JSON──> motor\Motor-Almacenamiento.ps1 (PowerShell 5.1)
                                            │                                   │
                                        SQLite (datos\)            CSV de registro (<destino>\Registro_Migracion_DiscoC)
                                            └── config\reglas.json + reglas.local.json; config.json (-Config) ──┘
```

| Pieza | Qué hace |
|---|---|
| `motor\Motor-Almacenamiento.ps1` | Única implementación de la clasificación, la protección (intocables) y los movimientos. Modos nuevos: `-Analizar`, `-PlanAprobado`, `-ListarReferencias`, `-SalidaJson`. Mantiene todos sus parámetros y fases originales. `motor\Liberar-DiscoC.ps1` es un envoltorio con el nombre anterior que reenvía todos los parámetros. |
| `config\reglas.json` | Datos de las reglas genéricas (taxonomía, extensiones, patrones, intocables, marcadores de bibliotecas). Encima: `config\taxonomias\<idioma>.json` y `config\reglas.local.json` (personal, no versionado). |
| `config\config.json` | Configuración del equipo (no versionada), generada por la configuración inicial y validada con un esquema; el motor la recibe con `-Config`. Valores por defecto en `config.example.json` (sin destino fijo). |
| `config\catalogo_caches.json` | Catálogo genérico de cachés de apps comunes, con la marca de reubicación segura. |
| `gestor\` | Backend en Python 3.10+ sin dependencias: escáner, configuración inicial, taxonomía, perfiles, servicio (flujo y seguridad), SQLite, servidor HTTP. |
| `web\` | Interfaz estática (HTML, CSS y JS sin librerías externas). |
| `tests\` | Pruebas pytest con un "equipo de mentira" en directorios temporales. |

Flujo estricto: **Escanear → Analizar → Proponer → Revisar → Aprobar → Validar → Ejecutar → Registrar**.
Más detalle y motivos en [`docs/DECISIONES.md`](docs/DECISIONES.md).

## Requisitos

- Windows 10 (1809+) u 11 con Windows PowerShell 5.1 (`powershell.exe`) o PowerShell 7.
- Python 3.10 o superior (solo biblioteca estándar).
- Un segundo disco para liberar espacio (NTFS para las *junctions*). Con un solo disco: modo solo análisis.

## Instalación y ejecución

```powershell
.\iniciar.ps1          # o doble clic en iniciar.bat, o el acceso directo
```

`iniciar.ps1` busca Python (`.venv`, luego `py`, luego `python`), lanza el servidor **sin ventana**
(`pythonw.exe`) en `http://127.0.0.1:8742/` (o el siguiente puerto libre) y abre el navegador. Cerrar la
consola no lo detiene. Si el gestor ya está abierto, solo abre el navegador.

- Detenerlo: botón **Detener servidor** (pestaña Ajustes) o `.\detener.ps1`.
- Diagnóstico: `.\iniciar.ps1 -Consola` (primer plano) y `datos\servidor.log`.
- Versión: `/salud` devuelve la versión y una huella del código. Si el código cambió en disco, la interfaz muestra
  "El gestor se actualizó: reinícialo" con un botón de reinicio, e `iniciar.ps1` (y el acceso directo) reinicia el
  servidor en marcha si es de otra versión.
- Instancias de prueba aisladas: variables `GESTOR_DATOS` y `GESTOR_CONFIG`, y `iniciar.ps1 -Puerto <n>` (solo mira ese puerto).
- Si el servidor se cierra con la página abierta, la interfaz muestra un aviso, desactiva las acciones y se
  reconecta sola cuando vuelve (si se reinició, recarga la página para obtener la nueva sesión).

### Acceso directo en el Escritorio

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\crear-acceso-directo.ps1
```

Crea `Gestor de Almacenamiento.lnk` en el Escritorio del usuario actual (fuera del repositorio). Ejecuta
`iniciar.ps1` en una ventana minimizada; con un doble clic se abre la interfaz.

### Configuración

`config\config.json` (no versionado) lo genera la configuración inicial (pestaña Equipo o `instalar.ps1`) y se valida
al arrancar. Puedes ajustar a mano los valores generales; la configuración inicial los conserva:

| Clave | Por defecto | Significado |
|---|---|---|
| `puerto` | 8742 | Puerto local (si está ocupado se prueban los 19 siguientes). |
| `raiz_destino` | sin valor | Disco o carpeta de destino (lo propone la configuración inicial). |
| `modo` | `completo` | `solo_analisis` si el destino está en el disco del sistema. |
| `taxonomia` | `es` | Idioma de las carpetas de destino (`es` o `en`). |
| `permitir_junctions` | `true` | Reubicar cachés con junction (falso por defecto si el destino es USB o no NTFS). |
| `intocables_detectados`, `caches` | detectados | Nube, bibliotecas de juegos y cachés elegidas en la configuración inicial. |
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
uv venv --python 3.11 .venv            # o: .\instalar.ps1 -ConPruebas
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
- En documentación y pruebas, las rutas de ejemplo en otras unidades usan carpetas de la taxonomía o nombres que
  empiezan por `Ejemplo` (por ejemplo `E:\Ejemplo_Juegos`).

Los hooks `.githooks\pre-commit` (sobre lo preparado) y `.githooks\commit-msg` (sobre el mensaje) usan la misma
lista local cuando existe. Las protecciones de carpetas de apps concretas de un equipo van en
`config\reglas.local.json`, que tampoco se versiona.
