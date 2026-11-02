"""Servidor HTTP: solo 127.0.0.1, token, Host y Origin; endpoints principales."""
import http.client
import json
import threading

import pytest

from conftest import escribir, requiere_motor
from gestor.servidor import crear_servidor, esperar_tarea


@pytest.fixture
def servidor(servicio):
    httpd, app = crear_servidor(servicio, 0, token="token-de-prueba")
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    yield app
    httpd.shutdown()
    httpd.server_close()


def pedir(app, metodo, ruta, cuerpo=None, token=True, host=None, origen=None, tipo="application/json", sitio=None):
    c = http.client.HTTPConnection("127.0.0.1", app.puerto, timeout=30)
    h = {"Host": host or f"127.0.0.1:{app.puerto}"}
    if token:
        h["X-Gestor-Token"] = app.token
    if origen:
        h["Origin"] = origen
    if sitio:
        h["Sec-Fetch-Site"] = sitio
    datos = None
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode()
        h["Content-Type"] = tipo
    c.request(metodo, ruta, body=datos, headers=h)
    r = c.getresponse()
    texto = r.read().decode("utf-8")
    c.close()
    try:
        return r.status, json.loads(texto)
    except json.JSONDecodeError:
        return r.status, texto


def test_escucha_solo_en_localhost(servidor):
    import socket
    assert servidor.puerto > 0
    with socket.socket() as s:
        s.settimeout(1)
        ip_lan = socket.gethostbyname(socket.gethostname())
        if ip_lan.startswith("127."):
            pytest.skip("sin IP de red para comprobar")
        assert s.connect_ex((ip_lan, servidor.puerto)) != 0


def test_pagina_y_estaticos(servidor):
    st, html = pedir(servidor, "GET", "/", token=False)
    assert st == 200 and "token-de-prueba" in html and "Gestor de almacenamiento" in html
    assert pedir(servidor, "GET", "/web/app.js", token=False)[0] == 200
    assert pedir(servidor, "GET", "/web/../gestor/servidor.py", token=False)[0] == 404
    assert pedir(servidor, "GET", "/web/..%5Cconfig%5Creglas.json", token=False)[0] == 404


def test_salud_identifica_la_app_sin_token(servidor):
    st, datos = pedir(servidor, "GET", "/salud", token=False)
    assert st == 200 and datos["app"] == "gestor-almacenamiento-windows"
    from gestor.__main__ import instancia_activa
    assert instancia_activa(str(servidor.servicio.dir_datos), [servidor.puerto]) == servidor.puerto


def test_protecciones(servidor):
    assert pedir(servidor, "GET", "/api/estado", token=False)[0] == 403
    assert pedir(servidor, "GET", "/api/estado", host="evil.example:80")[0] == 403
    assert pedir(servidor, "GET", "/", token=False, host="evil.example")[0] == 403
    assert pedir(servidor, "POST", "/api/historial/importar", {}, origen="http://evil.example")[0] == 403
    assert pedir(servidor, "POST", "/api/historial/importar", {}, sitio="cross-site")[0] == 403
    assert pedir(servidor, "POST", "/api/historial/importar", {}, tipo="text/plain")[0] == 403
    assert pedir(servidor, "POST", "/api/historial/importar", {}, token=False)[0] == 403
    ok = pedir(servidor, "POST", "/api/historial/importar", {}, origen=f"http://127.0.0.1:{servidor.puerto}", sitio="same-origin")
    assert ok[0] == 200


def test_endpoints_de_lectura(servidor):
    for ruta in ("/api/estado", "/api/elementos", "/api/historial", "/api/decisiones", "/api/ejecuciones",
                 "/api/analisis", "/api/duplicados", "/api/config", "/api/tarea"):
        st, datos = pedir(servidor, "GET", ruta)
        assert st == 200, (ruta, datos)
    assert pedir(servidor, "GET", "/api/no-existe")[0] == 404
    assert pedir(servidor, "POST", "/api/elementos/decidir", {"ids": "x", "accion": "aprobar"})[0] == 400
    assert pedir(servidor, "POST", "/api/elementos/decidir", {"ids": [1], "accion": "borrar"})[0] == 400


@requiere_motor
def test_flujo_por_http(servidor, equipo):
    escribir(equipo.descargas / "manual de usuario.pdf", b"m" * 500)
    st, r = pedir(servidor, "POST", "/api/escanear", {"modo": "rapido"})
    assert st == 200 and r["tarea"]["estado"] == "en_curso"
    assert pedir(servidor, "POST", "/api/escanear", {"modo": "rapido"})[0] in (200, 409)
    t = esperar_tarea(servidor, 300)
    assert t["estado"] == "terminada", t
    _, lista = pedir(servidor, "GET", "/api/elementos?clasificacion=Migrable")
    e = [x for x in lista["elementos"] if x["nombre"] == "manual de usuario.pdf"][0]
    _, d = pedir(servidor, "POST", "/api/elementos/decidir", {"ids": [e["id"]], "accion": "aprobar"})
    assert d["cambiados"] == [e["id"]]
    pedir(servidor, "POST", "/api/ejecutar", {"simular": False})
    t = esperar_tarea(servidor, 300)
    assert t["estado"] == "terminada" and t["resultado"]["correctos"] == 1
    _, h = pedir(servidor, "GET", "/api/historial")
    mov = h["movimientos"][0]
    pedir(servidor, "POST", "/api/deshacer", {"movimiento_id": mov["id"]})
    t = esperar_tarea(servidor, 300)
    assert t["resultado"]["estado"] == "RESTAURADO"
    assert (equipo.descargas / "manual de usuario.pdf").exists()


def test_detener_servidor_por_api(servicio):
    httpd, app = crear_servidor(servicio, 0, token="t2")
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    assert pedir(app, "POST", "/api/detener", {}, token=False)[0] == 403        # sin token no se puede
    st, r = pedir(app, "POST", "/api/detener", {})
    assert st == 200 and r["deteniendo"] is True
    hilo.join(10)
    assert not hilo.is_alive()
    httpd.server_close()


def test_escaneo_en_curso_queda_interrumpido_al_reiniciar(equipo, servicio):
    from gestor.config import cargar_reglas
    from gestor.servicio import Servicio
    servicio.db.ejecutar("INSERT INTO escaneos(modo, inicio, estado) VALUES('completo', '2026-01-01T00:00:00', 'en_curso')")
    cfg = equipo.cfg()
    Servicio(cfg, cargar_reglas(cfg["ruta_reglas"]))
    assert servicio.db.fila("SELECT estado FROM escaneos ORDER BY id DESC LIMIT 1")["estado"] == "interrumpido"
