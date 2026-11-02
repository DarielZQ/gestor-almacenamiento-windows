"""Servidor HTTP local (solo 127.0.0.1) con la API JSON y la interfaz estática.

Protecciones:
- Escucha solo en 127.0.0.1.
- Cabecera Host obligatoria 127.0.0.1:<puerto> o localhost:<puerto> (frena el DNS rebinding).
- Toda la API exige la cabecera X-Gestor-Token con el token aleatorio de esta sesión, que solo se
  entrega dentro de la página servida por este mismo servidor.
- Las peticiones POST además exigen Content-Type JSON y, si el navegador envía Origin o
  Sec-Fetch-Site, que sean del mismo origen.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, respaldo
from .version import Version
from .config import DIR_WEB
from .db import ahora
from .servicio import ErrorGestor, Servicio

APP_ID = "gestor-almacenamiento-windows"

TIPOS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Ocupado(RuntimeError):
    pass


class Tareas:
    """Una sola tarea larga a la vez (escaneo, ejecución, deshacer, duplicados)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.actual: dict | None = None
        self.historial: dict[str, dict] = {}

    def ocupada(self) -> bool:
        return bool(self.actual and self.actual["estado"] == "en_curso")

    def lanzar(self, tipo: str, funcion) -> dict:
        with self._lock:
            if self.ocupada():
                raise Ocupado(f"ya hay una tarea en curso: {self.actual['tipo']}")
            tarea = {"id": uuid.uuid4().hex[:12], "tipo": tipo, "estado": "en_curso", "inicio": ahora(), "fin": None,
                     "progreso": "Iniciando", "resultado": None, "error": None}
            self.actual = tarea
            self.historial[tarea["id"]] = tarea
            while len(self.historial) > 50:
                self.historial.pop(next(iter(self.historial)))

        def progreso(texto: str):
            tarea["progreso"] = texto

        def correr():
            try:
                tarea["resultado"] = funcion(progreso)
                tarea["estado"] = "terminada"
            except Exception as e:  # se informa en la interfaz
                tarea["estado"] = "error"
                tarea["error"] = f"{e}"
                tarea["detalle"] = traceback.format_exc()[-4000:]
            finally:
                tarea["fin"] = ahora()

        threading.Thread(target=correr, name=f"tarea-{tipo}", daemon=True).start()
        return tarea


class Aplicacion:
    def __init__(self, servicio: Servicio, puerto: int, token: str | None = None):
        self.servicio = servicio
        self.puerto = puerto
        self.token = token or secrets.token_urlsafe(32)
        self.tareas = Tareas()
        self._parar = threading.Event()
        self.ultima_vigilancia = None
        self.httpd = None
        self.version = Version()
        self.reinicio_solicitado = False

    @property
    def hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.puerto}", f"localhost:{self.puerto}"}

    def vigilancia(self):
        """Re-escaneo rápido periódico de Descargas y Escritorio mientras la app esté abierta."""
        minutos = float(self.servicio.cfg.get("vigilancia_minutos") or 0)
        if minutos <= 0:
            return
        while not self._parar.wait(minutos * 60):
            if not self.tareas.ocupada():
                try:
                    self.tareas.lanzar("vigilancia", lambda p: self.servicio.escanear("rapido", p))
                    self.ultima_vigilancia = ahora()
                except Ocupado:
                    pass

    def parar(self):
        self._parar.set()

    def solicitar_parada(self) -> dict:
        """Detiene el servidor desde la interfaz (POST protegido con token y Origin)."""
        self.parar()
        if self.httpd is not None:
            threading.Thread(target=self.httpd.shutdown, name="parada", daemon=True).start()
        return {"deteniendo": True, "tarea_interrumpida": self.tareas.ocupada()}

    def solicitar_reinicio(self) -> dict:
        """Detiene el servidor y pide a __main__ que arranque uno nuevo con el código actual del disco."""
        self.reinicio_solicitado = True
        r = self.solicitar_parada()
        r["reiniciando"] = True
        return r


