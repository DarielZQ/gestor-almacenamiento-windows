"""Respaldo automático de la configuración personal, con fixtures temporales."""
import json

import pytest

from gestor import respaldo
from gestor.servicio import ErrorGestor


def preparar(equipo, servicio, contenido='{"intocables": {"proyectos_usuario": ["ProyectoPrueba"]}}'):
    (equipo.config / "reglas.local.json").write_text(contenido, encoding="utf-8")
    (equipo.config / "config.json").write_text('{"horas_recientes": 3}', encoding="utf-8")
    servicio.cfg["ruta_config_equipo"] = str(equipo.config / "config.json")


def test_respalda_solo_si_cambia(equipo, servicio):
    preparar(equipo, servicio)
    r1 = servicio.respaldar_config("al arrancar")
    assert r1 and sorted(r1["archivos"]) == ["config.json", "reglas.local.json"]
    assert servicio.respaldar_config("al arrancar") is None           # sin cambios: no se repite
    (equipo.config / "reglas.local.json").write_text('{"patrones": {}}', encoding="utf-8")
    assert servicio.respaldar_config("al arrancar") is not None       # cambió el hash
    copias = servicio.respaldos()
    assert len(copias) == 2
    carpeta = equipo.registro / "respaldo_config" / copias[0]["nombre"]
    assert json.loads((carpeta / "reglas.local.json").read_text(encoding="utf-8")) == {"patrones": {}}
    assert not list(carpeta.glob("*.sqlite"))                          # la base no se respalda


def test_rotacion_conserva_20_y_solo_toca_lo_propio(equipo, servicio):
    preparar(equipo, servicio)
    base = equipo.registro / "respaldo_config"
    ajena = base / "0000_carpeta_del_usuario"
    ajena.mkdir(parents=True)
    (ajena / "nota.txt").write_text("no es de la app", encoding="utf-8")
    for i in range(25):
        servicio.respaldar_config(f"cambio {i}", forzar=True)
    assert len(servicio.respaldos()) == 20
    assert (ajena / "nota.txt").exists()                              # nunca se toca lo que no creó la app


def test_restaurar_guarda_antes_la_actual(equipo, servicio):
    preparar(equipo, servicio, '{"intocables": {"proyectos_usuario": ["Version1"]}}')
    servicio.respaldar_config("v1", forzar=True)
    v1 = servicio.respaldos()[0]["nombre"]
    (equipo.config / "reglas.local.json").write_text('{"intocables": {"proyectos_usuario": ["Version2"]}}', encoding="utf-8")
    r = servicio.restaurar_respaldo(v1)
    assert r["respaldo_previo"]
    assert "Version1" in (equipo.config / "reglas.local.json").read_text(encoding="utf-8")
    assert servicio.reglas["intocables"]["proyectos_usuario"] == ["Version1"]  # reglas recargadas
    previo = equipo.registro / "respaldo_config" / r["respaldo_previo"] / "reglas.local.json"
    assert "Version2" in previo.read_text(encoding="utf-8")
    with pytest.raises(ErrorGestor):
        servicio.restaurar_respaldo("..\\fuera")


def test_sin_carpeta_de_registro_no_falla(equipo, servicio):
    preparar(equipo, servicio)
    servicio.cfg["dir_registro"] = None
    assert servicio.respaldar_config() is None
    assert respaldo.listar(servicio.cfg) == []
