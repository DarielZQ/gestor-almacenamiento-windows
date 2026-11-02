"""Detección de posibles duplicados: primero por tamaño, luego hash parcial y, si se pide, hash completo.

Solo informa. Nunca elimina ni mueve nada. Los archivos se abren en solo lectura y los hashes se
guardan en caché (ruta, tamaño, fecha) para no releer lo que no cambió.
"""
from __future__ import annotations

import hashlib
import os
import time
from collections import defaultdict

from .escaner import ATRIB_REPARSE, atributos

BLOQUE = 64 * 1024
OMITIR_CARPETAS = {".git", "node_modules", "__pycache__", ".venv", "venv", "$recycle.bin", "system volume information"}


def hash_parcial(ruta: str, tam: int) -> str:
    h = hashlib.sha256(str(tam).encode())
    with open(ruta, "rb") as f:
        h.update(f.read(BLOQUE))
        if tam > 2 * BLOQUE:
            f.seek(-BLOQUE, os.SEEK_END)
            h.update(f.read(BLOQUE))
    return h.hexdigest()


def hash_completo(ruta: str, fin: float | None = None) -> str | None:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        while True:
            if fin and time.monotonic() > fin:
                return None
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def recorrer(raices: list[str], min_bytes: int, fin: float, progreso) -> tuple[list[tuple], bool]:
    archivos, cortado = [], False
    vistos = set()
    for raiz in raices:
        if not raiz or not os.path.isdir(raiz) or raiz.lower() in vistos:
            continue
        vistos.add(raiz.lower())
        progreso(f"Recorriendo {raiz}")
        pila = [raiz]
        while pila:
            if time.monotonic() > fin:
                return archivos, True
            d = pila.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if atributos(st) & ATRIB_REPARSE:
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if e.name.lower() not in OMITIR_CARPETAS and e.path.lower() not in vistos:
                            pila.append(e.path)
                    elif st.st_size >= min_bytes:
                        archivos.append((e.path, st.st_size, st.st_mtime))
    return archivos, cortado


def buscar(db, raices: list[str], min_bytes: int = 1024 * 1024, completo: bool = True,
           presupuesto_s: float = 300, progreso=None) -> dict:
    progreso = progreso or (lambda t: None)
    inicio = time.monotonic()
    fin = inicio + presupuesto_s
    archivos, cortado = recorrer(raices, min_bytes, fin, progreso)

    por_tam = defaultdict(list)
    for a in archivos:
        por_tam[a[1]].append(a)
    candidatos = [g for g in por_tam.values() if len(g) > 1]

    cache = {}
    with db.conn() as c:
        for r in c.execute("SELECT ruta, bytes, modificado, parcial, completo FROM hashes"):
            cache[r["ruta"].lower()] = dict(r)

    nuevos_hashes = {}

    def obtener(ruta, tam, mt, nivel):
        k = ruta.lower()
        previo = cache.get(k)
        if previo and previo["bytes"] == tam and abs(previo["modificado"] - mt) < 1 and previo.get(nivel):
            return previo[nivel]
        try:
            valor = hash_parcial(ruta, tam) if nivel == "parcial" else hash_completo(ruta, fin)
        except OSError:
            return None
        if valor:
            ent = cache.setdefault(k, {"ruta": ruta, "bytes": tam, "modificado": mt, "parcial": None, "completo": None})
            if ent["bytes"] != tam or abs(ent["modificado"] - mt) >= 1:
                ent.update(bytes=tam, modificado=mt, parcial=None, completo=None)
            ent[nivel] = valor
            nuevos_hashes[k] = ent
        return valor

    grupos = []
    total = sum(len(g) for g in candidatos)
    hechos = 0
    for g in candidatos:
        por_parcial = defaultdict(list)
        for ruta, tam, mt in g:
            hechos += 1
            if hechos % 50 == 0:
                progreso(f"Hash parcial {hechos}/{total}")
            if time.monotonic() > fin:
                cortado = True
                break
            h = obtener(ruta, tam, mt, "parcial")
            if h:
                por_parcial[h].append((ruta, tam, mt))
        for hp, miembros in por_parcial.items():
            if len(miembros) < 2:
                continue
            if not completo:
                grupos.append(("parcial", hp, miembros))
                continue
            por_completo = defaultdict(list)
            for ruta, tam, mt in miembros:
                if time.monotonic() > fin:
                    cortado = True
                    break
                progreso(f"Hash completo: {os.path.basename(ruta)}")
                h = obtener(ruta, tam, mt, "completo")
                if h:
                    por_completo[h].append((ruta, tam, mt))
            for hc, iguales in por_completo.items():
                if len(iguales) > 1:
                    grupos.append(("completo", hc, iguales))

    with db.conn() as c:
        for ent in nuevos_hashes.values():
            c.execute("INSERT INTO hashes(ruta, bytes, modificado, parcial, completo) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(ruta) DO UPDATE SET bytes=excluded.bytes, modificado=excluded.modificado, "
                      "parcial=excluded.parcial, completo=excluded.completo",
                      (ent["ruta"], ent["bytes"], ent["modificado"], ent["parcial"], ent["completo"]))
        c.execute("DELETE FROM duplicados")
        for n, (nivel, huella, miembros) in enumerate(sorted(grupos, key=lambda g: -g[2][0][1] * (len(g[2]) - 1)), 1):
            for ruta, tam, _ in miembros:
                c.execute("INSERT INTO duplicados(grupo, huella, nivel, bytes, ruta) VALUES(?,?,?,?,?)", (n, huella, nivel, tam, ruta))

    recuperable = sum(m[0][1] * (len(m) - 1) for _, _, m in grupos)
    resumen = {"archivos_revisados": len(archivos), "grupos": len(grupos), "bytes_repetidos": recuperable,
               "cortado_por_tiempo": cortado, "segundos": round(time.monotonic() - inicio, 1),
               "hash_completo": completo, "raices": [r for r in raices if r]}
    return resumen
