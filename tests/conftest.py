"""Fixtures: un 'equipo de mentira' dentro de un directorio temporal.

Todas las pruebas que mueven archivos lo hacen sobre estas carpetas temporales, pasando al motor
rutas raíz configurables (-RaizDestino, -DirRegistro, -Reglas). Nunca se tocan archivos reales.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from gestor.config import cargar_config, cargar_reglas  # noqa: E402
from gestor.motor import Motor  # noqa: E402
from gestor.servicio import Servicio  # noqa: E402

HACE_TRES_DIAS = time.time() - 3 * 24 * 3600

hay_powershell = shutil.which("powershell.exe") is not None
requiere_motor = pytest.mark.skipif(not hay_powershell, reason="requiere powershell.exe (Windows)")


def escribir(ruta: Path, contenido: bytes | str = b"x", antiguo: bool = True) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(contenido, str):
        contenido = contenido.encode("utf-8")
    ruta.write_bytes(contenido)
    if antiguo:
        os.utime(ruta, (HACE_TRES_DIAS, HACE_TRES_DIAS))
    return ruta


class Equipo:
    """Rutas del equipo de prueba."""

    def __init__(self, base: Path):
        self.base = base
        self.perfil = base / "perfil"
        self.descargas = self.perfil / "Descargas"
        self.escritorio = self.perfil / "Escritorio"
        self.documentos = self.perfil / "Documentos"
        self.local = self.perfil / "AppData" / "Local"
        self.roaming = self.perfil / "AppData" / "Roaming"
        self.sistema = base / "sistema"
        self.destino = base / "destino"
        self.registro = self.destino / "Registro_Migracion_DiscoC"
        self.datos = base / "datos"
        self.config = base / "config"
        self.cache = self.local / "CachePrueba"
        for d in (self.descargas, self.escritorio, self.documentos, self.local, self.roaming, self.sistema, self.destino,
                  self.config):
            d.mkdir(parents=True, exist_ok=True)

    def reglas(self, local: dict | None = None) -> Path:
        r = json.loads((RAIZ / "config" / "reglas.json").read_text(encoding="utf-8"))
        # Aislar la prueba del equipo real: cachés y carpetas conocidas apuntan al equipo de prueba.
        r["caches"]["lista"] = [{"nombre": "Cache_Prueba", "ruta": str(self.cache), "procesos": []}]
        r["analisis"]["carpetas_conocidas"] = [{"ruta": str(self.local / "Temp"), "nota": "Temporales de prueba"}]
        ruta = self.config / "reglas.json"
        ruta.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        local_ruta = self.config / "reglas.local.json"
        if local:
            local_ruta.write_text(json.dumps(local, ensure_ascii=False), encoding="utf-8")
        elif local_ruta.exists():
            local_ruta.unlink()
        return ruta

    def cfg(self, **extra) -> dict:
        base = {
            "raiz_destino": str(self.destino) + "\\",
            "dir_registro": str(self.registro),
            "dir_datos": str(self.datos),
            "ruta_reglas": str(self.config / "reglas.json"),
            "vigilancia_minutos": 0,
            "horas_recientes": 2,
            "umbral_grande_mb": 1,
            "presupuesto_segundos_carpeta": 10,
            "presupuesto_segundos_zona": 60,
            "duplicados": {"min_mb": 0, "hash_completo": True, "presupuesto_segundos": 60, "raices_extra": []},
            "rutas": {"perfil": str(self.perfil), "descargas": str(self.descargas), "escritorio": str(self.escritorio),
                      "documentos": str(self.documentos), "raiz_sistema": str(self.sistema),
                      "localappdata": str(self.local), "appdata": str(self.roaming)},
        }
        base.update(extra)
        return cargar_config(Path(self.config / "no-existe.json"), base)


@pytest.fixture
def equipo(tmp_path) -> Equipo:
    e = Equipo(tmp_path)
    # reglas.local.json de prueba con nombres inventados (proyecto propio y carpeta de una app ficticia)
    e.reglas({"intocables": {"proyectos_usuario": ["ProyectoPrueba"],
                             "rutas_extra": [str(e.local / "AppFicticia-Datos")]},
              "enlaces_app": {"lista": [{"nombre": "AppFicticia", "base": str(e.local / "AppFicticia-Datos"),
                                         "carpeta": "bundles", "procesos": [], "destino": "AppFicticia_bundles",
                                         "habilitada": False}]}})
    return e


@pytest.fixture
def motor(equipo) -> Motor:
    return Motor(str(RAIZ / "motor" / "Motor-Almacenamiento.ps1"), str(equipo.destino) + "\\", str(equipo.datos / "tmp"),
                 ruta_reglas=str(equipo.config / "reglas.json"))


@pytest.fixture
def servicio(equipo) -> Servicio:
    cfg = equipo.cfg()
    return Servicio(cfg, cargar_reglas(cfg["ruta_reglas"]))
