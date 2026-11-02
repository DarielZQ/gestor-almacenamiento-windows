"""Configuración por capas, esquema y paso de la configuración del equipo al motor con -Config."""
import json

import pytest

from conftest import RAIZ, escribir, requiere_motor
from gestor.config import ErrorConfig, cargar_config, config_para_motor, validar
from gestor.motor import ErrorMotor, Motor
from gestor.servicio import ErrorGestor, Servicio


def test_sin_destino_por_defecto(tmp_path):
    cfg = cargar_config(tmp_path / "no-existe.json")
    assert cfg["raiz_destino"] is None and cfg["configurado"] is False and cfg["dir_registro"] is None


def test_capas_y_validacion(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"raiz_destino": "E:\\Ejemplo_Organizado", "horas_recientes": 5}), encoding="utf-8")
    cfg = cargar_config(ruta, extra={"umbral_grande_mb": 50})
    assert cfg["raiz_destino"] == "E:\\Ejemplo_Organizado\\" and cfg["dir_registro"] == "E:\\Ejemplo_Organizado\\Registro_Migracion_DiscoC"
    assert cfg["horas_recientes"] == 5 and cfg["umbral_grande_mb"] == 50 and cfg["vigilancia_minutos"] == 10
    ruta.write_text(json.dumps({"raiz_destino": "carpeta relativa", "puerto": 70000, "modo": "otro"}), encoding="utf-8")
    with pytest.raises(ErrorConfig) as e:
        cargar_config(ruta)
    assert "ruta absoluta" in str(e.value) and "puerto" in str(e.value) and "modo" in str(e.value)
    ruta.write_text("{ no es json", encoding="utf-8")
    with pytest.raises(ErrorConfig, match="no es JSON válido"):
        cargar_config(ruta)


def test_validar_listas():
    assert validar({"caches": [{"nombre": "x", "ruta": "%TEMP%\\x"}], "intocables_detectados": ["C:\\x", {"ruta": "D:\\Ejemplo_y"}]}) == []
    assert validar({"caches": [{"ruta": "x"}]})
    assert validar({"permitir_junctions": "si"})


def test_config_para_motor_sin_datos_innecesarios(tmp_path):
    cfg = cargar_config(tmp_path / "no.json", extra={"raiz_destino": "E:\\", "intocables_detectados": [{"ruta": "E:\\Ejemplo_Juegos", "motivo": "m"}]})
    m = config_para_motor(cfg)
    assert m["raiz_destino"] == "E:\\" and m["intocables_detectados"][0]["ruta"] == "E:\\Ejemplo_Juegos"
    assert "rutas" not in m and "puerto" not in m


def test_servicio_sin_configurar_no_escanea(equipo):
    cfg = equipo.cfg(raiz_destino=None, dir_registro=None)
    cfg["raiz_destino"] = None
    s = Servicio(cfg, {"caches": {"lista": []}})
    with pytest.raises(ErrorGestor, match="configuración inicial"):
        s.escanear("rapido")
    assert s.estado()["configurado"] is False


@requiere_motor
def test_motor_recibe_todo_por_config(equipo):
    juegos = equipo.base / "OtraBiblioteca"
    escribir(juegos / "juego.bin")
    escribir(equipo.cache / "c.bin")
    archivo = escribir(equipo.descargas / "Manual_Ejemplo.pdf")
    config = equipo.config / "config_equipo.json"
    config.write_text(json.dumps({
        "raiz_destino": str(equipo.destino) + "\\",
        "intocables_detectados": [{"ruta": str(juegos), "motivo": "biblioteca de Steam detectada"}],
        "permitir_junctions": False,
        "caches": [{"nombre": "Cache_Prueba", "ruta": str(equipo.cache), "procesos": [], "habilitada": True}],
    }), encoding="utf-8")
    m = Motor(str(RAIZ / "motor" / "Motor-Almacenamiento.ps1"), None, str(equipo.datos / "tmp"),
              ruta_reglas=str(equipo.config / "reglas.json"), ruta_config=str(config))
    r = m.analizar([{"id": 1, "ruta": str(juegos / "juego.bin"), "contexto": "archivo"},
                    {"id": 2, "ruta": str(archivo), "contexto": "archivo"},
                    {"id": 3, "ruta": str(equipo.cache), "contexto": "cache"}])
    assert r[1]["codigo"] == "INTOCABLE" and "Steam" in r[1]["motivo"]
    assert r[2]["codigo"] == "OK" and r[2]["destino"].startswith(str(equipo.destino))   # destino sacado de -Config
    assert r[3]["codigo"] == "REVISAR" and "junctions desactivadas" in r[3]["motivo"]
    plan = m.ejecutar_plan([{"id": 9, "tipo": "Junction", "origen": str(equipo.cache),
                             "destino": str(equipo.destino / "Cache_Aplicaciones_Enlazadas" / "Cache_Prueba")}],
                           ejecutar=True, horas_recientes=0, dir_registro=str(equipo.registro))
    assert "junctions desactivadas" in plan["resultados"][0]["estado"] and (equipo.cache / "c.bin").exists()


@requiere_motor
def test_motor_sin_destino_da_un_error_claro(equipo, tmp_path):
    vacio = tmp_path / "vacio.json"
    vacio.write_text("{}", encoding="utf-8")
    m = Motor(str(RAIZ / "motor" / "Motor-Almacenamiento.ps1"), None, str(equipo.datos / "tmp"),
              ruta_reglas=str(equipo.config / "reglas.json"), ruta_config=str(vacio))
    with pytest.raises(ErrorMotor, match="No hay destino configurado"):
        m.analizar([{"id": 1, "ruta": str(equipo.descargas), "contexto": "archivo"}])
    with pytest.raises(ErrorMotor, match="configuración inicial"):
        Motor("x", None, str(equipo.datos / "tmp")).analizar([{"id": 1, "ruta": "C:\\x", "contexto": "archivo"}])