def crear_manejador(app: Aplicacion):
    s = app.servicio

    def cuerpo_json(h) -> dict:
        n = int(h.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ErrorGestor("petición demasiado grande")
        datos = h.rfile.read(n) if n else b"{}"
        valor = json.loads(datos.decode("utf-8") or "{}")
        if not isinstance(valor, dict):
            raise ErrorGestor("se esperaba un objeto JSON")
        return valor

    def lanzar(tipo, funcion):
        return {"tarea": app.tareas.lanzar(tipo, funcion)}

    def ids_de(d) -> list[int]:
        ids = d.get("ids") or []
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise ErrorGestor("'ids' debe ser una lista de enteros")
        return ids

    def q1(q, k, defecto=""):
        return (q.get(k) or [defecto])[0]

    GET = {
        "/api/estado": lambda q: {**s.estado(), "tarea": app.tareas.actual, "vigilancia_minutos": s.cfg.get("vigilancia_minutos"),
                                   "ultima_vigilancia": app.ultima_vigilancia, "version": app.version.estado()},
        "/api/elementos": lambda q: s.elementos(q1(q, "clasificacion"), q1(q, "estado"), q1(q, "zona"), q1(q, "q"),
                                                q1(q, "cola") == "1", q1(q, "presentes", "1") == "1", q1(q, "orden", "bytes"),
                                                min(int(q1(q, "limite", "300")), 2000), int(q1(q, "desplazamiento", "0"))),
        "/api/historial": lambda q: s.historial(q1(q, "q"), q1(q, "estado"), min(int(q1(q, "limite", "200")), 2000),
                                                int(q1(q, "desplazamiento", "0"))),
        "/api/decisiones": lambda q: {"decisiones": s.decisiones(int(q1(q, "limite", "200")))},
        "/api/ejecuciones": lambda q: {"ejecuciones": s.ejecuciones()},
        "/api/analisis": lambda q: {"analisis": s.analisis()},
        "/api/duplicados": lambda q: s.listar_duplicados(),
        "/api/config": lambda q: s.config_publica(),
        "/api/tarea": lambda q: {"tarea": app.tareas.actual},
        "/api/respaldos": lambda q: {"respaldos": s.respaldos(), "carpeta": str(respaldo.dir_respaldos(s.cfg) or "")},
    }
    POST = {
        "/api/escanear": lambda d: lanzar("escaneo", lambda p: s.escanear(d.get("modo", "completo"), p)),
        "/api/elementos/decidir": lambda d: s.decidir(ids_de(d), str(d.get("accion"))),
        "/api/cola/vistos": lambda d: {"marcados": s.marcar_vistos(ids_de(d))},
        "/api/ejecutar": lambda d: lanzar("simulacion" if d.get("simular") else "ejecucion",
                                          lambda p: s.ejecutar(ids_de(d) or None, bool(d.get("simular")), p)),
        "/api/deshacer": lambda d: lanzar("deshacer", lambda p: s.deshacer(int(d["movimiento_id"]), bool(d.get("simular")))),
        "/api/historial/importar": lambda d: {"importadas": s.importar_historial()},
        "/api/duplicados/buscar": lambda d: lanzar("duplicados", lambda p: s.buscar_duplicados(p)),
        "/api/detener": lambda d: app.solicitar_parada(),
        "/api/reiniciar": lambda d: app.solicitar_reinicio(),
        "/api/respaldos/crear": lambda d: {"respaldo": s.respaldar_config("manual", forzar=True)},
        "/api/respaldos/restaurar": lambda d: s.restaurar_respaldo(str(d.get("nombre") or "")),
    }

    class Manejador(BaseHTTPRequestHandler):
        server_version = "GestorAlmacenamiento"
        sys_version = ""

        def log_message(self, formato, *args):  # silencioso salvo errores
            pass

        def _enviar(self, codigo: int, cuerpo: bytes, tipo: str):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, codigo: int, datos):
            self._enviar(codigo, json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in app.hosts

        def _api_ok(self, es_post: bool) -> str | None:
            if not hmac.compare_digest(self.headers.get("X-Gestor-Token", ""), app.token):
                return "token ausente o incorrecto"
            if es_post:
                if not (self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"):
                    return "se requiere Content-Type: application/json"
                origen = self.headers.get("Origin")
                if origen is not None and origen not in {f"http://{h}" for h in app.hosts}:
                    return "origen no permitido"
                sitio = self.headers.get("Sec-Fetch-Site")
                if sitio is not None and sitio not in ("same-origin", "none"):
                    return "petición de otro sitio"
            return None

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "host no permitido"})
            url = urlparse(self.path)
            if url.path == "/salud":  # sin token: identifica la app y su versión (lo usa iniciar.ps1)
                return self._json(200, {"app": APP_ID, **app.version.estado()})
            if url.path in ("/", "/index.html"):
                html = ((DIR_WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                        .replace("__HASH__", app.version.hash_arranque))
                return self._enviar(200, html.encode("utf-8"), TIPOS[".html"])
            if url.path.startswith("/web/"):
                nombre = url.path[len("/web/"):]
                permitidos = {p.name: p for p in DIR_WEB.iterdir() if p.is_file()}
                if nombre in permitidos and nombre != "index.html":
                    p = permitidos[nombre]
                    return self._enviar(200, p.read_bytes(), TIPOS.get(p.suffix, "application/octet-stream"))
                return self._json(404, {"error": "no encontrado"})
            if url.path.startswith("/api/"):
                problema = self._api_ok(False)
                if problema:
                    return self._json(403, {"error": problema})
                if url.path.startswith("/api/tareas/"):
                    t = app.tareas.historial.get(url.path.rsplit("/", 1)[-1])
                    return self._json(200 if t else 404, {"tarea": t} if t else {"error": "tarea desconocida"})
                f = GET.get(url.path)
                if not f:
                    return self._json(404, {"error": "ruta desconocida"})
                return self._responder(lambda: f(parse_qs(url.query)))
            return self._json(404, {"error": "no encontrado"})

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": "host no permitido"})
            url = urlparse(self.path)
            problema = self._api_ok(True)
            if problema:
                return self._json(403, {"error": problema})
            f = POST.get(url.path)
            if not f:
                return self._json(404, {"error": "ruta desconocida"})
            return self._responder(lambda: f(cuerpo_json(self)))

        def _responder(self, funcion):
            try:
                return self._json(200, funcion())
            except Ocupado as e:
                return self._json(409, {"error": str(e)})
            except (ErrorGestor, ValueError, KeyError) as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": f"error interno: {e}"})

    return Manejador


def crear_servidor(servicio: Servicio, puerto: int, token: str | None = None) -> tuple[ThreadingHTTPServer, Aplicacion]:
    app = Aplicacion(servicio, puerto, token)
    httpd = ThreadingHTTPServer(("127.0.0.1", puerto), crear_manejador(app))
    app.puerto = httpd.server_address[1]
    app.httpd = httpd
    httpd.daemon_threads = True
    return httpd, app


def esperar_tarea(app: Aplicacion, limite_s: float = 600) -> dict | None:
    fin = time.monotonic() + limite_s
    while app.tareas.ocupada() and time.monotonic() < fin:
        time.sleep(0.2)
    return app.tareas.actual
