"""Configuración por capas y lectura de las reglas compartidas con el motor.

Capas de configuración:  config/config.example.json (valores del repo)
                      -> config/config.json (de este equipo, lo genera la configuración inicial; no versionado)
                      -> 'extra' (pruebas o línea de comandos)
Capas de reglas:         config/reglas.json -> config/taxonomias/<idioma>.json -> config/reglas.local.json (personal)

Las rutas reales del equipo nunca se escriben en archivos versionados: se detectan en tiempo de ejecución.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
DIR_CONFIG = RAIZ_PROYECTO / "config"
MOTOR_PS1 = RAIZ_PROYECTO / "motor" / "Motor-Almacenamiento.ps1"
DIR_WEB = RAIZ_PROYECTO / "web"
CONFIG_EQUIPO = DIR_CONFIG / "config.json"

# Claves que describen el equipo (las genera la configuración inicial). No se exportan en un perfil sin datos personales.
CLAVES_EQUIPO = ("raiz_destino", "dir_registro", "rutas", "intocables_detectados", "caches", "discos", "destino_usb",
                 "permitir_junctions", "modo", "nube", "bibliotecas")


class ErrorConfig(ValueError):
    pass


def fusionar(base: dict, extra: dict) -> dict:
    """Misma semántica que Merge-Reglas del motor: objetos clave a clave; listas y textos se reemplazan."""
    resultado = copy.deepcopy(base)
    for clave, valor in extra.items():
        if isinstance(resultado.get(clave), dict) and isinstance(valor, dict):
            resultado[clave] = fusionar(resultado[clave], valor)
        else:
            resultado[clave] = copy.deepcopy(valor)
    return resultado


def leer_json(ruta: Path) -> dict:
    with open(ruta, encoding="utf-8-sig") as f:
        return json.load(f)


def escribir_json(ruta: Path, datos: dict) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, ruta)


def cargar_reglas(ruta: Path | str | None = None, idioma: str | None = None) -> dict:
    """reglas.json + taxonomía del idioma elegido + reglas.local.json (si existen)."""
    ruta = Path(ruta) if ruta else DIR_CONFIG / "reglas.json"
    reglas = leer_json(ruta)
    if idioma and idioma != "es":
        tax = ruta.parent / "taxonomias" / f"{idioma}.json"
        if tax.exists():
            reglas = fusionar(reglas, leer_json(tax))
    local = ruta.with_name(ruta.stem + ".local.json")
    if local.exists():
        reglas = fusionar(reglas, leer_json(local))
    return reglas


def _carpeta_shell(valor: str, defecto: str) -> str:
    """Lee una carpeta de usuario del registro (User Shell Folders, respeta redirecciones a OneDrive). Solo lectura."""
    try:
        import winreg

        clave = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
            dato, _ = winreg.QueryValueEx(k, valor)
            return os.path.expandvars(dato)
    except OSError:
        return defecto


def rutas_sistema(entorno: dict | None = None, leer_shell=None) -> dict:
    env = entorno if entorno is not None else os.environ
    leer = leer_shell or _carpeta_shell
    perfil = env.get("USERPROFILE") or str(Path.home())
    return {
        "perfil": perfil,
        "descargas": leer("{374DE290-123F-4565-9164-39C4925E467B}", os.path.join(perfil, "Downloads")),
        "escritorio": leer("Desktop", os.path.join(perfil, "Desktop")),
        "documentos": leer("Personal", os.path.join(perfil, "Documents")),
        "raiz_sistema": (env.get("SystemDrive") or "C:") + "\\",
        "localappdata": env.get("LOCALAPPDATA") or os.path.join(perfil, "AppData", "Local"),
        "appdata": env.get("APPDATA") or os.path.join(perfil, "AppData", "Roaming"),
    }


def validar(cfg: dict) -> list[str]:
    """Esquema de config.json con mensajes claros. Devuelve la lista de problemas (vacía si es válida)."""
    errores = []

    def tipo(clave, tipos, nombre):
        if clave in cfg and cfg[clave] is not None and not isinstance(cfg[clave], tipos):
            errores.append(f"'{clave}' debe ser {nombre}.")

    tipo("puerto", int, "un número entero")
    tipo("raiz_destino", str, "una ruta (texto)")
    tipo("dir_registro", str, "una ruta (texto)")
    for k in ("horas_recientes", "umbral_grande_mb", "vigilancia_minutos", "presupuesto_segundos_carpeta", "presupuesto_segundos_zona"):
        tipo(k, (int, float), "un número")
        if isinstance(cfg.get(k), (int, float)) and not isinstance(cfg.get(k), bool) and cfg[k] < 0:
            errores.append(f"'{k}' no puede ser negativo.")
    tipo("permitir_junctions", bool, "verdadero o falso")
    tipo("intocables_detectados", list, "una lista")
    tipo("caches", list, "una lista")
    tipo("rutas", dict, "un objeto")
    if isinstance(cfg.get("puerto"), int) and not (1 <= cfg["puerto"] <= 65535):
        errores.append("'puerto' debe estar entre 1 y 65535.")
    for clave in ("raiz_destino", "dir_registro"):
        v = cfg.get(clave)
        if isinstance(v, str) and v and not re.match(r"^[A-Za-z]:\\", v):
            errores.append(f"'{clave}' debe ser una ruta absoluta de Windows, por ejemplo E:\\Ejemplo\\.")
    if cfg.get("modo") not in (None, "completo", "solo_analisis"):
        errores.append("'modo' debe ser 'completo' o 'solo_analisis'.")
    if cfg.get("taxonomia") not in (None, "es", "en"):
        errores.append("'taxonomia' debe ser 'es' o 'en'.")
    for i, d in enumerate(cfg.get("intocables_detectados") or []):
        if not (isinstance(d, str) or (isinstance(d, dict) and isinstance(d.get("ruta"), str))):
            errores.append(f"'intocables_detectados[{i}]' debe tener una 'ruta'.")
    for i, c in enumerate(cfg.get("caches") or []):
        if not (isinstance(c, dict) and isinstance(c.get("nombre"), str) and isinstance(c.get("ruta"), str)):
            errores.append(f"'caches[{i}]' debe tener 'nombre' y 'ruta'.")
    return errores


def cargar_config(ruta: Path | str | None = None, extra: dict | None = None, entorno: dict | None = None) -> dict:
    """Valores por defecto + config.json del equipo + 'extra'. Lanza ErrorConfig con mensajes claros si no es válida."""
    cfg = leer_json(DIR_CONFIG / "config.example.json")
    ruta = Path(ruta) if ruta else Path(os.environ.get("GESTOR_CONFIG") or CONFIG_EQUIPO)
    if ruta.exists():
        try:
            equipo = leer_json(ruta)
        except json.JSONDecodeError as e:
            raise ErrorConfig(f"{ruta.name} no es JSON válido (línea {e.lineno}): {e.msg}") from e
        problemas = validar(equipo)
        if problemas:
            raise ErrorConfig(f"{ruta.name} tiene errores: " + " ".join(problemas))
        cfg = fusionar(cfg, equipo)
    if extra:
        cfg = fusionar(cfg, extra)
    cfg["ruta_config_equipo"] = cfg.get("ruta_config_equipo") or str(ruta)
    detectadas = rutas_sistema(entorno)
    rutas = cfg.get("rutas") or {}
    cfg["rutas"] = {k: (rutas.get(k) or v) for k, v in detectadas.items()}
    for k, v in rutas.items():
        if v and k not in cfg["rutas"]:
            cfg["rutas"][k] = v
    raiz = cfg.get("raiz_destino")
    if raiz:
        cfg["raiz_destino"] = raiz if raiz.endswith("\\") else raiz + "\\"
        if not cfg.get("dir_registro"):
            cfg["dir_registro"] = os.path.join(cfg["raiz_destino"], "Registro_Migracion_DiscoC")
    else:
        cfg["raiz_destino"] = None
        cfg["dir_registro"] = cfg.get("dir_registro") or None
    cfg["configurado"] = bool(cfg["raiz_destino"])
    cfg["dir_datos"] = cfg.get("dir_datos") or os.environ.get("GESTOR_DATOS") or str(RAIZ_PROYECTO / "datos")
    cfg["ruta_reglas"] = cfg.get("ruta_reglas") or str(DIR_CONFIG / "reglas.json")
    cfg["ruta_motor"] = cfg.get("ruta_motor") or str(MOTOR_PS1)
    return cfg


def config_para_motor(cfg: dict) -> dict:
    """Lo que el motor necesita saber del equipo (se le pasa con -Config)."""
    datos = {k: cfg.get(k) for k in ("raiz_destino", "dir_registro", "intocables_detectados", "permitir_junctions", "taxonomia")}
    if cfg.get("caches") is not None:
        datos["caches"] = cfg["caches"]
    return {k: v for k, v in datos.items() if v is not None}


def expandir(ruta: str, entorno: dict | None = None) -> str | None:
    """Expande %VARIABLES%; devuelve None si alguna no existe (igual que el motor)."""
    if entorno is None:
        e = os.path.expandvars(ruta)
    else:
        bajo = {k.lower(): v for k, v in entorno.items()}  # en Windows las variables no distinguen mayúsculas
        e = re.sub(r"%([^%]+)%", lambda m: bajo.get(m.group(1).lower(), m.group(0)), ruta)
    return None if "%" in e else e.rstrip("\\")
