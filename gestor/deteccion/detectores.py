"""Detectores del equipo. Cada uno recibe un Contexto y devuelve su sección del perfil. Todos son de solo lectura.

Se pueden sustituir uno a uno con detectar_equipo(fuentes={"<sección>": funcion(ctx)}).
"""
from __future__ import annotations

import glob
import json
import ntpath
import os
import re
import subprocess
import time

from ..config import RAIZ_PROYECTO
from .catalogo import cargar_catalogo, normalizar_entrada
from .contexto import RE_ABSOLUTA, Contexto, dentro, es_reparse, normalizar, ruta_larga
from .formatos import (ErrorVDF, bibliotecas_steam, leer_gaming_root, leer_lnk, leer_vdf, limpiar_ruta_comando,
                       limpiar_ruta_simple)

SCRIPT_DISCOS = RAIZ_PROYECTO / "scripts" / "detectar-equipo.ps1"
SIN_VENTANA = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

USF = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
SF = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
# clave del perfil -> (valor en User Shell Folders, carpeta por defecto dentro del perfil)
CARPETAS_CONOCIDAS = {
    "escritorio": ("Desktop", "Desktop"),
    "documentos": ("Personal", "Documents"),
    "descargas": ("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
    "imagenes": ("My Pictures", "Pictures"),
    "videos": ("My Video", "Videos"),
    "musica": ("My Music", "Music"),
}
CLAVES_UNINSTALL = (
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
)
# Editores de launchers: sus programas instalados (InstallLocation) son juegos o partes del launcher.
EDITORES_LAUNCHER = {
    "blizzard entertainment": "Battle.net",
    "riot games": "Riot Games",
    "electronic arts": "EA",
    "ubisoft": "Ubisoft",
    "gog.com": "GOG",
    "gog sp. z o.o.": "GOG",
}
MAX_ACCESOS_DIRECTOS = 4000


def _entero(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


# ====================================================================== discos
def normalizar_volumen(v: dict, sistema: str, fuente: str = "") -> dict | None:
    """Unifica un volumen (de PowerShell, WMI, la API de Windows o una prueba) en el formato del perfil."""
    letra = str(v.get("letra") or "").strip().rstrip(":\\").upper()
    if len(letra) != 1 or not letra.isalpha():
        return None
    fs = str(v.get("sistema_archivos") or "").strip()
    etiqueta = str(v.get("etiqueta") or "").strip()
    bus = str(v.get("bus") or "").strip()
    unidad = str(v.get("tipo_unidad") or v.get("tipo") or "").strip().lower()
    bus_b = bus.lower()
    nube_virtual = "google drive" in etiqueta.lower() or fs.upper() == "GOOGLEDRIVEFS" or bool(v.get("nube_virtual"))
    if unidad in ("network", "red", "remote", "4"):
        tipo = "red"
    elif unidad in ("cd-rom", "cdrom", "optico", "5"):
        tipo = "optico"
    elif nube_virtual or bus_b in ("file backed virtual", "virtual") or unidad == "virtual":
        tipo = "virtual"
    elif bus_b == "usb" or unidad == "usb":
        tipo = "usb"
    elif bus_b in ("1394", "sd", "mmc") or unidad in ("removable", "extraible", "2"):
        tipo = "extraible"
    elif unidad in ("fixed", "interno", "3") or bus_b:
        tipo = "interno"
    else:
        tipo = "desconocido"
    ntfs = fs.upper() == "NTFS"
    return {
        "letra": letra,
        "raiz": f"{letra}:\\",
        "etiqueta": etiqueta,
        "sistema_archivos": fs,
        "total": _entero(v.get("total")),
        "libre": _entero(v.get("libre")),
        "bus": bus or "desconocido",
        "tipo": tipo,
        "externo": tipo in ("usb", "extraible"),
        "es_sistema": letra == sistema,
        "ntfs": ntfs,
        "admite_junctions": ntfs,
        "nube_virtual": nube_virtual,
        "numero_disco": v.get("disco") if isinstance(v.get("disco"), int) else None,
        "fuente": str(v.get("fuente") or fuente or ""),
    }


def volumenes_powershell(segundos: float = 40.0) -> tuple[list[dict], str | None]:
    """Volúmenes con BusType (Get-Volume/Get-Partition/Get-Disk, o WMI de respaldo) mediante scripts/detectar-equipo.ps1."""
    if not SCRIPT_DISCOS.exists():
        return [], "falta scripts\\detectar-equipo.ps1"
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(SCRIPT_DISCOS)], capture_output=True, timeout=max(1.0, segundos),
                              creationflags=SIN_VENTANA)
    except subprocess.TimeoutExpired:
        return [], f"PowerShell tardó más de {segundos:.0f} s"
    except OSError as e:
        return [], f"no se pudo ejecutar PowerShell: {e}"
    texto = proc.stdout.decode("utf-8", errors="replace").lstrip("﻿").strip()
    try:
        datos = json.loads(texto or "{}")
    except ValueError:
        return [], "la salida de PowerShell no es JSON válido"
    if isinstance(datos, list):
        return datos, None
    vols = datos.get("volumenes") or []
    vols = vols if isinstance(vols, list) else [vols]
    for v in vols:
        if isinstance(v, dict):
            v.setdefault("fuente", datos.get("fuente") or "powershell")
    errores = [e for e in (datos.get("errores") or []) if e]
    return vols, ("; ".join(map(str, errores)) if errores and not vols else None)


