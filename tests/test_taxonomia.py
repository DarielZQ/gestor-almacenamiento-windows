"""Taxonomía editable (capa personal), vista previa con el motor y taxonomía en inglés."""
import json

import pytest

from conftest import RAIZ, escribir, requiere_motor
from gestor import taxonomia as tx
from gestor.config import cargar_reglas
from gestor.motor import Motor
from gestor.servicio import ErrorGestor


def test_obtener_marca_categorias_en_uso(servicio):
    t = servicio.taxonomia()
    claves = {c["clave"]: c for c in t["taxonomia"]}
    assert claves["Notas"]["en_uso"] is True
    assert "documento" in t["extensiones"] and "cursos" in t["patrones"]


def test_validaciones():
    with pytest.raises(tx.ErrorTaxonomia, match="Clave"):
        tx.capa_desde_cambios({"taxonomia": {"mala clave": "X"}})
    with pytest.raises(tx.ErrorTaxonomia, match="Ruta"):
        tx.capa_desde_cambios({"taxonomia": {"Nueva": "C:\\absoluta"}})
    with pytest.raises(tx.ErrorTaxonomia, match="Extensiones"):
        tx.capa_desde_cambios({"extensiones": {"texto": ["txt"]}})
    with pytest.raises(tx.ErrorTaxonomia, match="Palabras clave"):
        tx.capa_desde_cambios({"patrones": {"cv": "(sin cerrar"}})
    with pytest.raises(tx.ErrorTaxonomia, match="Regla propia"):
        tx.capa_desde_cambios({"reglas_usuario": [{"extensiones": [], "categoria": "Notas"}]})


def test_no_se_quita_una_categoria_en_uso(servicio):
    with pytest.raises(ErrorGestor, match="usan las reglas"):
        servicio.taxonomia_guardar({"taxonomia": {"Notas": None}})


def test_guardar_en_capa_personal(equipo, servicio):
    r = servicio.taxonomia_guardar({
        "taxonomia": {"Fotos_Viaje": "Multimedia\\Fotos_de_viaje", "Audio": "Multimedia\\Musica"},
        "extensiones": {"texto": [".txt", ".log", ".nfo"]},
        "reglas_usuario": [{"extensiones": [".jpg"], "palabras": "viaje, vacaciones", "categoria": "Fotos_Viaje"}],
    })
    assert r["guardado"]
    local = json.loads((equipo.config / "reglas.local.json").read_text(encoding="utf-8"))
    assert local["taxonomia"]["Fotos_Viaje"] == "Multimedia\\Fotos_de_viaje"
    assert local["intocables"]["proyectos_usuario"] == ["ProyectoPrueba"]            # lo personal previo se conserva
    assert local["archivos"]["reglas_usuario"][0]["patron"] == "usuario_1"
    base = json.loads((equipo.config / "reglas.json").read_text(encoding="utf-8"))
    assert "Fotos_Viaje" not in base["taxonomia"]                                        # reglas.json no se toca
    assert servicio.reglas["taxonomia"]["Audio"] == "Multimedia\\Musica"


@requiere_motor
def test_vista_previa_y_reglas_propias_en_el_motor(equipo, servicio):
    escribir(equipo.descargas / "Foto_viaje_playa.jpg", b"jpg")
    escribir(equipo.descargas / "Foto_cumple.jpg", b"jpg")
    escribir(equipo.descargas / "notas.nfo", b"nfo")
    servicio.escanear("rapido")
    cambios = {"taxonomia": {"Fotos_Viaje": "Multimedia\\Fotos_de_viaje"},
               "extensiones": {"texto": [".txt", ".log", ".nfo"]},
               "reglas_usuario": [{"extensiones": [".jpg"], "palabras": "viaje", "categoria": "Fotos_Viaje"}]}
    r = servicio.taxonomia_vista_previa(cambios)
    por_nombre = {c["nombre"]: c for c in r["cambios"]}
    assert por_nombre["Foto_viaje_playa.jpg"]["despues"] == "Multimedia\\Fotos_de_viaje"
    assert por_nombre["notas.nfo"]["despues"] == "Notas_Texto_Borradores\\Notas_TXT"
    assert "Foto_cumple.jpg" not in por_nombre                                            # no tiene la palabra
    assert not (equipo.config / "reglas.local.json").read_text(encoding="utf-8").count("Fotos_Viaje")  # la vista previa no guarda
    servicio.taxonomia_guardar(cambios)
    servicio.escanear("rapido")
    e = [x for x in servicio.elementos(limite=100)["elementos"] if x["nombre"] == "Foto_viaje_playa.jpg"][0]
    assert e["categoria"] == "Multimedia\\Fotos_de_viaje"


@requiere_motor
def test_taxonomia_en_ingles(equipo, tmp_path):
    (equipo.config / "taxonomias").mkdir()
    (equipo.config / "taxonomias" / "en.json").write_text((RAIZ / "config" / "taxonomias" / "en.json").read_text(encoding="utf-8"),
                                                          encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"raiz_destino": str(equipo.destino) + "\\", "taxonomia": "en"}), encoding="utf-8")
    archivo = escribir(equipo.descargas / "Factura_Ejemplo.pdf")
    m = Motor(str(RAIZ / "motor" / "Motor-Almacenamiento.ps1"), None, str(tmp_path / "tmp"),
              ruta_reglas=str(equipo.config / "reglas.json"), ruta_config=str(config))
    r = m.analizar([{"id": 1, "ruta": str(archivo), "contexto": "archivo"}])[1]
    assert r["categoria"] == "Personal_Documents\\ID_and_Finance"
    reglas = cargar_reglas(equipo.config / "reglas.json", "en")
    assert reglas["taxonomia"]["Notas"] == "Notes_Text_Drafts\\Text_Notes"
    assert reglas["analisis"]["categorias_revisar"][0] == "Triage_To_Sort"
