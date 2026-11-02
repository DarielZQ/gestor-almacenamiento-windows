"""Configuración inicial: detecta el equipo, propone y, cuando el usuario confirma, genera config/config.json.

Uso por consola:  python -m gestor.configuracion_inicial [--si] [--salida RUTA]
  --si      acepta la propuesta sin preguntar (para instalaciones desatendidas)
  --salida  escribe en otra ruta (por defecto config/config.json)
La misma lógica la usa la interfaz web (pestaña Equipo).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from .config import CONFIG_EQUIPO, ErrorConfig, escribir_json, leer_json, validar

try:  # módulo de detección definitivo
    from .deteccion import detectar_equipo as _detectar_equipo, proponer_configuracion as _proponer_configuracion
except ImportError:  # mientras tanto, la versión provisional con la misma interfaz
    from .deteccion_provisional import detectar_equipo as _detectar_equipo, proponer_configuracion as _proponer_configuracion

CLAVES_GENERADAS = ("raiz_destino", "dir_registro", "modo", "taxonomia", "permitir_junctions", "destino_usb",
                    "intocables_detectados", "caches", "discos")


def detectar(fuentes=None, motor=None, progreso=None) -> dict:
    """Detección en solo lectura + propuesta. 'motor' (opcional) cuenta programas, accesos, PATH y servicios."""
    progreso = progreso or (lambda t: None)
    progreso("Detectando discos, carpetas, nube, bibliotecas y cachés")
    perfil = _detectar_equipo(fuentes)
    resultado = {"perfil": perfil, "propuesta": _proponer_configuracion(perfil)}
    if motor is not None:
        progreso("Leyendo programas instalados, accesos directos, PATH y servicios")
        try:
            refs = motor.listar_referencias()
            resultado["referencias"] = dict(Counter(r.get("fuente", "?") for r in refs))
        except Exception as e:  # informativo: el motor vuelve a leerlas en cada análisis
            resultado["referencias"] = {"error": str(e)}
    return resultado


def construir_config(deteccion: dict, elecciones: dict | None = None) -> dict:
    """Combina la propuesta con lo que el usuario eligió. Valida y devuelve el contenido de config.json."""
    e = elecciones or {}
    prop = deteccion["propuesta"]
    perfil = deteccion["perfil"]
    raiz = (e.get("raiz_destino") or prop["raiz_destino"]).strip()
    if raiz and not raiz.endswith("\\"):
        raiz += "\\"
    disco = next((d for d in perfil["discos"] if d["raiz"].lower() == raiz[:3].lower()), None)
    es_sistema = bool(disco and disco["es_sistema"]) or (not disco and raiz[:2].upper() == perfil["carpetas"]["raiz_sistema"][:2].upper())
    externo = bool(disco and disco["externo"])
    ntfs = disco["ntfs"] if disco else True
    permitir = bool(e.get("permitir_junctions", prop["permitir_junctions"] if raiz == prop["raiz_destino"] else (not externo and ntfs)))
    if not ntfs:
        permitir = False
    habilitadas = e.get("caches_habilitadas")
    caches = []
    for c in prop["caches"]:
        activa = c["habilitada"] if habilitadas is None else c["nombre"] in habilitadas
        caches.append({"nombre": c["nombre"], "ruta": c["ruta"], "procesos": c.get("procesos", []),
                       "habilitada": bool(activa and permitir)})
    intocables = list(prop["intocables_detectados"])
    cfg = {
        "raiz_destino": raiz,
        "dir_registro": e.get("dir_registro") or os.path.join(raiz, "Registro_Migracion_DiscoC"),
        "modo": "solo_analisis" if es_sistema else e.get("modo", "completo"),
        "taxonomia": e.get("taxonomia", "es"),
        "permitir_junctions": permitir,
        "destino_usb": externo,
        "intocables_detectados": intocables,
        "caches": caches,
        "discos": [{k: d.get(k) for k in ("letra", "sistema_archivos", "bus", "tipo", "es_sistema")} for d in perfil["discos"]],
    }
    problemas = validar(cfg)
    if problemas:
        raise ErrorConfig(" ".join(problemas))
    return cfg


def guardar(nueva: dict, ruta: Path | str | None = None, crear_destino: bool = True) -> Path:
    """Escribe config.json conservando los ajustes que el usuario haya puesto a mano (puerto, umbrales...)."""
    ruta = Path(ruta or os.environ.get("GESTOR_CONFIG") or CONFIG_EQUIPO)
    previa = {}
    if ruta.exists():
        try:
            previa = leer_json(ruta)
        except ValueError:
            previa = {}
    datos = {k: v for k, v in previa.items() if k not in CLAVES_GENERADAS}
    datos.update(nueva)
    raiz = Path(datos["raiz_destino"])
    if not raiz.exists():
        if not crear_destino or not raiz.parent.exists():
            raise ErrorConfig(f"El destino {raiz} no existe. ¿Está conectado el disco?")
        raiz.mkdir()  # carpeta destino nueva elegida por el usuario (por ejemplo, en modo de un solo disco)
    escribir_json(ruta, datos)
    return ruta


# ---------------------------------------------------------------------- consola
def _gb(b) -> str:
    return "—" if b is None else f"{b / 1024 ** 3:.1f} GB"


def resumen_texto(det: dict) -> str:
    perfil = det["perfil"]
    l = ["Discos:"]
    for d in perfil["discos"]:
        l.append(f"  {d['letra']}:  {d['tipo']:<13} {d['sistema_archivos']:<6} {_gb(d['libre'])} libres de {_gb(d['total'])}"
                 f"{'  (sistema)' if d['es_sistema'] else ''}")
    p = det["propuesta"]
    l.append(f"Destino propuesto: {p['raiz_destino']}  (modo {p['modo']}, junctions {'sí' if p['permitir_junctions'] else 'no'})")
    l += [f"  ! {a}" for a in p["advertencias"]]
    l.append(f"Nube (intocable): {', '.join(n['servicio'] + ' ' + n['ruta'] for n in perfil['nube']) or 'ninguna'}")
    l.append(f"Bibliotecas de juegos (intocables): {', '.join(b['plataforma'] + ' ' + b['ruta'] for b in perfil['bibliotecas']) or 'ninguna'}")
    l += [f"  ! {a}" for a in perfil.get("avisos_carpetas", [])]
    l.append("Cachés encontradas:")
    propuestas = {c["nombre"]: c["habilitada"] for c in p["caches"]}
    for c in perfil["caches"]:
        l.append(f"  [{'x' if propuestas.get(c['nombre']) else ' '}] {c['nombre']:<20} {_gb(c.get('bytes')):>8}  "
                 f"{'segura' if c.get('segura') else 'no recomendada'}{'  (ya enlazada)' if c.get('ya_enlazada') else ''}")
    if det.get("referencias"):
        l.append(f"Rutas protegidas por programas, accesos directos, PATH y servicios: {det['referencias']}")
    return "\n".join(l)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gestor.configuracion_inicial", description="Configuración inicial del gestor")
    ap.add_argument("--si", action="store_true", help="aceptar la propuesta sin preguntar")
    ap.add_argument("--salida", default=None, help="ruta de config.json (por defecto config/config.json)")
    args = ap.parse_args(argv)

    print("Detectando el equipo (solo lectura)...")
    det = detectar()
    print(resumen_texto(det))
    elecciones = {}
    if not args.si:
        r = input(f"\nDestino [{det['propuesta']['raiz_destino']}] (Enter para aceptar, o escribe otra ruta): ").strip()
        if r:
            elecciones["raiz_destino"] = r
        t = input("Taxonomía de carpetas: español o inglés [es/en] (Enter = es): ").strip().lower()
        if t in ("es", "en"):
            elecciones["taxonomia"] = t
        if det["propuesta"]["permitir_junctions"] or elecciones.get("raiz_destino"):
            j = input("¿Permitir reubicar cachés con junction? [s/n] (Enter = propuesta): ").strip().lower()
            if j in ("s", "n"):
                elecciones["permitir_junctions"] = j == "s"
    try:
        cfg = construir_config(det, elecciones)
        if not args.si and input("\n¿Guardar esta configuración? [s/N]: ").strip().lower() != "s":
            print("No se guardó nada.")
            return 1
        ruta = guardar(cfg, args.salida)
    except ErrorConfig as e:
        print(f"No se pudo guardar: {e}")
        return 2
    print(f"Configuración guardada en {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
