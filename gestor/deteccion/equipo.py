"""detectar_equipo(): ejecuta los detectores con presupuesto de tiempo y arma el perfil del equipo."""
from __future__ import annotations

import ntpath
import os
import platform
import re
import threading
import time
from datetime import datetime

from .contexto import Contexto, dentro, normalizar
from .detectores import (CARPETAS_CONOCIDAS, carpetas_por_defecto, detectar_bibliotecas, detectar_caches,
                         detectar_carpetas, detectar_discos, detectar_nube, detectar_referencias)

VERSION_PERFIL = 1
PRESUPUESTO_TOTAL = 180.0
MARGEN_SEGUNDOS = 2.0

# (sección, detector por defecto, segundos máximos, texto para los avisos). El orden importa: la nube usa los
# discos (unidad virtual de Google Drive), Xbox usa los discos y los accesos directos usan las carpetas.
DETECTORES = (
    ("discos", detectar_discos, 45.0, "los discos"),
    ("nube", detectar_nube, 15.0, "las carpetas de nube"),
    ("carpetas", detectar_carpetas, 10.0, "las carpetas del usuario"),
    ("bibliotecas", detectar_bibliotecas, 30.0, "las bibliotecas de juegos"),
    ("rutas_referenciadas", detectar_referencias, 45.0, "las rutas usadas por programas"),
    ("caches", detectar_caches, 45.0, "las cachés de aplicaciones"),
)
NOMBRES_CARPETAS = {"escritorio": "El Escritorio", "documentos": "Documentos", "descargas": "Descargas",
                    "imagenes": "Imágenes", "videos": "Vídeos", "musica": "Música"}
_VARIABLES_SISTEMA = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")


def _vacio(seccion: str, ctx: Contexto):
    if seccion == "carpetas":
        try:
            return carpetas_por_defecto(ctx)
        except Exception:
            return {}
    return []


def _como_dict(fuentes) -> dict:
    if fuentes is None:
        return {}
    if isinstance(fuentes, dict):
        return dict(fuentes)
    # Admite objetos con atributos (por ejemplo, una dataclass de fuentes).
    return {k: v for k, v in vars(fuentes).items() if not k.startswith("_")}


def analizar_carpetas(carpetas: dict, nube: list[dict]) -> tuple[dict, list[str]]:
    """Para cada carpeta conocida: si está redirigida y si está dentro de una carpeta de nube."""
    perfil = carpetas.get("perfil") or ""
    info, avisos = {}, []
    for clave, (_valor, ingles) in CARPETAS_CONOCIDAS.items():
        ruta = carpetas.get(clave)
        if not ruta:
            continue
        defecto = ntpath.join(perfil, ingles) if perfil else None
        en = next((n for n in nube if dentro(ruta, n.get("ruta"))), None)
        servicio = en.get("servicio") if en else None
        if not servicio and re.search(r"(^|\\)OneDrive( - [^\\]+)?(\\|$)", ruta, re.IGNORECASE):
            servicio = "OneDrive"
        info[clave] = {"ruta": ruta, "redirigida": bool(defecto) and ruta.lower() != defecto.lower(),
                       "en_nube": servicio, "existe": os.path.isdir(ruta)}
        if servicio:
            avisos.append(f"{NOMBRES_CARPETAS[clave]} está dentro de {servicio}: lo que contiene queda intocable "
                          "porque se sincroniza con la nube.")
    return info, avisos


