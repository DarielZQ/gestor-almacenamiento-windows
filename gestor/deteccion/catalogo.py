"""Catálogo genérico de cachés de apps comunes.

La única fuente es config/catalogo_caches.json (editable). Para cada caché: nombre (carpeta de destino dentro de
Cache_Aplicaciones_Enlazadas), app, categoría, ruta con %VARIABLES%, procesos que deben estar cerrados, 'segura'
(reubicable con junction) y una nota opcional.
"""
from __future__ import annotations

from pathlib import Path

from ..config import DIR_CONFIG, leer_json

RUTA_CATALOGO = DIR_CONFIG / "catalogo_caches.json"

def _leer_archivo(ruta: Path) -> list[dict]:
    datos = leer_json(ruta)
    lista = (datos.get("cachés") or datos.get("caches") or []) if isinstance(datos, dict) else []
    return [n for n in (normalizar_entrada(e) for e in lista) if n]


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
    """Catálogo efectivo: config/catalogo_caches.json es la única fuente. Devuelve (lista, aviso)."""
    ruta = Path(ruta) if ruta else RUTA_CATALOGO
    try:
        return _leer_archivo(ruta), None
    except (OSError, ValueError) as e:
        return [], f"No se pudo leer {ruta.name} ({e}); no se revisarán cachés."


try:  # compatibilidad: lista ya cargada para quien la importe
    CATALOGO_CACHES: list[dict] = _leer_archivo(RUTA_CATALOGO)
except (OSError, ValueError):
    CATALOGO_CACHES = []
