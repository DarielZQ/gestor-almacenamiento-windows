"""Versión y huella del código: sirven para detectar que el servidor en marcha es más antiguo que el código en disco."""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import __version__

RAIZ = Path(__file__).resolve().parent.parent
PATRONES = ("gestor/*.py", "web/*.html", "web/*.js", "web/*.css", "motor/*.ps1", "config/reglas.json",
            "config/config.example.json", "config/catalogo_caches.json", "config/taxonomias/*.json")


def huella_codigo(raiz: Path = RAIZ) -> str:
    """Hash corto del código versionado que usa el servidor (no incluye configuración local ni datos)."""
    h = hashlib.sha256()
    archivos = sorted({p for patron in PATRONES for p in raiz.glob(patron) if p.is_file()})
    for p in archivos:
        h.update(p.relative_to(raiz).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()[:16]


class Version:
    def __init__(self, raiz: Path = RAIZ):
        self.raiz = raiz
        self.version = __version__
        self.hash_arranque = huella_codigo(raiz)

    def estado(self) -> dict:
        actual = huella_codigo(self.raiz)
        return {"version": self.version, "hash": self.hash_arranque, "hash_disco": actual,
                "desactualizado": actual != self.hash_arranque}
