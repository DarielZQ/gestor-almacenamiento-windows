"""El instalador se puede repetir sin romper nada (sin configuración inicial ni acceso directo)."""
import subprocess

from conftest import RAIZ, requiere_motor


@requiere_motor
def test_instalador_idempotente():
    for _ in range(2):
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RAIZ / "instalar.ps1"),
                               "-Desatendido", "-SinConfiguracion", "-SinAccesoDirecto"], capture_output=True, timeout=600)
        salida = proc.stdout.decode("oem", errors="replace")
        assert proc.returncode == 0, salida + proc.stderr.decode("oem", errors="replace")
        assert "Comprobando requisitos" in salida and "Listo" in salida
