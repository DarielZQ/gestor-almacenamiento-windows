"""CLI de depuración: python -m gestor.deteccion [--json] [--propuesta]

Sin opciones muestra un resumen legible del perfil. Con --propuesta, la propuesta de configuración.
Con --json, el resultado completo en JSON. No escribe nada en disco.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import detectar_equipo, proponer_configuracion


def _gb(b) -> str:
    return "—" if b is None else f"{b / 1024 ** 3:.1f} GB"


def resumen_perfil(perfil: dict) -> str:
    l = ["Discos:"]
    for d in perfil.get("discos") or []:
        l.append(f"  {d['letra']}:  {d['tipo']:<11} {d['sistema_archivos'] or '?':<6} bus {d['bus']:<10} "
                 f"{_gb(d['libre'])} libres de {_gb(d['total'])}{'  (sistema)' if d['es_sistema'] else ''}")
    c = perfil.get("carpetas") or {}
    l.append("Carpetas del usuario:")
    for k, info in (perfil.get("carpetas_info") or {}).items():
        extra = (" [redirigida]" if info.get("redirigida") else "") + (f" [en {info['en_nube']}]" if info.get("en_nube") else "")
        l.append(f"  {k:<11} {info['ruta']}{extra}")
    if c.get("raiz_sistema"):
        l.append(f"  {'sistema':<11} {c['raiz_sistema']}")
    l.append("Nube:")
    l += [f"  {n['servicio']:<28} {n['ruta']}" for n in perfil.get("nube") or []] or ["  (ninguna)"]
    l.append("Bibliotecas y launchers de juegos:")
    l += [f"  {b['plataforma']:<11} {b['tipo']:<11} {b['ruta']}{'' if b['existe'] else '  (no existe ahora)'}"
          for b in perfil.get("bibliotecas") or []] or ["  (ninguna)"]
    refs = perfil.get("rutas_referenciadas") or []
    cuenta: dict[str, int] = {}
    for r in refs:
        cuenta[r["origen"]] = cuenta.get(r["origen"], 0) + 1
    l.append(f"Rutas referenciadas: {len(refs)} " + (str(cuenta) if cuenta else ""))
    l.append("Cachés existentes:")
    for x in perfil.get("caches") or []:
        if x.get("existe"):
            tam = _gb(x.get("bytes")) + ("+" if x.get("bytes_parcial") else "")
            l.append(f"  {x['nombre']:<20} {tam:>10}  {'reubicable' if x['reubicable_con_junction'] else 'no reubicable'}"
                     f"{'  (ya enlazada)' if x.get('ya_enlazada') else ''}")
    l += [f"  ! {a}" for a in perfil.get("avisos_carpetas") or []]
    l += [f"  ! {a}" for a in perfil.get("advertencias") or []]
    l.append(f"Tiempos (ms): {perfil.get('tiempos_ms')}")
    return "\n".join(l)


def resumen_propuesta(p: dict) -> str:
    l = [f"Origen: {p['origen']}   Destino: {p['raiz_destino']}   Modo: {p['modo']}",
         f"Junctions: {'sí' if p['permitir_junctions'] else 'no'}   Destino USB: {'sí' if p['destino_usb'] else 'no'}",
         f"Intocables detectados: {len(p['intocables_detectados'])}"]
    l += [f"  - {i['ruta']}  ({i['motivo']})" for i in p["intocables_detectados"][:40]]
    if len(p["intocables_detectados"]) > 40:
        l.append(f"  ... y {len(p['intocables_detectados']) - 40} más")
    l.append("Cachés reubicables:")
    l += [f"  [{'x' if c['habilitada'] else ' '}] {c['nombre']}" for c in p["caches"]] or ["  (ninguna)"]
    l += [f"  ! {a}" for a in p["advertencias"]]
    return "\n".join(l)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gestor.deteccion",
                                 description="Detecta el equipo en solo lectura (depuración de la configuración inicial).")
    ap.add_argument("--json", action="store_true", help="imprimir en JSON")
    ap.add_argument("--propuesta", action="store_true", help="mostrar la propuesta de configuración en vez del perfil")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    perfil = detectar_equipo()
    resultado = proponer_configuracion(perfil) if args.propuesta else perfil
    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        print(resumen_propuesta(resultado) if args.propuesta else resumen_perfil(resultado))
    return 0


if __name__ == "__main__":
    sys.exit(main())
