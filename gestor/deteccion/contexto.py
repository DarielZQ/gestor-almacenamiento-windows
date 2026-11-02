"""Contexto compartido por los detectores: entorno, registro de Windows, medición y utilidades de rutas.

Todo es de solo lectura. Las fuentes externas (variables de entorno, registro, volúmenes, medición de carpetas)
se pueden sustituir con 'fuentes' para simular cualquier equipo en las pruebas. El sistema de archivos es real:
en las pruebas, las variables de entorno simuladas apuntan a carpetas temporales.
"""
from __future__ import annotations

import copy
import ntpath
import os
import re
import time

ATRIB_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT (junctions y enlaces simbólicos)
RE_ABSOLUTA = re.compile(r"^[A-Za-z]:\\")
_RAICES_REGISTRO = {"HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE"}


# ---------------------------------------------------------------------- rutas
def normalizar(ruta) -> str | None:
    """Ruta de Windows limpia (barras invertidas, sin comillas ni barra final). None si no es texto útil."""
    if not isinstance(ruta, str):
        return None
    r = ruta.strip().strip('"').strip().replace("/", "\\")
    if not r:
        return None
    if r.startswith("\\\\?\\") and not r.lower().startswith("\\\\?\\unc\\"):
        r = r[4:]
    if len(r) == 2 and r[1] == ":":
        r += "\\"
    r = ntpath.normpath(r)
    if len(r) > 3:
        r = r.rstrip("\\")
    if len(r) >= 2 and r[1] == ":":
        r = r[0].upper() + r[1:]
    return r


def ruta_larga(ruta: str | None) -> str | None:
    """Convierte nombres cortos 8.3 (C:\\PROGRA~1\\...) en la ruta larga, si existe. Si no, la deja igual."""
    if not ruta or "~" not in ruta or os.name != "nt":
        return ruta
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetLongPathNameW(ruta, buf, 1024)
        return buf.value if 0 < n < 1024 else ruta
    except Exception:
        return ruta


def dentro(ruta: str | None, base: str | None) -> bool:
    """True si 'ruta' es 'base' o está dentro de ella (sin distinguir mayúsculas)."""
    if not ruta or not base:
        return False
    r = ruta.lower().rstrip("\\")
    b = base.lower().rstrip("\\")
    return r == b or r.startswith(b + "\\")


def es_raiz_unidad(ruta: str | None) -> bool:
    return bool(ruta) and bool(re.match(r"^[A-Za-z]:\\?$", ruta))


def es_reparse(ruta: str) -> bool:
    """True si la carpeta es una junction o un enlace simbólico."""
    try:
        st = os.stat(ruta, follow_symlinks=False)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & ATRIB_REPARSE) or os.path.islink(ruta)


# ---------------------------------------------------------------------- medición con presupuesto de tiempo
def medir_carpeta(ruta: str, segundos: float) -> tuple[int, bool]:
    """Suma el tamaño de los archivos de 'ruta' sin seguir junctions. Devuelve (bytes, completo)."""
    limite = time.monotonic() + max(0.0, float(segundos))
    total = 0
    pila = [ruta]
    vistos = 0
    while pila:
        if time.monotonic() > limite:
            return total, False
        actual = pila.pop()
        try:
            with os.scandir(actual) as it:
                for e in it:
                    vistos += 1
                    if vistos % 2000 == 0 and time.monotonic() > limite:
                        return total, False
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if getattr(st, "st_file_attributes", 0) & ATRIB_REPARSE:
                        continue
                    if e.is_dir(follow_symlinks=False):
                        pila.append(e.path)
                    else:
                        total += st.st_size
        except OSError:
            continue
    return total, True


# ---------------------------------------------------------------------- registro real (solo lectura)
def _abrir_clave(raiz: str, clave: str):
    import winreg

    base = getattr(winreg, _RAICES_REGISTRO[raiz.upper()])
    return winreg.OpenKey(base, clave, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)


