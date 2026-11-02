// Pruebas del cliente HTTP de la interfaz (web/conexion.js) con un fetch simulado. Ejecutar: node tests/js/prueba_conexion.cjs
"use strict";
const assert = require("assert");
const path = require("path");
const C = require(path.join(__dirname, "..", "..", "web", "conexion.js"));

function respuesta(status, cuerpo) {
  return { ok: status >= 200 && status < 300, status, statusText: "x", json: async () => cuerpo };
}

async function servidorCaido() {
  const cambios = [];
  let servidorArriba = false;
  const esperas = [];
  const cliente = C.crearCliente({
    token: "t",
    fetch: async (ruta) => { if (!servidorArriba) throw new TypeError("Failed to fetch"); return respuesta(200, { ok: true, ruta }); },
    esperar: async (ms) => { esperas.push(ms); if (esperas.length === 3) servidorArriba = true; },
    alCambiarConexion: c => cambios.push(c),
  });
  await assert.rejects(cliente.pedir("/api/estado"), e => e.sinServidor === true && e.message === C.MENSAJE_SIN_SERVIDOR);
  assert.strictEqual(cliente.conectado(), false);
  assert.deepStrictEqual(cambios, [false]);
  await cliente.reconectar();             // espera a que termine la reconexión en curso o la lanza
  for (let i = 0; i < 10 && !cliente.conectado(); i++) await new Promise(r => setImmediate(r));
  assert.strictEqual(cliente.conectado(), true);
  assert.deepStrictEqual(cambios, [false, true]);
  assert.deepStrictEqual(esperas, [1000, 2000, 4000]);   // espera creciente
  const r = await cliente.pedir("/api/elementos");
  assert.strictEqual(r.ruta, "/api/elementos");
}

async function erroresLegibles() {
  let caducada = 0;
  const cliente = C.crearCliente({
    token: "t",
    fetch: async (ruta) => ruta === "/a" ? respuesta(400, { error: "acción desconocida" })
      : ruta === "/b" ? respuesta(403, { error: "token ausente o incorrecto" }) : respuesta(500, null),
    alSesionCaducada: () => { caducada++; },
  });
  await assert.rejects(cliente.pedir("/a"), e => e.status === 400 && e.message === "Error 400: acción desconocida");
  await assert.rejects(cliente.pedir("/b"), e => e.sesionCaducada && /recarga la página/i.test(e.message));
  assert.strictEqual(caducada, 1);
  await assert.rejects(cliente.pedir("/c"), e => e.status === 500 && e.message.startsWith("Error 500"));
}

async function cabeceras() {
  let visto;
  const cliente = C.crearCliente({ token: "secreto", fetch: async (r, init) => { visto = init; return respuesta(200, {}); } });
  await cliente.pedir("/api/x", { a: 1 });
  assert.strictEqual(visto.method, "POST");
  assert.strictEqual(visto.headers["X-Gestor-Token"], "secreto");
  assert.strictEqual(visto.headers["Content-Type"], "application/json");
  assert.strictEqual(visto.body, '{"a":1}');
}

async function versiones() {
  assert.strictEqual(C.estadoVersion("aaa", { hash: "aaa", desactualizado: false }), null);
  assert.strictEqual(C.estadoVersion("aaa", { hash: "aaa", desactualizado: true }).accion, "reiniciar");
  assert.strictEqual(C.estadoVersion("viejo", { hash: "nuevo", desactualizado: false }).accion, "recargar");
  assert.strictEqual(C.estadoVersion("aaa", undefined), null);
}

(async () => {
  for (const f of [servidorCaido, erroresLegibles, cabeceras, versiones]) { await f(); console.log(`ok ${f.name}`); }
})().catch(e => { console.error(e); process.exit(1); });
