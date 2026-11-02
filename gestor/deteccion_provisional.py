"""Detección PROVISIONAL del equipo (se sustituirá por gestor/deteccion.py). Solo lectura.

Misma interfaz que el módulo definitivo: detectar_equipo(fuentes=None) -> perfil y proponer_configuracion(perfil).

Todas las fuentes externas (variables de entorno, volúmenes, registro de Windows) se inyectan en `Fuentes`, de
modo que las pruebas pueden simular cualquier equipo: sin segundo disco, destino USB, otra letra, otra biblioteca
de Steam, un usuario con espacios y tildes, etc. El sistema de archivos sí es real (en las pruebas, carpetas
temporales a las que apuntan las variables de entorno simuladas).
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable

GB = 1024 ** 3
SISTEMAS_JUNCTION = ("NTFS",)
BUSES_EXTERNOS = ("USB", "1394", "SD", "MMC")

PS_VOLUMENES = r"""
$ErrorActionPreference = 'SilentlyContinue'
$r = foreach ($v in Get-Volume | Where-Object { $_.DriveLetter }) {
    $p = Get-Partition -DriveLetter $v.DriveLetter
    $d = if ($p) { Get-Disk -Number $p.DiskNumber } else { $null }
    [pscustomobject]@{
        letra = [string]$v.DriveLetter; sistema_archivos = [string]$v.FileSystem; etiqueta = [string]$v.FileSystemLabel
        total = [long]$v.Size; libre = [long]$v.SizeRemaining; tipo = [string]$v.DriveType
        bus = $(if ($d) { [string]$d.BusType } else { '' }); disco = $(if ($p) { [int]$p.DiskNumber } else { -1 })
    }
}
ConvertTo-Json -InputObject @($r) -Depth 3
"""


def volumenes_reales() -> list[dict]:
    """Volúmenes con letra, sistema de archivos, espacio y bus (USB, SATA, NVMe...) vía PowerShell. Solo lectura."""
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PS_VOLUMENES],
                              capture_output=True, timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)
        datos = json.loads(proc.stdout.decode("utf-8", errors="replace") or "[]")
        return datos if isinstance(datos, list) else [datos]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def _reg_real(raiz: str, clave: str, valor: str):
    try:
        import winreg
        r = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[raiz]
        with winreg.OpenKey(r, clave) as k:
            return winreg.QueryValueEx(k, valor)[0]
    except (OSError, ImportError, KeyError):
        return None


def _subclaves_reales(raiz: str, clave: str) -> list[str]:
    try:
        import winreg
        r = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[raiz]
        res = []
        with winreg.OpenKey(r, clave) as k:
            i = 0
            while True:
                try:
                    res.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
        return res
    except (OSError, ImportError, KeyError):
        return []


def _shell_real(valor: str, defecto: str) -> str:
    v = _reg_real("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders", valor)
    return os.path.expandvars(v) if v else defecto


@dataclass
class Fuentes:
    entorno: dict = field(default_factory=lambda: dict(os.environ))
    volumenes: Callable[[], list[dict]] = volumenes_reales
    registro: Callable[[str, str, str], object] = _reg_real
    subclaves: Callable[[str, str], list[str]] = _subclaves_reales
    carpeta_shell: Callable[[str, str], str] = _shell_real   # User Shell Folders (respeta redirecciones a OneDrive)


class Detector:
    def __init__(self, fuentes: Fuentes | None = None):
        self.f = fuentes or Fuentes()
        self.env = {k.upper(): v for k, v in self.f.entorno.items()}

    # ------------------------------------------------------------------ utilidades
    def var(self, nombre: str) -> str | None:
        return self.env.get(nombre.upper())

    def expandir(self, ruta: str) -> str | None:
        e = re.sub(r"%([^%]+)%", lambda m: self.env.get(m.group(1).upper(), m.group(0)), ruta)
        return None if "%" in e else e.rstrip("\\")

    @staticmethod
    def _norm(ruta: str) -> str:
        return os.path.normpath(ruta.replace("/", "\\")).rstrip("\\") if ruta else ruta

    # ------------------------------------------------------------------ discos
    def discos(self) -> list[dict]:
        sistema = (self.var("SystemDrive") or "C:")[0].upper()
        res = []
        for v in self.f.volumenes() or []:
            letra = str(v.get("letra") or "").upper()[:1]
            if not letra:
                continue
            bus = str(v.get("bus") or "")
            tipo = str(v.get("tipo") or "")
            externo = bus.upper() in BUSES_EXTERNOS or tipo.lower() == "removable"
            fs = str(v.get("sistema_archivos") or "")
            res.append({"letra": letra, "raiz": f"{letra}:\\", "sistema_archivos": fs, "etiqueta": v.get("etiqueta") or "",
                        "total": int(v.get("total") or 0), "libre": int(v.get("libre") or 0), "bus": bus or "desconocido",
                        "tipo": "USB / externo" if externo else "interno", "externo": externo, "es_sistema": letra == sistema,
                        "ntfs": fs.upper() in SISTEMAS_JUNCTION,
                        "nube_virtual": "google" in str(v.get("etiqueta") or "").lower() or fs.upper() in ("GOOGLEDRIVEFS",)})
        return sorted(res, key=lambda d: d["letra"])

    def proponer_destino(self, discos: list[dict]) -> dict:
        sistema = next((d for d in discos if d["es_sistema"]), None)
        candidatos = [d for d in discos if not d["es_sistema"] and not d["nube_virtual"] and d["total"] >= 8 * GB
                      and d["sistema_archivos"].upper() in ("NTFS", "EXFAT", "REFS", "FAT32")]
        advertencias = []
        if candidatos:
            d = max(candidatos, key=lambda x: x["libre"])
            prop = {"modo": "completo", "raiz_destino": d["raiz"], "destino_usb": d["externo"], "permitir_junctions": True,
                    "disco_destino": d["letra"]}
            if d["externo"]:
                prop["permitir_junctions"] = False
                advertencias.append(f"El destino {d['letra']}: es USB/externo: si se desconecta, las carpetas enlazadas con "
                                    "junction dejan de funcionar. La reubicación de cachés queda desactivada por defecto.")
            if not d["ntfs"]:
                prop["permitir_junctions"] = False
                advertencias.append(f"El destino {d['letra']}: no es NTFS ({d['sistema_archivos']}): no admite junctions.")
        else:
            letra = sistema["letra"] if sistema else (self.var("SystemDrive") or "C:")[0]
            prop = {"modo": "solo_analisis", "raiz_destino": f"{letra}:\\Organizado\\", "destino_usb": False,
                    "permitir_junctions": False, "disco_destino": letra}
            advertencias.append("Solo hay un disco: modo «solo análisis y organización». Puedes elegir una carpeta en este "
                                "mismo disco o en uno externo, pero mover dentro del mismo disco no libera espacio.")
        prop["dir_registro"] = os.path.join(prop["raiz_destino"], "Registro_Migracion_DiscoC")
        prop["advertencias"] = advertencias
        return prop

    # ------------------------------------------------------------------ carpetas del usuario
    def carpetas_usuario(self) -> dict:
        perfil = self.var("USERPROFILE") or os.path.expanduser("~")
        leer = self.f.carpeta_shell
        rutas = {
            "perfil": perfil,
            "escritorio": leer("Desktop", os.path.join(perfil, "Desktop")),
            "documentos": leer("Personal", os.path.join(perfil, "Documents")),
            "descargas": leer("{374DE290-123F-4565-9164-39C4925E467B}", os.path.join(perfil, "Downloads")),
            "imagenes": leer("My Pictures", os.path.join(perfil, "Pictures")),
            "musica": leer("My Music", os.path.join(perfil, "Music")),
            "videos": leer("My Video", os.path.join(perfil, "Videos")),
            "localappdata": self.var("LOCALAPPDATA") or os.path.join(perfil, "AppData", "Local"),
            "appdata": self.var("APPDATA") or os.path.join(perfil, "AppData", "Roaming"),
            "raiz_sistema": (self.var("SystemDrive") or "C:") + "\\",
        }
        return {k: self._norm(v) if k != "raiz_sistema" else v for k, v in rutas.items()}

    # ------------------------------------------------------------------ nube
    def nube(self) -> list[dict]:
        res = []

        def agregar(servicio, ruta):
            if ruta and os.path.isdir(ruta):
                ruta = self._norm(ruta)
                if not any(r["ruta"].lower() == ruta.lower() for r in res):
                    res.append({"servicio": servicio, "ruta": ruta})

        for var, nombre in (("OneDrive", "OneDrive"), ("OneDriveConsumer", "OneDrive personal"),
                            ("OneDriveCommercial", "OneDrive de empresa")):
            agregar(nombre, self.var(var))
        for cuenta in self.f.subclaves("HKCU", r"Software\Microsoft\OneDrive\Accounts"):
            agregar("OneDrive", self.f.registro("HKCU", rf"Software\Microsoft\OneDrive\Accounts\{cuenta}", "UserFolder"))
        perfil = self.var("USERPROFILE") or ""
        for base in (self.var("LOCALAPPDATA"), self.var("APPDATA")):
            if base:
                info = os.path.join(base, "Dropbox", "info.json")
                try:
                    datos = json.loads(open(info, encoding="utf-8").read())
                    for tipo, d in datos.items():
                        agregar(f"Dropbox ({tipo})", d.get("path"))
                except (OSError, ValueError, AttributeError):
                    pass
        for nombre, servicio in (("Google Drive", "Google Drive"), ("My Drive", "Google Drive"), ("Mi unidad", "Google Drive"),
                                 ("iCloudDrive", "iCloud"), ("iCloud Drive", "iCloud"), ("iCloudPhotos", "iCloud Fotos"),
                                 ("Dropbox", "Dropbox"), ("Box", "Box"), ("MEGA", "MEGA"), ("pCloud Drive", "pCloud")):
            agregar(servicio, os.path.join(perfil, nombre) if perfil else None)
        for d in self.discos():
            if d["nube_virtual"]:
                agregar("Google Drive (unidad virtual)", d["raiz"])
        return res

    # ------------------------------------------------------------------ bibliotecas de juegos y launchers
    def bibliotecas(self) -> list[dict]:
        res = []

        def agregar(plataforma, ruta):
            if ruta:
                ruta = self._norm(str(ruta))
                if os.path.isdir(ruta) and not any(r["ruta"].lower() == ruta.lower() for r in res):
                    res.append({"plataforma": plataforma, "ruta": ruta})

        pf86 = self.var("ProgramFiles(x86)") or ""
        pdata = self.var("ProgramData") or ""
        # Steam: instalación y todas las bibliotecas de libraryfolders.vdf
        raices = [self.f.registro("HKCU", r"Software\Valve\Steam", "SteamPath"),
                  self.f.registro("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                  os.path.join(pf86, "Steam") if pf86 else None]
        for raiz in [r for r in raices if r]:
            raiz = self._norm(str(raiz))
            if not os.path.isdir(raiz):
                continue
            agregar("Steam", raiz)
            for vdf in (os.path.join(raiz, "steamapps", "libraryfolders.vdf"), os.path.join(raiz, "config", "libraryfolders.vdf")):
                try:
                    texto = open(vdf, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for m in re.finditer(r'"path"\s+"([^"]+)"', texto):
                    agregar("Steam", m.group(1).replace("\\\\", "\\"))
        # Epic Games: manifiestos de juegos instalados y lista del launcher
        for item in glob.glob(os.path.join(pdata, "Epic", "EpicGamesLauncher", "Data", "Manifests", "*.item")) if pdata else []:
            try:
                agregar("Epic Games", json.loads(open(item, encoding="utf-8").read()).get("InstallLocation"))
            except (OSError, ValueError):
                pass
        try:
            dat = json.loads(open(os.path.join(pdata, "Epic", "UnrealEngineLauncher", "LauncherInstalled.dat"), encoding="utf-8").read())
            for e in dat.get("InstallationList", []):
                agregar("Epic Games", e.get("InstallLocation"))
        except (OSError, ValueError, AttributeError):
            pass
        # Xbox / Microsoft Store: .GamingRoot en la raíz de cada disco
        for d in self.discos():
            gr = os.path.join(d["raiz"], ".GamingRoot")
            try:
                datos = open(gr, "rb").read()
                carpeta = datos[8:].decode("utf-16-le", errors="ignore").split("\x00")[0].strip("\\")
                agregar("Xbox", os.path.join(d["raiz"], carpeta or "XboxGames"))
            except OSError:
                agregar("Xbox", os.path.join(d["raiz"], "XboxGames") if os.path.isdir(os.path.join(d["raiz"], "XboxGames")) else None)
        # Battle.net
        agregar("Battle.net", os.path.join(pf86, "Battle.net") if pf86 else None)
        agregar("Battle.net", os.path.join(pdata, "Battle.net") if pdata else None)
        # Riot
        try:
            riot = json.loads(open(os.path.join(pdata, "Riot Games", "RiotClientInstalls.json"), encoding="utf-8").read())
            rutas = [riot.get("rc_default"), riot.get("rc_live")] + list((riot.get("associated_client") or {}).keys())
            for r in rutas:
                if r:
                    r = self._norm(r)
                    agregar("Riot Games", r if os.path.isdir(r) else os.path.dirname(r))
        except (OSError, ValueError, AttributeError):
            pass
        agregar("Riot Games", os.path.join(pdata, "Riot Games") if pdata else None)
        # EA
        agregar("EA", self.f.registro("HKLM", r"SOFTWARE\Electronic Arts\EA Desktop", "InstallLocation"))
        agregar("EA", os.path.join(pdata, "EA Desktop") if pdata else None)
        # Ubisoft
        agregar("Ubisoft", self.f.registro("HKLM", r"SOFTWARE\WOW6432Node\Ubisoft\Launcher", "InstallDir"))
        for sub in self.f.subclaves("HKLM", r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"):
            agregar("Ubisoft", self.f.registro("HKLM", rf"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs\{sub}", "InstallDir"))
        # GOG
        agregar("GOG", self.f.registro("HKLM", r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient\paths", "client"))
        for sub in self.f.subclaves("HKLM", r"SOFTWARE\WOW6432Node\GOG.com\Games"):
            agregar("GOG", self.f.registro("HKLM", rf"SOFTWARE\WOW6432Node\GOG.com\Games\{sub}", "path"))
        return res

    # ------------------------------------------------------------------ cachés del catálogo
    def caches(self, catalogo: list[dict], permitir_junctions: bool, medir=None) -> list[dict]:
        res = []
        for c in catalogo:
            ruta = self.expandir(c["ruta"])
            if not ruta or not os.path.isdir(ruta):
                continue
            enlazada = bool(getattr(os.stat(ruta, follow_symlinks=False), "st_file_attributes", 0) & 0x400)
            tam = None if enlazada or medir is None else medir(ruta)
            res.append({"nombre": c["nombre"], "app": c.get("app", ""), "ruta": c["ruta"], "ruta_real": ruta, "bytes": tam,
                        "segura": bool(c.get("segura")), "procesos": c.get("procesos", []), "nota": c.get("nota"),
                        "ya_enlazada": enlazada,
                        "habilitada": bool(c.get("segura")) and permitir_junctions and not enlazada})
        return res

    # ------------------------------------------------------------------ todo junto
    def detectar(self, catalogo: list[dict], medir=None, referencias=None) -> dict:
        discos = self.discos()
        propuesta = self.proponer_destino(discos)
        carpetas = self.carpetas_usuario()
        nube = self.nube()
        bibliotecas = self.bibliotecas()
        avisos_carpetas = []
        for k in ("escritorio", "documentos", "descargas", "imagenes"):
            for n in nube:
                if carpetas[k].lower().startswith(n["ruta"].lower() + "\\") or carpetas[k].lower() == n["ruta"].lower():
                    avisos_carpetas.append(f"{k.capitalize()} está dentro de {n['servicio']}: queda intocable (sincronizado).")
        return {"discos": discos, "propuesta": propuesta, "carpetas": carpetas, "nube": nube, "bibliotecas": bibliotecas,
                "caches": self.caches(catalogo, propuesta["permitir_junctions"], medir), "avisos_carpetas": avisos_carpetas,
                "referencias": referencias}


# ---------------------------------------------------------------------- interfaz pública (igual que gestor/deteccion.py)
def _catalogo() -> list[dict]:
    from .config import DIR_CONFIG, leer_json
    datos = leer_json(DIR_CONFIG / "catalogo_caches.json")
    return datos.get("cachés") or datos.get("caches") or []


def detectar_equipo(fuentes: Fuentes | None = None, medir_caches: bool = True) -> dict:
    """Perfil del equipo serializable a JSON (solo lectura)."""
    from .escaner import medir
    det = Detector(fuentes)
    perfil = det.detectar(_catalogo(), (lambda r: medir(r, 5.0).bytes) if medir_caches else None)
    perfil["version"] = 1
    perfil.pop("propuesta", None)
    return perfil


def proponer_configuracion(perfil: dict) -> dict:
    """Propuesta de origen, destino, intocables y cachés a partir del perfil."""
    sistema = perfil["carpetas"]["raiz_sistema"][:2]
    prop = Detector(Fuentes(entorno={"SystemDrive": sistema}, volumenes=lambda: [])).proponer_destino(perfil["discos"])
    prop["origen"] = perfil["carpetas"]["raiz_sistema"]
    prop["intocables_detectados"] = ([{"ruta": n["ruta"], "motivo": f"sincronizado con {n['servicio']}"} for n in perfil["nube"]] +
                                     [{"ruta": b["ruta"], "motivo": f"biblioteca de juegos ({b['plataforma']})"} for b in perfil["bibliotecas"]])
    prop["caches"] = [{"nombre": c["nombre"], "ruta": c["ruta"], "procesos": c.get("procesos", []),
                       "habilitada": bool(c["segura"] and prop["permitir_junctions"] and not c["ya_enlazada"])} for c in perfil["caches"]]
    return prop
