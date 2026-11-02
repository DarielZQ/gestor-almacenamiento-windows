"""Puente con el motor PowerShell (motor/Motor-Almacenamiento.ps1).

Toda la lógica de clasificación, protección y movimiento vive en el motor; aquí solo se le pasan
archivos JSON y se leen sus resultados JSON.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import uuid
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


class ErrorMotor(RuntimeError):
    pass


def _como_lista(valor) -> list:
    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    if isinstance(valor, dict) and "value" in valor and isinstance(valor["value"], list):
        return valor["value"]  # serialización rara de PowerShell 5.1
    return [valor]


class Motor:
    def __init__(self, ruta_motor: str, raiz_destino: str | None, dir_trabajo: str,
                 ruta_reglas: str | None = None, timeout: int = 3600, ruta_config: str | None = None):
        self.ruta_motor = str(ruta_motor)
        self.raiz_destino = raiz_destino
        self.ruta_config = ruta_config
        self.dir_trabajo = Path(dir_trabajo)
        self.ruta_reglas = ruta_reglas
        self.timeout = timeout
        self.dir_trabajo.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def _archivo(self, prefijo: str, ext: str = ".json") -> Path:
        return self.dir_trabajo / f"{prefijo}_{uuid.uuid4().hex[:10]}{ext}"

    def _invocar(self, args: list[str], salida: Path, timeout: int | None = None, ruta_reglas: str | None = None) -> dict:
        if not self.raiz_destino and not self.ruta_config:
            raise ErrorMotor("No hay destino configurado: completa la configuración inicial.")
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", self.ruta_motor, "-SalidaJson", str(salida)]
        if self.ruta_config:
            cmd += ["-Config", self.ruta_config]
        if self.raiz_destino:
            cmd += ["-RaizDestino", self.raiz_destino]
        if ruta_reglas or self.ruta_reglas:
            cmd += ["-Reglas", ruta_reglas or self.ruta_reglas]
        cmd += args
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout,
                              creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        stdout = proc.stdout.decode("oem" if os.name == "nt" else "utf-8", errors="replace")
        stderr = proc.stderr.decode("oem" if os.name == "nt" else "utf-8", errors="replace")
        if not salida.exists():
            raise ErrorMotor(f"El motor terminó con código {proc.returncode} sin escribir resultado.\n{stderr.strip() or stdout.strip()}")
        try:
            datos = json.loads(salida.read_text(encoding="utf-8-sig"))
        finally:
            salida.unlink(missing_ok=True)
        datos["_salida"] = stdout[-20000:]
        datos["_errores"] = stderr[-20000:]
        return datos

    # ------------------------------------------------------------------
    def analizar(self, elementos: list[dict], lote: int = 1500, ruta_reglas: str | None = None) -> dict:
        """elementos: [{id, ruta, contexto}] -> {id: {codigo, motivo, categoria, destino}}. Solo lectura."""
        resultado: dict = {}
        for i in range(0, len(elementos), lote):
            entrada = self._archivo("analizar_in")
            entrada.write_text(json.dumps({"elementos": elementos[i:i + lote]}, ensure_ascii=False), encoding="utf-8")
            try:
                datos = self._invocar(["-Analizar", str(entrada)], self._archivo("analizar_out"), timeout=1800,
                                      ruta_reglas=ruta_reglas)
            finally:
                entrada.unlink(missing_ok=True)
            for r in _como_lista(datos.get("resultados")):
                resultado[r.get("id")] = r
        return resultado

    def ejecutar_plan(self, operaciones: list[dict], ejecutar: bool, horas_recientes: int,
                      dir_registro: str | None = None) -> dict:
        """operaciones: [{id, tipo, origen, destino, categoria, bytes}]. Sin 'ejecutar' solo valida (SIMULADO)."""
        plan = self._archivo("plan")
        plan.write_text(json.dumps({"version": 1, "operaciones": operaciones}, ensure_ascii=False, indent=1), encoding="utf-8")
        args = ["-PlanAprobado", str(plan), "-OmitirRecientesHoras", str(int(horas_recientes))]
        if ejecutar:
            args.append("-Ejecutar")
        if dir_registro:
            args += ["-DirRegistro", dir_registro]
        try:
            datos = self._invocar(args, self._archivo("plan_out"))
        finally:
            if not ejecutar:
                plan.unlink(missing_ok=True)
        datos["plan"] = str(plan) if ejecutar else None
        datos["resultados"] = _como_lista(datos.get("resultados"))
        return datos

    def deshacer(self, filas: list[dict], ejecutar: bool, dir_registro: str | None = None) -> dict:
        """filas: filas de un CSV de registro (Fecha, Tipo, Categoria, MB, Estado, Origen, Destino)."""
        tmp = self._archivo("deshacer_in", ".csv")
        columnas = ["Fecha", "Tipo", "Categoria", "MB", "Estado", "Origen", "Destino"]
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columnas, quoting=csv.QUOTE_ALL)
            w.writeheader()
            for fila in filas:
                w.writerow({k: fila.get(k, "") for k in columnas})
        args = ["-Deshacer", str(tmp)]
        if ejecutar:
            args.append("-Ejecutar")
        if dir_registro:
            args += ["-DirRegistro", dir_registro]
        try:
            datos = self._invocar(args, self._archivo("deshacer_out"))
        finally:
            tmp.unlink(missing_ok=True)
        datos["resultados"] = _como_lista(datos.get("resultados"))
        return datos

    def listar_referencias(self) -> list[dict]:
        return _como_lista(self._invocar(["-ListarReferencias"], self._archivo("refs_out")).get("referencias"))
