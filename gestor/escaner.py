"""Escaneo en solo lectura de las zonas que importan para liberar C:.

No indexa todo Windows: recorre Descargas, Escritorio, el perfil de usuario, AppData (por carpetas de
primer nivel y cachés conocidas), la raíz de C: (sin entrar en las carpetas del sistema) y la raíz del
disco destino. La clasificación y la protección las decide el motor PowerShell (modo -Analizar).
"""
from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass, field

from .config import expandir

ATRIB_OCULTO = 0x2
ATRIB_SISTEMA = 0x4
ATRIB_REPARSE = 0x400


@dataclass
class Medicion:
    bytes: int = 0
    archivos: int = 0
    parcial: bool = False


@dataclass
class Candidato:
    ruta: str
    nombre: str
    zona: str
    contexto: str
    tipo: str            # Archivo | Carpeta | Junction
    bytes: int = 0
    parcial: bool = False
    n_archivos: int | None = None
    modificado: float | None = None
    creado: float | None = None
    medir: bool = False  # carpeta pendiente de medir
    resultado: dict = field(default_factory=dict)


def atributos(st) -> int:
    return getattr(st, "st_file_attributes", 0)


def medir(ruta: str, limite_s: float, umbral_grande: int | None = None, grandes: list | None = None,
          excluir: set[str] | None = None) -> Medicion:
    """Suma recursiva de tamaños sin seguir junctions ni enlaces. Se corta al agotar el presupuesto."""
    m = Medicion()
    fin = time.monotonic() + limite_s
    pila = [ruta]
    excluir = excluir or set()
    while pila:
        if time.monotonic() > fin:
            m.parcial = True
            break
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
                    if e.path.lower() not in excluir:
                        pila.append(e.path)
                else:
                    m.bytes += st.st_size
                    m.archivos += 1
                    if grandes is not None and umbral_grande and st.st_size >= umbral_grande:
                        grandes.append((e.path, st.st_size, st.st_mtime, st.st_ctime))
    return m


def listar(ruta: str):
    """Entradas de primer nivel con su stat (sin seguir enlaces). Silencioso ante errores de permisos."""
    try:
        with os.scandir(ruta) as it:
            for e in it:
                try:
                    yield e, e.stat(follow_symlinks=False)
                except OSError:
                    continue
    except OSError:
        return


def _bajo_patron(ruta: str, patron: str) -> bool:
    a = ruta.rstrip("\\").lower().split("\\")
    b = patron.rstrip("\\").lower().split("\\")
    return len(a) >= len(b) and all(fnmatch.fnmatchcase(x, y) for x, y in zip(a, b))


