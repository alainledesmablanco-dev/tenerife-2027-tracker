"""Vuelos directos Bilbao → Tenerife vía SerpApi (motor `google_flights`).

Por qué existe este módulo
--------------------------
`vuelos.py` lee Google Flights con Playwright. Funciona en un portátil y NO
funciona en GitHub Actions: Google bloquea las IPs de centro de datos, así que
todas las pasadas de agosto de 2026 terminaron con el mismo mensaje —"No se
pudo leer Google Flights en esta pasada"— y `ofertas_vuelos` lleva vacío desde
el primer día. Es decir: Vueling y Air Europa nunca se han cotizado, ni una vez.

SerpApi resuelve exactamente ese problema: hace la consulta a Google desde sus
propias IPs y devuelve JSON. La clave ya está en el repo como secreto
`SERPAPI_KEY`, porque `otas.py` la usa para Google Hotels.

Qué aerolíneas cubre
--------------------
Comprobado en flightconnections (27-ago-2026):

    BIO → TFS (Tenerife Sur)    solo Volotea, 2 vuelos por semana
    BIO → TFN (Tenerife Norte)  Vueling y Air Europa, 16 por semana, a diario

O sea que Vueling y Air Europa vuelan al aeropuerto NORTE, no al Sur. El
rastreo trataba TFN como secundario ("Iberia y Vueling lo usan a menudo")
cuando en realidad es el único aeropuerto con vuelo diario desde Bilbao.
Tenerife Norte está a ~1 h 15 del hotel y Tenerife Sur a ~45 min, así que el
coche de alquiler sale algo más caro por el norte; a cambio, la fecha de
entrada al hotel deja de estar atada a los miércoles y domingos de Volotea.

Volotea sigue leyéndose con su propio módulo (`volotea.py`): Google no la
indexa en esta ruta.

Precio
------
A diferencia del scraping, aquí SÍ se puede cotizar la familia real
(`adults=2`, `children=1`), así que `precio_total` es el precio de los tres
juntos, ida y vuelta, no una estimación de multiplicar por 3. `precio` se
mantiene como precio por persona para que el panel antiguo siga pintando.

Presupuesto de la clave
-----------------------
Plan gratuito de SerpApi: 250 búsquedas/mes.

    OTAs        1 zona + 1 ficha, una vez al día        ~60/mes
    Vuelos      MAX_VENTANAS_VUELOS ventanas, 1 vez/día ~90/mes
                                                        -------
                                                        ~150/mes

Por eso `main.py` cotiza vuelos una sola vez por día natural, igual que las
OTAs. Si algún día se sube MAX_VENTANAS_VUELOS, hay que rehacer esta cuenta.

Secreto que usa:
    SERPAPI_KEY    clave de serpapi.com (la misma que otas.py)
"""

from __future__ import annotations

import logging
import os
from datetime import date

import requests

from . import config as cfg

log = logging.getLogger(__name__)

ENDPOINT = "https://serpapi.com/search.json"
TIMEOUT = 60
FUENTE = "Google Flights (SerpApi)"

# Una sola llamada cubre los dos aeropuertos: Google acepta varios códigos
# separados por coma. Hacer dos consultas duplicaría el gasto de la clave para
# el mismo resultado.
DESTINOS = "TFS,TFN"

NOMBRE_AEROPUERTO = {"TFS": "Tenerife Sur", "TFN": "Tenerife Norte"}

# Filtro de cordura sobre el total de los tres pasajeros, ida y vuelta.
PRECIO_MIN = 60.0
PRECIO_MAX = 6000.0


def configurado() -> bool:
    return bool(os.environ.get("SERPAPI_KEY"))


def _duracion(minutos) -> str | None:
    if not isinstance(minutos, (int, float)) or minutos <= 0:
        return None
    horas, mins = divmod(int(minutos), 60)
    return f"{horas} h {mins:02d} min" if mins else f"{horas} h"


def _hora(sello: str | None) -> str | None:
    """De '2027-08-08 06:45' saca '06:45'."""
    if not sello or " " not in sello:
        return None
    return sello.split(" ", 1)[1]


