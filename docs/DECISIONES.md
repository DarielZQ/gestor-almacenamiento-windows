# Registro de decisiones técnicas

## 2026-11-01 — Python con la biblioteca estándar + SQLite + web estática
- **Decisión:** backend en Python 3.11 sin dependencias (`http.server`, `sqlite3`), interfaz HTML/JS sin librerías.
- **Motivo:** nada que instalar para usar la app, menos superficie de ataque, arranque inmediato. FastAPI no aporta
  lo suficiente para una API local pequeña. Solo las pruebas necesitan `pytest` (fijado en `requirements-dev.txt`).

## 2026-11-01 — El motor PowerShell es la única implementación de reglas y movimientos
- **Decisión:** las reglas (datos) viven en `config\reglas.json`; la lógica que las evalúa vive solo en el motor.
  El gestor le pide la clasificación con `-Analizar` (lote JSON) y le entrega el plan con `-PlanAprobado`.
- **Motivo:** evitar que Python y PowerShell clasifiquen o protejan distinto. Python solo enumera, mide tamaños y
  aplica la política de presentación (Migrable / Revisar / Intocable).
- **Coste:** una llamada a `powershell.exe` por escaneo (2–15 s según el disco).

## 2026-11-01 — Reglas genéricas versionadas + `reglas.local.json`
- **Decisión:** `reglas.json` lleva reglas genéricas; lo afinado para un equipo concreto (proyectos, cursos,
  carpetas que usan sus apps) va en `reglas.local.json`, no versionado, con la misma fusión en ambos lenguajes
  (objetos clave a clave, listas y textos reemplazados).
- **Motivo:** el repositorio no debe revelar datos personales y el motor debe comportarse igual que antes en el equipo.

## 2026-11-01 — Seguridad del flujo
- Aprobación solo por acción explícita en la interfaz; un Intocable no se puede aprobar.
- Si un aprobado cambia (tamaño, fecha, tipo) o pasa a Intocable en un re-escaneo, vuelve a pendiente.
- Doble validación antes de mover: en Python (origen existe y no cambió, destino libre y dentro de la raíz) y otra
  vez en el motor justo antes de cada operación (además: intocables, rutas normalizadas, recientes, procesos abiertos).
- En `-PlanAprobado`, si el destino existe se omite (no se renombra a " (2)"), porque el usuario aprobó ese destino.
  Archivos con `[IO.File]::Move`, que falla si el destino existe.
- Los repositorios git pasan a Intocables (antes la fase 2 los movía a `Proyectos_Codigo\Desde_Descargas`).
- Las fases clásicas del motor también consultan ahora los intocables (bases de datos, git, rutas protegidas).

## 2026-11-01 — Servidor local
- Solo `127.0.0.1`; cabecera `Host` obligatoria (frena DNS rebinding); token aleatorio por sesión en todas las
  rutas `/api`; POST con `Content-Type: application/json` y `Origin`/`Sec-Fetch-Site` del mismo origen; CSP estricta.
- `/salud` (sin token) solo identifica la app, para que el acceso directo no levante un segundo servidor.
- Puerto por defecto 8742 (8765 lo usaba otra app local); si está ocupado se prueban los siguientes.

## 2026-11-01 — Escaneo acotado e incremental
- No se indexa Windows. Zonas: Descargas y Escritorio (elementos), raíz del destino, cachés conocidas, perfil,
  AppData por primer nivel y raíz de C: sin entrar en carpetas del sistema. Presupuesto de tiempo por carpeta y zona.
- Incremental: las decisiones se conservan entre escaneos; el escaneo rápido reutiliza tamaños de carpetas cuya
  fecha no cambió; los elementos nuevos de Descargas y Escritorio entran en la cola (el primer escaneo es la base).
- Archivos grandes dentro de AppData o carpetas `.algo` del perfil se consideran datos de aplicaciones (Intocable).

## 2026-11-01 — Historial = CSV del motor
- La fuente de verdad de los movimientos son los CSV del motor; el gestor los importa en solo lectura
  (todos los `*.csv` del directorio de registro salvo `simulacion*`) e ignora una última fila a medio escribir.
- Deshacer una operación: el gestor escribe un CSV temporal con esa fila y llama al `-Deshacer` existente, que
  ahora verifica, devuelve JSON y registra `deshacer_*.csv` con estado `RESTAURADO`.

## 2026-11-01 — Privacidad del repositorio
- Identidad de commits configurada solo en el repo; historial recreado desde cero.
- `scripts\auditar-privacidad.ps1` + hooks `.githooks\pre-commit` y `commit-msg`. El script versionado solo lleva
  patrones genéricos; los términos privados se definen localmente (entorno, identidad global de git y
  `config\privacidad.local.txt`, no versionado), para no escribirlos en el repo.

