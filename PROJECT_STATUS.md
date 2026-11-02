# Estado del proyecto

Versión 0.1.0 — 2026-11-01. Primera versión funcional y probada.

## Terminado
- Motor `motor\Motor-Almacenamiento.ps1` integrado en el proyecto, con reglas en `config\reglas.json` y modos
  `-Analizar`, `-ListarReferencias`, `-PlanAprobado`, `-SalidaJson`, `-DirRegistro`, `-Reglas`; `-Deshacer`
  con verificación, JSON y registro. Sincronizado con la última versión original (`-OmitirSueltos`,
  `Get-TamanoCarpeta` con suma manual). Parámetros y fases originales intactos.
- Escaneo completo (≈1,5 min en el equipo de desarrollo) y rápido (Descargas y Escritorio), incremental.
- Clasificación Migrable / Intocable / Revisar con motivo, destino propuesto y espacio liberable en C:.
- Aprobar, rechazar y volver a pendiente, uno a uno o en lote. Intocables no aprobables.
- Simulación y ejecución de lo aprobado mediante el motor, con revalidación previa.
- Historial de movimientos (importa en solo lectura los CSV del motor) y de decisiones; deshacer por operación.
- Cola de archivos nuevos con re-escaneo periódico mientras la app está abierta.
- Duplicados por tamaño + hash parcial + hash completo, con caché de hashes. Solo informa.
- Servidor 127.0.0.1 con token, Host, Origin y CSP. Una sola instancia (`/salud` + `datos\servidor.json`).
  Arranca desacoplado (pythonw, sin ventana), se detiene desde la interfaz o con `detener.ps1`; PID en
  `datos\servidor.pid`; los escaneos cortados por un cierre quedan como `interrumpido`.
- Interfaz resiliente: cliente HTTP único (`web\conexion.js`), aviso si el servidor no corre, acciones
  desactivadas sin conexión, reconexión automática con espera creciente y errores con código HTTP.
- Acceso directo del Escritorio (`scripts\crear-acceso-directo.ps1`) probado: arranca, reutiliza el servidor
  si ya está abierto y abre el navegador.
