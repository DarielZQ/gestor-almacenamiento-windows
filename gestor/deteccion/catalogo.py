"""Catálogo genérico de cachés de apps comunes.

La fuente principal es config/catalogo_caches.json (editable, marca 'segura'). Este diccionario lo complementa con
entradas que aún no están en el archivo y sirve de respaldo si el archivo no se puede leer. Para cada caché:
nombre (carpeta de destino dentro de Cache_Aplicaciones_Enlazadas), app, categoría, ruta con %VARIABLES%,
procesos que deben estar cerrados, 'reubicable_con_junction' y una nota opcional.
"""
from __future__ import annotations

from pathlib import Path

from ..config import DIR_CONFIG, leer_json

RUTA_CATALOGO = DIR_CONFIG / "catalogo_caches.json"

_NAVEGADOR = "Caché del navegador: usa la limpieza del propio navegador; con varios perfiles cada uno tiene la suya."


def _c(nombre, app, categoria, ruta, reubicable, procesos=(), nota=None):
    return {"nombre": nombre, "app": app, "categoria": categoria, "ruta": ruta, "reubicable_con_junction": reubicable,
            "procesos": list(procesos), "nota": nota}


CATALOGO_CACHES: list[dict] = [
    # Desarrollo
    _c("npm_cache", "npm (Node.js)", "desarrollo", "%LOCALAPPDATA%\\npm-cache", True, ["node"]),
    _c("pip_cache", "pip (Python)", "desarrollo", "%LOCALAPPDATA%\\pip\\cache", True, []),
    _c("yarn_cache", "Yarn", "desarrollo", "%LOCALAPPDATA%\\Yarn\\Cache", True, ["node"]),
    _c("NuGet_v3_cache", "NuGet", "desarrollo", "%LOCALAPPDATA%\\NuGet\\v3-cache", True, ["devenv", "dotnet"]),
    _c("NuGet_packages", "NuGet", "desarrollo", "%USERPROFILE%\\.nuget\\packages", True, ["devenv", "dotnet"]),
    _c("Gradle_caches", "Gradle", "desarrollo", "%USERPROFILE%\\.gradle\\caches", True, ["java", "studio64"]),
    _c("Maven_repository", "Maven", "desarrollo", "%USERPROFILE%\\.m2\\repository", True, ["java"]),
    _c("JetBrains_caches", "IDEs de JetBrains", "desarrollo", "%LOCALAPPDATA%\\JetBrains", True,
       ["idea64", "pycharm64", "webstorm64", "rider64", "clion64", "goland64", "phpstorm64", "datagrip64"]),
    _c("VSCode_CachedData", "Visual Studio Code", "desarrollo", "%APPDATA%\\Code\\CachedData", True, ["Code"]),
    _c("VSCode_Cache", "Visual Studio Code", "desarrollo", "%APPDATA%\\Code\\Cache", True, ["Code"]),
    _c("NetBeans_Cache", "NetBeans", "desarrollo", "%LOCALAPPDATA%\\NetBeans\\Cache", True, ["netbeans64", "netbeans"]),
    _c("uv_cache", "uv (Python)", "desarrollo", "%LOCALAPPDATA%\\uv", False, [],
       "Usa enlaces duros hacia los entornos: no se reubica."),
    # Gráficos
    _c("NVIDIA_DXCache", "NVIDIA (sombreadores DirectX)", "graficos", "%LOCALAPPDATA%\\NVIDIA\\DXCache", True),
    _c("NVIDIA_GLCache", "NVIDIA (sombreadores OpenGL)", "graficos", "%LOCALAPPDATA%\\NVIDIA\\GLCache", True),
    _c("AMD_DxCache", "AMD (sombreadores DirectX)", "graficos", "%LOCALAPPDATA%\\AMD\\DxCache", True),
    _c("AMD_DxcCache", "AMD (sombreadores DXC)", "graficos", "%LOCALAPPDATA%\\AMD\\DxcCache", True),
    _c("AMD_GLCache", "AMD (sombreadores OpenGL)", "graficos", "%LOCALAPPDATA%\\AMD\\GLCache", True),
    _c("Unity_cache", "Unity", "graficos", "%LOCALAPPDATA%\\Unity\\cache", True, ["Unity", "Unity Hub"]),
    # Navegadores
    _c("Chrome_Cache", "Google Chrome", "navegador", "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Cache", False,
       ["chrome"], _NAVEGADOR),
    _c("Edge_Cache", "Microsoft Edge", "navegador", "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Cache", False,
       ["msedge"], _NAVEGADOR),
    _c("Brave_Cache", "Brave", "navegador", "%LOCALAPPDATA%\\BraveSoftware\\Brave-Browser\\User Data\\Default\\Cache",
       False, ["brave"], _NAVEGADOR),
    _c("Opera_Cache", "Opera", "navegador", "%LOCALAPPDATA%\\Opera Software\\Opera Stable\\Cache", False, ["opera"],
       _NAVEGADOR),
    _c("Firefox_Cache", "Firefox", "navegador", "%LOCALAPPDATA%\\Mozilla\\Firefox\\Profiles", False, ["firefox"],
       _NAVEGADOR),
    # Apps
    _c("Discord_Cache", "Discord", "app", "%APPDATA%\\discord\\Cache", True, ["Discord"]),
    _c("Spotify_Storage", "Spotify", "app", "%LOCALAPPDATA%\\Spotify\\Storage", True, ["Spotify"]),
    _c("Teams_nuevo", "Microsoft Teams", "app", "%LOCALAPPDATA%\\Packages\\MSTeams_8wekyb3d8bbwe", False, ["ms-teams"],
       "App MSIX (Packages): no admite junctions de forma fiable."),
    _c("Teams_clasico", "Microsoft Teams (clásico)", "app", "%APPDATA%\\Microsoft\\Teams", False, ["Teams"],
       "Teams clásico: la versión nueva vive en Packages (MSIX) y no admite junctions."),
    # Sistema
    _c("Temp", "Windows y apps (temporales del usuario)", "sistema", "%LOCALAPPDATA%\\Temp", False, [],
       "Temporales: límpialos desde Configuración > Sistema > Almacenamiento."),
]


