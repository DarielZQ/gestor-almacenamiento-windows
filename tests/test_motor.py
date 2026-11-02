"""Integración con el motor PowerShell real, siempre sobre directorios temporales."""
import csv
import os
import subprocess

import pytest

from conftest import RAIZ, escribir, requiere_motor

pytestmark = [requiere_motor, pytest.mark.motor]


def leer_csv(ruta):
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- -Analizar

def test_analizar_clasifica_archivos(equipo, motor):
    d = equipo.descargas
    casos = {
        "Factura enero.pdf": ("OK", "Documentos_Personales\\Identificacion_y_Finanzas"),
        "mi cv 2026.docx": ("OK", "Documentos_Personales\\CV_Curriculum"),
        "Libro de redes edition 3.pdf": ("OK", "Biblioteca_Digital_Libros\\Libros_y_Manuales"),
        "notas.txt": ("OK", "Notas_Texto_Borradores\\Notas_TXT"),
        "video.mp4": ("OK", "Multimedia\\Videos_Descargados"),
        "instalador.exe": ("OK", "Instaladores_Software"),
        "raro.xyz": ("OK", "Triage_Por_Clasificar\\Extension_XYZ"),
        "documento suelto.pdf": ("OK", "Triage_Por_Clasificar\\PDF_Sin_Clasificar"),
        "acceso.lnk": ("IGNORAR", None),
        "~temporal.docx": ("IGNORAR", None),
        "base.sqlite": ("INTOCABLE", None),
        "datos.mdf": ("INTOCABLE", None),
        "ñandú acentuado.txt": ("OK", "Notas_Texto_Borradores\\Notas_TXT"),
    }
    elementos = []
    for i, nombre in enumerate(casos):
        escribir(d / nombre)
        elementos.append({"id": i, "ruta": str(d / nombre), "contexto": "archivo"})
    res = motor.analizar(elementos)
    for i, (nombre, (codigo, categoria)) in enumerate(casos.items()):
        r = res[i]
        assert r["codigo"] == codigo, (nombre, r)
        if codigo == "OK":
            assert r["categoria"] == categoria, (nombre, r)
            assert r["destino"] == str(equipo.destino / categoria / nombre)


def test_analizar_propone_nombre_libre_si_existe(equipo, motor):
    escribir(equipo.descargas / "notas.txt", "nuevo")
    escribir(equipo.destino / "Notas_Texto_Borradores" / "Notas_TXT" / "notas.txt", "viejo")
    r = motor.analizar([{"id": 1, "ruta": str(equipo.descargas / "notas.txt"), "contexto": "archivo"}])[1]
    assert r["destino"].endswith("notas (2).txt")


