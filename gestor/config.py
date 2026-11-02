"""Configuración del gestor y lectura de las reglas compartidas con el motor.

Las rutas reales del equipo nunca se escriben en archivos versionados: se resuelven en tiempo de
ejecución con variables de entorno y el registro de Windows (solo lectura).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
DIR_CONFIG = RAIZ_PROYECTO / "config"
MOTOR_PS1 = RAIZ_PROYECTO / "motor" / "Motor-Almacenamiento.ps1"
DIR_WEB = RAIZ_PROYECTO / "web"


def fusionar(base: dict, extra: dict) -> dict:
    """Misma semántica que Merge-Reglas del motor: objetos clave a clave; listas y textos se reemplazan."""
    resultado = copy.deepcopy(base)
    for clave, valor in extra.items():
        if isinstance(resultado.get(clave), dict) and isinstance(valor, dict):
            resultado[clave] = fusionar(resultado[clave], valor)
        else:
            resultado[clave] = copy.deepcopy(valor)
    return resultado


def _leer_json(ruta: Path) -> dict:
    with open(ruta, encoding="utf-8-sig") as f:
        return json.load(f)


def cargar_reglas(ruta: Path | None = None) -> dict:
    """Lee config/reglas.json y, si existe, fusiona encima config/reglas.local.json."""
    ruta = Path(ruta) if ruta else DIR_CONFIG / "reglas.json"
    reglas = _leer_json(ruta)
    local = ruta.with_name(ruta.stem + ".local.json")
    if local.exists():
        reglas = fusionar(reglas, _leer_json(local))
    return reglas


def _carpeta_shell(valor: str, defecto: str) -> str:
    """Lee una carpeta de usuario del registro (User Shell Folders), en solo lectura."""
    try:
        import winreg

        clave = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
            dato, _ = winreg.QueryValueEx(k, valor)
            return os.path.expandvars(dato)
    except OSError:
        return defecto


def rutas_sistema() -> dict:
    perfil = os.environ.get("USERPROFILE", str(Path.home()))
    return {
        "perfil": perfil,
        "descargas": _carpeta_shell("{374DE290-123F-4565-9164-39C4925E467B}", os.path.join(perfil, "Downloads")),
        "escritorio": _carpeta_shell("Desktop", os.path.join(perfil, "Desktop")),
        "documentos": _carpeta_shell("Personal", os.path.join(perfil, "Documents")),
        "raiz_sistema": os.environ.get("SystemDrive", "C:") + "\\",
        "localappdata": os.environ.get("LOCALAPPDATA", os.path.join(perfil, "AppData", "Local")),
        "appdata": os.environ.get("APPDATA", os.path.join(perfil, "AppData", "Roaming")),
    }


def cargar_config(ruta: Path | None = None, extra: dict | None = None) -> dict:
    """Valores por defecto (config.example.json) + config/config.json local + 'extra' (pruebas)."""
    cfg = _leer_json(DIR_CONFIG / "config.example.json")
    ruta = Path(ruta) if ruta else Path(os.environ.get("GESTOR_CONFIG") or DIR_CONFIG / "config.json")
    if ruta.exists():
        cfg = fusionar(cfg, _leer_json(ruta))
    if extra:
        cfg = fusionar(cfg, extra)
    detectadas = rutas_sistema()
    rutas = cfg.get("rutas") or {}
    cfg["rutas"] = {k: (rutas.get(k) or v) for k, v in detectadas.items()}
    for k, v in rutas.items():
        if v and k not in cfg["rutas"]:
            cfg["rutas"][k] = v
    raiz = cfg.get("raiz_destino") or "D:\\"
    cfg["raiz_destino"] = raiz if raiz.endswith("\\") else raiz + "\\"
    if not cfg.get("dir_registro"):
        cfg["dir_registro"] = os.path.join(cfg["raiz_destino"], "Registro_Migracion_DiscoC")
    cfg["dir_datos"] = cfg.get("dir_datos") or os.environ.get("GESTOR_DATOS") or str(RAIZ_PROYECTO / "datos")
    cfg["ruta_reglas"] = cfg.get("ruta_reglas") or str(DIR_CONFIG / "reglas.json")
    cfg["ruta_motor"] = cfg.get("ruta_motor") or str(MOTOR_PS1)
    return cfg


def expandir(ruta: str) -> str | None:
    """Expande %VARIABLES%; devuelve None si alguna no existe (igual que el motor)."""
    e = os.path.expandvars(ruta)
    return None if "%" in e else e.rstrip("\\")
