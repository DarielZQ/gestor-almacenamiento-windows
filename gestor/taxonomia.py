"""Taxonomía editable: categorías de destino, grupos de extensiones, palabras clave y reglas propias.

Los cambios se guardan en config/reglas.local.json (capa personal); reglas.json no se toca. Antes de guardar se
puede pedir una vista previa: el motor vuelve a clasificar los archivos conocidos con las reglas propuestas y se
listan los que cambiarían de categoría. La clasificación la hace siempre el motor (no se duplica aquí).
"""
from __future__ import annotations

import copy
import re
import tempfile
from pathlib import Path

from .config import cargar_reglas, escribir_json, fusionar, leer_json

CLAVE_VALIDA = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
RUTA_VALIDA = re.compile(r"^[^\\/:*?\"<>|]+(\\[^\\/:*?\"<>|]+)*$")


class ErrorTaxonomia(ValueError):
    pass


def ruta_local(cfg: dict) -> Path:
    return Path(cfg["ruta_reglas"]).with_name("reglas.local.json")


def obtener(cfg: dict, reglas: dict) -> dict:
    """Vista editable de las reglas efectivas, con qué viene de la capa personal."""
    local = leer_json(ruta_local(cfg)) if ruta_local(cfg).exists() else {}
    tax = {k: v for k, v in reglas.get("taxonomia", {}).items() if not k.startswith("_") and v}
    usadas = categorias_usadas(reglas)
    return {
        "taxonomia": [{"clave": k, "ruta": v, "en_uso": k in usadas, "personal": k in local.get("taxonomia", {})}
                      for k, v in tax.items()],
        "extensiones": {k: v for k, v in reglas.get("extensiones", {}).items() if not k.startswith("_")},
        "patrones": {k: v for k, v in reglas.get("patrones", {}).items() if not k.startswith("_")},
        "reglas_usuario": reglas.get("archivos", {}).get("reglas_usuario", []),
        "idioma": cfg.get("taxonomia", "es"),
    }


def categorias_usadas(reglas: dict) -> set[str]:
    usadas = set()
    a = reglas.get("archivos", {})
    for r in a.get("reglas", []) + a.get("reglas_usuario", []):
        usadas.add(r.get("categoria"))
    for r in reglas.get("raiz_destino", {}).get("reglas_carpetas", []):
        usadas.add(r.get("categoria"))
    usadas.add(reglas.get("raiz_destino", {}).get("categoria_defecto"))
    cu = reglas.get("carpetas_usuario", {})
    usadas |= {cu.get("categoria_codigo"), cu.get("categoria_mensajeria"), cu.get("categoria_defecto")}
    return {u for u in usadas if u}


def capa_desde_cambios(cambios: dict) -> dict:
    """Convierte los cambios de la interfaz en una capa para reglas.local.json (validada)."""
    capa: dict = {}
    errores = []
    tax = cambios.get("taxonomia") or {}
    for clave, ruta in tax.items():
        if not CLAVE_VALIDA.match(str(clave)):
            errores.append(f"Clave de categoría no válida: «{clave}» (letras, números y _).")
        elif ruta is not None and not (isinstance(ruta, str) and RUTA_VALIDA.match(ruta.strip("\\"))):
            errores.append(f"Ruta de destino no válida para {clave}: «{ruta}» (relativa, sin : * ? \" < > |).")
    if tax:
        capa["taxonomia"] = {k: (v.strip("\\") if isinstance(v, str) else None) for k, v in tax.items()}
    ext = cambios.get("extensiones") or {}
    for grupo, lista in ext.items():
        if not isinstance(lista, list) or not all(isinstance(e, str) and re.match(r"^\.[A-Za-z0-9_-]{1,15}$", e) for e in lista):
            errores.append(f"Extensiones no válidas en el grupo {grupo}: usa .ext separadas por comas.")
    if ext:
        capa["extensiones"] = {g: list(dict.fromkeys(e.lower() for e in lista)) for g, lista in ext.items()}
    pat = cambios.get("patrones") or {}
    for nombre, patron in pat.items():
        try:
            re.compile(patron)
        except (re.error, TypeError) as e:
            errores.append(f"Palabras clave «{nombre}» no válidas: {e}")
    if pat:
        capa["patrones"] = dict(pat)
    if "reglas_usuario" in cambios:
        reglas = []
        for i, r in enumerate(cambios.get("reglas_usuario") or []):
            exts = [e.strip().lower() for e in r.get("extensiones", []) if e.strip()]
            if not exts or not all(re.match(r"^\.[a-z0-9_-]{1,15}$", e) for e in exts) or not r.get("categoria"):
                errores.append(f"Regla propia {i + 1}: necesita extensiones (.ext) y una categoría.")
                continue
            regla = {"extensiones": exts, "categoria": r["categoria"]}
            if r.get("palabras"):
                nombre = f"usuario_{i + 1}"
                palabras = [re.escape(p.strip()) for p in str(r["palabras"]).split(",") if p.strip()]
                capa.setdefault("patrones", {})[nombre] = "(?i)" + "|".join(palabras)
                regla["patron"] = nombre
            reglas.append(regla)
        capa["archivos"] = {"reglas_usuario": reglas}
    if errores:
        raise ErrorTaxonomia(" ".join(errores))
    return capa


