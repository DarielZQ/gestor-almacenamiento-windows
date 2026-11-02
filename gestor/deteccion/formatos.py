"""Lectores pequeños de formatos que usan los detectores: VDF de Steam, accesos directos .lnk, .GamingRoot de
Xbox y rutas escritas como línea de comandos (UninstallString, DisplayIcon, ImagePath). Sin dependencias."""
from __future__ import annotations

import re
import struct

from .contexto import RE_ABSOLUTA, normalizar


# ---------------------------------------------------------------------- VDF (Valve KeyValues en texto)
class ErrorVDF(ValueError):
    pass


_ESCAPES_VDF = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _tokens_vdf(texto: str):
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c.isspace():
            i += 1
        elif texto.startswith("//", i):
            fin = texto.find("\n", i)
            i = n if fin < 0 else fin + 1
        elif c in "{}":
            yield c, c
            i += 1
        elif c == "[":  # condicionales como [$WIN32]: se ignoran
            fin = texto.find("]", i)
            i = n if fin < 0 else fin + 1
        elif c == '"':
            i += 1
            partes = []
            while i < n and texto[i] != '"':
                if texto[i] == "\\" and i + 1 < n:
                    partes.append(_ESCAPES_VDF.get(texto[i + 1], "\\" + texto[i + 1]))
                    i += 2
                else:
                    partes.append(texto[i])
                    i += 1
            if i >= n:
                raise ErrorVDF("cadena sin cerrar")
            i += 1
            yield "cadena", "".join(partes)
        else:
            j = i
            while j < n and not texto[j].isspace() and texto[j] not in '{}"':
                j += 1
            yield "cadena", texto[i:j]
            i = j


def leer_vdf(texto: str) -> dict:
    """Convierte un VDF de texto en diccionarios anidados. Lanza ErrorVDF si la estructura está rota."""
    raiz: dict = {}
    pila = [raiz]
    clave = None
    for tipo, valor in _tokens_vdf(texto.lstrip("﻿")):
        if tipo == "cadena":
            if clave is None:
                clave = valor
            else:
                pila[-1][clave] = valor
                clave = None
        elif tipo == "{":
            if clave is None:
                raise ErrorVDF("bloque sin nombre")
            nuevo: dict = {}
            pila[-1][clave] = nuevo
            pila.append(nuevo)
            clave = None
        else:
            if len(pila) == 1:
                raise ErrorVDF("llave de cierre sobrante")
            pila.pop()
    if len(pila) != 1:
        raise ErrorVDF("faltan llaves de cierre")
    return raiz


def bibliotecas_steam(datos: dict) -> list[str]:
    """Rutas de las bibliotecas de libraryfolders.vdf (formato nuevo con 'path' y formato antiguo con la ruta directa)."""
    raiz = next((v for k, v in datos.items() if k.lower() == "libraryfolders" and isinstance(v, dict)), None)
    if raiz is None:
        return []
    res = []
    for k, v in raiz.items():
        if not k.isdigit():
            continue
        ruta = next((vv for kk, vv in v.items() if kk.lower() == "path"), None) if isinstance(v, dict) else v
        if isinstance(ruta, str) and ruta.strip():
            res.append(ruta.strip())
    return res


# ---------------------------------------------------------------------- accesos directos (.lnk, MS-SHLLINK)
def _cadena_u16(datos: bytes, inicio: int) -> str:
    fin = inicio
    while fin + 1 < len(datos) and datos[fin:fin + 2] != b"\x00\x00":
        fin += 2
    return datos[inicio:fin].decode("utf-16-le", errors="replace")


def _cadena_ansi(datos: bytes, inicio: int) -> str:
    fin = datos.find(b"\x00", inicio)
    trozo = datos[inicio:fin if fin >= 0 else len(datos)]
    try:
        return trozo.decode("mbcs")
    except (LookupError, UnicodeDecodeError):
        return trozo.decode("latin-1")


