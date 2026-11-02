"""Detección del equipo (solo lectura) y propuesta de configuración inicial.

    detectar_equipo(fuentes=None) -> dict      perfil del equipo, serializable a JSON
    proponer_configuracion(perfil) -> dict     propuesta compatible con config/config.json

Depuración por consola:  python -m gestor.deteccion [--json] [--propuesta]
"""
from .catalogo import CATALOGO_CACHES, cargar_catalogo
from .equipo import DETECTORES, VERSION_PERFIL, detectar_equipo
from .propuesta import proponer_configuracion

__all__ = ["CATALOGO_CACHES", "DETECTORES", "VERSION_PERFIL", "cargar_catalogo", "detectar_equipo",
           "proponer_configuracion"]
