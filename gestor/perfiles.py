"""Perfiles: exportar e importar la configuración para llevarla a otro equipo.

Por defecto la exportación NO lleva datos personales ni del equipo: solo ajustes generales, la taxonomía y las
extensiones. Si el usuario lo marca, añade también las palabras clave, las reglas propias, las protecciones
personales y los datos del equipo (rutas detectadas, destino), útiles para restaurar en el mismo equipo.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import CLAVES_EQUIPO, CONFIG_EQUIPO, escribir_json, fusionar, leer_json, validar

FORMATO = "gestor-almacenamiento-perfil"
CLAVES_GENERALES = ("horas_recientes", "umbral_grande_mb", "vigilancia_minutos", "presupuesto_segundos_carpeta",
                    "presupuesto_segundos_zona", "taxonomia")
LOCAL_GENERAL = ("taxonomia", "extensiones")
LOCAL_PERSONAL = ("patrones", "intocables", "carpetas_usuario", "enlaces_app", "raiz_destino", "archivos")


class ErrorPerfil(ValueError):
    pass


def _rutas(cfg: dict) -> tuple[Path, Path]:
    config = Path(cfg.get("ruta_config_equipo") or CONFIG_EQUIPO)
    local = Path(cfg["ruta_reglas"]).with_name("reglas.local.json")
    return config, local


def exportar(cfg: dict, incluir_personal: bool = False) -> dict:
    ruta_config, ruta_local = _rutas(cfg)
    equipo = leer_json(ruta_config) if ruta_config.exists() else {}
    local = leer_json(ruta_local) if ruta_local.exists() else {}
    general = {k: equipo[k] for k in CLAVES_GENERALES if k in equipo}
    dup = dict(equipo.get("duplicados") or {})
    if dup and not incluir_personal:
        dup.pop("raices_extra", None)
    if dup:
        general["duplicados"] = dup
    perfil = {"formato": FORMATO, "version": 1, "fecha": datetime.now().isoformat(timespec="seconds"),
              "incluye_personal": incluir_personal, "config": general,
              "reglas_local": {k: local[k] for k in LOCAL_GENERAL if k in local}}
    if incluir_personal:
        perfil["reglas_local"].update({k: local[k] for k in LOCAL_PERSONAL if k in local})
        perfil["equipo"] = {k: equipo[k] for k in CLAVES_EQUIPO if k in equipo}
    return perfil


def importar(cfg: dict, perfil: dict, aplicar_equipo: bool = False) -> dict:
    if not isinstance(perfil, dict) or perfil.get("formato") != FORMATO:
        raise ErrorPerfil("El archivo no es un perfil del gestor.")
    if perfil.get("version") != 1:
        raise ErrorPerfil(f"Versión de perfil no admitida: {perfil.get('version')}.")
    ruta_config, ruta_local = _rutas(cfg)
    equipo = leer_json(ruta_config) if ruta_config.exists() else {}
    nuevo = fusionar(equipo, {k: v for k, v in (perfil.get("config") or {}).items() if k in CLAVES_GENERALES + ("duplicados",)})
    if aplicar_equipo and perfil.get("equipo"):
        nuevo = fusionar(nuevo, {k: v for k, v in perfil["equipo"].items() if k in CLAVES_EQUIPO})
    problemas = validar(nuevo)
    if problemas:
        raise ErrorPerfil("El perfil tiene valores no válidos: " + " ".join(problemas))
    capa = {k: v for k, v in (perfil.get("reglas_local") or {}).items() if k in LOCAL_GENERAL + LOCAL_PERSONAL}
    local = leer_json(ruta_local) if ruta_local.exists() else {}
    if nuevo != equipo:
        escribir_json(ruta_config, nuevo)
    if capa:
        escribir_json(ruta_local, fusionar(local, capa))
    return {"config_actualizada": nuevo != equipo, "reglas_actualizadas": sorted(capa), "equipo_aplicado": bool(aplicar_equipo and perfil.get("equipo"))}
