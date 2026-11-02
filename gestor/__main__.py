"""Punto de entrada: python -m gestor [--puerto 8742] [--no-abrir]

Si ya hay un gestor corriendo (según datos/servidor.json y la ruta /salud), no arranca otro:
solo abre el navegador en la instancia existente.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from .config import cargar_config, cargar_reglas
from .db import ahora
from .servicio import Servicio
from .servidor import APP_ID, crear_servidor


def instancia_activa(dir_datos: str, puertos: list[int]) -> int | None:
    """Devuelve el puerto de un gestor ya en marcha, o None."""
    candidatos = []
    try:
        candidatos.append(int(json.loads((Path(dir_datos) / "servidor.json").read_text(encoding="utf-8"))["puerto"]))
    except (OSError, ValueError, KeyError):
        pass
    candidatos += [p for p in puertos if p not in candidatos]
    for p in candidatos:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/salud", timeout=0.7) as r:
                if json.loads(r.read().decode("utf-8")).get("app") == APP_ID:
                    return p
        except (OSError, ValueError):
            continue
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gestor", description="Gestor de almacenamiento de Windows (interfaz local)")
    ap.add_argument("--puerto", type=int, default=None, help="puerto local (por defecto, el de config)")
    ap.add_argument("--no-abrir", action="store_true", help="no abrir el navegador")
    ap.add_argument("--config", default=None, help="ruta a un config.json alternativo")
    args = ap.parse_args(argv)

    cfg = cargar_config(args.config)
    Path(cfg["dir_datos"]).mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        # Arrancado con pythonw.exe (sin consola): la salida va a datos/servidor.log.
        registro = open(Path(cfg["dir_datos"]) / "servidor.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = registro
    puerto = args.puerto if args.puerto is not None else int(cfg.get("puerto", 8742))
    # Si el puerto está ocupado por otra aplicación, se prueban los siguientes (solo con el puerto por defecto).
    intentos = [puerto] if args.puerto is not None else [puerto + i for i in range(20)]

    existente = instancia_activa(cfg["dir_datos"], intentos)
    if existente:
        url = f"http://127.0.0.1:{existente}/"
        print(f"El gestor ya está abierto en {url}; solo se abre el navegador.")
        if not args.no_abrir:
            webbrowser.open(url)
        return 0

    reglas = cargar_reglas(cfg["ruta_reglas"])
    servicio = Servicio(cfg, reglas)
    try:
        servicio.respaldar_config("al arrancar")
    except OSError as e:
        print(f"Aviso: no se pudo respaldar la configuración ({e})")
    try:
        importadas = servicio.importar_historial()
    except OSError as e:
        importadas = 0
        print(f"Aviso: no se pudo leer el historial ({e})")
    httpd = app = None
    ultimo_error = None
    for p in intentos:
        try:
            httpd, app = crear_servidor(servicio, p)
            break
        except OSError as e:
            ultimo_error = e
    if httpd is None:
        print(f"No se pudo abrir el puerto {puerto} en 127.0.0.1: {ultimo_error}. ¿Otra app usa ese puerto?")
        return 1
    url = f"http://127.0.0.1:{app.puerto}/"
    (Path(cfg["dir_datos"]) / "servidor.json").write_text(
        json.dumps({"puerto": app.puerto, "pid": os.getpid(), "inicio": ahora()}), encoding="utf-8")
    (Path(cfg["dir_datos"]) / "servidor.pid").write_text(str(os.getpid()), encoding="ascii")
    print(f"[{ahora()}] Gestor de almacenamiento en {url}  (para cerrarlo: botón 'Detener servidor' o detener.ps1)")
    print(f"Datos locales: {cfg['dir_datos']}  |  Registro de movimientos: {cfg['dir_registro']}  |  Filas importadas: {importadas}")
    threading.Thread(target=app.vigilancia, name="vigilancia", daemon=True).start()
    if not args.no_abrir:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando el gestor.")
    finally:
        app.parar()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
