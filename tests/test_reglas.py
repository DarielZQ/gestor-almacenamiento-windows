"""Reglas compartidas y archivos de configuración versionados."""
import json
import os
import re
import subprocess

from conftest import RAIZ
from gestor.config import cargar_reglas, fusionar


def test_reglas_versionadas_son_json_valido():
    for nombre in ("reglas.json", "reglas.local.example.json", "config.example.json"):
        json.loads((RAIZ / "config" / nombre).read_text(encoding="utf-8"))


def test_patrones_compilan_y_categorias_existen():
    r = json.loads((RAIZ / "config" / "reglas.json").read_text(encoding="utf-8"))
    for nombre, patron in r["patrones"].items():
        re.compile(patron)
    tax = r["taxonomia"]
    for regla in r["archivos"]["reglas"]:
        assert regla["categoria"] in tax
        for g in regla.get("grupos", []):
            assert g in r["extensiones"], g
        if "patron" in regla:
            assert regla["patron"] in r["patrones"]
    for regla in r["raiz_destino"]["reglas_carpetas"]:
        assert regla["patron"] in r["patrones"]
    cu = r["carpetas_usuario"]
    for k in ("categoria_codigo", "categoria_mensajeria", "categoria_defecto"):
        assert cu[k] in tax


def test_intocables_obligatorios_presentes():
    r = json.loads((RAIZ / "config" / "reglas.json").read_text(encoding="utf-8"))
    rutas = " ".join(r["intocables"]["rutas"])
    for necesario in ("%SystemRoot%", "%ProgramFiles%", "%ProgramData%", "pagefile.sys", "%OneDrive%"):
        assert necesario in rutas, necesario
    # Las carpetas de apps concretas de un equipo van en reglas.local.json -> intocables.rutas_extra
    assert r["intocables"]["rutas_extra"] == []
    assert r["enlaces_app"]["lista"] == []
    for ext in (".mdf", ".ldf", ".sqlite", ".db"):
        assert ext in r["intocables"]["extensiones"]
    # Bibliotecas de juegos y apps: por contenido (marcadores), no por nombres de juegos o launchers concretos
    for marcador in ("steamapps", "libraryfolder.vdf", ".egstore"):
        assert marcador in r["intocables"]["marcadores_biblioteca"]
    for nombre in r["intocables"]["nombres"]:
        assert nombre.lower() not in ("steam", "steamlibrary", "riot games", "battle.net", "epic games")
    assert ".git" in r["intocables"]["marcadores_repo"]


def test_fusion_objetos_y_listas():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": "uno"}
    extra = {"a": {"y": [3]}, "b": "dos", "c": True}
    assert fusionar(base, extra) == {"a": {"x": 1, "y": [3]}, "b": "dos", "c": True}
    assert base["a"]["y"] == [1, 2]  # no muta la base


def test_cargar_reglas_aplica_local(equipo):
    r = cargar_reglas(equipo.config / "reglas.json")
    assert r["intocables"]["proyectos_usuario"] == ["ProyectoPrueba"]
    assert r["intocables"]["nombres"]  # lo demás se conserva


def test_archivos_versionados_sin_datos_del_equipo():
    """Ningún archivo versionado debe contener el usuario real ni rutas reales del equipo."""
    usuario = os.environ.get("USERNAME", "")
    try:
        salida = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True).stdout
        archivos = [RAIZ / x for x in salida.splitlines() if x]
    except (OSError, subprocess.CalledProcessError):
        omitir = {".venv", "datos", ".git", "__pycache__", ".pytest_cache"}
        archivos = [p for p in RAIZ.rglob("*") if p.is_file() and not omitir & set(p.parts)
                    and p.name not in ("reglas.local.json", "config.json") and p.suffix != ".csv"]
    prohibidos = [x for x in (usuario, os.environ.get("USERPROFILE", "")) if len(x) > 3]
    for p in archivos:
        if not p.exists() or p.suffix in (".png", ".ico"):
            continue
        texto = p.read_text(encoding="utf-8", errors="ignore")
        for x in prohibidos:
            assert x.lower() not in texto.lower(), f"{p.name} contiene datos del equipo"