def _itinerario(bruto: dict, ida: date, vuelta: date) -> dict | None:
    """Convierte un itinerario de SerpApi en una oferta nuestra."""
    tramos = bruto.get("flights") or []
    if not tramos:
        return None

    # `stops=1` ya pide solo directos, pero la respuesta manda: si Google
    # devuelve un itinerario con dos tramos, es con escala y se descarta.
    escalas = "directo" if len(tramos) == 1 else f"{len(tramos) - 1} escala(s)"
    if cfg.SOLO_VUELOS_DIRECTOS and escalas != "directo":
        return None

    precio = bruto.get("price")
    if not isinstance(precio, (int, float)):
        return None
    precio = float(precio)
    if not PRECIO_MIN <= precio <= PRECIO_MAX:
        log.info("Vuelos SerpApi: %s € fuera de rango, descartado", precio)
        return None

    salida = tramos[0].get("departure_airport") or {}
    llegada = tramos[-1].get("arrival_airport") or {}
    destino = (llegada.get("id") or "?").upper()

    # Google reparte una misma reserva entre operadores. Nos quedamos con los
    # nombres distintos que aparezcan, en orden, sin repetir.
    nombres: list[str] = []
    for t in tramos:
        nombre = (t.get("airline") or "").strip()
        if nombre and nombre not in nombres:
            nombres.append(nombre)
    aerolinea = " + ".join(nombres) if nombres else "?"

    h_salida, h_llegada = _hora(salida.get("time")), _hora(llegada.get("time"))
    horario = f"{h_salida} – {h_llegada}" if h_salida and h_llegada else None

    return {
        "aerolinea": aerolinea,
        "destino": destino,
        "destino_nombre": NOMBRE_AEROPUERTO.get(destino, destino),
        "ida": ida.isoformat(),
        "vuelta": vuelta.isoformat(),
        "precio": round(precio / cfg.PASAJEROS, 2),   # por persona
        "precio_total": round(precio, 2),             # los tres, ida y vuelta
        "horario": horario,
        "duracion": _duracion(bruto.get("total_duration")),
        "escalas": escalas,
        "equipaje": "según tarifa de la aerolínea",
        "fuente": FUENTE,
    }


def _consultar(ida: date, vuelta: date) -> tuple[list[dict], str]:
    """Una ventana de fechas. Devuelve (ofertas, estado).

    Estados: 'ok' (hay vuelos), 'sin_vuelos' (Google responde pero no hay
    ninguno) y 'error' (no se pudo preguntar). La diferencia importa: decir
    "la venta está cerrada" cuando en realidad falló la consulta es el fallo
    que ya cometió la versión anterior de este rastreo.
    """
    params = {
        "engine": "google_flights",
        "api_key": os.environ["SERPAPI_KEY"],
        "departure_id": cfg.ORIGEN,
        "arrival_id": DESTINOS,
        "outbound_date": ida.isoformat(),
        "return_date": vuelta.isoformat(),
        "type": "1",                       # ida y vuelta
        "adults": str(cfg.ADULTOS),
        "children": str(cfg.NINOS),
        "travel_class": "1",               # turista
        "currency": "EUR",
        "hl": "es",
        "gl": "es",
    }
    if cfg.SOLO_VUELOS_DIRECTOS:
        params["stops"] = "1"              # 1 = sin escalas

    try:
        r = requests.get(ENDPOINT, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        datos = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Vuelos SerpApi %s→%s: fallo de consulta (%s)", ida, vuelta, exc)
        return [], "error"

    if datos.get("error"):
        # SerpApi devuelve 200 con {"error": "..."} cuando Google no tiene
        # resultados. Es respuesta válida, no avería.
        mensaje = str(datos["error"])
        log.info("Vuelos SerpApi %s→%s: %s", ida, vuelta, mensaje)
        sin_datos = any(p in mensaje.lower() for p in
                        ("hasn't returned any results", "no results",
                         "no flights", "not found"))
        return [], "sin_vuelos" if sin_datos else "error"

    brutos = (datos.get("best_flights") or []) + (datos.get("other_flights") or [])
    if not brutos:
        log.info("Vuelos SerpApi %s→%s: Google responde sin itinerarios", ida, vuelta)
        return [], "sin_vuelos"

    ofertas = [o for o in (_itinerario(b, ida, vuelta) for b in brutos) if o]
    if not ofertas:
        log.info("Vuelos SerpApi %s→%s: %d itinerarios, ninguno directo",
                 ida, vuelta, len(brutos))
        return [], "ok"          # hay venta abierta, pero no directa

    log.info("Vuelos SerpApi %s→%s: %d directos, el mejor %.0f € los %d (%s)",
             ida, vuelta, len(ofertas), min(o["precio_total"] for o in ofertas),
             cfg.PASAJEROS, min(ofertas, key=lambda o: o["precio_total"])["aerolinea"])
    return ofertas, "ok"


def buscar(ventanas: list[tuple[date, date, int]]) -> tuple[list[dict], list[str]]:
    """Cotiza varias ventanas. Devuelve (ofertas, estados)."""
    if not configurado():
        return [], []

    ofertas: list[dict] = []
    estados: list[str] = []
    for ida, vuelta, _noches in ventanas:
        encontradas, estado = _consultar(ida, vuelta)
        estados.append(estado)
        ofertas.extend(encontradas)

    ofertas.sort(key=lambda o: o["precio_total"])
    return ofertas, estados