def test_analizar_carpetas_intocables_y_clasificadas(equipo, motor):
    repo = equipo.escritorio / "MiRepo"
    escribir(repo / ".git" / "HEAD", "ref")
    escribir(repo / "src" / "main.py", "print(1)")
    proyecto = equipo.escritorio / "ProyectoPrueba"  # protegido por reglas.local.json
    escribir(proyecto / "a.txt")
    con_bd = equipo.descargas / "ConBase"
    escribir(con_bd / "datos.db")
    normal = equipo.descargas / "Telegram Desktop"
    escribir(normal / "foto.jpg")
    codigo = equipo.descargas / "app-node"
    escribir(codigo / "package.json", "{}")
    raiz_ingles = equipo.destino / "Curso de ingles"
    escribir(raiz_ingles / "a.pdf")
    steam = equipo.destino / "BibliotecaJuegos"          # biblioteca detectada por contenido, no por nombre
    escribir(steam / "steamapps" / "appmanifest_1.acf")
    portable = equipo.destino / "HerramientaPortable"
    escribir(portable / "herramienta.exe")
    propia = equipo.destino / "Multimedia"
    propia.mkdir(exist_ok=True)
    elementos = [
        {"id": 1, "ruta": str(repo), "contexto": "carpeta_usuario"},
        {"id": 2, "ruta": str(repo / "src" / "main.py"), "contexto": "archivo"},
        {"id": 3, "ruta": str(proyecto), "contexto": "carpeta_usuario"},
        {"id": 4, "ruta": str(con_bd), "contexto": "carpeta_usuario"},
        {"id": 5, "ruta": str(normal), "contexto": "carpeta_usuario"},
        {"id": 6, "ruta": str(codigo), "contexto": "carpeta_usuario"},
        {"id": 7, "ruta": str(raiz_ingles), "contexto": "carpeta_raiz_d"},
        {"id": 8, "ruta": str(steam), "contexto": "carpeta_raiz_d"},
        {"id": 9, "ruta": str(portable), "contexto": "carpeta_raiz_d"},
        {"id": 10, "ruta": str(propia), "contexto": "carpeta_raiz_d"},
        {"id": 11, "ruta": str(equipo.base / "no-existe"), "contexto": "archivo"},
    ]
    r = motor.analizar(elementos)
    assert r[1]["codigo"] == "INTOCABLE" and "git" in r[1]["motivo"]
    assert r[2]["codigo"] == "INTOCABLE" and "repositorio git" in r[2]["motivo"]
    assert r[3]["codigo"] == "INTOCABLE" and "ProyectoPrueba" in r[3]["motivo"]
    assert r[4]["codigo"] == "INTOCABLE" and "bases de datos" in r[4]["motivo"]
    assert r[5]["codigo"] == "OK" and r[5]["categoria"] == "Triage_Por_Clasificar\\Carpetas_De_Mensajeria"
    assert r[6]["codigo"] == "OK" and r[6]["categoria"] == "Proyectos_Codigo\\Desde_Descargas"
    assert r[7]["codigo"] == "OK" and r[7]["categoria"] == "Universidad_Academico\\Ingles_Idiomas"
    assert r[8]["codigo"] == "INTOCABLE" and "biblioteca" in r[8]["motivo"]
    assert r[9]["codigo"] == "CONSERVAR" and "ejecutable" in r[9]["motivo"]
    assert r[10]["codigo"] == "PROPIA"
    assert r[11]["codigo"] == "NO_EXISTE"


def test_analizar_rutas_del_sistema_son_intocables(equipo, motor):
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    r = motor.analizar([{"id": 1, "ruta": os.path.join(windir, "notepad.exe"), "contexto": "archivo"}])[1]
    assert r["codigo"] == "INTOCABLE"


# ---------------------------------------------------------------- -PlanAprobado

def op(i, origen, destino, tipo="Archivo", bytes_=None):
    d = {"id": i, "tipo": tipo, "origen": str(origen), "destino": str(destino), "categoria": "Prueba"}
    if bytes_ is not None:
        d["bytes"] = bytes_
    return d


def test_plan_mueve_registra_y_deshace(equipo, motor):
    origen = escribir(equipo.descargas / "informe ñ.pdf", b"contenido-123")
    destino = equipo.destino / "Triage_Por_Clasificar" / "PDF_Sin_Clasificar" / "informe ñ.pdf"
    res = motor.ejecutar_plan([op(7, origen, destino, bytes_=13)], ejecutar=True, horas_recientes=0, dir_registro=str(equipo.registro))
    r = res["resultados"][0]
    assert r["id"] == 7 and r["estado"] == "MOVIDO" and r["ok"] is True
    assert not origen.exists() and destino.read_bytes() == b"contenido-123"
    filas = leer_csv(res["registro"])
    assert list(filas[0].keys()) == ["Fecha", "Tipo", "Categoria", "MB", "Estado", "Origen", "Destino"]
    assert filas[0]["Estado"] == "MOVIDO" and filas[0]["Origen"] == str(origen) and filas[0]["Destino"] == str(destino)
    assert os.path.basename(res["registro"]).startswith("migracion_plan_")

    # Deshacer con el modo -Deshacer existente (reutilizando el CSV)
    d = motor.deshacer(filas, ejecutar=True, dir_registro=str(equipo.registro))
    assert d["resultados"][0]["estado"] == "RESTAURADO"
    assert origen.read_bytes() == b"contenido-123" and not destino.exists()
    restaurado = leer_csv(d["registro"])
    assert restaurado[0]["Estado"] == "RESTAURADO" and restaurado[0]["Origen"] == str(destino)


