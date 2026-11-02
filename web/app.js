// Interfaz del gestor de almacenamiento. Sin dependencias externas.
// Todo el texto que viene de rutas o nombres de archivo se inserta con textContent (nunca como HTML).
"use strict";

const TOKEN = document.querySelector('meta[name="gestor-token"]').content;
const estadoUI = { pestana: "resumen", seleccion: new Set(), seleccionCola: new Set(), limite: 300, tareaVista: null };

// ---------------------------------------------------------------- conexión con el servidor local
// Todas las peticiones pasan por un único cliente (web/conexion.js): token, errores legibles,
// banner si el servidor no está corriendo y reconexión automática.
const cliente = Conexion.crearCliente({
  token: TOKEN,
  alCambiarConexion: conectado => {
    document.getElementById("sin-servidor").classList.toggle("oculto", conectado);
    document.body.classList.toggle("desconectado", !conectado);
    document.querySelectorAll("main button, .pestanas button").forEach(b => {
      if (!b.closest(".pestanas")) b.disabled = !conectado;
    });
    document.getElementById("tarea").classList.add("oculto");
    if (conectado) refrescar();
  },
  alSesionCaducada: () => { location.reload(); },
});
const api = (ruta, datos) => cliente.pedir(ruta, datos);

function el(etiqueta, atributos, ...hijos) {
  const n = document.createElement(etiqueta);
  for (const [k, v] of Object.entries(atributos || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "clase") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "texto") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const h of hijos.flat()) {
    if (h === null || h === undefined || h === false) continue;
    n.append(h instanceof Node ? h : document.createTextNode(String(h)));
  }
  return n;
}