def leer_lnk(datos: bytes) -> dict:
    """Destino de un acceso directo sin COM ni PowerShell. Devuelve {'destino', 'carpeta_trabajo', ...}.

    Usa la ruta local de LinkInfo y, si no la hay (accesos de instaladores), el bloque de variables de entorno."""
    if len(datos) < 0x4C or struct.unpack_from("<I", datos, 0)[0] != 0x4C:
        raise ValueError("no es un acceso directo")
    banderas = struct.unpack_from("<I", datos, 0x14)[0]
    pos = 0x4C
    if banderas & 0x1:  # HasLinkTargetIDList
        pos += 2 + struct.unpack_from("<H", datos, pos)[0]
    destino = None
    if banderas & 0x2:  # HasLinkInfo
        inicio = pos
        tam_info, tam_cab, info_banderas, _vol, off_local, _red, off_sufijo = struct.unpack_from("<7I", datos, pos)
        if info_banderas & 0x1:  # VolumeIDAndLocalBasePath
            if tam_cab >= 0x24:
                off_local_u, off_sufijo_u = struct.unpack_from("<2I", datos, pos + 28)
                base = _cadena_u16(datos, inicio + off_local_u)
                sufijo = _cadena_u16(datos, inicio + off_sufijo_u) if off_sufijo_u else ""
            else:
                base = _cadena_ansi(datos, inicio + off_local)
                sufijo = _cadena_ansi(datos, inicio + off_sufijo) if off_sufijo else ""
            destino = base + ("\\" if sufijo and not base.endswith("\\") else "") + sufijo
        pos = inicio + tam_info
    unicode = bool(banderas & 0x80)
    res: dict = {}
    for bit, nombre in ((0x4, "nombre"), (0x8, "ruta_relativa"), (0x10, "carpeta_trabajo"), (0x20, "argumentos"),
                        (0x40, "icono")):
        if banderas & bit:
            n = struct.unpack_from("<H", datos, pos)[0]
            pos += 2
            largo = n * 2 if unicode else n
            trozo = datos[pos:pos + largo]
            res[nombre] = trozo.decode("utf-16-le", errors="replace") if unicode else trozo.decode("latin-1")
            pos += largo
    con_variables = None
    while pos + 8 <= len(datos):
        tam = struct.unpack_from("<I", datos, pos)[0]
        if tam < 8:
            break
        firma = struct.unpack_from("<I", datos, pos + 4)[0]
        if firma == 0xA0000001 and tam >= 0x314:  # EnvironmentVariableDataBlock
            con_variables = _cadena_u16(datos, pos + 8 + 260) or _cadena_ansi(datos, pos + 8)
        pos += tam
    res["destino"] = destino or con_variables or None
    res["destino_con_variables"] = con_variables
    return res


# ---------------------------------------------------------------------- .GamingRoot (Xbox / Microsoft Store)
def leer_gaming_root(datos: bytes) -> list[str]:
    """Carpetas de juegos declaradas en <unidad>\\.GamingRoot: 'RGBX', un contador y rutas UTF-16 terminadas en cero."""
    if len(datos) < 8 or datos[:4] != b"RGBX":
        return []
    texto = datos[8:].decode("utf-16-le", errors="ignore")
    return [p.strip().strip("\\") for p in texto.split("\x00") if p.strip().strip("\\")]


# ---------------------------------------------------------------------- rutas en líneas de comandos
_RE_EJECUTABLE = re.compile(r"^(.+?\.(?:exe|dll|ico|bat|cmd|msi|com|sys|cpl|scr|ocx))(?=[\s,]|$)", re.IGNORECASE)


def limpiar_ruta_comando(texto, expandir) -> str | None:
    """Ruta del ejecutable de un UninstallString, DisplayIcon o ImagePath. None si no es una ruta local útil."""
    if not isinstance(texto, str):
        return None
    s = texto.strip()
    if not s:
        return None
    if s.startswith('"'):
        fin = s.find('"', 1)
        s = s[1:fin] if fin > 0 else s.strip('"')
    else:
        s = re.sub(r",\s*-?\d+$", "", s)
        m = _RE_EJECUTABLE.match(s)
        if m:
            s = m.group(1)
    s = re.sub(r",\s*-?\d+$", "", s.strip())
    bajo = s.lower()
    if bajo.startswith("\\??\\"):
        s = s[4:]
    elif bajo.startswith("\\systemroot\\"):
        s = "%SystemRoot%" + s[len("\\systemroot"):]
    elif re.match(r"^(system32|syswow64)\\", bajo):
        s = "%SystemRoot%\\" + s
    if "%" in s:
        s = expandir(s)
    if not s or not RE_ABSOLUTA.match(s):
        return None
    if re.search(r"(^|\\)msiexec\.exe$", s, re.IGNORECASE):
        return None
    return normalizar(s)


def limpiar_ruta_simple(texto, expandir) -> str | None:
    """Ruta de un valor que ya es una carpeta (InstallLocation, entradas del PATH)."""
    if not isinstance(texto, str):
        return None
    s = texto.strip().strip('"').strip()
    if "%" in s:
        s = expandir(s)
    if not s or not RE_ABSOLUTA.match(s):
        return None
    return normalizar(s)
