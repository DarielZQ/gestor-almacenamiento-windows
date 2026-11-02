// Cliente HTTP único de la interfaz: añade el token, traduce errores y gestiona la pérdida de conexión
// con el servidor local (banner + reconexión automática con espera creciente).
// Se puede cargar en el navegador (window.Conexion) o en Node para las pruebas (module.exports).
(function (raiz) {
  "use strict";

  const MENSAJE_SIN_SERVIDOR =
    "El servidor local no está corriendo. Ábrelo con el acceso directo 'Gestor de Almacenamiento'.";
  const ESPERAS_MS = [1000, 2000, 4000, 8000, 15000, 30000];

  class ErrorConexion extends Error {
    constructor() { super(MENSAJE_SIN_SERVIDOR); this.name = "ErrorConexion"; this.sinServidor = true; }
  }

  class ErrorServidor extends Error {
    constructor(status, mensaje, sesionCaducada) {
      super(mensaje); this.name = "ErrorServidor"; this.status = status; this.sesionCaducada = !!sesionCaducada;
    }
  }

  function mensajeError(status, cuerpo, textoEstado) {
    const detalle = (cuerpo && cuerpo.error) || textoEstado || "sin detalle";
    if (status === 403 && /token/i.test(detalle))
      return "Error 403: la sesión caducó porque el servidor se reinició. Recarga la página (F5).";
    if (status === 409) return `Error 409: ${detalle}`;
    return `Error ${status}: ${detalle}`;
  }

  function crearCliente(opciones) {
    const o = Object.assign({
      token: "",
      fetch: (typeof fetch !== "undefined" ? fetch.bind(raiz) : null),
      esperar: ms => new Promise(r => setTimeout(r, ms)),
      alCambiarConexion: () => {},   // (conectado: boolean) => void
      alSesionCaducada: () => {},    // el servidor se reinició con otro token
    }, opciones || {});
    let conectado = true;
    let reconectando = false;

    function marcar(valor) {
      if (valor === conectado) return;
      conectado = valor;
      o.alCambiarConexion(valor);
      if (!valor) reconectar();
    }

    async function pedir(ruta, datos) {
      const init = { headers: { "X-Gestor-Token": o.token } };
      if (datos !== undefined) {
        init.method = "POST";
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(datos);
      }
      let r;
      try {
        r = await o.fetch(ruta, init);
      } catch (e) {              // "Failed to fetch" / ERR_CONNECTION_REFUSED
        marcar(false);
        throw new ErrorConexion();
      }
      marcar(true);
      let cuerpo = null;
      try { cuerpo = await r.json(); } catch (e) { cuerpo = null; }
      if (!r.ok) {
        const caducada = r.status === 403 && cuerpo && /token/i.test(cuerpo.error || "");
        if (caducada) o.alSesionCaducada();
        throw new ErrorServidor(r.status, mensajeError(r.status, cuerpo, r.statusText), caducada);
      }
      return cuerpo;
    }

    async function reconectar() {
      if (reconectando) return;
      reconectando = true;
      try {
        for (let i = 0; !conectado; i++) {
          await o.esperar(ESPERAS_MS[Math.min(i, ESPERAS_MS.length - 1)]);
          try {
            const r = await o.fetch("/api/estado", { headers: { "X-Gestor-Token": o.token } });
            if (r.status === 403) { conectado = true; o.alSesionCaducada(); o.alCambiarConexion(true); break; }
            if (r.ok) { marcar(true); break; }
          } catch (e) { /* sigue sin servidor */ }
        }
      } finally { reconectando = false; }
    }

    return { pedir, reconectar, conectado: () => conectado };
  }

  const api = { crearCliente, mensajeError, ErrorConexion, ErrorServidor, MENSAJE_SIN_SERVIDOR, ESPERAS_MS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else raiz.Conexion = api;
})(typeof window !== "undefined" ? window : globalThis);
