"""Pruebas del cliente HTTP de la interfaz (servidor caído, errores legibles) con Node, si está disponible."""
import shutil
import subprocess

import pytest

from conftest import RAIZ


@pytest.mark.skipif(shutil.which("node") is None, reason="requiere Node.js")
def test_cliente_http_de_la_interfaz():
    proc = subprocess.run(["node", str(RAIZ / "tests" / "js" / "prueba_conexion.cjs")], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("ok ") == 4


def test_la_interfaz_usa_un_unico_cliente_http():
    app = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")
    assert "fetch(" not in app  # todas las peticiones pasan por web/conexion.js
    html = (RAIZ / "web" / "index.html").read_text(encoding="utf-8")
    assert html.index("conexion.js") < html.index("app.js")
    assert 'id="sin-servidor"' in html
