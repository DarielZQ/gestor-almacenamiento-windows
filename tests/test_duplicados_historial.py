"""Duplicados (solo informe) e importación de los CSV del motor (solo lectura). Sin PowerShell."""
import os

from conftest import escribir
from gestor import duplicados


def test_duplicados_por_tamano_y_hash(equipo, servicio):
    contenido = os.urandom(300_000)
    a = escribir(equipo.descargas / "a.bin", contenido)
    b = escribir(equipo.documentos / "copia de a.bin", contenido)
    c = escribir(equipo.escritorio / "mismo tamano.bin", contenido[:-1] + bytes([contenido[-1] ^ 1]))
    escribir(equipo.descargas / "unico.bin", os.urandom(1000))
    r = servicio.buscar_duplicados()
    assert r["grupos"] == 1 and r["bytes_repetidos"] == 300_000
    grupos = servicio.listar_duplicados()["grupos"]
    assert sorted(grupos[0]["rutas"]) == sorted([str(a), str(b)])
    assert str(c) not in grupos[0]["rutas"]
    for p in (a, b, c):
        assert p.exists()  # nunca elimina
    # Segunda pasada: usa la caché de hashes
    assert servicio.buscar_duplicados()["grupos"] == 1


def test_duplicados_solo_parcial(equipo, servicio):
    base = os.urandom(200_000)
    escribir(equipo.descargas / "x1.bin", base)
    escribir(equipo.descargas / "x2.bin", base[:100_000] + b"Z" + base[100_001:])  # difiere en el medio
    r_parcial = duplicados.buscar(servicio.db, [str(equipo.descargas)], 0, completo=False)
    assert r_parcial["grupos"] == 1   # el hash parcial no ve el centro
    r_total = duplicados.buscar(servicio.db, [str(equipo.descargas)], 0, completo=True)
    assert r_total["grupos"] == 0     # el completo los distingue


CABECERA = '"Fecha","Tipo","Categoria","MB","Estado","Origen","Destino"\n'


def test_importar_csv_es_idempotente_y_de_solo_lectura(equipo, servicio):
    equipo.registro.mkdir(parents=True)
    ruta = equipo.registro / "migracion_2026-01-01_1000.csv"
    filas = CABECERA + '"2026-01-01T10:00:00","Archivo","Notas","0.5","MOVIDO","C:\\o\\a.txt","D:\\Notas_Texto_Borradores\\a.txt"\n'
    ruta.write_text(filas + '"2026-01-01T10:00:01","Archivo","Notas","0.1","MOV', encoding="utf-8-sig")  # última fila a medias
    antes = os.stat(ruta).st_mtime
    assert servicio.importar_historial() == 1
    assert servicio.importar_historial() == 0
    assert os.stat(ruta).st_mtime == antes
    ruta.write_text(filas + '"2026-01-01T10:00:01","Archivo","Notas","0.1","MOVIDO","C:\\o\\b.txt","D:\\Notas_Texto_Borradores\\b.txt"\n', encoding="utf-8-sig")
    assert servicio.importar_historial() == 1
    h = servicio.historial()
    assert h["total"] == 2 and all(m["deshacible"] for m in h["movimientos"])
    (equipo.registro / "simulacion_x.csv").write_text(
        CABECERA + '"2026-01-01T10:00:00","Archivo","N","1","SIMULADO","C:\\o\\s.txt","D:\\Notas_Texto_Borradores\\s.txt"\n', encoding="utf-8-sig")
    assert servicio.importar_historial() == 0  # las simulaciones no se importan
    (equipo.registro / "escritorio_2026-01-02_0900.csv").write_text(
        CABECERA + '"2026-01-02T09:00:00","Carpeta","Escritorio","0","MOVIDO","C:\\e\\A","C:\\e\\Trabajo\\A"\n', encoding="utf-8-sig")
    assert servicio.importar_historial() == 1  # otros registros del motor sí


def test_restaurado_marca_deshecho(equipo, servicio):
    equipo.registro.mkdir(parents=True)
    (equipo.registro / "migracion_a.csv").write_text(
        CABECERA + '"2026-01-01T10:00:00","Archivo","N","1","MOVIDO","C:\\o\\a.txt","D:\\Notas_Texto_Borradores\\a.txt"\n', encoding="utf-8-sig")
    (equipo.registro / "deshacer_b.csv").write_text(
        CABECERA + '"2026-01-02T10:00:00","Archivo","N","1","RESTAURADO","D:\\Notas_Texto_Borradores\\a.txt","C:\\o\\a.txt"\n', encoding="utf-8-sig")
    servicio.importar_historial()
    movido = [m for m in servicio.historial()["movimientos"] if m["estado"] == "MOVIDO"][0]
    assert movido["deshecho"] and not movido["deshacible"]