def normalizar_entrada(e) -> dict | None:
    """Unifica el formato del archivo (clave 'segura') y el del diccionario ('reubicable_con_junction')."""
    if not isinstance(e, dict) or not isinstance(e.get("nombre"), str) or not isinstance(e.get("ruta"), str):
        return None
    reubicable = e.get("reubicable_con_junction", e.get("segura", False))
    procesos = e.get("procesos") if isinstance(e.get("procesos"), list) else []
    return {"nombre": e["nombre"], "app": str(e.get("app") or e["nombre"]), "categoria": str(e.get("categoria") or "otra"),
            "ruta": e["ruta"], "reubicable_con_junction": bool(reubicable),
            "procesos": [str(p) for p in procesos], "nota": e.get("nota")}


def cargar_catalogo(ruta: Path | str | None = None) -> tuple[list[dict], str | None]:
    """Catálogo efectivo: entradas del archivo (mandan) + las del diccionario que no estén. Devuelve (lista, aviso)."""
    ruta = Path(ruta) if ruta else RUTA_CATALOGO
    aviso = None
    del_archivo: list[dict] = []
    if ruta.exists():
        try:
            datos = leer_json(ruta)
            lista = (datos.get("cachés") or datos.get("caches") or []) if isinstance(datos, dict) else []
            del_archivo = [n for n in (normalizar_entrada(e) for e in lista) if n]
        except (OSError, ValueError) as e:
            aviso = f"No se pudo leer {ruta.name} ({e}); se usa el catálogo interno de cachés."
    vistos = {e["nombre"].lower() for e in del_archivo}
    # La categoría del diccionario completa las entradas del archivo, que no la traen.
    categorias = {e["nombre"].lower(): e["categoria"] for e in CATALOGO_CACHES}
    for e in del_archivo:
        if e["categoria"] == "otra":
            e["categoria"] = categorias.get(e["nombre"].lower(), "otra")
    extra = [dict(e) for e in CATALOGO_CACHES if e["nombre"].lower() not in vistos]
    return del_archivo + extra, aviso
