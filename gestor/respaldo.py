"""Respaldo automático de la configuración personal (config.json y reglas.local.json).

- Se copia a <dir_registro>\\respaldo_config\\<fecha>\\ cuando cambia el contenido (se compara un hash).
- Se conservan las últimas 20 copias. La rotación solo quita carpetas que esta app creó (llevan el
  archivo marcador MARCADOR con la firma de la app); nunca toca nada más de esa carpeta.
- Restaurar una copia guarda antes la configuración actual.
- No se respalda la base SQLite.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

MARCADOR = "respaldo_gestor.json"
FIRMA = "gestor-almacenamiento-windows"
CONSERVAR = 20


class ErrorRespaldo(ValueError):
    pass


def archivos_personales(cfg: dict) -> dict[str, Path]:
    dir_config = Path(cfg["ruta_reglas"]).parent
    return {
        "config.json": Path(cfg.get("ruta_config_equipo") or dir_config / "config.json"),
        "reglas.local.json": dir_config / "reglas.local.json",
    }


def dir_respaldos(cfg: dict) -> Path | None:
    d = cfg.get("dir_registro")
    return Path(d) / "respaldo_config" if d else None


def huella(cfg: dict) -> str:
    h = hashlib.sha256()
    for nombre, ruta in sorted(archivos_personales(cfg).items()):
        h.update(nombre.encode())
        h.update(ruta.read_bytes() if ruta.exists() else b"<ausente>")
    return h.hexdigest()


def _es_respaldo_propio(carpeta: Path) -> bool:
    try:
        return json.loads((carpeta / MARCADOR).read_text(encoding="utf-8")).get("app") == FIRMA
    except (OSError, ValueError):
        return False


def listar(cfg: dict) -> list[dict]:
    base = dir_respaldos(cfg)
    if not base or not base.is_dir():
        return []
    res = []
    for c in sorted(base.iterdir(), reverse=True):
        if c.is_dir() and _es_respaldo_propio(c):
            meta = json.loads((c / MARCADOR).read_text(encoding="utf-8"))
            res.append({"nombre": c.name, "fecha": meta.get("fecha"), "motivo": meta.get("motivo"),
                        "archivos": sorted(p.name for p in c.iterdir() if p.name != MARCADOR)})
    return res


def respaldar(cfg: dict, db=None, motivo: str = "cambio detectado", forzar: bool = False) -> dict | None:
    """Copia los archivos personales si cambiaron desde el último respaldo (o si 'forzar'). Devuelve el respaldo o None."""
    base = dir_respaldos(cfg)
    if not base:
        return None
    actual = huella(cfg)
    if not forzar and db is not None and db.ajuste("respaldo_config_huella") == actual:
        return None
    existentes = {n: r for n, r in archivos_personales(cfg).items() if r.exists()}
    if not existentes:
        return None
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None  # destino no conectado: se intentará en el próximo arranque
    nombre = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    carpeta = base / nombre
    carpeta.mkdir()
    for n, r in existentes.items():
        shutil.copy2(r, carpeta / n)
    (carpeta / MARCADOR).write_text(json.dumps({"app": FIRMA, "fecha": datetime.now().isoformat(timespec="seconds"),
                                                "motivo": motivo, "huella": actual}, ensure_ascii=False), encoding="utf-8")
    if db is not None:
        db.guardar_ajuste("respaldo_config_huella", actual)
    rotar(cfg)
    return {"nombre": nombre, "archivos": sorted(existentes)}


def rotar(cfg: dict, conservar: int = CONSERVAR) -> list[str]:
    """Quita las copias propias más antiguas por encima de 'conservar'. Solo carpetas con el marcador de la app."""
    propios = [c for c in sorted(dir_respaldos(cfg).iterdir()) if c.is_dir() and _es_respaldo_propio(c)]
    quitados = []
    for c in propios[:-conservar] if len(propios) > conservar else []:
        permitidos = {MARCADOR, *archivos_personales(cfg).keys()}
        if any(p.name not in permitidos or p.is_dir() for p in c.iterdir()):
            continue  # contiene algo que la app no puso ahí: no se toca
        for p in c.iterdir():
            os.remove(p)
        os.rmdir(c)
        quitados.append(c.name)
    return quitados


def restaurar(cfg: dict, nombre: str, db=None) -> dict:
    base = dir_respaldos(cfg)
    if not base or "/" in nombre or "\\" in nombre or nombre.startswith("."):
        raise ErrorRespaldo("respaldo no válido")
    carpeta = base / nombre
    if not carpeta.is_dir() or not _es_respaldo_propio(carpeta):
        raise ErrorRespaldo("no existe ese respaldo")
    previo = respaldar(cfg, db, motivo=f"antes de restaurar {nombre}", forzar=True)
    restaurados = []
    for n, destino in archivos_personales(cfg).items():
        origen = carpeta / n
        if origen.exists():
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, destino)
            restaurados.append(n)
    if db is not None:
        db.guardar_ajuste("respaldo_config_huella", huella(cfg))
    return {"restaurado": nombre, "archivos": restaurados, "respaldo_previo": previo["nombre"] if previo else None}
