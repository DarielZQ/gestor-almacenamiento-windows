"""proponer_configuracion(perfil): origen, destino, modo, junctions, intocables y cachés para config.json.

Es una función pura sobre el perfil (no lee el equipo), así que se prueba con perfiles inventados.
"""
from __future__ import annotations

import copy
import ntpath

from .contexto import RE_ABSOLUTA, dentro, es_raiz_unidad, normalizar

GB = 1024 ** 3
TAMANO_MINIMO_DESTINO = 8 * GB      # unidades más pequeñas no se proponen como destino
LIBRE_BAJO = 10 * GB
SISTEMAS_ADMITIDOS = {"NTFS", "EXFAT", "REFS", "FAT32"}
TIPOS_NO_DESTINO = {"red", "optico", "virtual"}
CARPETA_UN_DISCO = "Organizado"
MOTIVOS_REFERENCIA = {
    "programa": "carpeta de un programa instalado",
    "servicio": "usada por un servicio de Windows",
    "acceso_directo": "destino de un acceso directo",
    "path_usuario": "está en el PATH del usuario",
    "path_maquina": "está en el PATH del sistema",
}
MOTIVOS_BIBLIOTECA = {
    "biblioteca": "biblioteca de juegos ({})",
    "launcher": "launcher de juegos ({})",
    "juego": "juego instalado ({})",
    "instalacion": "instalado por {}",
}


def _gb(b) -> str:
    return f"{(b or 0) / GB:.1f} GB"


def _letra_sistema(perfil: dict, discos: list[dict]) -> str:
    d = next((d for d in discos if d.get("es_sistema")), None)
    if d and d.get("letra"):
        return str(d["letra"])[:1].upper()
    for texto in ((perfil.get("equipo") or {}).get("unidad_sistema"), (perfil.get("carpetas") or {}).get("raiz_sistema")):
        if isinstance(texto, str) and texto[:1].isalpha():
            return texto[:1].upper()
    return "C"


def _candidatos(discos: list[dict]) -> tuple[list[dict], list[str]]:
    """Discos que pueden ser destino y motivos de los descartados."""
    buenos, descartes = [], []
    for d in discos:
        if d.get("es_sistema"):
            continue
        letra = d.get("letra")
        fs = str(d.get("sistema_archivos") or "").upper()
        if d.get("nube_virtual") or d.get("tipo") in TIPOS_NO_DESTINO:
            descartes.append(f"{letra}: es una unidad {d.get('tipo') or 'virtual'}")
        elif fs not in SISTEMAS_ADMITIDOS:
            descartes.append(f"{letra}: tiene un sistema de archivos no admitido ({fs or 'sin formato'})")
        elif int(d.get("total") or 0) < TAMANO_MINIMO_DESTINO:
            descartes.append(f"{letra}: es demasiado pequeña ({_gb(d.get('total'))})")
        else:
            buenos.append(d)
    return buenos, descartes


def _ruta_referencia(r: dict, protegidas: list[str], cubiertas: list[str]) -> str | None:
    """Qué proteger por una ruta referenciada: la carpeta del programa o, si sería demasiado amplia, el propio archivo."""
    if not r.get("existe"):
        return None
    ruta = normalizar(r.get("ruta"))
    if not ruta or not RE_ABSOLUTA.match(ruta) or es_raiz_unidad(ruta):
        return None
    es_carpeta = bool(r.get("es_carpeta"))
    origen = r.get("origen")
    candidato = ruta if es_carpeta or origen == "acceso_directo" else ntpath.dirname(ruta)
    # Nunca una carpeta que contenga el perfil, las carpetas conocidas o la raíz de una unidad.
    if es_raiz_unidad(candidato) or any(dentro(p, candidato) for p in protegidas):
        if es_carpeta or candidato == ruta:
            return None
        candidato = ruta
    if any(dentro(candidato, c) for c in cubiertas):  # ya protegidas por reglas.json (Windows, Program Files...)
        return None
    return candidato


def _intocables(perfil: dict) -> tuple[list[dict], list[str]]:
    equipo = perfil.get("equipo") or {}
    carpetas = perfil.get("carpetas") or {}
    cubiertas = [r for r in (normalizar(v) for v in (equipo.get("rutas_sistema") or {}).values()) if r]
    protegidas = [r for r in (normalizar(v) for v in carpetas.values() if isinstance(v, str)) if r]
    entradas: list[tuple[str, str, str]] = []
    for n in perfil.get("nube") or []:
        entradas.append((n.get("ruta"), f"sincronizado con {n.get('servicio') or 'la nube'}", "nube"))
    for b in perfil.get("bibliotecas") or []:
        plantilla = MOTIVOS_BIBLIOTECA.get(b.get("tipo"), "biblioteca de juegos ({})")
        entradas.append((b.get("ruta"), plantilla.format(b.get("plataforma") or "launcher"), "biblioteca"))
    for r in perfil.get("rutas_referenciadas") or []:
        ruta = _ruta_referencia(r, protegidas, cubiertas)
        if ruta:
            motivo = MOTIVOS_REFERENCIA.get(r.get("origen"), "referenciada por un programa")
            detalle = r.get("detalle")
            entradas.append((ruta, f"{motivo} ({detalle})" if detalle and detalle != "PATH" else motivo, "referencia"))
    # Normalizar, quitar repetidas y las que ya están dentro de otra (se conserva la exterior).
    limpias = []
    for ruta, motivo, tipo in entradas:
        r = normalizar(ruta)
        if r and RE_ABSOLUTA.match(r):
            limpias.append((r, motivo, tipo))
    limpias.sort(key=lambda e: (len(e[0]), e[0].lower()))
    res: list[dict] = []
    avisos = []
    for r, motivo, tipo in limpias:
        if any(dentro(r, x["ruta"]) for x in res):
            continue
        res.append({"ruta": r, "motivo": motivo})
        if es_raiz_unidad(r) and tipo != "nube":
            avisos.append(f"La raíz completa de {r[:2]} es intocable ({motivo}): no se reorganizará nada en esa unidad.")
    return res, avisos