def reglas_propuestas(reglas: dict, capa: dict) -> dict:
    nuevas = fusionar(reglas, capa)
    tax = {k: v for k, v in nuevas.get("taxonomia", {}).items() if not k.startswith("_") and v}
    quitadas = {k for k, v in capa.get("taxonomia", {}).items() if v is None}
    faltan = sorted(categorias_usadas(nuevas) & quitadas)
    if faltan:
        raise ErrorTaxonomia("No se puede quitar una categoría que usan las reglas: " + ", ".join(faltan) + ".")
    for r in nuevas.get("archivos", {}).get("reglas_usuario", []):
        if r["categoria"] not in tax:
            raise ErrorTaxonomia(f"La regla propia apunta a una categoría que no existe: {r['categoria']}.")
    return nuevas


def guardar(cfg: dict, cambios: dict) -> dict:
    """Valida y escribe la capa en reglas.local.json (fusionándola con lo que ya hubiera)."""
    capa = capa_desde_cambios(cambios)
    reglas_propuestas(cargar_reglas(cfg["ruta_reglas"], cfg.get("taxonomia")), capa)
    ruta = ruta_local(cfg)
    actual = leer_json(ruta) if ruta.exists() else {}
    if "archivos" in capa:  # las reglas propias se reemplazan completas
        actual.setdefault("archivos", {})["reglas_usuario"] = capa.pop("archivos")["reglas_usuario"]
    escribir_json(ruta, fusionar(actual, capa))
    return capa


def vista_previa(servicio, cambios: dict, progreso=None, limite: int = 3000) -> dict:
    """Clasifica con el motor los archivos conocidos usando las reglas propuestas y lista los cambios de categoría."""
    progreso = progreso or (lambda t: None)
    capa = capa_desde_cambios(cambios)
    nuevas = reglas_propuestas(servicio.reglas, capa)
    filas = servicio.db.filas("SELECT id, ruta, nombre, contexto, categoria FROM elementos WHERE presente=1 AND tipo='Archivo' "
                              "AND contexto IN ('archivo','archivo_raiz_d') ORDER BY bytes DESC LIMIT ?", (limite,))
    if not filas:
        return {"cambios": [], "revisados": 0}
    with tempfile.TemporaryDirectory(prefix="vista_previa_") as tmp:
        ruta = Path(tmp) / "reglas.json"   # sin reglas.local.json al lado: 'nuevas' ya incluye todas las capas
        nuevas = copy.deepcopy(nuevas)
        escribir_json(ruta, nuevas)
        progreso(f"Clasificando {len(filas)} archivos con las reglas propuestas")
        res = servicio.motor.analizar([{"id": f["id"], "ruta": f["ruta"], "contexto": f["contexto"]} for f in filas],
                                      ruta_reglas=str(ruta))
    cambios_cat = []
    for f in filas:
        r = res.get(f["id"]) or {}
        if r.get("codigo") == "OK" and r.get("categoria") != f["categoria"]:
            cambios_cat.append({"id": f["id"], "nombre": f["nombre"], "ruta": f["ruta"], "antes": f["categoria"],
                                "despues": r.get("categoria"), "destino": r.get("destino")})
    return {"cambios": cambios_cat, "revisados": len(filas)}
