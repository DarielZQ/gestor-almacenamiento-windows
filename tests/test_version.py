"""Comprobación de versión: el servidor detecta que su código en disco cambió."""
import http.client
import json
import threading

from gestor.servidor import crear_servidor
from gestor.version import Version, huella_codigo


def test_huella_cambia_con_el_codigo(tmp_path):
    (tmp_path / "gestor").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "gestor" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "web" / "app.js").write_text("let a;\n", encoding="utf-8")
    v = Version(tmp_path)
    assert v.estado()["desactualizado"] is False
    (tmp_path / "datos").mkdir()
    (tmp_path / "datos" / "gestor.sqlite").write_bytes(b"no cuenta")       # los datos no cambian la huella
    assert huella_codigo(tmp_path) == v.hash_arranque
    (tmp_path / "gestor" / "a.py").write_text("x = 2\n", encoding="utf-8")
    e = v.estado()
    assert e["desactualizado"] is True and e["hash"] != e["hash_disco"]


def test_huella_ignora_finales_de_linea(tmp_path):
    (tmp_path / "gestor").mkdir()
    (tmp_path / "gestor" / "a.py").write_bytes(b"x = 1\r\n")
    h1 = huella_codigo(tmp_path)
    (tmp_path / "gestor" / "a.py").write_bytes(b"x = 1\n")
    assert huella_codigo(tmp_path) == h1


def _pedir(app, metodo, ruta, cuerpo=None, token=True):
    c = http.client.HTTPConnection("127.0.0.1", app.puerto, timeout=30)
    h = {"Host": f"127.0.0.1:{app.puerto}"}
    if token:
        h["X-Gestor-Token"] = app.token
    datos = None
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode()
        h["Content-Type"] = "application/json"
    c.request(metodo, ruta, body=datos, headers=h)
    r = c.getresponse()
    texto = r.read().decode("utf-8")
    c.close()
    try:
        return r.status, json.loads(texto)
    except json.JSONDecodeError:
        return r.status, texto


def test_salud_estado_y_reinicio(servicio, tmp_path):
    httpd, app = crear_servidor(servicio, 0, token="t")
    app.version = Version(tmp_path)          # código ficticio para poder "actualizarlo"
    (tmp_path / "gestor").mkdir()
    (tmp_path / "gestor" / "a.py").write_text("1", encoding="utf-8")
    app.version = Version(tmp_path)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    st, salud = _pedir(app, "GET", "/salud", token=False)
    assert st == 200 and salud["hash"] == app.version.hash_arranque and salud["desactualizado"] is False
    st, html = _pedir(app, "GET", "/", token=False)
    assert app.version.hash_arranque in html
    (tmp_path / "gestor" / "a.py").write_text("2", encoding="utf-8")
    st, estado = _pedir(app, "GET", "/api/estado")
    assert estado["version"]["desactualizado"] is True
    assert _pedir(app, "POST", "/api/reiniciar", {}, token=False)[0] == 403
    st, r = _pedir(app, "POST", "/api/reiniciar", {})
    assert st == 200 and r["reiniciando"] is True and app.reinicio_solicitado
    hilo.join(10)
    assert not hilo.is_alive()
    httpd.server_close()