def volumenes_api() -> list[dict]:
    """Volúmenes con la API de Windows (sin bus: un USB externo de tipo fijo aparece como interno)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    anterior = wintypes.UINT(0)
    try:  # sin cuadros de diálogo "inserte un disco" en lectores vacíos (solo este hilo)
        k32.SetThreadErrorMode(0x0001 | 0x8000, ctypes.byref(anterior))
    except AttributeError:
        pass
    tipos = {2: "Removable", 3: "Fixed", 4: "Network", 5: "CD-ROM", 6: "Ram"}
    res = []
    try:
        mascara = k32.GetLogicalDrives()
        for i in range(26):
            if not mascara & (1 << i):
                continue
            raiz = f"{chr(65 + i)}:\\"
            tipo = k32.GetDriveTypeW(raiz)
            v = {"letra": chr(65 + i), "tipo_unidad": tipos.get(tipo, "Unknown"), "fuente": "api"}
            if tipo in (2, 3, 6):  # las de red y ópticas no se consultan: pueden tardar
                etiqueta = ctypes.create_unicode_buffer(261)
                fs = ctypes.create_unicode_buffer(261)
                if k32.GetVolumeInformationW(raiz, etiqueta, 261, None, None, None, fs, 261):
                    v["etiqueta"], v["sistema_archivos"] = etiqueta.value, fs.value
                libre, total = ctypes.c_ulonglong(0), ctypes.c_ulonglong(0)
                if k32.GetDiskFreeSpaceExW(raiz, ctypes.byref(libre), ctypes.byref(total), None):
                    v["libre"], v["total"] = libre.value, total.value
            res.append(v)
    finally:
        try:
            k32.SetThreadErrorMode(anterior.value, None)
        except AttributeError:
            pass
    return res


def _volumenes_sistema(ctx: Contexto) -> list[dict]:
    base: dict[str, dict] = {}
    try:
        for v in volumenes_api():
            base[v["letra"]] = v
    except Exception as e:  # sin ctypes o fuera de Windows
        ctx.advertir(f"No se pudo leer la lista de unidades con la API de Windows ({e}).")
    ps, error = volumenes_powershell(min(40.0, max(1.0, ctx.queda() - 2)))
    if error:
        ctx.advertir(f"No se pudo consultar el tipo de bus de los discos ({error}); se usa el tipo de unidad de "
                     "Windows y un disco USB externo podría aparecer como interno.")
    for v in ps:
        if not isinstance(v, dict):
            continue
        letra = str(v.get("letra") or "").strip().rstrip(":\\").upper()
        if not letra:
            continue
        actual = base.setdefault(letra, {"letra": letra})
        actual.update({k: val for k, val in v.items() if val not in (None, "")})
    return list(base.values())


def detectar_discos(ctx: Contexto) -> list[dict]:
    fuente = ctx.fuentes.get("volumenes")
    crudos = fuente() if callable(fuente) else _volumenes_sistema(ctx)
    sistema = ctx.unidad_sistema()
    discos: dict[str, dict] = {}
    for v in crudos or []:
        d = normalizar_volumen(v, sistema) if isinstance(v, dict) else None
        if d and d["letra"] not in discos:
            discos[d["letra"]] = d
    lista = sorted(discos.values(), key=lambda d: d["letra"])
    if not lista:
        ctx.advertir("No se detectó ningún disco con letra de unidad.")
    elif not any(d["es_sistema"] for d in lista):
        ctx.advertir(f"No se encontró el disco del sistema ({sistema}:) entre las unidades detectadas.")
    return lista


# ====================================================================== carpetas del usuario
def carpetas_por_defecto(ctx: Contexto) -> dict:
    perfil = normalizar(ctx.var("USERPROFILE") or os.path.expanduser("~")) or ""
    res = {"perfil": perfil}
    for clave, (_valor, ingles) in CARPETAS_CONOCIDAS.items():
        res[clave] = ntpath.join(perfil, ingles)
    res["localappdata"] = normalizar(ctx.var("LOCALAPPDATA")) or ntpath.join(perfil, "AppData", "Local")
    res["appdata"] = normalizar(ctx.var("APPDATA")) or ntpath.join(perfil, "AppData", "Roaming")
    res["raiz_sistema"] = f"{ctx.unidad_sistema()}:\\"
    return res


def detectar_carpetas(ctx: Contexto) -> dict:
    """Carpetas conocidas desde User Shell Folders (expande variables y respeta las redirecciones a OneDrive)."""
    res = carpetas_por_defecto(ctx)
    leer_shell = ctx.fuentes.get("carpeta_shell")
    for clave, (valor, _ingles) in CARPETAS_CONOCIDAS.items():
        defecto = res[clave]
        if callable(leer_shell):  # misma firma que gestor.config.rutas_sistema: (valor, defecto) -> ruta expandida
            ruta = leer_shell(valor, defecto)
        else:
            crudo = ctx.reg_texto("HKCU", USF, valor) or ctx.reg_texto("HKCU", SF, valor)
            ruta = ctx.expandir(crudo) if crudo else None
            if crudo and not ruta:
                ctx.advertir(f"La carpeta '{clave}' usa una variable de entorno desconocida; se usa la ubicación por defecto.")
        res[clave] = normalizar(ruta) or defecto
    return res


# ====================================================================== nube
def _proveedor_sync(nombre: str) -> str:
    b = nombre.lower()
    for clave, proveedor in (("onedrive", "OneDrive"), ("dropbox", "Dropbox"), ("google", "Google Drive"),
                             ("drivefs", "Google Drive"), ("icloud", "iCloud"), ("box", "Box"), ("mega", "MEGA")):
        if clave in b:
            return proveedor
    return nombre


def _servicio_nube(proveedor: str, tipo: str | None) -> str:
    etiquetas = {("OneDrive", "personal"): "OneDrive personal", ("OneDrive", "empresa"): "OneDrive de empresa",
                 ("Dropbox", "empresa"): "Dropbox de empresa", ("Google Drive", "cache"): "Google Drive (caché local)",
                 ("Google Drive", "unidad"): "Google Drive (unidad virtual)"}
    return etiquetas.get((proveedor, tipo or ""), proveedor)


def detectar_nube(ctx: Contexto) -> list[dict]:
    """Raíces de OneDrive (personal y de empresa), Google Drive, Dropbox, iCloud y otras carpetas sincronizadas."""
    res: list[dict] = []
    vistos: dict[str, dict] = {}

    def agregar(proveedor, tipo, ruta, origen, requiere_existir=True):
        r = normalizar(ruta_larga(normalizar(ruta)))
        if not r or not RE_ABSOLUTA.match(r) or (requiere_existir and not os.path.isdir(r)):
            return
        previo = vistos.get(r.lower())
        if previo:
            if not previo["tipo"] and tipo:
                previo["tipo"] = tipo
                previo["servicio"] = _servicio_nube(previo["proveedor"], tipo)
            return
        e = {"servicio": _servicio_nube(proveedor, tipo), "proveedor": proveedor, "tipo": tipo, "ruta": r, "origen": origen}
        vistos[r.lower()] = e
        res.append(e)

    agregar("OneDrive", "personal", ctx.var("OneDriveConsumer"), "variable OneDriveConsumer")
    agregar("OneDrive", "empresa", ctx.var("OneDriveCommercial"), "variable OneDriveCommercial")
    agregar("OneDrive", None, ctx.var("OneDrive"), "variable OneDrive")
    cuentas = r"Software\Microsoft\OneDrive\Accounts"
    for cuenta in ctx.subclaves("HKCU", cuentas):
        carpeta = ctx.reg_texto("HKCU", f"{cuentas}\\{cuenta}", "UserFolder")
        tipo = "personal" if cuenta.lower() == "personal" else ("empresa" if cuenta.lower().startswith("business") else None)
        agregar("OneDrive", tipo, ctx.expandir(carpeta) if carpeta else None, "registro de OneDrive")
    # Raíces de sincronización registradas en el Explorador (OneDrive, Dropbox, Google Drive, iCloud, Box...)
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"
    for sub in ctx.subclaves("HKLM", base):
        partes = sub.split("!")
        proveedor = _proveedor_sync(partes[0])
        cola = partes[-1].lower() if len(partes) > 1 else ""
        tipo = "personal" if cola.startswith("personal") else ("empresa" if cola.startswith("business") else None)
        for valor in ctx.valores("HKLM", f"{base}\\{sub}\\UserSyncRoots").values():
            if isinstance(valor, str):
                agregar(proveedor, tipo if proveedor in ("OneDrive", "Dropbox") else None, ctx.expandir(valor) or valor,
                        "registro SyncRootManager")
    # Dropbox: info.json
    for carpeta in (ctx.var("APPDATA"), ctx.var("LOCALAPPDATA")):
        if not carpeta:
            continue
        info = ntpath.join(carpeta, "Dropbox", "info.json")
        if not os.path.isfile(info):
            continue
        try:
            with open(info, encoding="utf-8-sig") as f:
                datos = json.load(f)
        except (OSError, ValueError) as e:
            ctx.advertir(f"No se pudo leer info.json de Dropbox ({type(e).__name__}).")
            continue
        for cuenta, d in (datos.items() if isinstance(datos, dict) else []):
            if isinstance(d, dict):
                agregar("Dropbox", "empresa" if cuenta.lower() == "business" else "personal", d.get("path"), "Dropbox info.json")
    perfil = ctx.var("USERPROFILE")
    local = ctx.var("LOCALAPPDATA")
    # Google Drive: caché local de DriveFS, carpetas clásicas y unidad virtual
    if local:
        agregar("Google Drive", "cache", ntpath.join(local, "Google", "DriveFS"), "caché local de Google Drive")
    if perfil:
        for nombre in ("Google Drive", "My Drive", "Mi unidad"):
            agregar("Google Drive", None, ntpath.join(perfil, nombre), "carpeta estándar")
    for d in ctx.resultados.get("discos") or []:
        if d.get("nube_virtual"):
            agregar("Google Drive", "unidad", d.get("raiz"), "unidad virtual", requiere_existir=False)
    # iCloud
    if perfil:
        for rel in ("iCloudDrive", "iCloud Drive", "iCloudPhotos", "Pictures\\iCloud Photos"):
            agregar("iCloud", None, ntpath.join(perfil, rel), "carpeta estándar")
    return res


# ====================================================================== programas instalados (compartido)
def programas_instalados(ctx: Contexto) -> tuple[list[dict], int]:
    """Entradas de las claves Uninstall (HKLM, HKLM WOW6432Node y HKCU). Devuelve (programas, claves_ilegibles)."""

    def leer():
        programas, ilegibles = [], 0
        for raiz, base in CLAVES_UNINSTALL:
            for sub in ctx.subclaves(raiz, base):
                clave = f"{base}\\{sub}"
                try:
                    datos = {c: ctx.reg_estricto(raiz, clave, c) for c in
                             ("DisplayName", "Publisher", "InstallLocation", "DisplayIcon", "UninstallString")}
                except Exception:
                    ilegibles += 1
                    continue
                raros = [k for k, v in datos.items() if v is not None and not isinstance(v, str)]
                if raros:
                    ilegibles += 1
                datos = {k: (v.strip() if isinstance(v, str) else None) for k, v in datos.items()}
                programas.append({"clave": f"{raiz}\\{clave}", "nombre": datos["DisplayName"] or sub,
                                  "editor": datos["Publisher"] or "", "InstallLocation": datos["InstallLocation"],
                                  "DisplayIcon": datos["DisplayIcon"], "UninstallString": datos["UninstallString"]})
        return programas, ilegibles

    return ctx.memo("programas", leer)


# ====================================================================== bibliotecas de juegos y launchers
def detectar_bibliotecas(ctx: Contexto) -> list[dict]:
    """Bibliotecas de juegos y launchers por detección (registro, manifiestos y archivos propios de cada launcher)."""
    res: list[dict] = []
    vistos: set[str] = set()

    def agregar(plataforma, tipo, ruta, origen, nombre=None, requiere_existir=True):
        r = normalizar(ruta_larga(normalizar(ruta)))
        if not r or not RE_ABSOLUTA.match(r) or r.lower() in vistos:
            return None
        existe = os.path.isdir(r)
        if requiere_existir and not existe:
            return None
        vistos.add(r.lower())
        e = {"plataforma": plataforma, "tipo": tipo, "ruta": r, "nombre": nombre if isinstance(nombre, str) else None,
             "origen": origen, "existe": existe}
        res.append(e)
        return e

    pf86 = ctx.var("ProgramFiles(x86)")
    pf = ctx.var("ProgramW6432") or ctx.var("ProgramFiles")
    pdata = ctx.var("ProgramData")

    # --- Steam: instalación (registro) y bibliotecas de steamapps\libraryfolders.vdf
    candidatos = [(ctx.reg_texto("HKCU", r"Software\Valve\Steam", "SteamPath"), "registro (SteamPath)"),
                  (ctx.reg_texto("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"), "registro (InstallPath)"),
                  (ctx.reg_texto("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath"), "registro (InstallPath)")]
    if pf86:
        candidatos.append((ntpath.join(pf86, "Steam"), "ruta estándar"))
    for ruta, origen in candidatos:
        raiz = agregar("Steam", "launcher", ruta, origen)
        if not raiz:
            continue
        for rel in ("steamapps\\libraryfolders.vdf", "config\\libraryfolders.vdf"):
            archivo = ntpath.join(raiz["ruta"], rel)
            if not os.path.isfile(archivo):
                continue
            try:
                with open(archivo, encoding="utf-8", errors="replace") as f:
                    rutas = bibliotecas_steam(leer_vdf(f.read()))
            except (OSError, ErrorVDF) as e:
                ctx.advertir(f"No se pudo leer {rel} de Steam ({e}).")
                continue
            for lib in rutas:
                agregar("Steam", "biblioteca", lib, "libraryfolders.vdf", requiere_existir=False)

    # --- Epic Games: manifiestos .item y LauncherInstalled.dat
    if pdata:
        manifiestos = ntpath.join(pdata, "Epic", "EpicGamesLauncher", "Data", "Manifests")
        for item in sorted(glob.glob(ntpath.join(glob.escape(manifiestos), "*.item"))):
            try:
                with open(item, encoding="utf-8-sig") as f:
                    datos = json.load(f)
            except (OSError, ValueError) as e:
                ctx.advertir(f"Manifiesto de Epic ilegible ({ntpath.basename(item)}: {type(e).__name__}).")
                continue
            if isinstance(datos, dict):
                agregar("Epic Games", "juego", datos.get("InstallLocation"), "manifiesto de Epic",
                        nombre=datos.get("DisplayName"), requiere_existir=False)
        instalados = ntpath.join(pdata, "Epic", "UnrealEngineLauncher", "LauncherInstalled.dat")
        if os.path.isfile(instalados):
            try:
                with open(instalados, encoding="utf-8-sig") as f:
                    lista = json.load(f).get("InstallationList") or []
                for e in lista:
                    if isinstance(e, dict):
                        agregar("Epic Games", "juego", e.get("InstallLocation"), "LauncherInstalled.dat",
                                nombre=e.get("AppName"), requiere_existir=False)
            except (OSError, ValueError, AttributeError) as e:
                ctx.advertir(f"No se pudo leer LauncherInstalled.dat de Epic ({type(e).__name__}).")
    for base in (pf86, pf):
        if base:
            agregar("Epic Games", "launcher", ntpath.join(base, "Epic Games", "Launcher"), "ruta estándar")

    # --- Xbox / Microsoft Store: .GamingRoot en la raíz de cada unidad y carpeta XboxGames
    for d in ctx.resultados.get("discos") or []:
        if d.get("tipo") in ("red", "optico") or not d.get("total") or ctx.agotado():
            continue
        raiz_unidad = d.get("raiz") or f"{d.get('letra')}:\\"
        gaming_root = ntpath.join(raiz_unidad, ".GamingRoot")
        if os.path.isfile(gaming_root):
            try:
                with open(gaming_root, "rb") as f:
                    carpetas = leer_gaming_root(f.read(4096))
            except OSError:
                carpetas = []
            for c in carpetas:
                destino = c if RE_ABSOLUTA.match(c) else ntpath.join(raiz_unidad, c)
                agregar("Xbox", "biblioteca", destino, ".GamingRoot", requiere_existir=False)
        agregar("Xbox", "biblioteca", ntpath.join(raiz_unidad, "XboxGames"), "carpeta estándar")

    # --- Battle.net
    icono = ctx.reg_texto("HKLM", r"SOFTWARE\WOW6432Node\Blizzard Entertainment\Battle.net\Capabilities", "ApplicationIcon")
    exe = limpiar_ruta_comando(icono, ctx.expandir) if icono else None
    if exe:
        agregar("Battle.net", "launcher", ntpath.dirname(exe), "registro")
    for base in (pf86, pf):
        if base:
            agregar("Battle.net", "launcher", ntpath.join(base, "Battle.net"), "ruta estándar")
    if pdata:
        agregar("Battle.net", "launcher", ntpath.join(pdata, "Battle.net"), "ruta estándar")

    # --- Riot Games: RiotClientInstalls.json y product_settings.yaml
    if pdata:
        riot = ntpath.join(pdata, "Riot Games")
        instalaciones = ntpath.join(riot, "RiotClientInstalls.json")
        if os.path.isfile(instalaciones):
            try:
                with open(instalaciones, encoding="utf-8-sig") as f:
                    datos = json.load(f)
                for clave in ("rc_default", "rc_live", "rc_beta"):
                    exe = normalizar(datos.get(clave)) if isinstance(datos, dict) else None
                    if exe:
                        agregar("Riot Games", "launcher", exe if os.path.isdir(exe) else ntpath.dirname(exe),
                                "RiotClientInstalls.json")
                for ruta in ((datos.get("associated_client") or {}) if isinstance(datos, dict) else {}):
                    agregar("Riot Games", "juego", ruta, "RiotClientInstalls.json", requiere_existir=False)
            except (OSError, ValueError, AttributeError) as e:
                ctx.advertir(f"No se pudo leer RiotClientInstalls.json ({type(e).__name__}).")
        # Riot Games / carpeta de metadatos / producto / *.product_settings.yaml
        for yaml in glob.glob(ntpath.join(glob.escape(riot), "*", "*", "*.product_settings.yaml")):
            try:
                with open(yaml, encoding="utf-8", errors="replace") as f:
                    texto = f.read(65536)
            except OSError:
                continue
            m = re.search(r'^\s*product_install_full_path:\s*"?([^"\r\n]+)"?', texto, re.MULTILINE)
            if m:
                agregar("Riot Games", "juego", m.group(1), "product_settings.yaml", requiere_existir=False)

    # --- EA (EA app y Origin)
    for clave, valor in ((r"SOFTWARE\Electronic Arts\EA Desktop", "InstallLocation"),
                         (r"SOFTWARE\WOW6432Node\Electronic Arts\EA Desktop", "InstallLocation"),
                         (r"SOFTWARE\WOW6432Node\Origin", "ClientPath")):
        v = ctx.reg_texto("HKLM", clave, valor)
        r = limpiar_ruta_comando(v, ctx.expandir) if v and valor == "ClientPath" else limpiar_ruta_simple(v, ctx.expandir)
        if r:
            agregar("EA", "launcher", ntpath.dirname(r) if valor == "ClientPath" else r, "registro")
    for sub in ctx.subclaves("HKLM", r"SOFTWARE\WOW6432Node\EA Games"):
        agregar("EA", "juego", ctx.reg_texto("HKLM", rf"SOFTWARE\WOW6432Node\EA Games\{sub}", "Install Dir"), "registro",
                nombre=sub, requiere_existir=False)

    # --- Ubisoft Connect
    ubi = ctx.reg_texto("HKLM", r"SOFTWARE\WOW6432Node\Ubisoft\Launcher", "InstallDir")
    if agregar("Ubisoft", "launcher", ubi, "registro"):
        agregar("Ubisoft", "biblioteca", ntpath.join(normalizar(ubi), "games"), "carpeta estándar")
    for sub in ctx.subclaves("HKLM", r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"):
        agregar("Ubisoft", "juego", ctx.reg_texto("HKLM", rf"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs\{sub}",
                                                  "InstallDir"), "registro", requiere_existir=False)

    # --- GOG Galaxy
    agregar("GOG", "launcher", ctx.reg_texto("HKLM", r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient\paths", "client"), "registro")
    for sub in ctx.subclaves("HKLM", r"SOFTWARE\WOW6432Node\GOG.com\Games"):
        clave = rf"SOFTWARE\WOW6432Node\GOG.com\Games\{sub}"
        agregar("GOG", "juego", ctx.reg_texto("HKLM", clave, "path"), "registro",
                nombre=ctx.reg_texto("HKLM", clave, "gameName"), requiere_existir=False)

    # --- Programas instalados por los launchers (por editor en las claves Uninstall)
    programas, _ = programas_instalados(ctx)
    for p in programas:
        editor = p["editor"].lower()
        plataforma = next((v for k, v in EDITORES_LAUNCHER.items() if editor.startswith(k)), None)
        if plataforma and p["InstallLocation"]:
            agregar(plataforma, "instalacion", limpiar_ruta_simple(p["InstallLocation"], ctx.expandir), "programas instalados",
                    nombre=p["nombre"])
    return res


# ====================================================================== rutas referenciadas
def detectar_referencias(ctx: Contexto) -> list[dict]:
    """Rutas que usan programas instalados, accesos directos, el PATH y los servicios. Excluye las de Windows."""
    res: list[dict] = []
    vistos: set[tuple[str, str]] = set()
    windows = normalizar(ctx.var("SystemRoot") or ctx.var("windir"))

    def agregar(ruta, origen, detalle, campo):
        r = normalizar(ruta_larga(normalizar(ruta)))
        if not r or not RE_ABSOLUTA.match(r) or (windows and dentro(r, windows)):
            return
        clave = (r.lower(), origen)
        if clave in vistos:
            return
        vistos.add(clave)
        existe = os.path.exists(r)
        res.append({"ruta": r, "origen": origen, "detalle": detalle, "campo": campo, "existe": existe,
                    "es_carpeta": os.path.isdir(r) if existe else None})

    # Programas instalados
    programas, ilegibles = programas_instalados(ctx)
    for p in programas:
        agregar(limpiar_ruta_simple(p["InstallLocation"], ctx.expandir), "programa", p["nombre"], "InstallLocation")
        agregar(limpiar_ruta_comando(p["DisplayIcon"], ctx.expandir), "programa", p["nombre"], "DisplayIcon")
        agregar(limpiar_ruta_comando(p["UninstallString"], ctx.expandir), "programa", p["nombre"], "UninstallString")
    if ilegibles:
        ctx.advertir(f"Se omitieron {ilegibles} entradas de programas instalados dañadas o ilegibles.")

    # PATH de usuario y de máquina
    for raiz, clave, origen in (("HKCU", "Environment", "path_usuario"),
                                ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "path_maquina")):
        valor = ctx.reg_texto(raiz, clave, "Path") or ""
        for parte in valor.split(";"):
            agregar(limpiar_ruta_simple(parte, ctx.expandir), origen, "PATH", "Path")

    # Servicios (ImagePath)
    servicios = r"SYSTEM\CurrentControlSet\Services"
    for svc in ctx.subclaves("HKLM", servicios):
        if ctx.queda() < 3:
            ctx.advertir("Se cortó la lectura de servicios por tiempo.")
            break
        imagen = ctx.reg_texto("HKLM", f"{servicios}\\{svc}", "ImagePath")
        if imagen:
            agregar(limpiar_ruta_comando(imagen, ctx.expandir), "servicio", svc, "ImagePath")

    # Accesos directos del Escritorio y del menú Inicio (usuario y común)
    carpetas = ctx.resultados.get("carpetas") or {}
    raices = [carpetas.get("escritorio") or (ntpath.join(ctx.var("USERPROFILE"), "Desktop") if ctx.var("USERPROFILE") else None),
              ntpath.join(ctx.var("PUBLIC"), "Desktop") if ctx.var("PUBLIC") else None,
              ntpath.join(ctx.var("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs") if ctx.var("APPDATA") else None,
              ntpath.join(ctx.var("ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs") if ctx.var("ProgramData") else None]
    leidos = 0
    cortado = False
    for raiz in [r for r in raices if r and os.path.isdir(r)]:
        for actual, dirs, archivos in os.walk(raiz):
            if actual.count("\\") - raiz.count("\\") >= 5:
                dirs[:] = []
            for nombre in archivos:
                if not nombre.lower().endswith(".lnk"):
                    continue
                if leidos >= MAX_ACCESOS_DIRECTOS or ctx.queda() < 2:
                    cortado = True
                    break
                leidos += 1
                try:
                    with open(ntpath.join(actual, nombre), "rb") as f:
                        datos = leer_lnk(f.read(1 << 20))
                except Exception:  # acceso directo dañado o ilegible: se ignora
                    continue
                destino = datos.get("destino") or ""
                if "%" in destino:
                    destino = ctx.expandir(destino) or ""
                agregar(destino, "acceso_directo", nombre[:-4], "destino")
            if cortado:
                break
        if cortado:
            break
    if cortado:
        ctx.advertir(f"Se cortó la lectura de accesos directos (leídos {leidos}) por tiempo o por cantidad.")
    return res


# ====================================================================== cachés
def detectar_caches(ctx: Contexto) -> list[dict]:
    """Cachés del catálogo: ruta, si existe, tamaño (con presupuesto de tiempo), procesos y si es reubicable."""
    catalogo = ctx.fuentes.get("catalogo")
    if catalogo is None:
        catalogo, aviso = cargar_catalogo()
        if aviso:
            ctx.advertir(aviso)
    total = float(ctx.fuentes.get("presupuesto_caches_segundos", 30.0))
    por_cache = float(ctx.fuentes.get("presupuesto_cache_segundos", 5.0))
    fin = time.monotonic() + max(0.0, min(total, ctx.queda() - 1))
    res, sin_medir = [], 0
    for entrada in catalogo:
        c = normalizar_entrada(entrada)
        if not c:
            continue
        real = ctx.expandir(c["ruta"])
        real = normalizar(real) if real else None
        existe = bool(real) and os.path.isdir(real)
        enlazada = existe and es_reparse(real)
        tam, parcial = None, False
        if existe and not enlazada:
            restante = fin - time.monotonic()
            if restante > 0.05:
                medido = ctx.medir(real, min(por_cache, restante))
                tam, completo = medido if isinstance(medido, tuple) else (medido, True)
                parcial = not completo
            else:
                sin_medir += 1
                parcial = True
        res.append({"nombre": c["nombre"], "app": c["app"], "categoria": c["categoria"], "ruta": c["ruta"],
                    "ruta_real": real, "existe": existe, "bytes": tam, "bytes_parcial": parcial,
                    "procesos": c["procesos"], "reubicable_con_junction": c["reubicable_con_junction"],
                    "segura": c["reubicable_con_junction"], "ya_enlazada": enlazada, "nota": c["nota"]})
    if sin_medir:
        ctx.advertir(f"No dio tiempo a medir {sin_medir} cachés; su tamaño queda sin calcular.")
    return res
