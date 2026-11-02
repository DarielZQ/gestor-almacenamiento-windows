"""Perfiles exportables: sin datos personales por defecto; importación validada y con respaldo."""
import json

import pytest

from gestor import perfiles
from gestor.servicio import ErrorGestor


def preparar(equipo, servicio):
    config = equipo.config / "config_equipo.json"
    config.write_text(json.dumps({
        "raiz_destino": str(equipo.destino) + "\\", "horas_recientes": 6, "taxonomia": "es",
        "intocables_detectados": [{"ruta": str(equipo.base / "Ejemplo_Biblioteca"), "motivo": "biblioteca"}],
        "duplicados": {"min_mb": 5, "raices_extra": [str(equipo.base / "Ejemplo_Extra")]},
    }), encoding="utf-8")
    servicio.cfg["ruta_config_equipo"] = str(config)
    local = json.loads((equipo.config / "reglas.local.json").read_text(encoding="utf-8"))
    local.update({"taxonomia": {"Fotos": "Multimedia\\Fotos"}, "patrones": {"cv": "(?i)palabra_privada"}})
    (equipo.config / "reglas.local.json").write_text(json.dumps(local), encoding="utf-8")
    return config


def test_exportar_sin_datos_personales(equipo, servicio):
    preparar(equipo, servicio)
    p = servicio.exportar_perfil()
    texto = json.dumps(p)
    assert p["formato"] == perfiles.FORMATO and p["incluye_personal"] is False
    assert p["config"]["horas_recientes"] == 6 and p["config"]["duplicados"] == {"min_mb": 5}
    assert p["reglas_local"] == {"taxonomia": {"Fotos": "Multimedia\\Fotos"}}
    for privado in ("palabra_privada", "ProyectoPrueba", "AppFicticia", "Ejemplo_Biblioteca", "Ejemplo_Extra", str(equipo.destino)):
        assert privado.replace("\\", "\\\\") not in texto and privado not in texto


def test_exportar_con_datos_personales(equipo, servicio):
    preparar(equipo, servicio)
    p = servicio.exportar_perfil(incluir_personal=True)
    assert p["reglas_local"]["patrones"]["cv"] == "(?i)palabra_privada"
    assert p["reglas_local"]["intocables"]["proyectos_usuario"] == ["ProyectoPrueba"]
    assert p["equipo"]["raiz_destino"] == str(equipo.destino) + "\\"


def test_importar_en_otro_equipo(equipo, servicio):
    config = preparar(equipo, servicio)
    perfil = {"formato": perfiles.FORMATO, "version": 1, "config": {"horas_recientes": 12, "taxonomia": "en"},
              "reglas_local": {"taxonomia": {"Libros": "Biblioteca\\Libros"}},
              "equipo": {"raiz_destino": "Z:\\Ejemplo_otro_equipo\\"}}
    r = servicio.importar_perfil(perfil)
    assert r["equipo_aplicado"] is False
    datos = json.loads(config.read_text(encoding="utf-8"))
    assert datos["horas_recientes"] == 12 and datos["taxonomia"] == "en"
    assert datos["raiz_destino"] == str(equipo.destino) + "\\"                 # los datos de este equipo no se tocan
    assert servicio.cfg["horas_recientes"] == 12
    local = json.loads((equipo.config / "reglas.local.json").read_text(encoding="utf-8"))
    assert local["taxonomia"]["Libros"] == "Biblioteca\\Libros" and local["intocables"]["proyectos_usuario"] == ["ProyectoPrueba"]
    assert len(servicio.respaldos()) >= 1                                       # se respaldó antes de importar


def test_importar_rechaza_archivos_que_no_son_perfiles(equipo, servicio):
    preparar(equipo, servicio)
    with pytest.raises(ErrorGestor, match="no es un perfil"):
        servicio.importar_perfil({"cualquier": "cosa"})
    with pytest.raises(ErrorGestor, match="no válidos"):
        servicio.importar_perfil({"formato": perfiles.FORMATO, "version": 1, "config": {"horas_recientes": -3}})
