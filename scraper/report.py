"""Genera docs/index.html, el panel que se publica en GitHub Pages.

El indicador principal es el viaje completo (hotel + vuelos), porque es el
único número que se puede comparar de verdad: la estancia más barata por noche
no tiene por qué ser el viaje más barato.
"""

from __future__ import annotations

import json
from pathlib import Path

PLANTILLA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tenerife agosto 2027 · rastreo de precios</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js"></script>
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:#fbfaf8; color:#1b1a17;
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  max-width:1000px; margin-inline:auto; }
h1 { font-size:21px; margin:0 0 4px; letter-spacing:-.01em; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.07em;
  color:#7a736a; margin:30px 0 10px; font-weight:600; }
.sub { color:#6b645b; font-size:13.5px; margin:0 0 20px; }
.card { background:#fff; border:1px solid #e8e3dc; border-radius:10px; padding:16px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; }
.kpi .label { font-size:11.5px; text-transform:uppercase; letter-spacing:.06em;
  color:#8a8279; margin-bottom:6px; }
.kpi .value { font-size:25px; font-weight:650; letter-spacing:-.02em; }
.kpi .note { font-size:12.5px; color:#7a736a; margin-top:4px; }
.banner { border-left:3px solid #c9803a; background:#fdf6ee; padding:13px 16px;
  border-radius:6px; margin-bottom:22px; font-size:14px; }
.banner.ok { border-color:#3f8f52; background:#f0f7f1; }
/* En móvil las columnas no caben: la tabla se desliza en horizontal
   dentro de su tarjeta en vez de cortarse. */
.tabla { overflow-x:auto; -webkit-overflow-scrolling:touch; }
table { width:100%; min-width:540px; border-collapse:collapse; font-size:13.5px; }
th { text-align:left; font-weight:600; color:#7a736a; font-size:11.5px;
  text-transform:uppercase; letter-spacing:.05em; padding:8px 10px;
  border-bottom:1px solid #e8e3dc; white-space:nowrap; }
td { padding:9px 10px; border-bottom:1px solid #f1ede7; white-space:nowrap; }
tr:last-child td { border-bottom:none; }
.num { font-variant-numeric:tabular-nums; }
.best { background:#f4f9f5; font-weight:650; }
.tenue { color:#8a8279; }
.nota { color:#8a8279; font-size:12px; margin:12px 0 0; white-space:normal; }
.chartbox { height:250px; position:relative; }
a { color:#1f5f9e; text-decoration:none; }
a:hover { text-decoration:underline; }
footer { margin-top:30px; color:#9a938a; font-size:12px; }
@media (max-width:700px) {
  body { padding:14px; }
  h1 { font-size:19px; }
  .card { padding:12px; }
  .kpi .value { font-size:22px; }
  th, td { padding:7px 8px; }
}
</style>
</head>
<body>
<h1>Bilbao → Tenerife · agosto 2027</h1>
<p class="sub">Landmar Costa Los Gigantes · 2 adultos + 1 niño · Todo Incluido ·
solo cancelación gratuita · solo vuelos directos ·
obligatorio estar la noche del 8 de agosto</p>

<div class="banner" id="banner"></div>

<div class="grid">
  <div class="card kpi"><div class="label">Viaje completo</div>
    <div class="value" id="kTrip">—</div><div class="note" id="kTripNote"></div></div>
  <div class="card kpi"><div class="label">Mejor €/noche (hotel)</div>
    <div class="value" id="kBest">—</div><div class="note" id="kBestNote"></div></div>
  <div class="card kpi"><div class="label">Vuelos directos</div>
    <div class="value" id="kFly">—</div><div class="note" id="kFlyNote"></div></div>
  <div class="card kpi"><div class="label">Última comprobación</div>
    <div class="value" id="kLast">—</div><div class="note" id="kRunsNote"></div></div>
</div>

<h2>Viaje completo · hotel + vuelos</h2>
<div class="card"><div class="tabla"><table id="c"><thead><tr>
<th>Entrada</th><th>Salida</th><th>N</th><th>Habitación</th><th>Aerolínea</th>
<th>Hotel</th><th>Vuelos</th><th>Total</th></tr></thead><tbody></tbody></table></div>
<p class="nota" id="cNota"></p></div>

<h2>Evolución (€/noche)</h2>
<div class="card chartbox"><canvas id="chart"></canvas></div>

<h2>Web oficial del hotel · mejores opciones</h2>
<div class="card"><div class="tabla"><table id="t"><thead><tr>
<th>Entrada</th><th>Salida</th><th>Noches</th><th>Habitación</th>
<th>€/noche</th><th>Total</th></tr></thead><tbody></tbody></table></div></div>

<h2>En otras webs (vía Google Hotels)</h2>
<div class="card"><div class="tabla"><table id="o"><thead><tr>
<th>Entrada</th><th>Salida</th><th>Noches</th><th>Web</th>
<th>€/noche</th><th>Total</th></tr></thead><tbody></tbody></table></div>
<p class="nota" id="oNota"></p></div>

<h2>Vuelos directos Bilbao → Tenerife</h2>
<div class="card"><div class="tabla"><table id="v"><thead><tr>
<th>Aerolínea</th><th>Fechas</th><th>Horario</th><th>Duración</th>
<th>Destino</th><th>Total 3 pax</th></tr></thead><tbody></tbody></table></div>
<p class="nota" id="vNota"></p></div>

<footer>Actualizado automáticamente por GitHub Actions ·
<a href="https://www.landmarhotels.com/es/landmar-costa-los-gigantes.html">web del hotel</a>
</footer>

<script>
const DATA = __DATOS__;
const eur = n => n == null ? "—" :
  new Intl.NumberFormat("es-ES",{style:"currency",currency:"EUR",maximumFractionDigits:0}).format(n);
const vacio = (tb, n, msg) => {
  tb.insertRow().innerHTML = '<td colspan="' + n + '" style="color:#8a8279;' +
    'text-align:center;padding:24px;white-space:normal">' + msg + '</td>';
};

const mejor = DATA.mejor_precio_historico;
document.getElementById("kBest").textContent = eur(mejor && mejor.por_noche);
document.getElementById("kBestNote").textContent = mejor
  ? mejor.habitacion + " · " + mejor.entrada + " → " + mejor.salida +
    " · " + mejor.noches + " noches · " + eur(mejor.total) + " en total"
  : "sin datos";
document.getElementById("kLast").textContent = (DATA.ultima_comprobacion || "—").slice(5);
document.getElementById("kRunsNote").textContent =
  (DATA.registros || []).length + " comprobaciones · 2 al día";
document.getElementById("kFly").textContent = DATA.vuelos_abiertos ? "Abiertos" : "Cerrados";
const ultimo = (DATA.registros || []).slice(-1)[0] || {};
document.getElementById("kFlyNote").textContent = ultimo.detalle_vuelos || "";

const b = document.getElementById("banner");
if (DATA.vuelos_abiertos) {
  b.className = "banner ok";
  b.innerHTML = "<b>Los vuelos ya están a la venta.</b> Compara el paquete " +
    "contra vuelo y hotel por separado antes de reservar.";
} else {
  b.innerHTML = "<b>Los vuelos todavía no están a la venta.</b> Las aerolíneas " +
    "abren 10–12 meses antes. El hotel sí se puede reservar con cancelación gratuita.";
}

// --- viaje completo -----------------------------------------------------
// Es el número que de verdad importa: la estancia más barata por noche no
// tiene por qué ser el viaje más barato, porque cada noche extra suma hotel
// pero no suma billete.
const combis = DATA.combinaciones || [];
const pax = DATA.pasajeros || 3;
document.getElementById("kTrip").textContent = combis.length ? eur(combis[0].total) : "—";
document.getElementById("kTripNote").textContent = combis.length
  ? combis[0].noches + " noches · " + combis[0].entrada + " → " + combis[0].salida +
    " · hotel " + eur(combis[0].hotel_total) + " + vuelos " + eur(combis[0].vuelos_total)
  : "faltan vuelos para poder sumarlo";

const cb = document.querySelector("#c tbody");
combis.forEach((c, i) => {
  const tr = cb.insertRow();
  tr.innerHTML = '<td class="num">' + c.entrada + '</td>' +
    '<td class="num">' + c.salida + '</td>' +
    '<td class="num">' + c.noches + '</td>' +
    '<td>' + c.habitacion + '</td>' +
    '<td>' + c.aerolinea + ' (' + c.destino + ')</td>' +
    '<td class="num">' + eur(c.hotel_total) + '</td>' +
    '<td class="num">' + eur(c.vuelos_total) + '</td>' +
    '<td class="num' + (i === 0 ? ' best' : '') + '">' + eur(c.total) + '</td>';
});
if (!cb.rows.length) {
  vacio(cb, 8, DATA.vuelos_abiertos
    ? "Hay vuelos, pero para fechas distintas a las mejores del hotel."
    : "Cuando las aerolíneas abran la venta se podrá sumar el viaje entero.");
}
document.getElementById("cNota").textContent = combis.length
  ? "Hotel para " + pax + " personas con Todo Incluido, más " + pax +
    " billetes directos de ida y vuelta, cotizados para 2 adultos y 1 niño." +
    " No incluye equipaje facturado ni el coche de alquiler desde el" +
    " aeropuerto, que es más caro desde Tenerife Norte que desde el Sur."
  : "";

const tb = document.querySelector("#t tbody");
(DATA.tarifas_actuales || []).forEach((t, i) => {
  const tr = tb.insertRow();
  tr.innerHTML = '<td class="num">' + t.entrada + '</td>' +
    '<td class="num">' + t.salida + '</td>' +
    '<td class="num">' + t.noches + '</td>' +
    '<td>' + t.habitacion + '</td>' +
    '<td class="num' + (i === 0 ? ' best' : '') + '">' + eur(t.por_noche) + '</td>' +
    '<td class="num">' + eur(t.total) + '</td>';
});
if (!tb.rows.length) vacio(tb, 6, "Sin tarifas leídas en la última pasada.");

// --- otras webs ---------------------------------------------------------
// Google devuelve el precio agregado del hotel en la búsqueda por zona, pero
// el desglose por web solo en la ficha. Si el desglose falta, se enseña igual
// el agregado: antes se mostraba "no publica precios" teniendo el dato.
const ob = document.querySelector("#o tbody");
let filasOta = 0;
(DATA.ofertas_otas || []).forEach(v => {
  const fuentes = v.fuentes || [];
  if (fuentes.length) {
    fuentes.forEach((f, i) => {
      const tr = ob.insertRow();
      tr.innerHTML = '<td class="num">' + v.entrada + '</td>' +
        '<td class="num">' + v.salida + '</td>' +
        '<td class="num">' + v.noches + '</td>' +
        '<td>' + f.web + '</td>' +
        '<td class="num' + (i === 0 ? ' best' : '') + '">' + eur(f.por_noche) + '</td>' +
        '<td class="num">' + eur(f.total) + '</td>';
      filasOta++;
    });
  } else if (v.por_noche != null) {
    const tr = ob.insertRow();
    tr.innerHTML = '<td class="num">' + v.entrada + '</td>' +
      '<td class="num">' + v.salida + '</td>' +
      '<td class="num">' + v.noches + '</td>' +
      '<td class="tenue">Google Hotels (mejor precio)</td>' +
      '<td class="num best">' + eur(v.por_noche) + '</td>' +
      '<td class="num">' + eur(v.total) + '</td>';
    filasOta++;
  }
});
let notaOtas = "";
if (!filasOta) {
  if (!DATA.otas_comprobado) {
    vacio(ob, 6, "Pendiente de configurar el secreto SERPAPI_KEY.");
  } else {
    vacio(ob, 6, "Google Hotels todavía no publica precios para estas fechas.");
    notaOtas = "Su calendario llega a unos 11 meses vista, así que agosto de 2027" +
      " entrará hacia septiembre de 2026. Se reintenta cada día. Última" +
      " comprobación: " + DATA.otas_comprobado + ".";
  }
} else {
  notaOtas = "Consultado el " + (DATA.otas_actualizado || DATA.otas_comprobado) +
    " · una vez al día. Precios orientativos de Google Hotels; confírmalos en" +
    " la web correspondiente antes de reservar.";
}
document.getElementById("oNota").textContent = notaOtas;

// --- vuelos -------------------------------------------------------------
const vb = document.querySelector("#v tbody");
(DATA.ofertas_vuelos || []).forEach((f, i) => {
  const tr = vb.insertRow();
  const total = f.precio_total || (f.precio * pax);
  const fechas = (f.ida && f.vuelta) ? (f.ida.slice(5) + " → " + f.vuelta.slice(5)) : "—";
  tr.innerHTML = '<td>' + f.aerolinea + '</td>' +
    '<td class="num">' + fechas + '</td>' +
    '<td class="num">' + (f.horario || "—") + '</td>' +
    '<td class="num">' + (f.duracion || "—") + '</td>' +
    '<td class="num">' + (f.destino_nombre || f.destino) + '</td>' +
    '<td class="num' + (i === 0 ? ' best' : '') + '">' + eur(total) + '</td>';
});
let notaVuelos = "";
if (!vb.rows.length) {
  vacio(vb, 6, ultimo.detalle_vuelos || "Todavía no hay vuelos a la venta.");
} else {
  const fuentes = [...new Set((DATA.ofertas_vuelos || []).map(f => f.fuente).filter(Boolean))];
  notaVuelos = "Solo vuelos directos, ida y vuelta, precio de los " + pax +
    " pasajeros juntos. Volotea vuela a Tenerife SUR (~45 min del hotel) y solo" +
    " miércoles y domingos; Vueling y Air Europa vuelan a Tenerife NORTE" +
    " (~1 h 15 del hotel) a diario. Equipaje facturado aparte." +
    (fuentes.length ? " Fuente: " + fuentes.join(", ") + "." : "") +
    " Leído el " + (DATA.vuelos_actualizado || "—") + ".";
}
document.getElementById("vNota").textContent = notaVuelos;

// Chart.js viene de un CDN. Si no carga —bloqueador de anuncios, red de
// empresa, el CDN caido— el resto del panel tiene que seguir funcionando: la
// grafica es lo accesorio, las tablas son el dato.
const pts = (DATA.registros || []).filter(r => r.mejor_por_noche != null);
if (pts.length && typeof Chart !== "undefined") {
  new Chart(document.getElementById("chart"), {
    type: "line",
    data: { labels: pts.map(p => p.fecha),
      datasets: [{ data: pts.map(p => p.mejor_por_noche), borderColor: "#1f5f9e",
        backgroundColor: "rgba(31,95,158,.08)", fill: true, tension: .25, pointRadius: 3 }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: v => v + " €/noche" } } } }
  });
} else if (pts.length) {
  const c = document.getElementById("chart");
  if (c && c.parentNode) {
    c.parentNode.innerHTML =
      '<p class="nota">No se pudo cargar la librería de la gráfica. ' +
      'El último precio registrado fue ' + pts[pts.length - 1].mejor_por_noche +
      ' €/noche.</p>';
  }
}
</script>
</body>
</html>
"""


def generar(datos: dict, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    ligero = {
        "mejor_precio_historico": datos.get("mejor_precio_historico"),
        "ultima_comprobacion": datos.get("ultima_comprobacion"),
        "vuelos_abiertos": datos.get("vuelos_abiertos", False),
        "tarifas_actuales": datos.get("tarifas_actuales", []),
        "ofertas_otas": datos.get("ofertas_otas", []),
        "otas_actualizado": datos.get("otas_actualizado"),
        "otas_comprobado": datos.get("otas_comprobado"),
        "ofertas_vuelos": datos.get("ofertas_vuelos", []),
        "vuelos_actualizado": datos.get("vuelos_actualizado"),
        "combinaciones": datos.get("combinaciones", []),
        "pasajeros": datos.get("pasajeros", 3),
        "registros": [
            {"fecha": r["fecha"],
             "mejor_por_noche": r.get("mejor_por_noche"),
             "mejor_total": r.get("mejor_total"),
             "mejor_viaje": r.get("mejor_viaje"),
             "detalle_vuelos": r.get("detalle_vuelos")}
            for r in datos.get("registros", [])
        ][-180:],
    }
    html = PLANTILLA.replace("__DATOS__", json.dumps(ligero, ensure_ascii=False))
    destino.write_text(html, encoding="utf-8")