def registro_real(raiz: str, clave: str, valor: str):
    """Valor del registro o None si no existe. Otros errores (permisos, clave dañada) se propagan."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with _abrir_clave(raiz, clave) as k:
            return winreg.QueryValueEx(k, valor)[0]
    except FileNotFoundError:
        return None


def subclaves_real(raiz: str, clave: str) -> list[str]:
    try:
        import winreg
    except ImportError:
        return []
    res = []
    try:
        with _abrir_clave(raiz, clave) as k:
            i = 0
            while True:
                try:
                    res.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except FileNotFoundError:
        return []
    return res


def valores_real(raiz: str, clave: str) -> dict:
    try:
        import winreg
    except ImportError:
        return {}
    res = {}
    try:
        with _abrir_clave(raiz, clave) as k:
            i = 0
            while True:
                try:
                    nombre, dato, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                res[nombre] = dato
                i += 1
    except FileNotFoundError:
        return {}
    return res


def _nada(*_args):
    return None


# ---------------------------------------------------------------------- contexto
class Contexto:
    """Lo que ve cada detector. 'fuentes' admite: entorno, registro, subclaves, valores, volumenes, carpeta_shell,
    medir, catalogo y presupuestos (ver equipo.detectar_equipo)."""

    def __init__(self, fuentes: dict):
        self.fuentes = fuentes
        entorno = fuentes.get("entorno")
        if entorno is None:
            entorno = dict(os.environ)
        # En Windows las variables de entorno no distinguen mayúsculas.
        self._env = {str(k).upper(): str(v) for k, v in entorno.items() if v is not None}
        # Si se inyecta alguna parte del registro, las que falten quedan vacías: nunca se mezcla con el registro real.
        inyectado = any(k in fuentes for k in ("registro", "subclaves", "valores"))
        self._registro = fuentes.get("registro") or (_nada if inyectado else registro_real)
        self._subclaves = fuentes.get("subclaves") or ((lambda *_: []) if inyectado else subclaves_real)
        self._valores = fuentes.get("valores") or ((lambda *_: {}) if inyectado else valores_real)
        self.medir = fuentes.get("medir") or medir_carpeta
        self.resultados: dict = {}   # secciones ya detectadas (compartido entre detectores)
        self.advertencias: list[str] = []
        self.limite: float | None = None
        self._memo: dict = {}

    def hijo(self, limite: float) -> "Contexto":
        """Copia para un detector: comparte entorno, registro y resultados; tiene sus propias advertencias y plazo."""
        c = copy.copy(self)
        c.advertencias = []
        c.limite = limite
        return c

    # -- entorno
    def var(self, nombre: str, defecto: str | None = None) -> str | None:
        v = self._env.get(nombre.upper())
        return v if v else defecto

    def expandir(self, texto) -> str | None:
        """Expande %VARIABLES% con el entorno del contexto; None si queda alguna sin resolver."""
        if not isinstance(texto, str):
            return None
        e = re.sub(r"%([^%\\]+)%", lambda m: self._env.get(m.group(1).upper(), m.group(0)), texto)
        return None if re.search(r"%[^%\\]+%", e) else e

    def unidad_sistema(self) -> str:
        return (self.var("SystemDrive") or "C:")[:1].upper()

    # -- registro (tolerante: cualquier error cuenta como valor ausente)
    def reg(self, raiz: str, clave: str, valor: str):
        try:
            return self._registro(raiz, clave, valor)
        except Exception:
            return None

    def reg_texto(self, raiz: str, clave: str, valor: str) -> str | None:
        v = self.reg(raiz, clave, valor)
        return v.strip() if isinstance(v, str) and v.strip() else None

    def reg_estricto(self, raiz: str, clave: str, valor: str):
        """Como reg(), pero deja pasar los errores para poder contar claves dañadas."""
        return self._registro(raiz, clave, valor)

    def subclaves(self, raiz: str, clave: str) -> list[str]:
        try:
            return [s for s in (self._subclaves(raiz, clave) or []) if isinstance(s, str)]
        except Exception:
            return []

    def valores(self, raiz: str, clave: str) -> dict:
        try:
            v = self._valores(raiz, clave)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}

    # -- avisos, plazos y memoria
    def advertir(self, mensaje: str) -> None:
        self.advertencias.append(mensaje)

    def queda(self) -> float:
        return float("inf") if self.limite is None else self.limite - time.monotonic()

    def agotado(self) -> bool:
        return self.queda() <= 0

    def memo(self, clave: str, calcular):
        if clave not in self._memo:
            self._memo[clave] = calcular()
        return self._memo[clave]
