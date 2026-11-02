"""Persistencia local en SQLite. Solo metadatos (rutas, tamaños, fechas, hashes): nunca contenido de archivos."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS escaneos (
    id INTEGER PRIMARY KEY,
    modo TEXT NOT NULL,
    inicio TEXT NOT NULL,
    fin TEXT,
    estado TEXT NOT NULL,
    resumen TEXT
);
CREATE TABLE IF NOT EXISTS elementos (
    id INTEGER PRIMARY KEY,
    ruta TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nombre TEXT NOT NULL,
    zona TEXT NOT NULL,
    contexto TEXT NOT NULL,
    tipo TEXT NOT NULL,                -- Archivo | Carpeta | Junction
    bytes INTEGER NOT NULL DEFAULT 0,
    parcial INTEGER NOT NULL DEFAULT 0, -- 1 si el tamaño se cortó por presupuesto de tiempo
    n_archivos INTEGER,
    modificado REAL,
    creado REAL,
    clasificacion TEXT NOT NULL,       -- Migrable | Intocable | Revisar
    motivo TEXT,
    categoria TEXT,
    destino TEXT,
    libera_c INTEGER NOT NULL DEFAULT 0,
    estado TEXT NOT NULL DEFAULT 'pendiente', -- pendiente | aprobado | rechazado | ejecutado | error
    mensaje TEXT,
    en_cola INTEGER NOT NULL DEFAULT 0,
    presente INTEGER NOT NULL DEFAULT 1,
    detectado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    escaneo_id INTEGER,
    movimiento_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_elementos_estado ON elementos(estado, clasificacion);
CREATE TABLE IF NOT EXISTS analisis (
    id INTEGER PRIMARY KEY,
    escaneo_id INTEGER NOT NULL,
    zona TEXT NOT NULL,
    ruta TEXT NOT NULL,
    nombre TEXT NOT NULL,
    bytes INTEGER,
    n_archivos INTEGER,
    parcial INTEGER NOT NULL DEFAULT 0,
    nota TEXT
);
CREATE TABLE IF NOT EXISTS decisiones (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    elemento_id INTEGER,
    ruta TEXT,
    accion TEXT NOT NULL,
    detalle TEXT
);
CREATE TABLE IF NOT EXISTS movimientos (
    id INTEGER PRIMARY KEY,
    archivo TEXT NOT NULL,
    fila INTEGER NOT NULL,
    fecha TEXT,
    tipo TEXT,
    categoria TEXT,
    mb REAL,
    estado TEXT,
    origen TEXT,
    destino TEXT,
    importado_en TEXT NOT NULL,
    UNIQUE(archivo, fila)
);
CREATE INDEX IF NOT EXISTS ix_mov_origen ON movimientos(origen COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS archivos_importados (
    archivo TEXT PRIMARY KEY,
    tamano INTEGER,
    modificado REAL,
    filas INTEGER
);
CREATE TABLE IF NOT EXISTS ejecuciones (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    operacion TEXT NOT NULL,          -- plan | deshacer
    modo TEXT NOT NULL,               -- SIMULACION | EJECUCION
    n_total INTEGER, n_ok INTEGER,
    registro TEXT,
    resultado TEXT
);
CREATE TABLE IF NOT EXISTS hashes (
    ruta TEXT PRIMARY KEY COLLATE NOCASE,
    bytes INTEGER NOT NULL,
    modificado REAL NOT NULL,
    parcial TEXT,
    completo TEXT
);
CREATE TABLE IF NOT EXISTS duplicados (
    id INTEGER PRIMARY KEY,
    grupo INTEGER NOT NULL,
    huella TEXT NOT NULL,
    nivel TEXT NOT NULL,              -- parcial | completo
    bytes INTEGER NOT NULL,
    ruta TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ajustes (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Db:
    """Una conexión nueva por operación: simple y segura entre hilos (WAL + timeout)."""

    def __init__(self, ruta: str | Path):
        self.ruta = str(ruta)
        Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(ESQUEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.ruta, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with self._lock:
                yield c
                c.commit()
        finally:
            c.close()

    def filas(self, sql: str, params=()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def fila(self, sql: str, params=()) -> dict | None:
        r = self.filas(sql, params)
        return r[0] if r else None

    def ejecutar(self, sql: str, params=()) -> int:
        with self.conn() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid

    def ajuste(self, clave: str, defecto=None):
        r = self.fila("SELECT valor FROM ajustes WHERE clave=?", (clave,))
        return json.loads(r["valor"]) if r else defecto

    def guardar_ajuste(self, clave: str, valor) -> None:
        self.ejecutar("INSERT INTO ajustes(clave, valor) VALUES(?, ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                      (clave, json.dumps(valor, ensure_ascii=False)))

    def registrar_decision(self, accion: str, elemento_id=None, ruta=None, detalle=None, c=None) -> None:
        sql = "INSERT INTO decisiones(fecha, elemento_id, ruta, accion, detalle) VALUES(?,?,?,?,?)"
        params = (ahora(), elemento_id, ruta, accion, detalle)
        if c is not None:
            c.execute(sql, params)
        else:
            self.ejecutar(sql, params)
