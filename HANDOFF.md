# Cómo retomar el trabajo

## Qué leer primero
1. `README.md`: qué es, arquitectura, ejecución y pruebas.
2. `PROJECT_STATUS.md`: qué está hecho, problemas conocidos y la lista priorizada de próximas tareas.
3. `docs\DECISIONES.md`: por qué está hecho así (no lo deshagas sin leerlo).
4. `gestor\servicio.py`: el flujo completo y las reglas de seguridad de la capa Python.
5. `motor\Motor-Almacenamiento.ps1`, secciones 1b (protección), 1c (decisiones) y los modos `-Analizar` y `-PlanAprobado`.

## Dónde está cada cosa
| Ruta | Contenido |
|---|---|
| `motor\Motor-Almacenamiento.ps1` | Motor canónico (UTF-8 con BOM, necesario en PowerShell 5.1). |
| `motor\Liberar-DiscoC.ps1` | Envoltorio con el nombre anterior: reenvía todos los parámetros. |
| `gestor\respaldo.py` | Respaldo y restauración de la configuración personal. |
| `config\reglas.json` | Reglas genéricas compartidas. `reglas.local.json` (local, no versionado) las afina. |
| `config\config.example.json` | Valores por defecto; `config.json` local los sobrescribe. |
| `gestor\escaner.py` | Enumeración y medición de zonas (solo lectura). |
| `gestor\servicio.py` | Política de clasificación, aprobaciones, validación, ejecución, historial, deshacer. |
| `gestor\motor.py` | Llamadas a `powershell.exe` con archivos JSON. |
| `gestor\servidor.py` | API HTTP, protecciones y tareas en segundo plano. |
| `gestor\duplicados.py` | Duplicados por tamaño y hash. |
| `web\` | Interfaz. |
| `tests\conftest.py` | Equipo ficticio para pruebas. |
| `scripts\` | Acceso directo y auditoría de privacidad. |
| `datos\` (local) | `gestor.sqlite`, `servidor.json`, temporales y simulaciones. Se puede borrar para empezar de cero. |
| `<raiz_destino>\Registro_Migracion_DiscoC\` | CSV reales del motor (fuente del historial). |

## Comandos
```powershell
.\iniciar.ps1                                         # arrancar sin ventana (o el acceso directo)
.\iniciar.ps1 -Consola -Puerto 8811                  # diagnóstico en primer plano, puerto de pruebas
.\detener.ps1                                         # detener solo el servidor del gestor
.venv\Scripts\python.exe -m pytest -q                 # pruebas (≈1 min)
.venv\Scripts\python.exe -m pytest -q -m "not motor"  # sin PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\crear-acceso-directo.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\auditar-privacidad.ps1
git config core.hooksPath .githooks                   # en cada clon nuevo
```

## Reglas que no se negocian
- Nada se mueve sin aprobación explícita; nada se borra; nunca se sobrescribe.
- Las pruebas de mover y deshacer se hacen solo sobre directorios temporales (fixtures).
- Las verificaciones manuales usan un puerto distinto del 8742 (el de uso diario) y se detienen con
  `detener.ps1` o por PID, nunca matando procesos ajenos.
- No escribir en archivos versionados nombres, rutas reales, registros ni tokens. Pasa la auditoría antes de cada commit.
- Si cambias reglas o lógica de clasificación, hazlo en `reglas.json` o en el motor, nunca duplicándolas en Python.

## Deshacer una junction a mano (mientras no exista en la app)
Con la app que usa la caché cerrada: `cmd /c rmdir "<ruta de la junction>"` (quita solo el enlace) y luego
mueve la carpeta de `<raiz_destino>\Cache_Aplicaciones_Enlazadas\<Nombre>` a su ruta original.

## Estado exacto
- Rama `main` con un único commit, marcado con el tag local `etapa-1`. Sin remoto. Árbol limpio y auditoría limpia.
- Etapa 2 en curso (ver `PROJECT_STATUS.md`, punto 1): hechos la comprobación de versión y la configuración por capas
  con `-Config`; siguen los detectores, la configuración inicial y el instalador.

## Siguiente paso recomendado
1. Publicar `etapa-1` (punto 2 de `PROJECT_STATUS.md`).
2. Comprobación de versión entre interfaz y servidor (punto 3): `__version__` en `gestor\__init__.py`, `/salud`,
   `web\conexion.js` (comparar al conectar) e `iniciar.ps1` (si la versión de `/salud` no coincide con la del código,
   detener con `detener.ps1` y arrancar de nuevo).
3. Etapa 2 en commits separados, empezando por la configuración por capas y `-Config` del motor, con pruebas.