def test_plan_nunca_sobrescribe(equipo, motor):
    origen = escribir(equipo.descargas / "a.txt", "nuevo")
    destino = escribir(equipo.destino / "Notas" / "a.txt", "viejo")
    r = motor.ejecutar_plan([op(1, origen, destino)], ejecutar=True, horas_recientes=0, dir_registro=str(equipo.registro))["resultados"][0]
    assert r["estado"].startswith("OMITIDO") and "ya existe" in r["estado"]
    assert origen.read_text() == "nuevo" and destino.read_text() == "viejo"


def test_plan_rechaza_cambios_intocables_y_rutas_fuera(equipo, motor):
    cambiado = escribir(equipo.descargas / "cambiado.txt", "12345")
    repo = equipo.descargas / "repo"
    escribir(repo / ".git" / "config")
    bd = escribir(equipo.descargas / "datos.sqlite")
    fuera = escribir(equipo.descargas / "fuera.txt")
    reciente = escribir(equipo.descargas / "reciente.txt", antiguo=False)
    ops = [
        op(1, cambiado, equipo.destino / "x" / "cambiado.txt", bytes_=999),
        op(2, repo, equipo.destino / "x" / "repo", tipo="Carpeta"),
        op(3, bd, equipo.destino / "x" / "datos.sqlite"),
        op(4, fuera, equipo.base / "otro_sitio" / "fuera.txt"),
        op(5, equipo.descargas / "no-existe.txt", equipo.destino / "x" / "no-existe.txt"),
        op(6, reciente, equipo.destino / "x" / "reciente.txt"),
        op(7, fuera, str(equipo.destino) + "\\x\\..\\..\\fuera.txt"),
    ]
    res = motor.ejecutar_plan(ops, ejecutar=True, horas_recientes=24, dir_registro=str(equipo.registro))
    estados = {r["id"]: r["estado"] for r in res["resultados"]}
    assert "cambió" in estados[1]
    assert "intocable" in estados[2]
    assert "intocable" in estados[3]
    assert "fuera" in estados[4]
    assert "ya no existe" in estados[5]
    assert "reciente" in estados[6]
    assert "no normalizada" in estados[7]
    assert all(not r["ok"] for r in res["resultados"])
    for p in (cambiado, repo, bd, fuera, reciente):
        assert p.exists()
    assert not (equipo.destino / "x").exists()


def test_plan_simulacion_no_mueve(equipo, motor):
    origen = escribir(equipo.descargas / "b.txt")
    destino = equipo.destino / "Notas" / "b.txt"
    sim = equipo.datos / "sim"
    r = motor.ejecutar_plan([op(1, origen, destino)], ejecutar=False, horas_recientes=0, dir_registro=str(sim))["resultados"][0]
    assert r["estado"] == "SIMULADO" and r["ok"] is True
    assert origen.exists() and not destino.exists()
    assert not equipo.registro.exists()


def test_plan_mueve_carpeta_y_destinos_repetidos(equipo, motor):
    carpeta = equipo.descargas / "Material"
    escribir(carpeta / "uno.txt")
    escribir(carpeta / "sub" / "dos.txt")
    destino = equipo.destino / "Triage" / "Material"
    otro = escribir(equipo.descargas / "Material.txt")
    res = motor.ejecutar_plan([op(1, carpeta, destino, tipo="Carpeta"), op(2, otro, destino)], ejecutar=True,
                              horas_recientes=0, dir_registro=str(equipo.registro))
    estados = {r["id"]: r["estado"] for r in res["resultados"]}
    assert estados[1] == "MOVIDO"
    assert estados[2].startswith("OMITIDO")
    assert (destino / "sub" / "dos.txt").exists() and not carpeta.exists() and otro.exists()