def detectar_equipo(fuentes=None) -> dict:
    """Perfil del equipo, serializable a JSON. Solo lectura y tolerante a errores.

    'fuentes' (opcional) permite simular el equipo. Claves admitidas:
      - una por sección ('discos', 'nube', 'carpetas', 'bibliotecas', 'rutas_referenciadas', 'caches'):
        función(ctx) que sustituye al detector;
      - 'entorno' (dict de variables), 'registro'(raiz, clave, valor), 'subclaves'(raiz, clave),
        'valores'(raiz, clave), 'volumenes'() -> lista de volúmenes, 'carpeta_shell'(valor, defecto),
        'medir'(ruta, segundos) -> (bytes, completo), 'catalogo' (lista de cachés);
      - presupuestos: 'presupuesto_segundos' (total), 'limites' ({sección: segundos}),
        'presupuesto_caches_segundos' y 'presupuesto_cache_segundos'.
    Si un detector falla o se pasa de tiempo, su sección queda vacía y el motivo va a 'advertencias'.
    """
    f = _como_dict(fuentes)
    ctx = Contexto(f)
    inicio = time.monotonic()
    fin_global = inicio + float(f.get("presupuesto_segundos", PRESUPUESTO_TOTAL))
    limites = f.get("limites") if isinstance(f.get("limites"), dict) else {}
    perfil: dict = {"version": VERSION_PERFIL, "generado": datetime.now().isoformat(timespec="seconds")}
    advertencias: list[str] = []
    tiempos: dict[str, int] = {}

    for seccion, por_defecto, limite, texto in DETECTORES:
        detector = f.get(seccion) if callable(f.get(seccion)) else por_defecto
        restante = fin_global - time.monotonic()
        if restante <= 0:
            advertencias.append(f"Se agotó el tiempo total de detección: no se detectaron {texto}.")
            perfil[seccion] = ctx.resultados[seccion] = _vacio(seccion, ctx)
            continue
        plazo = min(float(limites.get(seccion, limite)), restante)
        hijo = ctx.hijo(time.monotonic() + plazo)
        estado: dict = {}

        def correr(detector=detector, hijo=hijo, estado=estado):
            try:
                estado["valor"] = detector(hijo)
            except BaseException as e:  # noqa: BLE001 - cualquier fallo de un detector se anota y se sigue
                estado["error"] = e

        t0 = time.monotonic()
        hilo = threading.Thread(target=correr, name=f"deteccion-{seccion}", daemon=True)
        hilo.start()
        hilo.join(plazo + min(MARGEN_SEGUNDOS, plazo))  # margen para que el detector cierre lo que midió
        tiempos[seccion] = int((time.monotonic() - t0) * 1000)
        vacio = _vacio(seccion, ctx)
        if hilo.is_alive():
            advertencias.append(f"La detección de {texto} superó {plazo:.0f} s y se omitió.")
            valor = vacio
        elif "error" in estado:
            e = estado["error"]
            advertencias.append(f"No se pudieron detectar {texto}: {type(e).__name__}: {e}")
            valor = vacio
        else:
            valor = estado.get("valor")
            advertencias.extend(hijo.advertencias)
            if not isinstance(valor, type(vacio)):
                advertencias.append(f"La detección de {texto} devolvió un resultado no válido; se ignoró.")
                valor = vacio
        perfil[seccion] = ctx.resultados[seccion] = valor

    carpetas = perfil.get("carpetas") or {}
    perfil["carpetas_info"], perfil["avisos_carpetas"] = analizar_carpetas(carpetas, perfil.get("nube") or [])
    perfil["equipo"] = {
        "unidad_sistema": f"{ctx.unidad_sistema()}:",
        "perfil_usuario": carpetas.get("perfil") or normalizar(ctx.var("USERPROFILE")),
        "rutas_sistema": {v: normalizar(ctx.var(v)) for v in _VARIABLES_SISTEMA if ctx.var(v)},
        "sistema_operativo": platform.platform(),
    }
    perfil["resumen"] = {
        "discos": len(perfil.get("discos") or []),
        "nube": len(perfil.get("nube") or []),
        "bibliotecas": len(perfil.get("bibliotecas") or []),
        "rutas_referenciadas": len(perfil.get("rutas_referenciadas") or []),
        "caches_existentes": sum(1 for c in perfil.get("caches") or [] if c.get("existe")),
    }
    perfil["advertencias"] = advertencias
    perfil["tiempos_ms"] = tiempos
    perfil["duracion_ms"] = int((time.monotonic() - inicio) * 1000)
    return perfil
