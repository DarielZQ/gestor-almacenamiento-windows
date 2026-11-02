# Adaptar el gestor a tu equipo

Guía paso a paso para usar el gestor en cualquier Windows 10/11, desde clonar el repositorio hasta la primera
migración segura. No hace falta editar código: todo lo propio de tu equipo se detecta y se guarda en archivos
locales que no se versionan.

> **Alcance:** solo Windows. El gestor depende de PowerShell, NTFS y las *junctions* de Windows. macOS y Linux
> quedan fuera de alcance por ahora (posible trabajo futuro).

## 1. Requisitos

- Windows 10 (1809 o posterior) u 11.
- Windows PowerShell 5.1 (viene con Windows) o PowerShell 7.
- Python 3.10 o superior. Si no lo tienes: `winget install --id Python.Python.3.12 -e` o
  <https://www.python.org/downloads/> (marca «Add python.exe to PATH»). El instalador no instala Python por su cuenta.
- Lo ideal: un segundo disco (interno o externo) con espacio libre. Con un solo disco el gestor funciona en modo
  «solo análisis y organización».

## 2. Clonar e instalar

```powershell
git clone <url-del-repositorio> gestor-almacenamiento-windows
cd gestor-almacenamiento-windows
.\instalar.ps1          # o doble clic en instalar.bat
```

El instalador:
1. comprueba Windows, PowerShell y Python;
2. crea el entorno virtual `.venv` (o reutiliza el que haya);
3. activa los hooks de privacidad del repositorio;
4. lanza la **configuración inicial** por consola (puedes saltarla y hacerla luego en la interfaz);
5. pregunta si quieres el acceso directo «Gestor de Almacenamiento» en el Escritorio.

Puedes repetirlo cuando quieras: no rompe nada y pregunta antes de rehacer la configuración.

## 3. Configuración inicial

Se hace en la pestaña **Equipo** de la interfaz (se abre sola la primera vez) o por consola con
`.venv\Scripts\python.exe -m gestor.configuracion_inicial`. Todo es de solo lectura hasta que confirmas.

El gestor detecta y te propone:

| Qué | Cómo se usa |
|---|---|
| **Discos**: letra, interno o USB, sistema de archivos y espacio | Origen = disco del sistema. Destino propuesto = el disco que no es del sistema con más espacio libre. |
| **Destino USB** | Aviso: si el disco se desconecta, las carpetas enlazadas con *junction* dejan de funcionar. La reubicación de cachés queda desactivada por defecto. |
| **Destino que no es NTFS** (exFAT, FAT32) | Se desactivan las *junctions*; los movimientos de archivos siguen funcionando. |
| **Un solo disco** | Modo «solo análisis»: puedes elegir una carpeta en el mismo disco o en uno externo, pero mover dentro del mismo disco **no libera espacio**. |
| **Carpetas del usuario** (Escritorio, Documentos, Descargas…) | Se leen del registro, respetando las redirecciones a OneDrive. |
| **Nube**: OneDrive personal y de empresa, Google Drive, Dropbox, iCloud | Pasan a **Intocable**. |
| **Bibliotecas de juegos y launchers**: Steam (todas las de `libraryfolders.vdf`), Epic, Xbox (`.GamingRoot`), Battle.net, Riot, EA, Ubisoft, GOG | Pasan a **Intocable**. Además, cualquier carpeta que contenga `steamapps`, `.egstore` o similares se protege por su contenido. |
| **Programas instalados, accesos directos, PATH y servicios** | Las rutas que usan se protegen en cada análisis. |
| **Cachés de apps comunes** (npm, pip, NuGet, Gradle, Maven, NVIDIA, AMD, IDEs, editores…) | Se miden y se marcan las que es seguro reubicar con *junction*. Tú eliges cuáles. |
| **Taxonomía** de carpetas de destino | Español (por defecto) o inglés. |

Al guardar se crea `config\config.json` (no se versiona). Si cambias de disco o conectas uno nuevo, vuelve a la
pestaña Equipo y pulsa **Volver a detectar equipo**.

## 4. Personalizar (opcional)

- **Taxonomía** (pestaña Taxonomía): renombra carpetas de destino, añade o quita categorías, edita extensiones y
  palabras clave y crea reglas propias («los `.jpg` con la palabra *viaje* van a *Fotos de viaje*»). Usa **Vista
  previa** antes de guardar para ver qué archivos cambiarían de categoría.
- **Protecciones propias**: en `config\reglas.local.json` (copia de `reglas.local.example.json`) puedes declarar
  proyectos que deben quedar intocables (`intocables.proyectos_usuario`), carpetas de datos de tus apps
  (`intocables.rutas_extra`) y carpetas pesadas de apps que quieras reubicar con *junction* (`enlaces_app`).
- **Perfil**: en Ajustes puedes exportar tu configuración para otro equipo. Por defecto no incluye datos personales.

## 5. Primera migración segura

1. **Resumen → Escaneo completo.** Solo lee. Muestra el espacio por zonas y lo que se podría liberar.
2. **Elementos.** Filtra por *Migrable*, revisa el motivo y el destino propuesto de cada elemento y apruébalo
   (uno a uno o en lote). Lo *Intocable* no se puede aprobar; lo de *Revisar* necesita que lo mires.
3. **Ejecutar → Simular.** El motor valida cada operación sin mover nada.
4. **Ejecutar → Ejecutar aprobados.** Cada elemento se revalida justo antes de moverse, nunca se sobrescribe nada
   y todo queda registrado en `<destino>\Registro_Migracion_DiscoC\`.
5. **Historial.** Si algo no te convence, **Deshacer** devuelve ese elemento a su sitio.

Consejos: empieza por pocos elementos, revisa el resultado en el Explorador y crece desde ahí. El gestor nunca
borra archivos.

## 6. Problemas frecuentes

| Síntoma | Qué hacer |
|---|---|
| «El servidor local no está corriendo» | Ábrelo con el acceso directo o `.\iniciar.ps1`. |
| «El gestor se actualizó: reinícialo» | Pulsa *Reiniciar ahora* (o vuelve a abrirlo con el acceso directo). |
| «No hay destino configurado» | Completa la pestaña Equipo. |
| «No se encuentra el destino» | Conecta el disco de destino o vuelve a detectar el equipo. |
| El puerto 8742 está ocupado | El gestor prueba los siguientes automáticamente. |
| Diagnóstico | `.\iniciar.ps1 -Consola` muestra la salida en primer plano; también `datos\servidor.log`. |
