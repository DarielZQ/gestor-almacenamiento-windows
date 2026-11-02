"""Flujo completo Escanear -> Proponer -> Aprobar -> Validar -> Ejecutar -> Registrar -> Deshacer, con fixtures."""
import os
from pathlib import Path

import pytest

from conftest import escribir, requiere_motor
from gestor.escaner import Candidato
from gestor.servicio import ErrorGestor

pytestmark = [requiere_motor, pytest.mark.motor]


def por_nombre(servicio, nombre):
    filas = [e for e in servicio.elementos(presentes=False, limite=2000)["elementos"] if e["nombre"] == nombre]
    assert filas, f"no se encontró {nombre}"
    return filas[0]


def preparar(equipo):
    escribir(equipo.descargas / "Factura luz.pdf", b"f" * 2000)
    escribir(equipo.descargas / "apuntes.txt", "hola")
    escribir(equipo.descargas / "raro.xyz", "?")
    escribir(equipo.descargas / "base.sqlite", "bd")
    escribir(equipo.descargas / "acceso.lnk", "lnk")
    escribir(equipo.descargas / "recien bajado.pdf", "nuevo", antiguo=False)
    escribir(equipo.escritorio / "ProyectoPrueba" / "a.txt")
    escribir(equipo.escritorio / "Repo" / ".git" / "HEAD")
    escribir(equipo.documentos / "pelicula.mkv", b"v" * (2 * 1024 * 1024))  # archivo grande (umbral 1 MB)
    escribir(equipo.local / "AppGrande" / "datos.bin", b"a" * (2 * 1024 * 1024))
    escribir(equipo.cache / "c.bin", b"c" * 100)
    escribir(equipo.destino / "Curso de ingles" / "u1.pdf")
    escribir(equipo.sistema / "Herramientas" / "h.bin")


def test_escaneo_clasifica(equipo, servicio):
    preparar(equipo)
    resumen = servicio.escanear("completo")
    assert resumen["elementos"] > 0
    assert por_nombre(servicio, "Factura luz.pdf")["clasificacion"] == "Migrable"
    assert por_nombre(servicio, "Factura luz.pdf")["destino"].endswith("Identificacion_y_Finanzas\\Factura luz.pdf")
    assert por_nombre(servicio, "apuntes.txt")["clasificacion"] == "Migrable"
    assert por_nombre(servicio, "raro.xyz")["clasificacion"] == "Revisar"            # triage
    assert por_nombre(servicio, "recien bajado.pdf")["clasificacion"] == "Revisar"   # reciente
    assert por_nombre(servicio, "base.sqlite")["clasificacion"] == "Intocable"
    assert por_nombre(servicio, "acceso.lnk")["clasificacion"] == "Intocable"
    assert por_nombre(servicio, "ProyectoPrueba")["clasificacion"] == "Intocable"
    assert por_nombre(servicio, "Repo")["clasificacion"] == "Intocable"
    grande = por_nombre(servicio, "pelicula.mkv")
    assert grande["zona"] == "grandes" and grande["clasificacion"] == "Revisar"
    # AppData no genera elementos movibles: solo aparece en el análisis de espacio
    assert not [e for e in servicio.elementos(limite=2000)["elementos"] if e["nombre"] == "datos.bin"]
    app = [a for a in servicio.analisis() if a["nombre"] == "AppGrande"][0]
    assert app["zona"] == "appdata" and app["bytes"] == 2 * 1024 * 1024
    cache = por_nombre(servicio, "Cache_Prueba")
    assert cache["tipo"] == "Junction" and cache["clasificacion"] == "Migrable"
    assert por_nombre(servicio, "Curso de ingles")["destino"].endswith("Ingles_Idiomas\\Curso de ingles")
    zonas = {a["zona"] for a in servicio.analisis()}
    assert {"perfil", "appdata", "raiz_sistema"} <= zonas
    # Nada se movió durante el escaneo
    assert (equipo.descargas / "Factura luz.pdf").exists()
    assert all(e["estado"] == "pendiente" for e in servicio.elementos(limite=2000)["elementos"])


def test_aprobar_ejecutar_registrar_deshacer(equipo, servicio):
    preparar(equipo)
    servicio.escanear("rapido")
    factura = por_nombre(servicio, "Factura luz.pdf")
    intocable = por_nombre(servicio, "base.sqlite")

    r = servicio.decidir([factura["id"], intocable["id"]], "aprobar")
    assert r["cambiados"] == [factura["id"]]
    assert r["omitidos"][0]["id"] == intocable["id"]

    # Simular no mueve nada y deja el elemento aprobado
    sim = servicio.ejecutar(simular=True)
    assert sim["correctos"] == 1 and (equipo.descargas / "Factura luz.pdf").exists()
    assert por_nombre(servicio, "Factura luz.pdf")["estado"] == "aprobado"

    real = servicio.ejecutar()
    assert real["correctos"] == 1 and real["registro"]
    destino = factura["destino"]
    assert os.path.exists(destino) and not (equipo.descargas / "Factura luz.pdf").exists()
    e = por_nombre(servicio, "Factura luz.pdf")
    assert e["estado"] == "ejecutado" and e["movimiento_id"]

    h = servicio.historial()
    mov = [m for m in h["movimientos"] if m["destino"] == destino][0]
    assert mov["estado"] == "MOVIDO" and mov["deshacible"]
    assert len(servicio.decisiones()) >= 2

    # Deshacer: simulación primero, luego real
    assert servicio.deshacer(mov["id"], simular=True)["estado"] == "SIMULADO"
    assert os.path.exists(destino)
    d = servicio.deshacer(mov["id"])
    assert d["estado"] == "RESTAURADO"
    assert (equipo.descargas / "Factura luz.pdf").exists() and not os.path.exists(destino)
    assert por_nombre(servicio, "Factura luz.pdf")["estado"] == "pendiente"
    mov2 = [m for m in servicio.historial()["movimientos"] if m["id"] == mov["id"]][0]
    assert mov2["deshecho"] and not mov2["deshacible"]
    with pytest.raises(ErrorGestor):
        servicio.deshacer(mov["id"])


