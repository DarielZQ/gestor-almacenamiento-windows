"""Lógica del gestor: Escanear -> Analizar -> Proponer -> Revisar -> Aprobar -> Validar -> Ejecutar -> Registrar.

Reglas de seguridad que aplica esta capa (el motor vuelve a validar todo antes de mover):
- Nada se mueve sin aprobación explícita (estado 'aprobado' puesto por una acción del usuario).
- Un elemento Intocable no se puede aprobar.
- Si un elemento aprobado cambia o pasa a Intocable en un re-escaneo, vuelve a 'pendiente'.
- Justo antes de ejecutar se revalida: el origen existe y no cambió, el destino no existe y está
  dentro de la raíz de destino.
- No existe ninguna operación de borrado.
"""
from __future__ import annotations

import csv
import glob
import io
import json
import os
import shutil
import time
from pathlib import Path

from . import configuracion_inicial, duplicados, perfiles, respaldo, taxonomia
from .config import ErrorConfig, cargar_config, cargar_reglas, config_para_motor, escribir_json, expandir
from .db import Db, ahora
from .escaner import Candidato, Escaner
from .motor import Motor

ESTADOS = ("pendiente", "aprobado", "rechazado", "ejecutado", "error")
ZONAS_COLA = ("descargas", "escritorio")


class ErrorGestor(ValueError):
    pass


def _dentro(ruta: str, base: str) -> bool:
    r, b = ruta.rstrip("\\").lower(), base.rstrip("\\").lower()
    return r == b or r.startswith(b + "\\")


def _unidad(ruta: str) -> str:
    return (ruta or "")[:2].upper()


