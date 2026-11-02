"""Configuración inicial: de un perfil de equipo (simulado) a config.json, y escenarios de portabilidad.

Los perfiles son sintéticos: no dependen del equipo real ni del módulo de detección.
"""
import json

import pytest

from conftest import RAIZ, escribir, requiere_motor
from gestor import configuracion_inicial as ci
from gestor.config import ErrorConfig, cargar_config, cargar_reglas
from gestor.motor import Motor
from gestor.servicio import ErrorGestor, Servicio

GB = 1024 ** 3


def disco(letra, sistema=False, externo=False, fs="NTFS", libre=100):
    return {"letra": letra, "raiz": f"{letra}:\\", "sistema_archivos": fs, "etiqueta": "", "total": 500 * GB,
            "libre": libre * GB, "bus": "USB" if externo else "SATA", "tipo": "USB / externo" if externo else "interno",
            "externo": externo, "es_sistema": sistema, "ntfs": fs == "NTFS", "nube_virtual": False}


def deteccion(discos, raiz, modo="completo", usb=False, junctions=True, nube=(), bibliotecas=(), caches=()):
    perfil = {"discos": discos, "carpetas": {"raiz_sistema": "C:\\"}, "nube": list(nube), "bibliotecas": list(bibliotecas),
              "caches": [{"nombre": n, "ruta": f"%LOCALAPPDATA%\\{n}", "segura": True, "ya_enlazada": False} for n in caches]}
    propuesta = {"raiz_destino": raiz, "modo": modo, "destino_usb": usb, "permitir_junctions": junctions, "advertencias": [],
                 "intocables_detectados": [{"ruta": x["ruta"], "motivo": "nube"} for x in nube] +
                                          [{"ruta": x["ruta"], "motivo": "biblioteca"} for x in bibliotecas],
                 "caches": [{"nombre": n, "ruta": f"%LOCALAPPDATA%\\{n}", "procesos": [], "habilitada": junctions} for n in caches]}
    return {"perfil": perfil, "propuesta": propuesta}


def test_sin_disco_d_destino_en_e():
    det = deteccion([disco("C", sistema=True), disco("E")], "E:\\", caches=["npm_cache"])
    cfg = ci.construir_config(det)
    assert cfg["raiz_destino"] == "E:\\" and cfg["modo"] == "completo" and cfg["permitir_junctions"] is True
    assert cfg["dir_registro"] == "E:\\Registro_Migracion_DiscoC"
    assert cfg["caches"] == [{"nombre": "npm_cache", "ruta": "%LOCALAPPDATA%\\npm_cache", "procesos": [], "habilitada": True}]


def test_destino_usb_desactiva_junctions_por_defecto():
    det = deteccion([disco("C", sistema=True), disco("F", externo=True)], "F:\\", usb=True, junctions=False, caches=["pip_cache"])
    cfg = ci.construir_config(det)
    assert cfg["destino_usb"] is True and cfg["permitir_junctions"] is False
    assert cfg["caches"][0]["habilitada"] is False
    # El usuario puede activarlas a sabiendas, pero nunca en un disco que no sea NTFS
    assert ci.construir_config(det, {"permitir_junctions": True, "caches_habilitadas": ["pip_cache"]})["caches"][0]["habilitada"]
    det2 = deteccion([disco("C", sistema=True), disco("G", externo=True, fs="exFAT")], "G:\\", usb=True, junctions=False)
    assert ci.construir_config(det2, {"permitir_junctions": True})["permitir_junctions"] is False


def test_un_solo_disco_modo_solo_analisis():
    det = deteccion([disco("C", sistema=True)], "C:\\Organizado\\", modo="solo_analisis", junctions=False)
    cfg = ci.construir_config(det)
    assert cfg["modo"] == "solo_analisis" and cfg["raiz_destino"] == "C:\\Organizado\\"
    # Si elige una carpeta en el disco del sistema, también es solo análisis aunque haya otro disco
    det2 = deteccion([disco("C", sistema=True), disco("E")], "E:\\")
    assert ci.construir_config(det2, {"raiz_destino": "C:\\Carpeta"})["modo"] == "solo_analisis"