## 2026-11-01 — Motor renombrado y sin referencias a apps concretas
- `motor\Motor-Almacenamiento.ps1` es el nombre canónico; `Liberar-DiscoC.ps1` reenvía `@args` para no romper comandos
  antiguos ni `-Deshacer` sobre CSV antiguos.
- La fase que enlazaba la carpeta de una app concreta pasa a ser genérica (`-EnlazarVMApp` + `enlaces_app`), con las
  mismas garantías de `Move-ConJunction` (prueba de bloqueo por renombrado, verificación y restauración) y la app cerrada.
- Las protecciones de apps propias de un equipo se declaran en `reglas.local.json` (`intocables.rutas_extra`), que se
  suma a la lista genérica en vez de reemplazarla.

## 2026-11-01 — Servidor desacoplado de la consola
- `iniciar.ps1` lanza `pythonw.exe` oculto; cerrar la consola ya no deja la interfaz sin servidor. Se detiene desde la
  interfaz (`POST /api/detener`, con token y Origin) o con `detener.ps1` (por HTTP, o por PID verificado).
- La interfaz usa un cliente HTTP único con aviso persistente y reconexión con espera creciente.

## 2026-11-01 — Respaldo de la configuración personal
- Copias en `<dir_registro>\respaldo_config\` porque ese disco ya guarda el historial y sobrevive a reinstalar Windows.
- Se detectan cambios por hash; 20 copias; la rotación solo borra carpetas con el marcador de la app y sin archivos
  ajenos. Es la única operación de borrado del gestor y afecta solo a sus propias copias.

## 2026-11-01 — Publicación
- Repositorio `gestor-almacenamiento-windows`, privado y sin licencia por ahora; README solo con texto.

## 2026-11-02 — Comprobación de versión
- La huella es un hash del código versionado que usa el servidor (Python, web, motor y configuración genérica), con
  finales de línea normalizados. El servidor guarda la de su arranque y la compara con la del disco en cada `/salud`.
- La página lleva la huella con la que se sirvió; si difiere de la del servidor, pide recargar.
- El reinicio desde la interfaz lanza un proceso nuevo desacoplado en el mismo puerto después de cerrar el actual.

## 2026-11-02 — Configuración por capas y motor sin destino fijo
- `config.example.json` ya no trae destino: sin `config.json` el gestor no escanea ni ejecuta y pide la configuración
  inicial. `config.json` se valida con un esquema propio (sin dependencias) y mensajes claros.
- El motor recibe la configuración del equipo con `-Config` (el gestor genera `datos\tmp\config_motor.json`); sin
  `-RaizDestino` ni configuración, falla con un mensaje claro. `-RaizDestino` sigue funcionando.
- Las bibliotecas de juegos se reconocen por su contenido (`steamapps`, `libraryfolder.vdf`, `.egstore`...) y por las
  rutas que detecte la configuración inicial, no por nombres de launchers. Los servicios de Windows cuentan como
  referencias, igual que los programas instalados, los accesos directos y el PATH.
- El catálogo `config\catalogo_caches.json` marca qué cachés son seguras de reubicar; la lista efectiva del equipo
  vive en `config.json` (`caches`, con `habilitada`). Si el destino es USB o no NTFS, las junctions se desactivan.

## 2026-11-02 — Configuración inicial, taxonomía editable y perfiles
- La configuración inicial solo detecta y propone; nada se guarda sin confirmación. Destino propuesto: el disco que
  no es del sistema con más espacio libre. Destino USB: junctions desactivadas por defecto (fallan si se desconecta).
  No NTFS: junctions desactivadas. Un solo disco: modo `solo_analisis` (mover en el mismo disco no libera espacio).
- La detección vive en un módulo con fuentes inyectables (variables de entorno, volúmenes, registro) para poder
  simular cualquier equipo en las pruebas.
- La taxonomía se edita en la capa personal (`reglas.local.json`); quitar una categoría se expresa como `null`; las
  reglas propias (`archivos.reglas_usuario`) van antes que las genéricas. La vista previa reclasifica con el motor y
  unas reglas temporales, así la lógica no se duplica en Python.
- La taxonomía en inglés es una capa (`config\taxonomias\en.json`) que solo cambia los nombres de carpetas.
- Los perfiles exportados no llevan datos personales ni del equipo salvo que se marque; importar respalda antes.
- `instalar.ps1` no instala Python: si falta, explica cómo hacerlo. Es idempotente.
- Alcance: solo Windows (PowerShell, NTFS y junctions).
