"""Detección del equipo y propuesta de configuración, con detectores y registro simulados.

Ningún caso depende del equipo real salvo la prueba de humo final, que solo comprueba la forma del resultado.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pytest

from conftest import RAIZ
from gestor.config import cargar_config, validar
from gestor.deteccion import CATALOGO_CACHES, cargar_catalogo, detectar_equipo, proponer_configuracion
from gestor.deteccion.contexto import Contexto, normalizar
from gestor.deteccion.detectores import detectar_bibliotecas, detectar_referencias, volumenes_powershell
from gestor.deteccion.formatos import (ErrorVDF, bibliotecas_steam, leer_gaming_root, leer_lnk, leer_vdf,
                                       limpiar_ruta_comando)

FIXTURES = Path(__file__).parent / "fixtures" / "deteccion"
GB = 1024 ** 3
USF = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
UNINSTALL = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
CLAVES_PERFIL = {"version", "generado", "discos", "nube", "carpetas", "carpetas_info", "avisos_carpetas", "bibliotecas",
                 "rutas_referenciadas", "caches", "equipo", "resumen", "advertencias", "tiempos_ms", "duracion_ms"}


# ---------------------------------------------------------------------- equipo simulado
def registro_simulado(datos: dict) -> dict:
    """Registro de mentira: {'HKCU\\ruta\\clave': {'Valor': dato} | Exception}."""
    claves = {k.lower(): v for k, v in datos.items()}

    def nodo(raiz, clave):
        v = claves.get(f"{raiz}\\{clave}".lower())
        if isinstance(v, Exception):
            raise v
        return v if isinstance(v, dict) else None

    def registro(raiz, clave, valor):
        v = nodo(raiz, clave) or {}
        return next((x for k, x in v.items() if k.lower() == valor.lower()), None)

    def subclaves(raiz, clave):
        prefijo = f"{raiz}\\{clave}\\".lower()
        res = []
        for k in datos:
            if k.lower().startswith(prefijo):
                sub = k[len(prefijo):].split("\\")[0]
                if sub not in res:
                    res.append(sub)
        return res

    def valores(raiz, clave):
        return dict(nodo(raiz, clave) or {})

    return {"registro": registro, "subclaves": subclaves, "valores": valores}


def volumen(letra, libre_gb, total_gb=1000, fs="NTFS", bus="SATA", tipo="Fixed", etiqueta="Datos"):
    return {"letra": letra, "sistema_archivos": fs, "etiqueta": etiqueta, "total": total_gb * GB, "libre": libre_gb * GB,
            "tipo_unidad": tipo, "bus": bus}


SISTEMA = volumen("C", 50, 500, etiqueta="Sistema")


class Equipo:
    """Perfil de usuario y carpetas del sistema dentro de tmp_path, con variables de entorno que apuntan ahí."""

    def __init__(self, base: Path, usuario: str = "Usuario Ejemplo"):
        self.base = base
        self.perfil = base / "Users" / usuario
        self.local = self.perfil / "AppData" / "Local"
        self.roaming = self.perfil / "AppData" / "Roaming"
        self.programdata = base / "ProgramData"
        self.pf86 = base / "Program Files (x86)"
        self.pf = base / "Program Files"
        self.windows = base / "Windows"
        for d in (self.local, self.roaming, self.programdata, self.pf86, self.pf, self.windows):
            d.mkdir(parents=True, exist_ok=True)
        self.entorno = {
            "USERPROFILE": str(self.perfil), "LOCALAPPDATA": str(self.local), "APPDATA": str(self.roaming),
            "ProgramData": str(self.programdata), "ProgramFiles": str(self.pf), "ProgramW6432": str(self.pf),
            "ProgramFiles(x86)": str(self.pf86), "SystemDrive": "C:", "SystemRoot": str(self.windows),
            "PUBLIC": str(base / "Users" / "Public"),
        }
        self.registro: dict = {}
        self.catalogo = [
            {"nombre": "Ejemplo_Cache", "app": "App de ejemplo", "ruta": "%LOCALAPPDATA%\\EjemploCache",
             "segura": True, "procesos": ["ejemplo"]},
            {"nombre": "uv_cache", "app": "uv", "ruta": "%LOCALAPPDATA%\\uv", "reubicable_con_junction": False,
             "procesos": [], "nota": "enlaces duros"},
            {"nombre": "No_Existe", "app": "Otra", "ruta": "%LOCALAPPDATA%\\NoExiste", "segura": True},
        ]

    def fuentes(self, volumenes, **extra) -> dict:
        f = {"entorno": dict(self.entorno), "volumenes": lambda: list(volumenes), "catalogo": self.catalogo,
             **registro_simulado(self.registro)}
        f.update(extra)
        return f


@pytest.fixture
def equipo(tmp_path):
    e = Equipo(tmp_path)
    (e.local / "EjemploCache" / "sub").mkdir(parents=True)
    (e.local / "EjemploCache" / "sub" / "a.bin").write_bytes(b"x" * 1000)
    (e.local / "uv").mkdir()
    return e


def detectar_y_proponer(fuentes):
    perfil = detectar_equipo(fuentes)
    json.dumps(perfil, ensure_ascii=False)  # siempre serializable
    propuesta = proponer_configuracion(perfil)
    json.dumps(propuesta, ensure_ascii=False)
    assert validar(propuesta["config"]) == [], validar(propuesta["config"])
    return perfil, propuesta


# ---------------------------------------------------------------------- discos y destino
def test_sin_disco_d_destino_e(equipo):
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600)]))
    assert [d["letra"] for d in perfil["discos"]] == ["C", "E"]
    c, e = perfil["discos"]
    assert c["es_sistema"] and not e["es_sistema"] and e["ntfs"] and e["tipo"] == "interno" and e["raiz"] == "E:\\"
    assert p["raiz_destino"] == "E:\\" and p["origen"] == "C:\\" and p["modo"] == "completo"
    assert p["config"]["dir_registro"] == "E:\\Registro_Migracion_DiscoC"
    assert p["permitir_junctions"] is True and p["destino_usb"] is False
    caches = {c["nombre"]: c for c in p["caches"]}
    assert caches == {"Ejemplo_Cache": {"nombre": "Ejemplo_Cache", "ruta": "%LOCALAPPDATA%\\EjemploCache",
                                        "procesos": ["ejemplo"], "habilitada": True}}
    assert [c["nombre"] for c in p["caches_no_reubicables"]] == ["uv_cache"]
    assert not perfil["advertencias"]


def test_destino_es_el_disco_no_sistema_con_mas_espacio(equipo):
    vols = [SISTEMA, volumen("D", 200), volumen("E", 700), volumen("F", 900, tipo="Network", bus="")]
    perfil, p = detectar_y_proponer(equipo.fuentes(vols))
    assert p["disco_destino"] == "E" and p["raiz_destino"] == "E:\\" and p["alternativas"] == ["D"]
    assert next(d for d in perfil["discos"] if d["letra"] == "F")["tipo"] == "red"


def test_destino_usb(equipo):
    _, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 800, bus="USB"), volumen("D", 100)]))
    assert p["disco_destino"] == "E" and p["destino_usb"] is True and p["permitir_junctions"] is False
    assert all(not c["habilitada"] for c in p["caches"]) and p["caches"]
    assert any("USB" in a for a in p["advertencias"])
    assert any("disco interno" in a and "D:" in a for a in p["advertencias"])


def test_extraible_sin_bus_tambien_es_externo(equipo):
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 100, bus="", tipo="Removable")]))
    assert perfil["discos"][1]["tipo"] == "extraible" and perfil["discos"][1]["externo"]
    assert p["destino_usb"] is True and p["permitir_junctions"] is False


def test_un_solo_disco(equipo):
    vols = [SISTEMA, volumen("G", 0, 1, fs="FAT32", tipo="Fixed", etiqueta="Google Drive"), volumen("R", 0, 0, fs="", tipo="CD-ROM", bus="")]
    perfil, p = detectar_y_proponer(equipo.fuentes(vols))
    assert p["modo"] == "solo_analisis" and p["raiz_destino"] == "C:\\Organizado\\" and p["disco_destino"] == "C"
    assert p["permitir_junctions"] is False and all(not c["habilitada"] for c in p["caches"])
    aviso = next(a for a in p["advertencias"] if "un disco" in a)
    assert "no libera espacio" in aviso and "G:" in aviso and "R:" in aviso
    # La unidad virtual de Google Drive se detecta como nube.
    assert any(n["ruta"] == "G:\\" and n["proveedor"] == "Google Drive" for n in perfil["nube"])
    # El modo de un disco es válido para la configuración por capas.
    assert cargar_config(Path(equipo.base) / "no.json", extra=p["config"])["configurado"] is True


def test_solo_el_disco_del_sistema(equipo):
    _, p = detectar_y_proponer(equipo.fuentes([SISTEMA]))
    assert p["modo"] == "solo_analisis" and p["alternativas"] == [] and p["detalle_destino"] is None


def test_destino_no_ntfs(equipo):
    _, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 300, fs="exFAT")]))
    assert p["raiz_destino"] == "E:\\" and p["permitir_junctions"] is False and p["destino_usb"] is False
    assert any("no NTFS" in a and "exFAT" in a for a in p["advertencias"])
    assert all(not c["habilitada"] for c in p["caches"])


def test_poco_espacio_en_destino(equipo):
    _, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 2, 64)]))
    assert any("poco espacio" in a for a in p["advertencias"])


# ---------------------------------------------------------------------- nube y carpetas del usuario
def test_sin_onedrive(equipo):
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600)]))
    assert perfil["nube"] == [] and perfil["avisos_carpetas"] == []
    assert not any("sincronizado" in i["motivo"] for i in p["intocables_detectados"])
    assert perfil["carpetas"]["descargas"] == str(equipo.perfil / "Downloads")
    assert perfil["carpetas_info"]["descargas"]["redirigida"] is False


def test_onedrive_de_empresa_con_documentos_redirigidos(equipo):
    empresa = equipo.perfil / "OneDrive - Empresa Ejemplo"
    (empresa / "Documentos").mkdir(parents=True)
    equipo.entorno["OneDriveCommercial"] = str(empresa)
    equipo.entorno["OneDrive"] = str(empresa)
    equipo.registro[r"HKCU\Software\Microsoft\OneDrive\Accounts\Business1"] = {"UserFolder": str(empresa)}
    equipo.registro[USF] = {"Personal": "%OneDriveCommercial%\\Documentos", "Desktop": "%USERPROFILE%\\Desktop"}
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600)]))
    assert len(perfil["nube"]) == 1
    n = perfil["nube"][0]
    assert n["tipo"] == "empresa" and n["servicio"] == "OneDrive de empresa" and n["ruta"] == str(empresa)
    assert perfil["carpetas"]["documentos"] == str(empresa / "Documentos")
    info = perfil["carpetas_info"]["documentos"]
    assert info["redirigida"] and info["en_nube"] == "OneDrive de empresa"
    assert perfil["carpetas_info"]["escritorio"]["redirigida"] is False
    assert any("Documentos" in a and "OneDrive de empresa" in a for a in p["advertencias"])
    assert {"ruta": str(empresa), "motivo": "sincronizado con OneDrive de empresa"} in p["intocables_detectados"]


def test_onedrive_personal_dropbox_e_icloud(equipo):
    personal = equipo.perfil / "OneDrive"
    personal.mkdir(parents=True)
    equipo.registro[r"HKCU\Software\Microsoft\OneDrive\Accounts\Personal"] = {"UserFolder": str(personal)}
    for d in ("Dropbox", "Dropbox (Empresa Ejemplo)", "iCloudDrive"):
        (equipo.perfil / d).mkdir()
    texto = (FIXTURES / "dropbox_info.json").read_text(encoding="utf-8")
    (equipo.local / "Dropbox").mkdir()
    (equipo.local / "Dropbox" / "info.json").write_text(texto.replace("{PERFIL}", str(equipo.perfil).replace("\\", "\\\\")),
                                                        encoding="utf-8")
    perfil, _ = detectar_y_proponer(equipo.fuentes([SISTEMA]))
    servicios = {n["servicio"]: n["ruta"] for n in perfil["nube"]}
    assert servicios["OneDrive personal"] == str(personal)
    assert servicios["Dropbox"] == str(equipo.perfil / "Dropbox")
    assert servicios["Dropbox de empresa"] == str(equipo.perfil / "Dropbox (Empresa Ejemplo)")
    assert servicios["iCloud"] == str(equipo.perfil / "iCloudDrive")


def test_perfil_con_espacios_y_tildes(tmp_path):
    usuario = "Usuario Ejemplo Ñúñez"
    e = Equipo(tmp_path, usuario)
    (e.local / "EjemploCache").mkdir(parents=True)
    (e.local / "EjemploCache" / "datos ñ.bin").write_bytes(b"y" * 2048)
    e.registro[USF] = {"{374DE290-123F-4565-9164-39C4925E467B}": "%USERPROFILE%\\Descargas Ñ",
                       "My Pictures": "%USERPROFILE%\\Imágenes"}
    perfil, p = detectar_y_proponer(e.fuentes([SISTEMA, volumen("E", 600)]))
    assert perfil["carpetas"]["perfil"] == str(e.perfil)
    assert perfil["carpetas"]["descargas"] == str(e.perfil / "Descargas Ñ")
    assert perfil["carpetas"]["imagenes"] == str(e.perfil / "Imágenes")
    cache = next(c for c in perfil["caches"] if c["nombre"] == "Ejemplo_Cache")
    assert cache["existe"] and cache["bytes"] == 2048 and cache["ruta_real"] == str(e.local / "EjemploCache")
    # Ida y vuelta por JSON sin perder caracteres, con y sin escapes.
    for ascii_ in (True, False):
        assert json.loads(json.dumps(perfil, ensure_ascii=ascii_)) == perfil


def test_ruta_textual_de_usuario_con_tildes():
    """Sin sistema de archivos: solo expansión de variables con un perfil con espacios y tildes."""
    perfil = r"C:\Users\Usuario Ejemplo Ñúñez"
    f = {"entorno": {"USERPROFILE": perfil, "SystemDrive": "C:"}, "volumenes": lambda: [SISTEMA], "catalogo": [],
         **registro_simulado({USF: {"Desktop": "%USERPROFILE%\\Escritorio", "Personal": "%userprofile%\\Documentos"}})}
    perfil_eq = detectar_equipo(f)
    assert perfil_eq["carpetas"]["escritorio"] == perfil + "\\Escritorio"
    assert perfil_eq["carpetas"]["documentos"] == perfil + "\\Documentos"
    assert perfil_eq["carpetas"]["descargas"] == perfil + "\\Downloads"


# ---------------------------------------------------------------------- bibliotecas de juegos
def test_steam_con_biblioteca_en_otra_unidad(equipo):
    steam = equipo.pf86 / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    shutil.copy(FIXTURES / "libraryfolders.vdf", steam / "steamapps" / "libraryfolders.vdf")
    # Steam guarda SteamPath con barras normales y en minúsculas.
    equipo.registro[r"HKCU\Software\Valve\Steam"] = {"SteamPath": str(steam).replace("\\", "/").lower()}
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600)]))
    steam_entradas = [b for b in perfil["bibliotecas"] if b["plataforma"] == "Steam"]
    assert steam_entradas[0]["tipo"] == "launcher" and steam_entradas[0]["ruta"].lower() == str(steam).lower()
    lib = next(b for b in steam_entradas if b["ruta"] == r"E:\EjemploSteamLibrary")
    assert lib["tipo"] == "biblioteca" and lib["origen"] == "libraryfolders.vdf"
    rutas = {i["ruta"]: i["motivo"] for i in p["intocables_detectados"]}
    assert rutas[r"E:\EjemploSteamLibrary"] == "biblioteca de juegos (Steam)"


def test_vdf_formatos_y_errores():
    assert bibliotecas_steam(leer_vdf((FIXTURES / "libraryfolders.vdf").read_text(encoding="utf-8"))) == [
        r"C:\Program Files (x86)\Steam", r"E:\EjemploSteamLibrary"]
    assert bibliotecas_steam(leer_vdf((FIXTURES / "libraryfolders_antiguo.vdf").read_text(encoding="utf-8"))) == [
        r"F:\EjemploJuegosAntiguos"]
    datos = leer_vdf('"a" { "b" "con \\"comillas\\"" sin_comillas valor [$WIN32] }')
    assert datos == {"a": {"b": 'con "comillas"', "sin_comillas": "valor"}}
    for roto in ('"a" { "b" "c"', '"a" "b" }', '"a" { "b" "sin cerrar'):
        with pytest.raises(ErrorVDF):
            leer_vdf(roto)


def test_vdf_roto_queda_en_advertencias(equipo):
    steam = equipo.pf86 / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text('"libraryfolders" { "0" {', encoding="utf-8")
    perfil = detectar_equipo(equipo.fuentes([SISTEMA]))
    assert [b["tipo"] for b in perfil["bibliotecas"] if b["plataforma"] == "Steam"] == ["launcher"]
    assert any("libraryfolders.vdf" in a for a in perfil["advertencias"])


def test_epic_con_manifiesto_de_ejemplo(equipo):
    manifiestos = equipo.programdata / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    manifiestos.mkdir(parents=True)
    shutil.copy(FIXTURES / "epic_juego_ejemplo.item", manifiestos / "A1B2C3D4.item")
    (manifiestos / "roto.item").write_text("{ no es json", encoding="utf-8")
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600)]))
    juego = next(b for b in perfil["bibliotecas"] if b["plataforma"] == "Epic Games")
    assert juego == {"plataforma": "Epic Games", "tipo": "juego", "ruta": r"E:\Ejemplo Epic\JuegoEjemplo",
                     "nombre": "Juego Ejemplo", "origen": "manifiesto de Epic", "existe": False}
    assert any("roto.item" in a for a in perfil["advertencias"])
    assert {"ruta": r"E:\Ejemplo Epic\JuegoEjemplo", "motivo": "juego instalado (Epic Games)"} in p["intocables_detectados"]


def test_xbox_gamingroot_y_otros_launchers(tmp_path):
    unidad = tmp_path / "unidad"
    (unidad / "JuegosXbox").mkdir(parents=True)
    contenido = b"RGBX" + struct.pack("<I", 1) + "JuegosXbox".encode("utf-16-le") + b"\x00\x00"
    (unidad / ".GamingRoot").write_bytes(contenido)
    assert leer_gaming_root(contenido) == ["JuegosXbox"] and leer_gaming_root(b"otra cosa") == []
    ubi = tmp_path / "Ubisoft Launcher"
    (ubi / "games").mkdir(parents=True)
    gog = tmp_path / "GOG Galaxy"
    gog.mkdir()
    riot = tmp_path / "ProgramData" / "Riot Games"
    (riot / "Metadatos" / "producto_ejemplo").mkdir(parents=True)
    (riot / "Metadatos" / "producto_ejemplo" / "producto_ejemplo.live.product_settings.yaml").write_text(
        'product_install_full_path: "E:/Ejemplo Riot/Juego"\n', encoding="utf-8")
    registro = registro_simulado({
        r"HKLM\SOFTWARE\WOW6432Node\Ubisoft\Launcher": {"InstallDir": str(ubi)},
        r"HKLM\SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs\1": {"InstallDir": r"E:\Ejemplo Ubisoft\Juego"},
        r"HKLM\SOFTWARE\WOW6432Node\GOG.com\GalaxyClient\paths": {"client": str(gog)},
        r"HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\1": {"path": r"E:\Ejemplo GOG\Juego", "gameName": "Juego GOG"},
        UNINSTALL + r"\JuegoBlizzard": {"DisplayName": "Juego de ejemplo", "Publisher": "Blizzard Entertainment",
                                        "InstallLocation": str(tmp_path / "unidad")},
    })
    ctx = Contexto({"entorno": {"ProgramData": str(tmp_path / "ProgramData")}, **registro})
    ctx.resultados["discos"] = [{"letra": "E", "raiz": str(unidad), "total": 10 * GB, "tipo": "interno"}]
    res = {(b["plataforma"], b["tipo"], b["ruta"]) for b in detectar_bibliotecas(ctx)}
    assert ("Xbox", "biblioteca", str(unidad / "JuegosXbox")) in res
    assert ("Ubisoft", "launcher", str(ubi)) in res and ("Ubisoft", "biblioteca", str(ubi / "games")) in res
    assert ("Ubisoft", "juego", r"E:\Ejemplo Ubisoft\Juego") in res
    assert ("GOG", "launcher", str(gog)) in res and ("GOG", "juego", r"E:\Ejemplo GOG\Juego") in res
    assert ("Riot Games", "juego", r"E:\Ejemplo Riot\Juego") in res
    assert ("Battle.net", "instalacion", str(unidad)) in res


# ---------------------------------------------------------------------- rutas referenciadas
def crear_lnk(destino: str) -> bytes:
    """Acceso directo mínimo con LinkInfo y ruta local en Unicode."""
    cab = bytearray(0x4C)
    struct.pack_into("<I", cab, 0, 0x4C)
    struct.pack_into("<I", cab, 0x14, 0x2 | 0x80)
    vol = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\x00"
    ansi, sufijo_a = b"?\x00", b"\x00"
    uni, sufijo_u = destino.encode("utf-16-le") + b"\x00\x00", b"\x00\x00"
    off_vol = 0x24
    off_local = off_vol + len(vol)
    off_sufijo = off_local + len(ansi)
    off_local_u = off_sufijo + len(sufijo_a)
    off_sufijo_u = off_local_u + len(uni)
    total = off_sufijo_u + len(sufijo_u)
    info = struct.pack("<9I", total, 0x24, 1, off_vol, off_local, 0, off_sufijo, off_local_u, off_sufijo_u)
    return bytes(cab) + info + vol + ansi + sufijo_a + uni + sufijo_u + struct.pack("<I", 0)


def crear_lnk_con_variables(destino: str) -> bytes:
    """Acceso directo de instalador: sin LinkInfo, solo con el bloque de variables de entorno."""
    cab = bytearray(0x4C)
    struct.pack_into("<I", cab, 0, 0x4C)
    struct.pack_into("<I", cab, 0x14, 0x80 | 0x200)
    bloque = struct.pack("<II", 0x314, 0xA0000001) + destino.encode("latin-1", "replace").ljust(260, b"\x00")
    bloque += destino.encode("utf-16-le").ljust(520, b"\x00")
    return bytes(cab) + bloque + struct.pack("<I", 0)


def test_lector_de_accesos_directos():
    assert leer_lnk(crear_lnk(r"C:\Herramientas Ñ\app.exe"))["destino"] == r"C:\Herramientas Ñ\app.exe"
    assert leer_lnk(crear_lnk_con_variables(r"%LOCALAPPDATA%\Programs\Ejemplo\app.exe"))["destino"] == \
        r"%LOCALAPPDATA%\Programs\Ejemplo\app.exe"
    with pytest.raises(ValueError):
        leer_lnk(b"no es un acceso directo")


def test_limpieza_de_rutas_de_comando():
    exp = lambda s: s.replace("%SystemRoot%", r"C:\Windows").replace("%ProgramFiles%", r"C:\Program Files")  # noqa: E731
    casos = {
        r'"C:\Program Files\Ejemplo\desinstalar.exe" /quiet': r"C:\Program Files\Ejemplo\desinstalar.exe",
        r"C:\Program Files\Ejemplo App\app.exe,0": r"C:\Program Files\Ejemplo App\app.exe",
        r"C:\Program Files\Ejemplo App\unins000.exe /SILENT": r"C:\Program Files\Ejemplo App\unins000.exe",
        r"\SystemRoot\System32\drivers\ejemplo.sys": r"C:\Windows\System32\drivers\ejemplo.sys",
        r"system32\drivers\ejemplo.sys": r"C:\Windows\system32\drivers\ejemplo.sys",
        r"\??\C:\Ejemplo\driver.sys": r"C:\Ejemplo\driver.sys",
        r"%ProgramFiles%\Ejemplo\svc.exe -k red": r"C:\Program Files\Ejemplo\svc.exe",
        "MsiExec.exe /X{A1B2C3D4-AAAA-BBBB-CCCC-DDDDEEEEFFFF}": None,
        r"C:\Windows\System32\msiexec.exe /x algo": None,
        '"",,': None,
        "": None,
    }
    for entrada, esperado in casos.items():
        assert limpiar_ruta_comando(entrada, exp) == esperado, entrada
    assert limpiar_ruta_comando(12345, exp) is None


def test_clave_uninstall_corrupta_y_otras_referencias(equipo):
    app = equipo.local / "Programs" / "Ejemplo App"
    app.mkdir(parents=True)
    (app / "app.exe").write_bytes(b"MZ")
    portable = equipo.perfil / "Herramientas Ñ"
    portable.mkdir()
    (portable / "herramienta.exe").write_bytes(b"MZ")
    escritorio = equipo.perfil / "Desktop"
    escritorio.mkdir()
    (escritorio / "Herramienta.lnk").write_bytes(crear_lnk(str(portable / "herramienta.exe")))
    (escritorio / "Roto.lnk").write_bytes(b"basura")
    bin_usuario = equipo.perfil / "bin"
    bin_usuario.mkdir()
    servicio = equipo.base / "Servicios Ejemplo"
    servicio.mkdir()
    (servicio / "svc.exe").write_bytes(b"MZ")
    equipo.registro.update({
        UNINSTALL + r"\EjemploApp": {"DisplayName": "Ejemplo App", "InstallLocation": str(app),
                                     "DisplayIcon": f'"{app}\\app.exe",0', "UninstallString": f'"{app}\\app.exe" /uninstall'},
        UNINSTALL + r"\Rota": OSError("clave dañada"),
        UNINSTALL + r"\TiposRaros": {"DisplayName": "Tipos raros", "InstallLocation": 42, "DisplayIcon": b"\x00\x01"},
        UNINSTALL + r"\Msi": {"DisplayName": "Paquete MSI", "UninstallString": "MsiExec.exe /X{A1B2C3D4-AAAA-BBBB}"},
        r"HKCU\Environment": {"Path": "%USERPROFILE%\\bin;;C:\\;" + str(equipo.windows / "System32")},
        r"HKLM\SYSTEM\CurrentControlSet\Services\EjemploSvc": {"ImagePath": f'"{servicio}\\svc.exe" -k red'},
        r"HKLM\SYSTEM\CurrentControlSet\Services\Controlador": {"ImagePath": r"\SystemRoot\System32\drivers\x.sys"},
    })
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600)]))
    refs = {(r["origen"], r["ruta"]) for r in perfil["rutas_referenciadas"]}
    assert ("programa", str(app)) in refs and ("programa", str(app / "app.exe")) in refs
    assert ("acceso_directo", str(portable / "herramienta.exe")) in refs
    assert ("path_usuario", str(bin_usuario)) in refs and ("path_usuario", "C:\\") in refs
    assert ("servicio", str(servicio / "svc.exe")) in refs
    assert not any("System32" in r for _, r in refs)  # lo de Windows ya está protegido
    assert any("Se omitieron 2 entradas" in a for a in perfil["advertencias"])
    intocables = {i["ruta"] for i in p["intocables_detectados"]}
    assert str(app) in intocables and str(servicio) in intocables and str(bin_usuario) in intocables
    # Un acceso directo protege su destino, no la carpeta que lo contiene; nunca la raíz de una unidad.
    assert str(portable / "herramienta.exe") in intocables and "C:\\" not in intocables
    assert str(equipo.perfil) not in intocables


def test_intocables_sin_rutas_demasiado_amplias():
    perfil = {
        "discos": [], "nube": [], "bibliotecas": [{"plataforma": "Steam", "tipo": "biblioteca", "ruta": r"E:\EjemploJuegos"}],
        "carpetas": {"perfil": r"C:\Users\Usuario", "descargas": r"C:\Users\Usuario\Downloads", "raiz_sistema": "C:\\"},
        "equipo": {"unidad_sistema": "C:", "rutas_sistema": {"ProgramFiles": r"C:\Program Files"}},
        "rutas_referenciadas": [
            {"ruta": r"C:\Users\Usuario\Downloads\instalador.exe", "origen": "programa", "existe": True, "es_carpeta": False},
            {"ruta": r"C:\Users\Usuario\Downloads", "origen": "acceso_directo", "existe": True, "es_carpeta": True},
            {"ruta": r"C:\Program Files\Ejemplo\app.exe", "origen": "programa", "existe": True, "es_carpeta": False},
            {"ruta": r"E:\EjemploJuegos\juego\juego.exe", "origen": "acceso_directo", "existe": True, "es_carpeta": False},
            {"ruta": r"C:\Herramientas\no_existe.exe", "origen": "programa", "existe": False, "es_carpeta": None},
        ],
    }
    p = proponer_configuracion(perfil)
    assert [i["ruta"] for i in p["intocables_detectados"]] == [r"E:\EjemploJuegos",
                                                               r"C:\Users\Usuario\Downloads\instalador.exe"]
    assert p["modo"] == "solo_analisis"  # sin discos detectados


# ---------------------------------------------------------------------- tolerancia a fallos
def test_detector_que_lanza_excepcion_termina_en_advertencias(equipo):
    def roto(ctx):
        raise RuntimeError("fallo de prueba")

    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600)], nube=roto))
    assert perfil["nube"] == []
    assert any("carpetas de nube" in a and "RuntimeError: fallo de prueba" in a for a in perfil["advertencias"])
    assert p["raiz_destino"] == "E:\\" and perfil["caches"]  # el resto sigue funcionando
    assert any(a.startswith("Detección: ") for a in p["advertencias"])


def test_detector_lento_y_resultado_invalido(equipo):
    def lento(ctx):
        time.sleep(3)
        return []

    t0 = time.monotonic()
    perfil = detectar_equipo(equipo.fuentes([SISTEMA], bibliotecas=lento, caches=lambda ctx: "no es una lista",
                                            limites={"bibliotecas": 0.3}))
    assert time.monotonic() - t0 < 2.5
    assert perfil["bibliotecas"] == [] and perfil["caches"] == []
    assert any("bibliotecas de juegos superó" in a for a in perfil["advertencias"])
    assert any("resultado no válido" in a for a in perfil["advertencias"])


def test_fallo_de_discos_y_carpetas(equipo):
    def roto(ctx):
        raise OSError("sin acceso")

    perfil, p = detectar_y_proponer(equipo.fuentes([], discos=roto, carpetas=roto))
    assert perfil["discos"] == [] and perfil["carpetas"]["raiz_sistema"] == "C:\\"  # carpetas por defecto
    assert p["modo"] == "solo_analisis" and p["raiz_destino"] == "C:\\Organizado\\"


def test_presupuesto_total_agotado(equipo):
    perfil = detectar_equipo(equipo.fuentes([SISTEMA], presupuesto_segundos=0))
    assert set(perfil) >= CLAVES_PERFIL and perfil["discos"] == []
    assert any("tiempo total" in a for a in perfil["advertencias"])


# ---------------------------------------------------------------------- cachés y catálogo
def test_catalogo_de_caches():
    catalogo, aviso = cargar_catalogo()
    assert aviso is None
    por_nombre = {c["nombre"]: c for c in catalogo}
    for nombre in ("Chrome_Cache", "Edge_Cache", "Firefox_Cache", "Brave_Cache", "Opera_Cache", "NVIDIA_DXCache",
                   "NVIDIA_GLCache", "AMD_DxCache", "npm_cache", "pip_cache", "NuGet_v3_cache", "Gradle_caches",
                   "Maven_repository", "JetBrains_caches", "VSCode_Cache", "Discord_Cache", "Spotify_Storage",
                   "Teams_nuevo", "Temp", "uv_cache"):
        assert nombre in por_nombre, nombre
        c = por_nombre[nombre]
        assert c["ruta"].startswith("%") and isinstance(c["procesos"], list)
    for no in ("Teams_nuevo", "uv_cache", "Temp", "Chrome_Cache"):
        assert por_nombre[no]["reubicable_con_junction"] is False
    assert por_nombre["npm_cache"]["reubicable_con_junction"] is True
    assert len({c["nombre"].lower() for c in catalogo}) == len(catalogo)
    assert len(CATALOGO_CACHES) >= 20


def test_catalogo_ilegible_da_un_aviso(tmp_path):
    roto = tmp_path / "catalogo.json"
    roto.write_text("{ roto", encoding="utf-8")
    catalogo, aviso = cargar_catalogo(roto)
    assert catalogo == [] and "No se pudo leer" in aviso
    assert len(CATALOGO_CACHES) >= 30   # el archivo del repositorio es la única fuente
    propio = tmp_path / "propio.json"
    propio.write_text(json.dumps({"cachés": [{"nombre": "npm_cache", "ruta": "%TEMP%\\otra", "segura": False}]}),
                      encoding="utf-8")
    catalogo, _ = cargar_catalogo(propio)
    npm = next(c for c in catalogo if c["nombre"] == "npm_cache")
    assert npm["ruta"] == "%TEMP%\\otra" and npm["reubicable_con_junction"] is False  # el archivo manda


def test_medicion_parcial_y_cache_ya_enlazada(equipo):
    destino = equipo.base / "destino_cache"
    destino.mkdir()
    enlace = equipo.local / "Enlazada"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(enlace), str(destino)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("no se pudo crear una junction de prueba")
    equipo.catalogo.append({"nombre": "Ya_Enlazada", "ruta": "%LOCALAPPDATA%\\Enlazada", "segura": True})
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600)], medir=lambda ruta, s: (123, False)))
    caches = {c["nombre"]: c for c in perfil["caches"]}
    assert caches["Ejemplo_Cache"]["bytes"] == 123 and caches["Ejemplo_Cache"]["bytes_parcial"] is True
    assert caches["Ya_Enlazada"]["ya_enlazada"] is True and caches["Ya_Enlazada"]["bytes"] is None
    assert caches["No_Existe"]["existe"] is False and caches["No_Existe"]["bytes"] is None
    habilitadas = {c["nombre"]: c["habilitada"] for c in p["caches"]}
    assert habilitadas == {"Ejemplo_Cache": True, "Ya_Enlazada": False}
    os.rmdir(enlace)  # quita solo la junction de prueba


# ---------------------------------------------------------------------- compatibilidad, CLI y equipo real
def test_formato_compatible_con_la_configuracion_inicial(equipo):
    perfil, p = detectar_y_proponer(equipo.fuentes([SISTEMA, volumen("E", 600, bus="USB")]))
    for d in perfil["discos"]:
        assert {"letra", "raiz", "sistema_archivos", "etiqueta", "total", "libre", "bus", "tipo", "externo",
                "es_sistema", "ntfs", "nube_virtual"} <= set(d)
    assert "raiz_sistema" in perfil["carpetas"]
    for c in perfil["caches"]:
        assert {"nombre", "app", "ruta", "bytes", "segura", "reubicable_con_junction", "procesos", "ya_enlazada",
                "existe"} <= set(c)
    for clave in ("raiz_destino", "dir_registro", "modo", "permitir_junctions", "destino_usb", "intocables_detectados",
                  "caches", "advertencias", "origen", "disco_destino", "config"):
        assert clave in p
    assert set(p["config"]) == {"raiz_destino", "dir_registro", "modo", "taxonomia", "permitir_junctions", "destino_usb",
                                "intocables_detectados", "caches", "discos"}


def test_cli(monkeypatch, capsys, equipo):
    import gestor.deteccion.__main__ as cli

    fuentes = equipo.fuentes([SISTEMA, volumen("E", 600)])
    monkeypatch.setattr(cli, "detectar_equipo", lambda: detectar_equipo(fuentes))
    assert cli.main(["--json"]) == 0
    assert set(json.loads(capsys.readouterr().out)) >= CLAVES_PERFIL
    assert cli.main(["--json", "--propuesta"]) == 0
    assert json.loads(capsys.readouterr().out)["raiz_destino"] == "E:\\"
    assert cli.main([]) == 0 and "Discos:" in capsys.readouterr().out
    assert cli.main(["--propuesta"]) == 0 and "Destino: E:\\" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_humo_equipo_real():
    """Ejecuta la detección real (solo lectura) y comprueba solo la forma del resultado."""
    perfil = detectar_equipo()
    texto = json.dumps(perfil, ensure_ascii=False)
    assert set(json.loads(texto)) >= CLAVES_PERFIL
    assert isinstance(perfil["discos"], list) and isinstance(perfil["carpetas"], dict)
    for clave in ("nube", "bibliotecas", "rutas_referenciadas", "caches", "advertencias"):
        assert isinstance(perfil[clave], list)
    for d in perfil["discos"]:
        assert d["raiz"] == d["letra"] + ":\\" and isinstance(d["ntfs"], bool) and isinstance(d["externo"], bool)
    p = proponer_configuracion(perfil)
    json.dumps(p, ensure_ascii=False)
    assert validar(p["config"]) == []
    assert p["modo"] in ("completo", "solo_analisis")


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="requiere powershell.exe")
def test_script_powershell_de_discos():
    volumenes, error = volumenes_powershell(60)
    assert error is None
    assert isinstance(volumenes, list) and volumenes
    for v in volumenes:
        assert {"letra", "sistema_archivos", "total", "libre", "tipo_unidad", "bus"} <= set(v)
    assert (RAIZ / "scripts" / "detectar-equipo.ps1").read_bytes().startswith(b"\xef\xbb\xbf")


def test_normalizar_rutas():
    assert normalizar("c:/program files (x86)/steam/") == r"C:\program files (x86)\steam"
    assert normalizar('"E:\\"') == "E:\\" and normalizar("E:") == "E:\\"
    assert normalizar("") is None and normalizar(None) is None and normalizar(5) is None


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_nombres_cortos_se_convierten_en_largos(tmp_path):
    import ctypes

    from gestor.deteccion.contexto import ruta_larga

    larga = tmp_path / "Carpeta con nombre largo de ejemplo"
    larga.mkdir()
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(larga), buf, 1024)
    if "~" not in buf.value:
        pytest.skip("el volumen no genera nombres cortos 8.3")
    assert ruta_larga(buf.value).lower() == str(larga).lower()
    assert ruta_larga(r"C:\No~Existe\x") == r"C:\No~Existe\x"