def test_revalidacion_antes_de_ejecutar(equipo, servicio):
    preparar(equipo)
    servicio.escanear("rapido")
    a = por_nombre(servicio, "apuntes.txt")
    f = por_nombre(servicio, "Factura luz.pdf")
    servicio.decidir([a["id"], f["id"]], "aprobar")
    (equipo.descargas / "apuntes.txt").unlink()                    # el origen desaparece
    escribir(Path(f["destino"]), "ocupado")                              # el destino aparece
    r = servicio.ejecutar()
    assert r["correctos"] == 0
    assert por_nombre(servicio, "apuntes.txt")["estado"] == "error"
    assert "ya no existe" in por_nombre(servicio, "apuntes.txt")["mensaje"]
    assert "destino ya existe" in por_nombre(servicio, "Factura luz.pdf")["mensaje"]
    assert (equipo.descargas / "Factura luz.pdf").exists()


def test_cambio_tras_aprobar_vuelve_a_pendiente(equipo, servicio):
    preparar(equipo)
    servicio.escanear("rapido")
    a = por_nombre(servicio, "apuntes.txt")
    servicio.decidir([a["id"]], "aprobar")
    escribir(equipo.descargas / "apuntes.txt", "texto distinto y más largo")
    servicio.escanear("rapido")
    e = por_nombre(servicio, "apuntes.txt")
    assert e["estado"] == "pendiente" and "Cambió" in e["mensaje"]
    assert servicio.ejecutar()["total"] == 0


def test_cola_de_nuevos(equipo, servicio):
    preparar(equipo)
    servicio.escanear("rapido")
    assert servicio.elementos(cola=True)["total"] == 0          # el primer escaneo es la línea base
    escribir(equipo.descargas / "nuevo apunte.md", "# hola")
    escribir(equipo.escritorio / "captura.png", b"png")
    servicio.escanear("rapido")
    cola = {e["nombre"] for e in servicio.elementos(cola=True)["elementos"]}
    assert cola == {"nuevo apunte.md", "captura.png"}
    ids = [e["id"] for e in servicio.elementos(cola=True)["elementos"]]
    assert servicio.marcar_vistos(ids[:1]) == 1
    assert servicio.elementos(cola=True)["total"] == 1
    (equipo.descargas / "nuevo apunte.md").unlink(missing_ok=True)
    servicio.escanear("rapido")
    assert por_nombre(servicio, "nuevo apunte.md")["presente"] == 0


def test_junction_desde_el_gestor(equipo, servicio):
    preparar(equipo)
    servicio.escanear("completo")
    cache = por_nombre(servicio, "Cache_Prueba")
    servicio.decidir([cache["id"]], "aprobar")
    r = servicio.ejecutar()
    assert r["resultados"][0]["estado"] == "ENLAZADO"
    assert (equipo.cache / "c.bin").exists()
    import subprocess
    subprocess.run(["cmd", "/c", "rmdir", str(equipo.cache)], check=True)


def test_politica_libera_c(servicio):
    servicio.cfg["rutas"]["raiz_sistema"] = "C:\\"
    c = Candidato(ruta=r"C:\x\a.pdf", nombre="a.pdf", zona="descargas", contexto="archivo", tipo="Archivo", bytes=10)
    assert servicio._libera_c(c, r"D:\Triage_Por_Clasificar\a.pdf") == 10
    assert servicio._libera_c(c, r"C:\otra\a.pdf") == 0
    c2 = Candidato(ruta=r"D:\Multimedia\x", nombre="x", zona="raiz_destino", contexto="carpeta_raiz_d", tipo="Carpeta", bytes=10)
    assert servicio._libera_c(c2, r"D:\Universidad_Academico\x") == 0


def test_no_hay_operacion_de_borrado():
    """La herramienta no expone ningún borrado de archivos del usuario."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    # respaldo.py es la única excepción: rota sus propias copias de configuración (ver test_respaldo.py)
    codigo = "".join(p.read_text(encoding="utf-8") for p in (raiz / "gestor").glob("*.py") if p.name != "respaldo.py")
    for prohibido in ("os.remove(", "shutil.rmtree(", "os.rmdir(", "send2trash"):
        assert prohibido not in codigo
    web = (raiz / "web" / "app.js").read_text(encoding="utf-8") + (raiz / "web" / "index.html").read_text(encoding="utf-8")
    assert "eliminar" not in web.lower() or "nunca elimina" in web.lower()