function tamano(b) {
  if (b === null || b === undefined) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = Number(b);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function fecha(iso) { return iso ? iso.replace("T", " ") : "—"; }

function avisar(texto, esError) {
  const a = document.getElementById("aviso-flotante");
  a.textContent = texto;
  a.classList.toggle("error", !!esError);
  a.classList.remove("oculto");
  clearTimeout(avisar._t);
  avisar._t = setTimeout(() => a.classList.add("oculto"), esError ? 9000 : 5000);
}

function confirmar(titulo, texto, textoBoton) {
  const d = document.getElementById("dialogo");
  document.getElementById("dialogo-titulo").textContent = titulo;
  document.getElementById("dialogo-texto").textContent = texto;
  document.getElementById("dialogo-si").textContent = textoBoton || "Confirmar";
  return new Promise(res => {
    d.addEventListener("close", () => res(d.returnValue === "si"), { once: true });
    d.showModal();
  });
}

function tabla(columnas, filas, vacio) {
  if (!filas.length) return el("div", { clase: "vacio", texto: vacio || "Sin resultados." });
  return el("table", {},
    el("thead", {}, el("tr", {}, columnas.map(c => el("th", { clase: c.num ? "num" : null }, c.titulo)))),
    el("tbody", {}, filas.map(f => el("tr", {}, columnas.map(c => {
      const v = c.valor(f);
      return v instanceof Node && v.tagName === "TD" ? v : el("td", { clase: c.clase || (c.num ? "num" : null) }, v);
    })))));
}

function etiquetaClase(c) { return el("span", { clase: `etiqueta-clase c-${c}`, texto: c }); }

function celdaEstado(f) {
  return el("td", {}, el("span", { clase: `estado e-${f.estado}`, texto: f.estado }),
    f.mensaje ? el("span", { clase: "mensaje", texto: f.mensaje }) : null);
}

// ---------------------------------------------------------------- tareas en segundo plano
async function lanzarTarea(ruta, datos) {
  try {
    const r = await api(ruta, datos);
    estadoUI.tareaVista = r.tarea.id;
    mostrarTarea(r.tarea);
    vigilarTarea();
  } catch (e) { avisar(e.message, true); }
}

function mostrarTarea(t) {
  const caja = document.getElementById("tarea");
  const activa = t && t.estado === "en_curso";
  caja.classList.toggle("oculto", !activa);
  if (activa) document.getElementById("tarea-texto").textContent = `${nombreTarea(t.tipo)}: ${t.progreso}`;
  document.querySelectorAll("#btn-escanear, #btn-escanear-rapido, #btn-cola-rapido, #btn-simular, #btn-ejecutar, #btn-duplicados")
    .forEach(b => { b.disabled = activa; });
}

function nombreTarea(t) {
  return { escaneo: "Escaneando", vigilancia: "Buscando nuevos", ejecucion: "Ejecutando", simulacion: "Simulando",
           deshacer: "Deshaciendo", duplicados: "Buscando duplicados" }[t] || t;
}

async function vigilarTarea() {
  clearTimeout(vigilarTarea._t);
  let t;
  try { t = (await api("/api/tarea")).tarea; } catch (e) { return; }
  mostrarTarea(t);
  if (t && t.estado === "en_curso") { vigilarTarea._t = setTimeout(vigilarTarea, 1000); return; }
  if (t && estadoUI.tareaVista === t.id) {
    estadoUI.tareaVista = null;
    if (t.estado === "error") avisar(`${nombreTarea(t.tipo)} falló: ${t.error}`, true);
    else avisar(resumenTarea(t));
    if ((t.tipo === "ejecucion" || t.tipo === "simulacion") && t.resultado) pintarResultadoEjecucion(t.resultado);
  }
  refrescar();
}

function resumenTarea(t) {
  const r = t.resultado || {};
  if (t.tipo === "escaneo" || t.tipo === "vigilancia")
    return `Escaneo terminado en ${r.segundos} s: ${r.elementos} elementos, ${r.nuevos_en_cola} nuevos en la cola.`;
  if (t.tipo === "ejecucion" || t.tipo === "simulacion")
    return `${r.modo === "SIMULACION" ? "Simulación" : "Ejecución"}: ${r.correctos} de ${r.total} correctos.`;
  if (t.tipo === "deshacer") return `Deshacer: ${r.estado}`;
  if (t.tipo === "duplicados") return `Duplicados: ${r.grupos} grupos (${tamano(r.bytes_repetidos)} repetidos).`;
  return "Tarea terminada.";
}

// ---------------------------------------------------------------- pestañas
function cambiarPestana(p) {
  estadoUI.pestana = p;
  document.querySelectorAll(".pestanas button").forEach(b => b.classList.toggle("activa", b.dataset.pestana === p));
  document.querySelectorAll(".panel").forEach(s => s.classList.toggle("oculto", s.id !== `p-${p}`));
  refrescar();
}

async function refrescar() {
  try {
    await pintarContadores();
    const f = { resumen: pintarResumen, elementos: pintarElementos, cola: pintarCola, ejecutar: pintarEjecutar,
                historial: pintarHistorial, duplicados: pintarDuplicados, ajustes: pintarAjustes }[estadoUI.pestana];
    if (f) await f();
  } catch (e) { avisar(e.message, true); }
}

let ultimoEstado = null;
async function pintarContadores() {
  const e = await api("/api/estado");
  ultimoEstado = e;
  document.getElementById("n-cola").textContent = e.en_cola || "";
  const aprob = e.conteo.filter(c => c.estado === "aprobado").reduce((s, c) => s + c.n, 0);
  document.getElementById("n-aprobados").textContent = aprob || "";
  document.getElementById("min-vigilancia").textContent = e.vigilancia_minutos || "—";
  mostrarTarea(e.tarea);
  if (e.tarea && e.tarea.estado === "en_curso") { clearTimeout(vigilarTarea._t); vigilarTarea._t = setTimeout(vigilarTarea, 1000); }
}

// ---------------------------------------------------------------- Resumen
async function pintarResumen() {
  const e = ultimoEstado;
  const discos = document.getElementById("discos");
  discos.replaceChildren(...e.discos.map(d => {
    if (!d.presente) return el("div", { clase: "disco" }, el("div", { clase: "titulo" }, `${d.etiqueta} ${d.unidad}`), el("p", { clase: "tenue", texto: "No disponible (¿está conectado?)" }));
    const pct = d.usado / d.total;
    const barra = el("span");
    barra.style.width = `${(pct * 100).toFixed(1)}%`;
    return el("div", { clase: "disco" },
      el("div", { clase: "titulo" }, el("span", {}, `${d.etiqueta} ${d.unidad}`), el("span", {}, `${(pct * 100).toFixed(0)} % usado`)),
      el("div", { clase: `medidor${pct > 0.9 ? " alto" : ""}` }, barra),
      el("div", { clase: "tenue" }, `${tamano(d.libre)} libres de ${tamano(d.total)}`));
  }));
  const kpi = (valor, etiqueta) => el("div", { clase: "kpi" }, el("div", { clase: "valor", texto: valor }), el("div", { clase: "etiqueta", texto: etiqueta }));
  const n = cls => e.conteo.filter(c => c.clasificacion === cls).reduce((s, c) => s + c.n, 0);
  document.getElementById("kpis").replaceChildren(
    kpi(tamano(e.libera_potencial_migrable), "Liberable en C: (Migrable, sin decidir o aprobado)"),
    kpi(tamano(e.libera_potencial_revisar), "Posible además si apruebas lo de Revisar"),
    kpi(tamano(e.libera_aprobado), "Aprobado, pendiente de ejecutar"),
    kpi(tamano(e.liberado_historico_bytes), "Ya liberado de C: según los registros"),
    kpi(`${n("Migrable")} / ${n("Revisar")} / ${n("Intocable")}`, "Migrable / Revisar / Intocable"),
    kpi(String(e.en_cola), "Nuevos en la cola"));
  const u = e.ultimo_escaneo;
  document.getElementById("ultimo-escaneo").textContent = u
    ? `Último escaneo (${u.modo}): ${fecha(u.inicio)} · ${u.estado}${u.resumen && u.resumen.segundos ? ` · ${u.resumen.segundos} s` : ""}`
    : "Aún no hay escaneos: empieza con un escaneo completo.";

  const { analisis } = await api("/api/analisis");
  const titulos = { raiz_sistema: "Raíz de C:", perfil: "Perfil de usuario", appdata: "AppData (primer nivel)",
                    conocidas: "Cachés y carpetas conocidas", raiz_destino: "Taxonomía en el disco destino" };
  const cont = document.getElementById("analisis");
  const grupos = {};
  for (const a of analisis) (grupos[a.zona] = grupos[a.zona] || []).push(a);
  const bloques = Object.keys(titulos).filter(z => grupos[z]).map(z => {
    const filas = grupos[z].slice(0, 15);
    const max = Math.max(1, ...filas.map(f => f.bytes || 0));
    return el("div", { clase: "zona-analisis" }, el("h3", { texto: titulos[z] }), el("div", { clase: "tabla-contenedor" }, tabla([
      { titulo: "Carpeta", valor: f => el("td", { clase: "nombre" }, f.nombre, el("span", { clase: "ruta-sub", texto: f.ruta })) },
      { titulo: "Tamaño", num: true, valor: f => `${tamano(f.bytes)}${f.parcial ? " (parcial)" : ""}` },
      { titulo: "", valor: f => { const s = el("span"); s.style.width = `${((f.bytes || 0) / max * 100).toFixed(1)}%`; return el("div", { clase: "barra-mini" }, s); } },
      { titulo: "Archivos", num: true, valor: f => f.n_archivos ?? "—" },
      { titulo: "Nota", valor: f => f.nota || "" },
    ], filas)));
  });
  cont.replaceChildren(...(bloques.length ? bloques : [el("p", { clase: "tenue", texto: "Sin datos: ejecuta un escaneo completo." })]));
}

// ---------------------------------------------------------------- Elementos
function columnasElementos(seleccion, conAcciones) {
  return [
    { titulo: "", valor: f => {
        const c = el("input", { type: "checkbox", "aria-label": "seleccionar" });
        c.checked = seleccion.has(f.id);
        c.addEventListener("change", () => { c.checked ? seleccion.add(f.id) : seleccion.delete(f.id); });
        return c;
      } },
    { titulo: "Elemento", valor: f => el("td", { clase: "nombre" }, `${f.tipo === "Archivo" ? "" : "[" + f.tipo + "] "}${f.nombre}`,
        el("span", { clase: "ruta-sub", texto: f.ruta })) },
    { titulo: "Tamaño", num: true, valor: f => `${tamano(f.bytes)}${f.parcial ? "+" : ""}` },
    { titulo: "Clasificación", valor: f => el("td", {}, etiquetaClase(f.clasificacion), f.motivo ? el("span", { clase: "mensaje", texto: f.motivo }) : null) },
    { titulo: "Destino propuesto", clase: "ruta", valor: f => f.destino || "—" },
    { titulo: "Libera C:", num: true, valor: f => f.libera_c ? tamano(f.libera_c) : "—" },
    { titulo: "Estado", valor: celdaEstado },
    conAcciones ? { titulo: "", valor: f => el("td", { clase: "acciones" },
        f.clasificacion !== "Intocable" && f.destino && ["pendiente", "rechazado", "error"].includes(f.estado)
          ? el("button", { clase: "mini primario", texto: "Aprobar", onclick: () => decidir([f.id], "aprobar") }) : null,
        f.clasificacion !== "Intocable" && ["pendiente", "aprobado", "error"].includes(f.estado)
          ? el("button", { clase: "mini", texto: "Rechazar", onclick: () => decidir([f.id], "rechazar") }) : null,
        ["aprobado", "rechazado", "error"].includes(f.estado)
          ? el("button", { clase: "mini", texto: "Pendiente", onclick: () => decidir([f.id], "pendiente") }) : null) } : null,
  ].filter(Boolean);
}

function cabeceraSeleccionTodo(contenedor, filas, seleccion) {
  const th = contenedor.querySelector("th");
  if (!th || !filas.length) return;
  const c = el("input", { type: "checkbox", "aria-label": "seleccionar todo lo visible" });
  c.addEventListener("change", () => {
    filas.forEach(f => { if (c.checked) seleccion.add(f.id); else seleccion.delete(f.id); });
    contenedor.querySelectorAll("tbody input[type=checkbox]").forEach(x => { x.checked = c.checked; });
  });
  th.replaceChildren(c);
}

async function pintarElementos() {
  const p = new URLSearchParams({
    clasificacion: document.getElementById("f-clasificacion").value, estado: document.getElementById("f-estado").value,
    zona: document.getElementById("f-zona").value, orden: document.getElementById("f-orden").value,
    q: document.getElementById("f-q").value, limite: estadoUI.limite });
  const r = await api(`/api/elementos?${p}`);
  const cont = document.getElementById("tabla-elementos");
  cont.replaceChildren(tabla(columnasElementos(estadoUI.seleccion, true), r.elementos, "No hay elementos con estos filtros. ¿Ya escaneaste?"));
  cabeceraSeleccionTodo(cont, r.elementos, estadoUI.seleccion);
  document.getElementById("resumen-elementos").textContent =
    `${r.elementos.length} de ${r.total} elementos · ${tamano(r.bytes)} · liberarían ${tamano(r.libera_c)} en C:`;
  document.getElementById("mas-elementos").classList.toggle("oculto", r.elementos.length >= r.total);
}

async function decidir(ids, accion) {
  if (!ids.length) { avisar("No hay nada seleccionado."); return; }
  try {
    const r = await api("/api/elementos/decidir", { ids, accion });
    const omit = r.omitidos.length ? ` · ${r.omitidos.length} omitidos (${r.omitidos[0].motivo}${r.omitidos.length > 1 ? "…" : ""})` : "";
    avisar(`${r.cambiados.length} elementos: ${accion}${omit}`, r.omitidos.length > 0 && !r.cambiados.length);
    r.cambiados.forEach(i => { estadoUI.seleccion.delete(i); estadoUI.seleccionCola.delete(i); });
    refrescar();
  } catch (e) { avisar(e.message, true); }
}

// ---------------------------------------------------------------- Cola
async function pintarCola() {
  const r = await api("/api/elementos?cola=1&orden=detectado&limite=1000");
  const cont = document.getElementById("tabla-cola");
  cont.replaceChildren(tabla(columnasElementos(estadoUI.seleccionCola, true), r.elementos, "No hay elementos nuevos en la cola."));
  cabeceraSeleccionTodo(cont, r.elementos, estadoUI.seleccionCola);
}

// ---------------------------------------------------------------- Ejecutar
async function pintarEjecutar() {
  const r = await api("/api/elementos?estado=aprobado&limite=2000&orden=bytes");
  document.getElementById("tabla-aprobados").replaceChildren(tabla(columnasElementos(new Set(), true).slice(1), r.elementos,
    "No hay elementos aprobados. Apruébalos en Elementos o en la Cola."));
  document.getElementById("resumen-aprobados").textContent = `${r.total} aprobados · ${tamano(r.bytes)} · liberarían ${tamano(r.libera_c)} en C:`;
  document.getElementById("btn-ejecutar").disabled = !r.total || (ultimoEstado && ultimoEstado.tarea && ultimoEstado.tarea.estado === "en_curso");
  if (!document.getElementById("resultado-ejecucion").childElementCount) {
    const { ejecuciones } = await api("/api/ejecuciones");
    const ult = ejecuciones.find(x => x.operacion === "plan");
    if (ult) pintarResultadoEjecucion({ modo: ult.modo, resultados: ult.resultado || [], registro: ult.registro, fecha: ult.fecha });
  }
}

function pintarResultadoEjecucion(r) {
  const cont = document.getElementById("resultado-ejecucion");
  cont.replaceChildren(
    el("p", { clase: "tenue", texto: `${r.modo === "SIMULACION" ? "Simulación" : "Ejecución"}${r.fecha ? " del " + fecha(r.fecha) : ""}${r.registro ? " · registro: " + r.registro : ""}` }),
    tabla([
      { titulo: "Origen", clase: "ruta", valor: f => f.ruta || "" },
      { titulo: "Destino", clase: "ruta", valor: f => f.destino || "" },
      { titulo: "Resultado", valor: f => el("span", { clase: `estado ${f.ok ? "e-ejecutado" : "e-error"}`, texto: f.estado }) },
    ], r.resultados || [], "Sin operaciones."));
}

// ---------------------------------------------------------------- Historial
async function pintarHistorial() {
  const p = new URLSearchParams({ q: document.getElementById("h-q").value, estado: document.getElementById("h-estado").value, limite: 500 });
  const r = await api(`/api/historial?${p}`);
  document.getElementById("resumen-historial").textContent =
    `${r.movimientos.length} de ${r.total} movimientos registrados (CSV del motor, solo lectura).`;
  document.getElementById("tabla-historial").replaceChildren(tabla([
    { titulo: "Fecha", valor: f => fecha(f.fecha) },
    { titulo: "Tipo", valor: f => f.tipo },
    { titulo: "Estado", valor: f => el("td", {}, el("span", { clase: "estado", texto: f.estado }), f.deshecho ? el("span", { clase: "mensaje", texto: "deshecho" }) : null) },
    { titulo: "Origen", clase: "ruta", valor: f => f.origen },
    { titulo: "Destino", clase: "ruta", valor: f => f.destino },
    { titulo: "MB", num: true, valor: f => f.mb ?? "" },
    { titulo: "Registro", valor: f => f.archivo_nombre },
    { titulo: "", valor: f => el("td", { clase: "acciones" }, f.deshacible ? [
        el("button", { clase: "mini", texto: "Simular", onclick: () => lanzarTarea("/api/deshacer", { movimiento_id: f.id, simular: true }) }),
        el("button", { clase: "mini peligro", texto: "Deshacer", onclick: async () => {
          if (await confirmar("Deshacer movimiento", `Se devolverá:\n${f.destino}\na su ubicación original:\n${f.origen}\nSi el origen está ocupado no se sobrescribe.`, "Deshacer"))
            lanzarTarea("/api/deshacer", { movimiento_id: f.id, simular: false });
        } })] : null) },
  ], r.movimientos, "No hay movimientos registrados."));
  const d = await api("/api/decisiones?limite=200");
  document.getElementById("tabla-decisiones").replaceChildren(tabla([
    { titulo: "Fecha", valor: f => fecha(f.fecha) },
    { titulo: "Acción", valor: f => f.accion },
    { titulo: "Ruta", clase: "ruta", valor: f => f.ruta || "" },
    { titulo: "Detalle", clase: "ruta", valor: f => f.detalle || "" },
  ], d.decisiones, "Aún no hay decisiones."));
}

// ---------------------------------------------------------------- Duplicados
async function pintarDuplicados() {
  const r = await api("/api/duplicados");
  const s = r.resumen;
  document.getElementById("resumen-duplicados").textContent = s
    ? `${fecha(s.fecha)} · ${s.archivos_revisados} archivos revisados · ${s.grupos} grupos · ${tamano(s.bytes_repetidos)} repetidos${s.cortado_por_tiempo ? " · cortado por tiempo (resultado parcial)" : ""}`
    : "Aún no se ha buscado.";
  document.getElementById("lista-duplicados").replaceChildren(...r.grupos.slice(0, 300).map(g =>
    el("div", { clase: "grupo-dup" },
      el("strong", {}, `${g.rutas.length} copias de ${tamano(g.bytes)}`),
      el("span", { clase: "tenue" }, ` · hash ${g.nivel} ${g.huella}…`),
      el("ul", {}, g.rutas.map(x => el("li", { texto: x }))))));
}

// ---------------------------------------------------------------- Ajustes
async function pintarAjustes() {
  document.getElementById("config").textContent = JSON.stringify(await api("/api/config"), null, 2);
  const r = await api("/api/respaldos");
  document.getElementById("dir-respaldos").textContent = r.carpeta ? `Carpeta: ${r.carpeta}` : "Sin carpeta de registro configurada.";
  document.getElementById("tabla-respaldos").replaceChildren(tabla([
    { titulo: "Fecha", valor: f => fecha(f.fecha) },
    { titulo: "Motivo", valor: f => f.motivo || "" },
    { titulo: "Archivos", valor: f => f.archivos.join(", ") },
    { titulo: "", valor: f => el("td", { clase: "acciones" }, el("button", { clase: "mini", texto: "Restaurar", onclick: async () => {
        if (!(await confirmar("Restaurar configuración", `Se guardará la configuración actual y se restaurará la copia del ${fecha(f.fecha)}.`, "Restaurar"))) return;
        try { const x = await api("/api/respaldos/restaurar", { nombre: f.nombre }); avisar(`Restaurado. ${x.aviso}`); pintarAjustes(); }
        catch (e) { avisar(e.message, true); }
      } })) },
  ], r.respaldos, "Aún no hay respaldos (se crean al cambiar la configuración personal)."));
}

// ---------------------------------------------------------------- eventos
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".pestanas button").forEach(b => b.addEventListener("click", () => cambiarPestana(b.dataset.pestana)));
  document.getElementById("btn-escanear").addEventListener("click", () => lanzarTarea("/api/escanear", { modo: "completo" }));
  document.getElementById("btn-escanear-rapido").addEventListener("click", () => lanzarTarea("/api/escanear", { modo: "rapido" }));
  document.getElementById("btn-cola-rapido").addEventListener("click", () => lanzarTarea("/api/escanear", { modo: "rapido" }));
  ["f-clasificacion", "f-estado", "f-zona", "f-orden"].forEach(id => document.getElementById(id).addEventListener("change", () => { estadoUI.limite = 300; pintarElementos(); }));
  let t;
  document.getElementById("f-q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(pintarElementos, 300); });
  document.getElementById("h-q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(pintarHistorial, 300); });
  document.getElementById("h-estado").addEventListener("change", pintarHistorial);
  document.getElementById("mas-elementos").addEventListener("click", () => { estadoUI.limite += 300; pintarElementos(); });
  document.querySelectorAll("[data-lote]").forEach(b => b.addEventListener("click", () => decidir([...estadoUI.seleccion], b.dataset.lote)));
  document.querySelectorAll("[data-lote-cola]").forEach(b => b.addEventListener("click", async () => {
    const ids = [...estadoUI.seleccionCola];
    if (b.dataset.loteCola === "vistos") {
      if (!ids.length) { avisar("No hay nada seleccionado."); return; }
      await api("/api/cola/vistos", { ids }); ids.forEach(i => estadoUI.seleccionCola.delete(i)); refrescar();
    } else decidir(ids, b.dataset.loteCola);
  }));
  document.getElementById("btn-simular").addEventListener("click", () => lanzarTarea("/api/ejecutar", { simular: true }));
  document.getElementById("btn-ejecutar").addEventListener("click", async () => {
    const r = await api("/api/elementos?estado=aprobado&limite=1");
    if (!r.total) { avisar("No hay elementos aprobados."); return; }
    if (await confirmar("Ejecutar movimientos aprobados",
      `Se moverán ${r.total} elementos (${tamano(r.bytes)}), liberando ${tamano(r.libera_c)} en C:. Cada uno se revalida antes de moverse, nada se sobrescribe y todo queda en el registro para poder deshacerlo.`,
      "Ejecutar"))
      lanzarTarea("/api/ejecutar", { simular: false });
  });
  document.getElementById("btn-importar").addEventListener("click", async () => {
    try { const r = await api("/api/historial/importar", {}); avisar(`${r.importadas} filas nuevas importadas.`); pintarHistorial(); }
    catch (e) { avisar(e.message, true); }
  });
  document.getElementById("btn-duplicados").addEventListener("click", () => lanzarTarea("/api/duplicados/buscar", {}));
  document.getElementById("btn-respaldar").addEventListener("click", async () => {
    try { const r = await api("/api/respaldos/crear", {}); avisar(r.respaldo ? "Respaldo creado." : "No hay configuración personal que respaldar."); pintarAjustes(); }
    catch (e) { avisar(e.message, true); }
  });
  document.getElementById("btn-detener").addEventListener("click", async () => {
    if (!(await confirmar("Detener el servidor", "La interfaz dejará de funcionar hasta que vuelvas a abrir el gestor con el acceso directo. Si hay una tarea en curso, se interrumpirá.", "Detener"))) return;
    try { await api("/api/detener", {}); avisar("Servidor detenido. Ciérralo o vuelve a abrirlo con el acceso directo."); }
    catch (e) { avisar(e.message, true); }
  });
  refrescar();
  setInterval(() => { if (!document.hidden) pintarContadores().catch(() => {}); }, 30000);
});