- Respaldo automático de `config.json` y `reglas.local.json` en `<dir_registro>\respaldo_config\` (20 copias,
  rotación solo de copias propias) con restauración desde Ajustes.
- Motor renombrado a `motor\Motor-Almacenamiento.ps1`; `Liberar-DiscoC.ps1` queda como envoltorio de compatibilidad.
  La fase de apps concretas se sustituyó por `-EnlazarVMApp`, genérica y configurable (`enlaces_app`).
- Auditoría de privacidad (`scripts\auditar-privacidad.ps1`) y hooks pre-commit y commit-msg.
- 46 pruebas automáticas (pytest, incluida una del cliente web en Node) en verde; verificación manual con escaneo real en solo lectura.

- Comprobación de versión entre interfaz, servidor y código en disco, con reinicio desde la interfaz o al usar el
  acceso directo.

## Estado de GitHub
- Repositorio público `gestor-almacenamiento-windows`, con licencia MIT (`LICENSE`).
- Tags `etapa-1` y `etapa-2` publicados. Identidad configurada solo en el repo; auditoría de privacidad en cada commit.

## Problemas conocidos
- La primera llamada al motor por escaneo tarda 2–15 s (arranque de PowerShell y lectura de accesos directos).
- Las carpetas enormes se miden con presupuesto de tiempo; si se corta, el tamaño se marca como parcial (`+`).
- `-Deshacer` no revierte junctions (estado `ENLAZADO`): hay que hacerlo a mano (ver HANDOFF).
- Los archivos grandes dentro de AppData solo aparecen en el análisis de espacio, no como elementos.
- La interfaz no tiene paginación del historial más allá de 500 filas ni edición del destino propuesto.
- No hay inspección del contenido de documentos para clasificar (el usuario lo autorizó; ver próximas tareas).

## Pendiente (por prioridad)
1. **Etapa 2: portabilidad a cualquier Windows 10/11.** Terminada (tag `etapa-2`). Hecho: comprobación de versión; configuración por capas con
   esquema; motor con `-Config` y sin destino fijo; protección por detección (servicios, marcadores de bibliotecas,
   `intocables_detectados`); catálogo genérico de cachés; configuración inicial en la interfaz (pestaña Equipo,
   también "volver a detectar") y por consola (`python -m gestor.configuracion_inicial`), sobre el módulo de detección
   `gestor\deteccion` (fuentes inyectables; depuración con `python -m gestor.deteccion`).
   Hechos también: instalador (`instalar.ps1`/`.bat`), taxonomía editable con vista previa y en inglés,
   perfiles exportables y `docs\ADAPTAR-A-TU-EQUIPO.md`. La configuración inicial nunca reemplaza un `config.json`
   existente sin mostrar las diferencias y pedir confirmación; con destino USB, las junctions se pueden activar a
   sabiendas. El catálogo de cachés tiene una sola fuente (`config\catalogo_caches.json`). Diseño original de la etapa:
   - Configuración por capas: `config.example.json` sin destino fijo (`raiz_destino: null`) → `config.json` del equipo
     (generado por la configuración inicial, validado con esquema y mensajes claros) → `reglas.local.json`.
   - Motor con `-Config <ruta>`: destino, `dir_registro`, `intocables_detectados` (nube, bibliotecas de juegos,
     launchers), lista de cachés del equipo con `habilitada`, `permitir_junctions` (falso si el destino es USB o no NTFS)
     y `taxonomia` (`es`/`en`, superpuesta desde `config\taxonomias\<idioma>.json`). Sin `D:\` por defecto en el código.
   - Protección genérica: servicios de Windows (`ImagePath`) en las referencias; marcadores de bibliotecas por contenido
     (`steamapps`, `libraryfolder.vdf`, `.egstore`, `appxmanifest.xml`, `MicrosoftGame.config`) en vez de nombres fijos.
   - Catálogo genérico de cachés (`config\catalogo_caches.json`: npm, pip, Yarn, NuGet, Gradle, Maven, NVIDIA, AMD,
     IDEs, editores, navegadores...) con la marca `segura`.
   - Detectores inyectables (discos con BusType y sistema de archivos, Known Folders / User Shell Folders, nube,
     Steam `libraryfolder.vdf`, manifiestos de Epic, `.GamingRoot`, Battle.net, Riot, EA, Ubisoft, GOG) y propuesta:
     origen = disco del sistema; destino = disco no sistema con más espacio; avisos para USB, no NTFS y un solo disco.
   - Configuración inicial en la interfaz (primera vez sin `config.json`) y por consola; `instalar.ps1`/`instalar.bat`
     idempotentes (Windows, Python 3.10+, PowerShell 5.1/7, `.venv`, configuración inicial, acceso directo opcional).
   - Taxonomía editable con vista previa (el motor clasifica con unas reglas temporales), taxonomía en inglés.
   - Perfiles: exportar/importar sin datos del equipo salvo que se marque; botón "volver a detectar equipo".
   - Pruebas de portabilidad con detectores simulados (sin D:, destino E:, USB, un disco, sin nube, Steam en otra
     biblioteca, perfil con espacios y tildes) y `docs\ADAPTAR-A-TU-EQUIPO.md`. Alcance: solo Windows.
2. Publicado: `etapa-1` y `etapa-2` están en el repositorio público `gestor-almacenamiento-windows`.
4. Permitir elegir o editar el destino de un elemento antes de aprobarlo (con validación de raíz).
5. Deshacer junctions (`ENLAZADO`): quitar la junction y devolver la carpeta, con las mismas garantías.
6. Clasificación opcional por contenido (texto de PDF/DOCX) para lo que cae en triage, sin guardar contenido.
7. Historial: filtros por fecha y estado, exportación y paginación; "abrir carpeta" en duplicados (nunca borrar).
8. Medición más rápida de carpetas enormes (caché por carpeta y fecha, en segundo plano).
9. Capturas con datos de prueba al hacerlo público (hoy el README es solo texto).