def test_sin_onedrive_y_con_bibliotecas_detectadas():
    det = deteccion([disco("C", sistema=True), disco("E")], "E:\\", nube=[],
                    bibliotecas=[{"plataforma": "Steam", "ruta": "E:\\Ejemplo_Juegos\\SteamLibrary"}])
    cfg = ci.construir_config(det)
    assert cfg["intocables_detectados"] == [{"ruta": "E:\\Ejemplo_Juegos\\SteamLibrary", "motivo": "biblioteca"}]


def test_destino_invalido():
    det = deteccion([disco("C", sistema=True)], "C:\\Organizado\\")
    with pytest.raises(ErrorConfig):
        ci.construir_config(det, {"raiz_destino": "carpeta relativa"})


def test_guardar_conserva_ajustes_manuales_y_crea_carpeta(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"puerto": 9001, "raiz_destino": "X:\\Ejemplo_viejo\\"}), encoding="utf-8")
    destino = tmp_path / "Destino nuevo"
    ci.guardar({"raiz_destino": str(destino) + "\\", "modo": "completo"}, ruta)
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["puerto"] == 9001 and datos["raiz_destino"] == str(destino) + "\\" and destino.is_dir()
    with pytest.raises(ErrorConfig, match="no existe"):
        ci.guardar({"raiz_destino": str(tmp_path / "no" / "existe") + "\\"}, ruta)


def test_flujo_completo_desde_el_servicio(equipo):
    cfg = equipo.cfg(raiz_destino=None, dir_registro=None, ruta_config_equipo=str(equipo.config / "config_equipo.json"))
    cfg["raiz_destino"] = None
    s = Servicio(cfg, cargar_reglas(cfg["ruta_reglas"]))
    with pytest.raises(ErrorGestor, match="detecta"):
        s.guardar_equipo({})
    det = deteccion([disco("C", sistema=True)], str(equipo.destino) + "\\")
    s.db.guardar_ajuste("ultima_deteccion", det)
    r = s.guardar_equipo({"taxonomia": "en"})
    assert r["configurado"] is True and s.cfg["raiz_destino"] == str(equipo.destino) + "\\"
    assert s.cfg["taxonomia"] == "en"
    assert json.loads((equipo.config / "config_equipo.json").read_text(encoding="utf-8"))["taxonomia"] == "en"


@requiere_motor
def test_usuario_con_espacios_y_tildes(tmp_path):
    """Perfil 'Usuario Ñandú José' y destino en otra carpeta: el motor mueve, registra y deshace."""
    perfil = tmp_path / "Usuario Ñandú José"
    origen = escribir(perfil / "Descargas" / "Informe_Ejemplo año 2026.pdf", b"contenido")
    destino_raiz = tmp_path / "Destino Ñ"
    destino_raiz.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"raiz_destino": str(destino_raiz) + "\\"}, ensure_ascii=False), encoding="utf-8")
    m = Motor(str(RAIZ / "motor" / "Motor-Almacenamiento.ps1"), None, str(tmp_path / "tmp"),
              ruta_reglas=str(RAIZ / "config" / "reglas.json"), ruta_config=str(config))
    a = m.analizar([{"id": 1, "ruta": str(origen), "contexto": "archivo"}])[1]
    assert a["codigo"] == "OK" and a["destino"].startswith(str(destino_raiz))
    res = m.ejecutar_plan([{"id": 1, "tipo": "Archivo", "origen": str(origen), "destino": a["destino"], "bytes": 9}],
                          ejecutar=True, horas_recientes=0, dir_registro=str(tmp_path / "registro"))
    assert res["resultados"][0]["estado"] == "MOVIDO" and not origen.exists()
    import csv
    filas = list(csv.DictReader(open(res["registro"], encoding="utf-8-sig")))
    d = m.deshacer(filas, ejecutar=True, dir_registro=str(tmp_path / "registro"))
    assert d["resultados"][0]["estado"] == "RESTAURADO" and origen.read_bytes() == b"contenido"