def _caches(perfil: dict, habilitar: bool) -> tuple[list[dict], list[dict]]:
    reubicables, otras = [], []
    for c in perfil.get("caches") or []:
        if not isinstance(c, dict) or not c.get("existe", True) or not c.get("nombre") or not c.get("ruta"):
            continue
        if c.get("reubicable_con_junction", c.get("segura")):
            reubicables.append({"nombre": c["nombre"], "ruta": c["ruta"], "procesos": list(c.get("procesos") or []),
                                "habilitada": bool(habilitar and not c.get("ya_enlazada"))})
        else:
            otras.append({"nombre": c["nombre"], "app": c.get("app"), "ruta": c["ruta"], "bytes": c.get("bytes"),
                          "nota": c.get("nota")})
    return reubicables, otras


def proponer_configuracion(perfil: dict) -> dict:
    """Propuesta para la configuración inicial. 'config' trae exactamente lo que va a config.json (pasa
    gestor.config.validar); el resto (origen, destino, alternativas, advertencias...) es para mostrarlo."""
    perfil = perfil or {}
    discos = [d for d in perfil.get("discos") or [] if isinstance(d, dict) and d.get("letra")]
    sistema = _letra_sistema(perfil, discos)
    candidatos, descartes = _candidatos(discos)
    advertencias: list[str] = []
    destino = None
    if candidatos:
        destino = max(candidatos, key=lambda d: (int(d.get("libre") or 0), not d.get("externo"), -ord(d["letra"][0])))
        letra = destino["letra"]
        raiz = destino.get("raiz") or f"{letra}:\\"
        modo = "completo"
        usb = bool(destino.get("externo"))
        ntfs = bool(destino.get("ntfs"))
        permitir = ntfs and not usb
        if usb:
            advertencias.append(f"El destino propuesto ({letra}:) es un disco USB o extraíble: si se desconecta, lo que se "
                                "haya movido no estará disponible. La reubicación de cachés con junction queda "
                                "desactivada por defecto.")
            internos = [d["letra"] + ":" for d in candidatos if not d.get("externo")]
            if internos:
                advertencias.append(f"También hay un disco interno que podría servir como destino ({', '.join(internos)}), "
                                    "aunque con menos espacio libre.")
        if not ntfs:
            advertencias.append(f"El destino {letra}: usa {destino.get('sistema_archivos') or 'otro sistema de archivos'} y "
                                "no NTFS: las junctions quedan desactivadas y no se reubicarán cachés.")
        if int(destino.get("libre") or 0) < LIBRE_BAJO:
            advertencias.append(f"El destino {letra}: tiene poco espacio libre ({_gb(destino.get('libre'))}).")
    else:
        letra = sistema
        raiz = f"{sistema}:\\{CARPETA_UN_DISCO}\\"
        modo = "solo_analisis"
        usb = False
        permitir = False
        texto = (f"Solo se detectó un disco ({sistema}:). Modo «solo análisis y cachés»: puedes ver en qué se usa el "
                 "espacio y revisar las cachés, pero mover archivos dentro del mismo disco no libera espacio. Conecta "
                 "otro disco y vuelve a detectar el equipo para poder mover.")
        if descartes:
            texto += " Otras unidades no sirven como destino: " + "; ".join(descartes) + "."
        advertencias.append(texto)

    intocables, avisos_intocables = _intocables(perfil)
    advertencias += avisos_intocables
    caches, no_reubicables = _caches(perfil, habilitar=permitir and modo == "completo")
    advertencias += list(perfil.get("avisos_carpetas") or [])
    advertencias += [f"Detección: {a}" for a in perfil.get("advertencias") or []]

    config = {
        "raiz_destino": raiz,
        "dir_registro": ntpath.join(raiz, "Registro_Migracion_DiscoC"),
        "modo": modo,
        "taxonomia": "es",
        "permitir_junctions": permitir,
        "destino_usb": usb,
        "intocables_detectados": intocables,
        "caches": caches,
        "discos": [{k: d.get(k) for k in ("letra", "sistema_archivos", "bus", "tipo", "es_sistema")} for d in discos],
    }
    propuesta = copy.deepcopy(config)
    propuesta.update({
        "version": 1,
        "origen": f"{sistema}:\\",
        "disco_origen": sistema,
        "disco_destino": letra,
        "detalle_destino": ({k: destino.get(k) for k in ("letra", "raiz", "etiqueta", "sistema_archivos", "tipo", "bus",
                                                         "total", "libre", "ntfs")} if destino else None),
        "alternativas": [d["letra"] for d in candidatos if destino and d["letra"] != destino["letra"]],
        "caches_no_reubicables": no_reubicables,
        "advertencias": advertencias,
        "config": config,
    })
    return propuesta