class Servicio:
    def __init__(self, cfg: dict, reglas: dict, db: Db | None = None, motor: Motor | None = None):
        self.cfg = cfg
        self.reglas = reglas
        self.dir_datos = Path(cfg["dir_datos"])
        self.dir_datos.mkdir(parents=True, exist_ok=True)
        self.db = db or Db(self.dir_datos / "gestor.sqlite")
        self._motor_propio = motor is None
        self.motor = motor or self._crear_motor()
        # Si el servidor se cerró a mitad de un escaneo, ese escaneo no puede seguir "en curso".
        self.db.ejecutar("UPDATE escaneos SET estado='interrumpido', fin=? WHERE estado='en_curso'", (ahora(),))

    def _crear_motor(self) -> Motor:
        """El motor recibe la configuración del equipo con -Config (archivo derivado en datos/tmp)."""
        ruta = self.dir_datos / "tmp" / "config_motor.json"
        escribir_json(ruta, config_para_motor(self.cfg))
        return Motor(self.cfg["ruta_motor"], self.cfg.get("raiz_destino"), str(self.dir_datos / "tmp"),
                     ruta_reglas=self.cfg.get("ruta_reglas"), ruta_config=str(ruta))

    def recargar(self, cfg: dict, reglas: dict | None = None) -> None:
        """Aplica una configuración nueva (por ejemplo, tras la configuración inicial) sin reiniciar."""
        self.cfg = cfg
        self.reglas = reglas or cargar_reglas(cfg["ruta_reglas"], cfg.get("taxonomia"))
        if self._motor_propio:
            self.motor = self._crear_motor()

    def exigir_configuracion(self) -> None:
        if not self.cfg.get("raiz_destino"):
            raise ErrorGestor("Falta la configuración inicial: elige el disco o la carpeta de destino.")

    # ==================================================================
    # Escanear + Analizar + Proponer
    # ==================================================================
    def politica(self, c: Candidato) -> dict | None:
        """Traduce la decisión del motor a Migrable / Intocable / Revisar. None = no es un elemento."""
        r = c.resultado
        codigo = r.get("codigo")
        if codigo in ("NO_EXISTE", "PROPIA"):
            return None
        categoria, destino, motivo = r.get("categoria"), r.get("destino"), r.get("motivo")
        if codigo in ("INTOCABLE", "CONSERVAR", "IGNORAR"):
            return {"clasificacion": "Intocable", "motivo": motivo, "categoria": categoria, "destino": None}
        rutas = self.cfg["rutas"]
        if c.zona == "grandes":
            for base in (rutas.get("localappdata"), rutas.get("appdata")):
                if base and _dentro(c.ruta, base):
                    return {"clasificacion": "Intocable", "motivo": "datos de una aplicación (AppData); usa la reubicación de cachés",
                            "categoria": categoria, "destino": None}
            perfil = rutas.get("perfil") or ""
            if _dentro(c.ruta, perfil):
                primer = c.ruta[len(perfil):].lstrip("\\").split("\\")[0]
                if primer.startswith("."):
                    return {"clasificacion": "Intocable", "motivo": f"configuración o caché de una herramienta ({primer})",
                            "categoria": categoria, "destino": None}
        if codigo == "REVISAR" or not destino:
            return {"clasificacion": "Revisar", "motivo": motivo or "sin destino claro", "categoria": categoria, "destino": destino}
        horas = float(self.cfg.get("horas_recientes", 2))
        if c.tipo == "Archivo" and c.modificado and c.modificado > time.time() - horas * 3600:
            return {"clasificacion": "Revisar", "motivo": f"modificado hace menos de {horas:g} h: puede estar en uso",
                    "categoria": categoria, "destino": destino}
        if c.zona == "grandes":
            return {"clasificacion": "Revisar", "motivo": "archivo grande fuera de Descargas y Escritorio: confirma que puede moverse",
                    "categoria": categoria, "destino": destino}
        if self.cfg.get("modo") == "solo_analisis" and _unidad(c.ruta) == _unidad(destino):
            return {"clasificacion": "Revisar", "motivo": "el destino está en el mismo disco: organiza pero no libera espacio",
                    "categoria": categoria, "destino": destino}
        dudosas = self.reglas.get("analisis", {}).get("categorias_revisar", [])
        if any(_dentro(categoria or "", d) for d in dudosas):
            return {"clasificacion": "Revisar", "motivo": "clasificación dudosa: irá a una carpeta de triage",
                    "categoria": categoria, "destino": destino}
        return {"clasificacion": "Migrable", "motivo": None, "categoria": categoria, "destino": destino}

    def _libera_c(self, c: Candidato, destino: str | None) -> int:
        sistema = _unidad(self.cfg["rutas"].get("raiz_sistema", "C:"))
        if destino and _unidad(c.ruta) == sistema and _unidad(destino) != sistema:
            return int(c.bytes or 0)
        return 0

    def escanear(self, modo: str = "completo", progreso=None) -> dict:
        if modo not in ("rapido", "completo"):
            raise ErrorGestor("modo de escaneo desconocido")
        self.exigir_configuracion()
        progreso = progreso or (lambda t: None)
        t0 = time.monotonic()
        esc_id = self.db.ejecutar("INSERT INTO escaneos(modo, inicio, estado) VALUES(?,?,?)", (modo, ahora(), "en_curso"))
        try:
            cache = {r["ruta"].lower(): (r["modificado"], r["bytes"], r["n_archivos"], r["parcial"])
                     for r in self.db.filas("SELECT ruta, modificado, bytes, n_archivos, parcial FROM elementos WHERE tipo<>'Archivo'")}
            escaner = Escaner(self.cfg, self.reglas, self.motor, progreso)
            candidatos, analisis, zonas = escaner.ejecutar(modo, cache)
            progreso("Guardando resultados")
            resumen = self._guardar_escaneo(esc_id, candidatos, analisis, zonas)
            resumen["segundos"] = round(time.monotonic() - t0, 1)
            resumen["modo"] = modo
            self.db.ejecutar("UPDATE escaneos SET fin=?, estado=?, resumen=? WHERE id=?",
                             (ahora(), "terminado", json.dumps(resumen, ensure_ascii=False), esc_id))
            try:
                resumen["historial_importado"] = self.importar_historial()
            except OSError:
                pass
            return resumen
        except Exception as e:
            self.db.ejecutar("UPDATE escaneos SET fin=?, estado=?, resumen=? WHERE id=?",
                             (ahora(), "error", json.dumps({"error": str(e)}, ensure_ascii=False), esc_id))
            raise

    def _guardar_escaneo(self, esc_id: int, candidatos: list[Candidato], analisis: list[dict], zonas: list[str]) -> dict:
        marca = ahora()
        primeras = {z: not self.db.ajuste(f"zona_vista:{z}", False) for z in zonas}
        nuevos = cambiados = 0
        with self.db.conn() as c:
            existentes = {r["ruta"].lower(): dict(r) for r in c.execute("SELECT * FROM elementos")}
            vistos = set()
            for cand in candidatos:
                p = self.politica(cand)
                if p is None:
                    continue
                k = cand.ruta.lower()
                vistos.add(k)
                libera = self._libera_c(cand, p["destino"]) if p["clasificacion"] != "Intocable" else 0
                ex = existentes.get(k)
                if ex is None:
                    en_cola = 1 if cand.zona in ZONAS_COLA and not primeras.get(cand.zona, True) and p["clasificacion"] != "Intocable" else 0
                    nuevos += en_cola
                    c.execute(
                        "INSERT INTO elementos(ruta, nombre, zona, contexto, tipo, bytes, parcial, n_archivos, modificado, creado, "
                        "clasificacion, motivo, categoria, destino, libera_c, estado, en_cola, presente, detectado_en, actualizado_en, escaneo_id) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pendiente',?,1,?,?,?)",
                        (cand.ruta, cand.nombre, cand.zona, cand.contexto, cand.tipo, cand.bytes, int(cand.parcial), cand.n_archivos,
                         cand.modificado, cand.creado, p["clasificacion"], p["motivo"], p["categoria"], p["destino"], libera,
                         en_cola, marca, marca, esc_id))
                    continue
                cambio = ex["bytes"] != cand.bytes or abs((ex["modificado"] or 0) - (cand.modificado or 0)) > 1 or ex["tipo"] != cand.tipo
                estado, mensaje, destino, en_cola = ex["estado"], ex["mensaje"], p["destino"], ex["en_cola"]
                if estado == "aprobado":
                    if cambio or p["clasificacion"] == "Intocable" or not p["destino"]:
                        estado, mensaje = "pendiente", "Cambió desde la aprobación: revísalo de nuevo"
                        self.db.registrar_decision("auto-pendiente", ex["id"], cand.ruta, mensaje, c=c)
                        cambiados += 1
                    else:
                        destino = ex["destino"]  # se respeta el destino que el usuario aprobó
                elif estado == "ejecutado" and not ex["presente"]:
                    estado, mensaje = "pendiente", "Volvió a aparecer en el origen"
                    if cand.zona in ZONAS_COLA and p["clasificacion"] != "Intocable":
                        en_cola = 1
                elif estado == "error" and cambio:
                    estado, mensaje = "pendiente", None
                c.execute(
                    "UPDATE elementos SET nombre=?, zona=?, contexto=?, tipo=?, bytes=?, parcial=?, n_archivos=?, modificado=?, creado=?, "
                    "clasificacion=?, motivo=?, categoria=?, destino=?, libera_c=?, estado=?, mensaje=?, en_cola=?, presente=1, "
                    "actualizado_en=?, escaneo_id=? WHERE id=?",
                    (cand.nombre, cand.zona, cand.contexto, cand.tipo, cand.bytes, int(cand.parcial), cand.n_archivos, cand.modificado,
                     cand.creado, p["clasificacion"], p["motivo"], p["categoria"], destino, libera, estado, mensaje,
                     en_cola if p["clasificacion"] != "Intocable" else 0, marca, esc_id, ex["id"]))
            desaparecidos = 0
            for k, ex in existentes.items():
                if ex["zona"] in zonas and k not in vistos and ex["presente"]:
                    c.execute("UPDATE elementos SET presente=0, en_cola=0, actualizado_en=? WHERE id=?", (marca, ex["id"]))
                    desaparecidos += 1
            if "perfil" in zonas:  # solo el escaneo completo produce análisis de espacio: se reemplaza entero
                c.execute("DELETE FROM analisis")
            for a in analisis:
                c.execute("INSERT INTO analisis(escaneo_id, zona, ruta, nombre, bytes, n_archivos, parcial, nota) VALUES(?,?,?,?,?,?,?,?)",
                          (esc_id, a["zona"], a["ruta"], a["nombre"], a["bytes"], a["n_archivos"], int(bool(a["parcial"])), a["nota"]))
        for z in zonas:
            self.db.guardar_ajuste(f"zona_vista:{z}", True)
        conteo = {r["clasificacion"]: r["n"] for r in self.db.filas(
            "SELECT clasificacion, COUNT(*) n FROM elementos WHERE presente=1 GROUP BY clasificacion")}
        return {"elementos": len(candidatos), "nuevos_en_cola": nuevos, "aprobados_vueltos_a_pendiente": cambiados,
                "desaparecidos": desaparecidos, "por_clasificacion": conteo, "zonas": zonas}

    # ==================================================================
    # Revisar + Aprobar
    # ==================================================================
    def decidir(self, ids: list[int], accion: str) -> dict:
        if accion not in ("aprobar", "rechazar", "pendiente"):
            raise ErrorGestor("acción desconocida")
        hechos, omitidos = [], []
        with self.db.conn() as c:
            for i in ids:
                r = c.execute("SELECT * FROM elementos WHERE id=?", (int(i),)).fetchone()
                if not r:
                    omitidos.append({"id": i, "motivo": "no existe"})
                    continue
                motivo = None
                if accion == "aprobar":
                    if r["clasificacion"] == "Intocable":
                        motivo = "es Intocable: no se puede aprobar"
                    elif not r["destino"]:
                        motivo = "no tiene destino propuesto"
                    elif not r["presente"]:
                        motivo = "ya no está en el origen"
                    elif r["estado"] not in ("pendiente", "rechazado", "error"):
                        motivo = f"estado actual: {r['estado']}"
                    nuevo = "aprobado"
                elif accion == "rechazar":
                    if r["estado"] not in ("pendiente", "aprobado", "error"):
                        motivo = f"estado actual: {r['estado']}"
                    nuevo = "rechazado"
                else:
                    if r["estado"] not in ("aprobado", "rechazado", "error"):
                        motivo = f"estado actual: {r['estado']}"
                    nuevo = "pendiente"
                if motivo:
                    omitidos.append({"id": r["id"], "motivo": motivo})
                    continue
                c.execute("UPDATE elementos SET estado=?, mensaje=NULL, en_cola=0, actualizado_en=? WHERE id=?", (nuevo, ahora(), r["id"]))
                self.db.registrar_decision(accion, r["id"], r["ruta"], r["destino"], c=c)
                hechos.append(r["id"])
        return {"cambiados": hechos, "omitidos": omitidos}

    def marcar_vistos(self, ids: list[int]) -> int:
        with self.db.conn() as c:
            n = 0
            for i in ids:
                n += c.execute("UPDATE elementos SET en_cola=0 WHERE id=?", (int(i),)).rowcount
        return n

    # ==================================================================
    # Validar + Ejecutar + Registrar
    # ==================================================================
    def validar(self, r: dict) -> str | None:
        """Revalidación justo antes de ejecutar. Devuelve el motivo para no ejecutar, o None."""
        if r["estado"] != "aprobado":
            return "no está aprobado"
        if r["clasificacion"] == "Intocable":
            return "es Intocable"
        destino, origen = r["destino"], r["ruta"]
        if not destino:
            return "no tiene destino"
        if not os.path.lexists(origen):
            return "el origen ya no existe"
        es_dir = os.path.isdir(origen)
        if (r["tipo"] == "Archivo") == es_dir:
            return "el tipo del origen cambió"
        if r["tipo"] == "Archivo":
            st = os.stat(origen)
            if st.st_size != r["bytes"] or abs(st.st_mtime - (r["modificado"] or 0)) > 2:
                return "cambió desde el análisis: vuelve a escanear"
        if os.path.lexists(destino):
            return "el destino ya existe (nunca se sobrescribe)"
        if not _dentro(destino, self.cfg["raiz_destino"]) or destino.rstrip("\\").lower() == self.cfg["raiz_destino"].rstrip("\\").lower():
            return "el destino está fuera de la raíz de destino"
        if _dentro(destino, origen):
            return "el destino está dentro del origen"
        return None

    def ejecutar(self, ids: list[int] | None = None, simular: bool = False, progreso=None) -> dict:
        self.exigir_configuracion()
        progreso = progreso or (lambda t: None)
        filas = self.db.filas("SELECT * FROM elementos WHERE estado='aprobado' ORDER BY id")
        if ids:
            pedidos = {int(i) for i in ids}
            filas = [f for f in filas if f["id"] in pedidos]
        operaciones, resultados = [], []
        for f in filas:
            motivo = self.validar(f)
            if motivo:
                resultados.append({"id": f["id"], "ruta": f["ruta"], "estado": f"NO VALIDADO: {motivo}", "ok": False})
                if not simular:
                    self.db.ejecutar("UPDATE elementos SET estado='error', mensaje=?, actualizado_en=? WHERE id=?",
                                     (f"Validación previa: {motivo}", ahora(), f["id"]))
                    self.db.registrar_decision("validacion-fallida", f["id"], f["ruta"], motivo)
                else:
                    self.db.ejecutar("UPDATE elementos SET mensaje=? WHERE id=?", (f"Simulación: {motivo}", f["id"]))
                continue
            operaciones.append({"id": f["id"], "tipo": f["tipo"], "origen": f["ruta"], "destino": f["destino"],
                                "categoria": f["categoria"] or "Plan aprobado", "bytes": f["bytes"]})
        modo = "SIMULACION" if simular else "EJECUCION"
        registro = None
        if operaciones:
            progreso(f"{'Simulando' if simular else 'Ejecutando'} {len(operaciones)} operaciones con el motor")
            dir_reg = str(self.dir_datos / "simulaciones") if simular else self.cfg["dir_registro"]
            datos = self.motor.ejecutar_plan(operaciones, ejecutar=not simular,
                                             horas_recientes=int(self.cfg.get("horas_recientes", 2)), dir_registro=dir_reg)
            registro = datos.get("registro")
            if registro and not simular:
                self.importar_archivo(registro)
            for r in datos["resultados"]:
                estado = r.get("estado") or ""
                eid = r.get("id")
                resultados.append({"id": eid, "ruta": r.get("origen"), "destino": r.get("destino"), "estado": estado, "ok": bool(r.get("ok"))})
                if simular:
                    msg = "Simulación correcta: listo para ejecutar" if r.get("ok") else f"Simulación: {estado}"
                    self.db.ejecutar("UPDATE elementos SET mensaje=? WHERE id=?", (msg, eid))
                    continue
                if estado in ("MOVIDO", "ENLAZADO"):
                    mov = self.db.fila("SELECT id FROM movimientos WHERE archivo=? AND origen=? COLLATE NOCASE AND destino=? COLLATE NOCASE "
                                       "ORDER BY id DESC LIMIT 1", (registro, r.get("origen"), r.get("destino"))) if registro else None
                    self.db.ejecutar("UPDATE elementos SET estado='ejecutado', mensaje=?, presente=0, en_cola=0, movimiento_id=?, "
                                     "actualizado_en=? WHERE id=?", (estado, mov["id"] if mov else None, ahora(), eid))
                else:
                    self.db.ejecutar("UPDATE elementos SET estado='error', mensaje=?, actualizado_en=? WHERE id=?", (estado, ahora(), eid))
                self.db.registrar_decision("ejecutar", eid, r.get("origen"), estado)
        n_ok = sum(1 for r in resultados if r["ok"])
        self.db.ejecutar("INSERT INTO ejecuciones(fecha, operacion, modo, n_total, n_ok, registro, resultado) VALUES(?,?,?,?,?,?,?)",
                         (ahora(), "plan", modo, len(resultados), n_ok, registro, json.dumps(resultados, ensure_ascii=False)))
        return {"modo": modo, "total": len(resultados), "correctos": n_ok, "registro": registro, "resultados": resultados}

    # ==================================================================
    # Historial (CSV del motor, solo lectura) y deshacer
    # ==================================================================
    def importar_archivo(self, ruta: str) -> int:
        try:
            st = os.stat(ruta)
        except OSError:
            return 0
        previo = self.db.fila("SELECT * FROM archivos_importados WHERE archivo=?", (ruta,))
        if previo and previo["tamano"] == st.st_size and previo["modificado"] == st.st_mtime:
            return 0
        with open(ruta, encoding="utf-8-sig", newline="") as f:  # solo lectura
            texto = f.read()
        if texto and not texto.endswith("\n"):
            texto = texto[: texto.rfind("\n") + 1]  # el motor podría estar escribiendo la última fila
        filas = list(csv.DictReader(io.StringIO(texto)))
        nuevas = 0
        marca = ahora()
        with self.db.conn() as c:
            for n, fila in enumerate(filas, 1):
                if not fila.get("Origen"):
                    continue
                try:
                    mb = float(fila.get("MB") or 0)
                except ValueError:
                    mb = None
                cur = c.execute("INSERT OR IGNORE INTO movimientos(archivo, fila, fecha, tipo, categoria, mb, estado, origen, destino, importado_en) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (ruta, n, fila.get("Fecha"), fila.get("Tipo"), fila.get("Categoria"), mb, fila.get("Estado"),
                                 fila.get("Origen"), fila.get("Destino"), marca))
                nuevas += cur.rowcount
            c.execute("INSERT INTO archivos_importados(archivo, tamano, modificado, filas) VALUES(?,?,?,?) "
                      "ON CONFLICT(archivo) DO UPDATE SET tamano=excluded.tamano, modificado=excluded.modificado, filas=excluded.filas",
                      (ruta, st.st_size, st.st_mtime, len(filas)))
        return nuevas

    def importar_historial(self) -> int:
        d = self.cfg.get("dir_registro")
        if not d or not os.path.isdir(d):
            return 0
        total = 0
        # Todos los registros reales (migracion_*, migracion_plan_*, deshacer_*, escritorio_*...), nunca las simulaciones.
        for ruta in sorted(glob.glob(os.path.join(d, "*.csv"))):
            if not os.path.basename(ruta).lower().startswith("simulacion"):
                total += self.importar_archivo(ruta)
        return total

    SQL_DESHECHO = ("EXISTS (SELECT 1 FROM movimientos d WHERE d.estado='RESTAURADO' AND d.origen=m.destino COLLATE NOCASE "
                    "AND d.destino=m.origen COLLATE NOCASE AND d.fecha>=m.fecha)")

    def historial(self, q: str = "", estado: str = "", limite: int = 200, desplazamiento: int = 0) -> dict:
        where, params = ["1=1"], []
        if q:
            where.append("(m.origen LIKE ? OR m.destino LIKE ? OR m.categoria LIKE ?)")
            params += [f"%{q}%"] * 3
        if estado:
            where.append("m.estado LIKE ?")
            params.append(f"{estado}%")
        w = " AND ".join(where)
        total = self.db.fila(f"SELECT COUNT(*) n FROM movimientos m WHERE {w}", params)["n"]
        filas = self.db.filas(
            f"SELECT m.*, {self.SQL_DESHECHO} AS deshecho FROM movimientos m WHERE {w} ORDER BY m.fecha DESC, m.id DESC LIMIT ? OFFSET ?",
            params + [int(limite), int(desplazamiento)])
        for f in filas:
            f["archivo_nombre"] = os.path.basename(f["archivo"])
            f["deshacible"] = f["estado"] == "MOVIDO" and not f["deshecho"]
        return {"total": total, "movimientos": filas}

    def deshacer(self, movimiento_id: int, simular: bool = False) -> dict:
        m = self.db.fila(f"SELECT m.*, {self.SQL_DESHECHO} AS deshecho FROM movimientos m WHERE m.id=?", (int(movimiento_id),))
        if not m:
            raise ErrorGestor("no existe ese movimiento")
        if m["estado"] != "MOVIDO":
            raise ErrorGestor(f"solo se deshacen movimientos con estado MOVIDO (este: {m['estado']})")
        if m["deshecho"]:
            raise ErrorGestor("ese movimiento ya se deshizo")
        fila = {"Fecha": m["fecha"], "Tipo": m["tipo"], "Categoria": m["categoria"], "MB": m["mb"], "Estado": "MOVIDO",
                "Origen": m["origen"], "Destino": m["destino"]}
        datos = self.motor.deshacer([fila], ejecutar=not simular, dir_registro=None if simular else self.cfg["dir_registro"])
        r0 = datos["resultados"][0] if datos["resultados"] else {"estado": "SIN RESULTADO", "ok": False}
        if not simular:
            if datos.get("registro"):
                self.importar_archivo(datos["registro"])
            if r0.get("estado") == "RESTAURADO":
                self.db.ejecutar("UPDATE elementos SET estado='pendiente', mensaje='Restaurado a su ubicación original (deshecho)', "
                                 "presente=1, en_cola=0, actualizado_en=? WHERE ruta=? COLLATE NOCASE", (ahora(), m["origen"]))
            self.db.registrar_decision("deshacer", None, m["origen"], r0.get("estado"))
        self.db.ejecutar("INSERT INTO ejecuciones(fecha, operacion, modo, n_total, n_ok, registro, resultado) VALUES(?,?,?,?,?,?,?)",
                         (ahora(), "deshacer", "SIMULACION" if simular else "EJECUCION", 1, int(bool(r0.get("ok"))),
                          datos.get("registro"), json.dumps(r0, ensure_ascii=False)))
        return {"movimiento_id": m["id"], "simulacion": simular, **r0}

    # ==================================================================
    # Duplicados (solo informe)
    # ==================================================================
    def raices_duplicados(self) -> list[str]:
        r = self.cfg["rutas"]
        raices = [r.get("descargas"), r.get("escritorio"), r.get("documentos")]
        raiz_d = self.cfg.get("raiz_destino")
        if raiz_d and os.path.isdir(raiz_d):
            primeras = sorted({v.split("\\")[0] for v in self.reglas.get("taxonomia", {}).values() if isinstance(v, str)})
            raices += [os.path.join(raiz_d, p) for p in primeras]
        raices += list(self.cfg.get("duplicados", {}).get("raices_extra", []))
        return [x for x in raices if x and os.path.isdir(x)]

    def buscar_duplicados(self, progreso=None) -> dict:
        d = self.cfg.get("duplicados", {})
        resumen = duplicados.buscar(self.db, self.raices_duplicados(), int(float(d.get("min_mb", 1)) * 1024 * 1024),
                                    bool(d.get("hash_completo", True)), float(d.get("presupuesto_segundos", 300)), progreso)
        resumen["fecha"] = ahora()
        self.db.guardar_ajuste("duplicados_resumen", resumen)
        return resumen

    def listar_duplicados(self) -> dict:
        filas = self.db.filas("SELECT * FROM duplicados ORDER BY grupo, ruta")
        grupos: dict[int, dict] = {}
        for f in filas:
            g = grupos.setdefault(f["grupo"], {"grupo": f["grupo"], "huella": f["huella"][:16], "nivel": f["nivel"], "bytes": f["bytes"], "rutas": []})
            g["rutas"].append(f["ruta"])
        return {"resumen": self.db.ajuste("duplicados_resumen"), "grupos": list(grupos.values())}

    # ==================================================================
    # Consultas para la interfaz
    # ==================================================================
    def elementos(self, clasificacion="", estado="", zona="", q="", cola=False, presentes=True,
                  orden="bytes", limite=300, desplazamiento=0) -> dict:
        where, params = [], []
        if presentes:
            where.append("(presente=1 OR estado IN ('ejecutado','error'))")
        for campo, valor in (("clasificacion", clasificacion), ("estado", estado), ("zona", zona)):
            if valor:
                where.append(f"{campo}=?")
                params.append(valor)
        if cola:
            where.append("en_cola=1")
        if q:
            where.append("(ruta LIKE ? OR destino LIKE ? OR categoria LIKE ?)")
            params += [f"%{q}%"] * 3
        w = " AND ".join(where) or "1=1"
        orden_sql = {"bytes": "bytes DESC", "nombre": "nombre COLLATE NOCASE", "detectado": "detectado_en DESC",
                     "libera": "libera_c DESC"}.get(orden, "bytes DESC")
        total = self.db.fila(f"SELECT COUNT(*) n, COALESCE(SUM(bytes),0) b, COALESCE(SUM(libera_c),0) l FROM elementos WHERE {w}", params)
        filas = self.db.filas(f"SELECT * FROM elementos WHERE {w} ORDER BY {orden_sql} LIMIT ? OFFSET ?",
                              params + [int(limite), int(desplazamiento)])
        return {"total": total["n"], "bytes": total["b"], "libera_c": total["l"], "elementos": filas}

    def estado(self) -> dict:
        discos = []
        for etiqueta, ruta in (("Sistema", self.cfg["rutas"].get("raiz_sistema", "C:\\")), ("Destino", self.cfg.get("raiz_destino"))):
            if not ruta:
                discos.append({"etiqueta": etiqueta, "unidad": "—", "presente": False})
                continue
            try:
                u = shutil.disk_usage(ruta)
                discos.append({"etiqueta": etiqueta, "unidad": _unidad(ruta), "total": u.total, "usado": u.used, "libre": u.free, "presente": True})
            except OSError:
                discos.append({"etiqueta": etiqueta, "unidad": _unidad(ruta), "presente": False})
        conteo = self.db.filas("SELECT clasificacion, estado, COUNT(*) n, COALESCE(SUM(bytes),0) b, COALESCE(SUM(libera_c),0) l "
                               "FROM elementos WHERE presente=1 GROUP BY clasificacion, estado")
        potencial = sum(r["l"] for r in conteo if r["clasificacion"] == "Migrable" and r["estado"] in ("pendiente", "aprobado"))
        revisar = sum(r["l"] for r in conteo if r["clasificacion"] == "Revisar" and r["estado"] in ("pendiente", "aprobado"))
        aprobado = sum(r["l"] for r in conteo if r["estado"] == "aprobado")
        cola = self.db.fila("SELECT COUNT(*) n FROM elementos WHERE en_cola=1 AND presente=1")["n"]
        ultimo = self.db.fila("SELECT * FROM escaneos ORDER BY id DESC LIMIT 1")
        if ultimo and ultimo.get("resumen"):
            ultimo["resumen"] = json.loads(ultimo["resumen"])
        liberado = self.db.fila("SELECT COALESCE(SUM(m.mb),0) mb FROM movimientos m WHERE m.estado IN ('MOVIDO','ENLAZADO') "
                                "AND m.origen LIKE ? AND NOT " + self.SQL_DESHECHO, (_unidad(self.cfg["rutas"].get("raiz_sistema", "C:")) + "\\%",))
        return {"configurado": bool(self.cfg.get("raiz_destino")), "discos": discos, "conteo": conteo, "libera_potencial_migrable": potencial, "libera_potencial_revisar": revisar,
                "libera_aprobado": aprobado, "en_cola": cola, "ultimo_escaneo": ultimo,
                "liberado_historico_bytes": int((liberado["mb"] or 0) * 1024 * 1024)}

    def analisis(self) -> list[dict]:
        return self.db.filas("SELECT zona, ruta, nombre, bytes, n_archivos, parcial, nota FROM analisis ORDER BY zona, COALESCE(bytes,-1) DESC")

    def decisiones(self, limite: int = 200) -> list[dict]:
        return self.db.filas("SELECT * FROM decisiones ORDER BY id DESC LIMIT ?", (int(limite),))

    def ejecuciones(self, limite: int = 50) -> list[dict]:
        filas = self.db.filas("SELECT * FROM ejecuciones ORDER BY id DESC LIMIT ?", (int(limite),))
        for f in filas:
            f["resultado"] = json.loads(f["resultado"]) if f.get("resultado") else None
        return filas

    # ==================================================================
    # Respaldo de la configuración personal
    # ==================================================================
    def respaldar_config(self, motivo: str = "cambio detectado", forzar: bool = False) -> dict | None:
        return respaldo.respaldar(self.cfg, self.db, motivo, forzar)

    def respaldos(self) -> list[dict]:
        return respaldo.listar(self.cfg)

    def restaurar_respaldo(self, nombre: str) -> dict:
        try:
            r = respaldo.restaurar(self.cfg, nombre, self.db)
        except respaldo.ErrorRespaldo as e:
            raise ErrorGestor(str(e)) from e
        self.reglas = cargar_reglas(self.cfg["ruta_reglas"])
        r["aviso"] = "Reglas recargadas. Si cambió config.json, reinicia el gestor para aplicarlo."
        return r

    # ==================================================================
    # Configuración inicial del equipo (detección -> propuesta -> confirmación)
    # ==================================================================
    def detectar_equipo(self, progreso=None, fuentes=None) -> dict:
        motor = self.motor if self.cfg.get("raiz_destino") else None
        det = configuracion_inicial.detectar(fuentes, motor, progreso)
        self.db.guardar_ajuste("ultima_deteccion", det)
        return det

    def equipo(self) -> dict:
        return {"configurado": bool(self.cfg.get("raiz_destino")), "config": self.config_publica(),
                "deteccion": self.db.ajuste("ultima_deteccion")}

    def guardar_equipo(self, elecciones: dict) -> dict:
        det = self.db.ajuste("ultima_deteccion")
        if not det:
            raise ErrorGestor("Primero detecta el equipo.")
        try:
            nueva = configuracion_inicial.construir_config(det, elecciones)
            ruta = configuracion_inicial.guardar(nueva, self.cfg.get("ruta_config_equipo"))
            extra = {k: self.cfg[k] for k in ("dir_datos", "ruta_reglas", "ruta_motor") if self.cfg.get(k)}
            self.recargar(cargar_config(ruta, extra=extra))
        except (ErrorConfig, OSError) as e:
            raise ErrorGestor(str(e)) from e
        self.respaldar_config("configuración del equipo guardada")
        return {"guardado": str(ruta), **self.equipo()}

    # ==================================================================
    # Taxonomía editable (capa personal reglas.local.json)
    # ==================================================================
    def taxonomia(self) -> dict:
        return taxonomia.obtener(self.cfg, self.reglas)

    def taxonomia_vista_previa(self, cambios: dict, progreso=None) -> dict:
        try:
            return taxonomia.vista_previa(self, cambios, progreso)
        except taxonomia.ErrorTaxonomia as e:
            raise ErrorGestor(str(e)) from e

    def taxonomia_guardar(self, cambios: dict) -> dict:
        try:
            taxonomia.guardar(self.cfg, cambios)
        except taxonomia.ErrorTaxonomia as e:
            raise ErrorGestor(str(e)) from e
        self.reglas = cargar_reglas(self.cfg["ruta_reglas"], self.cfg.get("taxonomia"))
        self.respaldar_config("taxonomía editada")
        return {"guardado": True, "aviso": "Vuelve a escanear para aplicar la taxonomía nueva.", **self.taxonomia()}

    # ==================================================================
    # Perfiles exportables
    # ==================================================================
    def exportar_perfil(self, incluir_personal: bool = False) -> dict:
        return perfiles.exportar(self.cfg, incluir_personal)

    def importar_perfil(self, perfil: dict, aplicar_equipo: bool = False) -> dict:
        self.respaldar_config("antes de importar un perfil", forzar=True)
        try:
            r = perfiles.importar(self.cfg, perfil, aplicar_equipo)
            extra = {k: self.cfg[k] for k in ("dir_datos", "ruta_reglas", "ruta_motor", "ruta_config_equipo") if self.cfg.get(k)}
            self.recargar(cargar_config(self.cfg.get("ruta_config_equipo"), extra=extra))
        except (perfiles.ErrorPerfil, ErrorConfig) as e:
            raise ErrorGestor(str(e)) from e
        self.respaldar_config("perfil importado")
        return r

    def config_publica(self) -> dict:
        claves = ("raiz_destino", "dir_registro", "modo", "taxonomia", "permitir_junctions", "horas_recientes", "umbral_grande_mb", "vigilancia_minutos",
                  "presupuesto_segundos_carpeta", "presupuesto_segundos_zona", "duplicados", "rutas", "dir_datos", "ruta_reglas", "ruta_motor")
        return {k: self.cfg.get(k) for k in claves}