def test_plan_junction_de_cache(equipo, motor):
    escribir(equipo.cache / "blob.bin", b"0" * 1000)
    destino = equipo.destino / "Cache_Aplicaciones_Enlazadas" / "Cache_Prueba"
    r = motor.ejecutar_plan([op(1, equipo.cache, destino, tipo="Junction")], ejecutar=True, horas_recientes=0,
                            dir_registro=str(equipo.registro))["resultados"][0]
    assert r["estado"] == "ENLAZADO"
    assert (destino / "blob.bin").exists()
    assert (equipo.cache / "blob.bin").exists()  # la app sigue viendo la misma ruta, a través de la junction
    # Una carpeta que no está en la lista de cachés no se enlaza
    otra = equipo.local / "OtraCosa"
    escribir(otra / "a.txt")
    r2 = motor.ejecutar_plan([op(2, otra, equipo.destino / "Cache_Aplicaciones_Enlazadas" / "Otra", tipo="Junction")], ejecutar=True,
                             horas_recientes=0, dir_registro=str(equipo.registro))["resultados"][0]
    assert r2["estado"].startswith("OMITIDO") and otra.exists()
    # Limpieza: quitar solo la junction (rmdir no borra el contenido del destino)
    subprocess.run(["cmd", "/c", "rmdir", str(equipo.cache)], check=True)


def test_deshacer_no_sobrescribe_si_origen_ocupado(equipo, motor):
    origen = escribir(equipo.descargas / "c.txt", "original")
    destino = equipo.destino / "Notas" / "c.txt"
    res = motor.ejecutar_plan([op(1, origen, destino)], ejecutar=True, horas_recientes=0, dir_registro=str(equipo.registro))
    filas = leer_csv(res["registro"])
    escribir(origen, "otro archivo con el mismo nombre")
    d = motor.deshacer(filas, ejecutar=True, dir_registro=str(equipo.registro))
    assert d["resultados"][0]["estado"].startswith("OMITIDO")
    assert origen.read_text() == "otro archivo con el mismo nombre" and destino.read_text() == "original"


def test_parametros_originales_siguen_funcionando(equipo):
    """El modo clásico (simulación por defecto) sigue aceptando sus parámetros y no mueve nada."""
    salida = equipo.datos / "clasico"
    salida.mkdir(parents=True)
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(RAIZ / "motor" / "Motor-Almacenamiento.ps1"), "-RaizDestino", str(equipo.destino),
                           "-Reglas", str(equipo.config / "reglas.json"), "-DirRegistro", str(salida),
                           "-OmitirRecientesHoras", "100000"],
                          capture_output=True, timeout=300)
    texto = proc.stdout.decode("oem", errors="replace")
    assert proc.returncode == 0, proc.stderr.decode("oem", errors="replace")
    assert "SIMULACION" in texto
    # Sin -Ejecutar nada se mueve: cualquier fila registrada es una simulación.
    for csv_sim in salida.glob("*.csv"):
        assert csv_sim.name.startswith("simulacion_")
        assert all(f["Estado"] == "SIMULADO" for f in leer_csv(csv_sim))


def test_rutas_extra_de_reglas_locales_son_intocables(equipo, motor):
    dato = escribir(equipo.local / "AppFicticia-Datos" / "sesion.json", "{}")
    r = motor.analizar([{"id": 1, "ruta": str(dato), "contexto": "archivo"}])[1]
    assert r["codigo"] == "INTOCABLE" and "AppFicticia-Datos" in r["motivo"]


def test_envoltorio_con_el_nombre_anterior_reenvia_los_parametros(equipo):
    import json
    archivo = escribir(equipo.descargas / "Libro_Ejemplo.pdf")
    entrada = equipo.datos / "in.json"
    salida = equipo.datos / "out.json"
    entrada.parent.mkdir(parents=True, exist_ok=True)
    entrada.write_text(json.dumps({"elementos": [{"id": 1, "ruta": str(archivo), "contexto": "archivo"}]}), encoding="utf-8")
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(RAIZ / "motor" / "Liberar-DiscoC.ps1"), "-RaizDestino", str(equipo.destino),
                           "-Reglas", str(equipo.config / "reglas.json"), "-Analizar", str(entrada), "-SalidaJson", str(salida)],
                          capture_output=True, timeout=300)
    assert proc.returncode == 0, proc.stderr.decode("oem", errors="replace")
    r = json.loads(salida.read_text(encoding="utf-8-sig"))["resultados"][0]
    assert r["codigo"] == "OK" and r["categoria"] == r"Biblioteca_Digital_Libros\Libros_y_Manuales"