class Escaner:
    def __init__(self, cfg: dict, reglas: dict, motor, progreso=None):
        self.cfg = cfg
        self.reglas = reglas
        self.motor = motor
        self.progreso = progreso or (lambda texto: None)
        self.rutas = cfg["rutas"]
        self.umbral = int(cfg.get("umbral_grande_mb", 500)) * 1024 * 1024
        self.presupuesto = float(cfg.get("presupuesto_segundos_carpeta", 20))
        self.presupuesto_zona = float(cfg.get("presupuesto_segundos_zona", 180))
        self.no_medir = [p for p in (expandir(r) for r in reglas.get("intocables", {}).get("rutas", [])) if p]

    # ------------------------------------------------------------------
    def _cand_entrada(self, e, st, zona: str, ctx_archivo: str, ctx_carpeta: str) -> Candidato | None:
        es_dir = e.is_dir(follow_symlinks=False) or bool(atributos(st) & ATRIB_REPARSE and os.path.isdir(e.path))
        if not es_dir and atributos(st) & ATRIB_SISTEMA:
            return None  # desktop.ini y similares (el motor tampoco los toca)
        return Candidato(
            ruta=e.path, nombre=e.name, zona=zona,
            contexto=ctx_carpeta if es_dir else ctx_archivo,
            tipo="Carpeta" if es_dir else "Archivo",
            bytes=0 if es_dir else st.st_size, modificado=st.st_mtime, creado=st.st_ctime,
            medir=es_dir and not (atributos(st) & ATRIB_REPARSE),
        )

    def _zona_usuario(self, zona: str, ruta: str) -> list[Candidato]:
        if not ruta or not os.path.isdir(ruta):
            return []
        self.progreso(f"Listando {zona}")
        return [c for e, st in listar(ruta) if (c := self._cand_entrada(e, st, zona, "archivo", "carpeta_usuario"))]

    def _zona_raiz_destino(self) -> list[Candidato]:
        raiz = self.cfg["raiz_destino"]
        if not os.path.isdir(raiz):
            return []
        self.progreso("Listando la raíz del disco destino")
        return [c for e, st in listar(raiz) if (c := self._cand_entrada(e, st, "raiz_destino", "archivo_raiz_d", "carpeta_raiz_d"))]

    def _zona_caches(self) -> list[Candidato]:
        res = []
        for c in self.reglas.get("caches", {}).get("lista", []):
            ruta = expandir(c["ruta"])
            if not ruta or not os.path.lexists(ruta):
                continue
            st = os.stat(ruta, follow_symlinks=False)
            res.append(Candidato(ruta=ruta, nombre=c["nombre"], zona="caches", contexto="cache", tipo="Junction",
                                 modificado=st.st_mtime, creado=st.st_ctime,
                                 medir=not (atributos(st) & ATRIB_REPARSE)))
        return res

    # ------------------------------------------------------------------
    def ejecutar(self, modo: str, cache_tamanos: dict | None = None) -> tuple[list[Candidato], list[dict], list[str]]:
        """modo 'rapido': Descargas y Escritorio. 'completo': además perfil, AppData, raíz de C:, cachés y destino."""
        cache_tamanos = cache_tamanos or {}
        r = self.rutas
        candidatos: list[Candidato] = []
        analisis: list[dict] = []
        zonas = ["descargas", "escritorio"]
        vistos: set[str] = set()

        def agregar(lista):
            for c in lista:
                k = c.ruta.lower()
                if k not in vistos:
                    vistos.add(k)
                    candidatos.append(c)

        agregar(self._zona_usuario("descargas", r.get("descargas")))
        agregar(self._zona_usuario("escritorio", r.get("escritorio")))
        if modo == "completo":
            zonas += ["raiz_destino", "caches", "grandes"]
            agregar(self._zona_raiz_destino())
            agregar(self._zona_caches())

        # 1) Clasificación y protección: una sola llamada al motor.
        self.progreso(f"Clasificando {len(candidatos)} elementos con el motor")
        resultados = self.motor.analizar([{"id": i, "ruta": c.ruta, "contexto": c.contexto} for i, c in enumerate(candidatos)])
        for i, c in enumerate(candidatos):
            c.resultado = resultados.get(i) or {"codigo": "REVISAR", "motivo": "el motor no devolvió resultado"}

        # 2) Tamaños de carpetas: primero lo que podría moverse; lo protegido con presupuesto corto.
        def prioridad(c):
            return 0 if c.resultado.get("codigo") in ("OK", "REVISAR") else 1
        pendientes = sorted([c for c in candidatos if c.medir and c.resultado.get("codigo") != "NO_EXISTE"], key=prioridad)
        fin_zona = time.monotonic() + self.presupuesto_zona
        for n, c in enumerate(pendientes, 1):
            previo = cache_tamanos.get(c.ruta.lower())
            if modo == "rapido" and previo and previo[0] == c.modificado:
                c.bytes, c.n_archivos, c.parcial = previo[1], previo[2], bool(previo[3])
                continue
            restante = fin_zona - time.monotonic()
            if restante <= 0:
                c.parcial = True
                continue
            limite = min(self.presupuesto if prioridad(c) == 0 else min(5.0, self.presupuesto), restante)
            self.progreso(f"Midiendo carpetas ({n}/{len(pendientes)}): {c.nombre}")
            m = medir(c.ruta, limite)
            c.bytes, c.n_archivos, c.parcial = m.bytes, m.archivos, m.parcial

        # Carpetas propias de la taxonomía del destino: solo como análisis.
        for c in candidatos:
            if c.resultado.get("codigo") == "PROPIA":
                analisis.append({"zona": "raiz_destino", "ruta": c.ruta, "nombre": c.nombre, "bytes": c.bytes,
                                 "n_archivos": c.n_archivos, "parcial": c.parcial, "nota": "Taxonomía del gestor"})

        if modo == "completo":
            grandes: list = []
            analisis += self._analisis_perfil(grandes)
            analisis += self._analisis_appdata()
            analisis += self._analisis_raiz_sistema(grandes)
            nuevos = []
            for ruta, tam, mt, ct in grandes:
                if ruta.lower() in vistos:
                    continue
                vistos.add(ruta.lower())
                nuevos.append(Candidato(ruta=ruta, nombre=os.path.basename(ruta), zona="grandes", contexto="archivo",
                                        tipo="Archivo", bytes=tam, modificado=mt, creado=ct))
            if nuevos:
                self.progreso(f"Clasificando {len(nuevos)} archivos grandes")
                res = self.motor.analizar([{"id": i, "ruta": c.ruta, "contexto": c.contexto} for i, c in enumerate(nuevos)])
                for i, c in enumerate(nuevos):
                    c.resultado = res.get(i) or {"codigo": "REVISAR", "motivo": "el motor no devolvió resultado"}
                candidatos += nuevos
            zonas += ["perfil", "appdata", "raiz_sistema"]
        return candidatos, analisis, zonas

    # ------------------------------------------------------------------
    def _medir_fila(self, zona: str, ruta: str, nombre: str, grandes=None, nota=None, limite=None) -> dict:
        self.progreso(f"Analizando {zona}: {nombre}")
        m = medir(ruta, limite or self.presupuesto, self.umbral, grandes)
        return {"zona": zona, "ruta": ruta, "nombre": nombre, "bytes": m.bytes, "n_archivos": m.archivos,
                "parcial": m.parcial, "nota": nota}

    def _analisis_perfil(self, grandes: list) -> list[dict]:
        r = self.rutas
        perfil = r.get("perfil")
        if not perfil or not os.path.isdir(perfil):
            return []
        aparte = {(r.get(k) or "").lower() for k in ("descargas", "escritorio", "localappdata", "appdata")}
        aparte.add(os.path.join(perfil, "AppData").lower())
        filas = []
        for e, st in listar(perfil):
            if not e.is_dir(follow_symlinks=False) or atributos(st) & ATRIB_REPARSE:
                continue
            if e.path.lower() in aparte:
                continue
            if any(_bajo_patron(e.path, p) for p in self.no_medir):
                filas.append({"zona": "perfil", "ruta": e.path, "nombre": e.name, "bytes": None, "n_archivos": None,
                              "parcial": False, "nota": "Intocable: no se mide"})
                continue
            filas.append(self._medir_fila("perfil", e.path, e.name, grandes))
        return filas

    def _analisis_appdata(self) -> list[dict]:
        r = self.rutas
        notas = {}
        for c in self.reglas.get("analisis", {}).get("carpetas_conocidas", []):
            ruta = expandir(c["ruta"])
            if ruta:
                notas[ruta.lower()] = (ruta, c.get("nota"))
        filas = []
        medidas = set()
        for base in (r.get("localappdata"), r.get("appdata")):
            if not base or not os.path.isdir(base):
                continue
            for e, st in listar(base):
                if not e.is_dir(follow_symlinks=False) or atributos(st) & ATRIB_REPARSE:
                    continue
                nota = notas.get(e.path.lower(), (None, None))[1]
                filas.append(self._medir_fila("appdata", e.path, e.name, nota=nota, limite=min(10.0, self.presupuesto)))
                medidas.add(e.path.lower())
        for clave, (ruta, nota) in notas.items():
            if clave in medidas or not os.path.isdir(ruta):
                continue
            try:
                if atributos(os.stat(ruta, follow_symlinks=False)) & ATRIB_REPARSE:
                    filas.append({"zona": "conocidas", "ruta": ruta, "nombre": os.path.basename(ruta), "bytes": 0,
                                  "n_archivos": 0, "parcial": False, "nota": (nota or "") + " Ya enlazada (junction)."})
                    continue
            except OSError:
                continue
            filas.append(self._medir_fila("conocidas", ruta, os.path.basename(ruta), nota=nota))
        return filas

    def _analisis_raiz_sistema(self, grandes: list) -> list[dict]:
        raiz = self.rutas.get("raiz_sistema")
        if not raiz or not os.path.isdir(raiz):
            return []
        perfiles = os.path.dirname(self.rutas.get("perfil", "")).lower()
        filas = []
        for e, st in listar(raiz):
            es_dir = e.is_dir(follow_symlinks=False)
            if not es_dir:
                filas.append({"zona": "raiz_sistema", "ruta": e.path, "nombre": e.name, "bytes": st.st_size,
                              "n_archivos": 1, "parcial": False,
                              "nota": "Archivo del sistema" if atributos(st) & (ATRIB_SISTEMA | ATRIB_OCULTO) else None})
                continue
            if atributos(st) & ATRIB_REPARSE:
                continue
            if e.path.lower() == perfiles or any(_bajo_patron(e.path, p) for p in self.no_medir):
                filas.append({"zona": "raiz_sistema", "ruta": e.path, "nombre": e.name, "bytes": None, "n_archivos": None,
                              "parcial": False, "nota": "Sistema o perfiles: no se mide (intocable)"})
                continue
            filas.append(self._medir_fila("raiz_sistema", e.path, e.name, grandes))
        return filas
