"""Precios del hotel en otras webs (Booking, Expedia, Agoda...) vía SerpApi.

Google Hotels ya agrega los precios de las principales OTAs para un hotel y
unas fechas concretas. SerpApi expone ese resultado como API, así que en vez de
scrapear Booking (bloqueado desde IPs de centro de datos y contra sus términos)
se consulta Google Hotels y se lee su comparativa.

Por qué se busca por zona y no por el nombre del hotel
-----------------------------------------------------
La primera versión consultaba "Landmar Costa Los Gigantes Tenerife" y devolvía
SIEMPRE cero propiedades, sin error de la API. El motivo: cuando la búsqueda
identifica un hotel concreto, Google no devuelve un listado sino la ficha de
ese hotel, y en ese modo el campo `properties` viene vacío.

La solución es buscar por zona y localizar nuestro hotel entre los resultados.
Como no está garantizado qué formulación funciona mejor, se prueban varias en
orden y se registra cuál dio resultados, para poder fijarla más adelante.

Presupuesto
-----------
El plan gratuito de SerpApi son 250 búsquedas/mes. Con MAX_VENTANAS_OTAS=1 y
como mucho 3 consultas de reserva, el tope son 3 al día ≈ 90 al mes. En cuanto
una consulta localiza el hotel se corta: las siguientes no se lanzan.

Secreto que usa:
    SERPAPI_KEY    clave de serpapi.com (plan gratuito)
"""

from __future__ import annotations

import logging
import os
from datetime import date

import requests

from . import config as cfg

log = logging.getLogger(__name__)

ENDPOINT = "https://serpapi.com/search.json"

# Se prueban en orden hasta que una localice el hotel. De zona pequeña a zona
# grande, para que salga lo más arriba posible entre los resultados.
CONSULTAS = (
    "hoteles en Puerto de Santiago Tenerife",
    "hoteles en Los Gigantes Tenerife",
    "hoteles en Santiago del Teide Tenerife",
)

# Para identificar nuestro hotel entre los resultados: todas estas palabras
# deben aparecer en el nombre devuelto por Google.
CLAVES_NOMBRE = ("landmar", "gigantes")
TIMEOUT = 40


def configurado() -> bool:
    return bool(os.environ.get("SERPAPI_KEY"))


def _extraer(bloque: dict | None) -> float | None:
    """Saca el número de un {"lowest": "123 €", "extracted_lowest": 123}."""
    if not isinstance(bloque, dict):
        return None
    valor = bloque.get("extracted_lowest")
    return float(valor) if isinstance(valor, (int, float)) else None


def _nuestro_hotel(propiedades: list[dict]) -> dict | None:
    for prop in propiedades:
        nombre = (prop.get("name") or "").lower()
        if all(clave in nombre for clave in CLAVES_NOMBRE):
            return prop
    return None


def _pedir(consulta: str, clave: str, entrada: date, salida: date) -> list[dict] | None:
    """Una llamada a la API. Devuelve la lista de propiedades, o None si falló."""
    params = {
        "engine": "google_hotels",
        "q": consulta,
        "check_in_date": entrada.isoformat(),
        "check_out_date": salida.isoformat(),
        "adults": cfg.ADULTOS,
        "currency": "EUR",
        "gl": "es",
        "hl": "es",
        "api_key": clave,
    }
    if cfg.NINOS:
        params["children"] = cfg.NINOS
        params["children_ages"] = str(cfg.EDAD_NINO)

    try:
        r = requests.get(ENDPOINT, params=params, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.warning("OTAs: fallo de red con '%s' (%s)", consulta, exc)
        return None

    if r.status_code == 401:
        log.error("OTAs: SERPAPI_KEY rechazada (401). Revisa el secreto.")
        return None
    if r.status_code == 429:
        log.warning("OTAs: cuota de SerpApi agotada (429)")
        return None
    if r.status_code >= 400:
        log.warning("OTAs: HTTP %s con '%s'", r.status_code, consulta)
        return None

    try:
        datos = r.json()
    except ValueError:
        log.warning("OTAs: respuesta no es JSON con '%s'", consulta)
        return None

    if datos.get("error"):
        log.warning("OTAs: la API devuelve error con '%s': %s", consulta, datos["error"])
        return None

    return datos.get("properties") or []


def _consultar(clave: str, entrada: date, salida: date, noches: int) -> dict | None:
    hotel = None
    for consulta in CONSULTAS:
        props = _pedir(consulta, clave, entrada, salida)
        if props is None:
            # Error de red o de la API: no tiene sentido gastar más consultas.
            return None
        log.info("OTAs '%s' → %d propiedades", consulta, len(props))
        if not props:
            continue
        hotel = _nuestro_hotel(props)
        if hotel:
            log.info("OTAs: hotel localizado con la consulta '%s'", consulta)
            break
        nombres = " | ".join((p.get("name") or "?") for p in props[:6])
        log.info("OTAs: no está entre los resultados. Primeros: %s", nombres)

    if not hotel:
        log.info("OTAs %s → %s: ninguna consulta localizó el hotel", entrada, salida)
        return None

    total = _extraer(hotel.get("total_rate"))
    por_noche = _extraer(hotel.get("rate_per_night"))
    if total is None and por_noche is not None:
        total = round(por_noche * noches, 2)
    if por_noche is None and total is not None:
        por_noche = round(total / noches, 2) if noches else None
    if total is None:
        log.info("OTAs: encontrado '%s' pero sin precio para %s → %s",
                 hotel.get("name"), entrada, salida)
        return None

    # Desglose por web. Google da el precio por noche de cada fuente; el total
    # lo calculamos nosotros si no viene.
    fuentes = []
    for oferta in hotel.get("prices") or []:
        pn = _extraer(oferta.get("rate_per_night"))
        tt = _extraer(oferta.get("total_rate"))
        if tt is None and pn is not None and noches:
            tt = round(pn * noches, 2)
        if pn is None and tt is None:
            continue
        fuentes.append({
            "web": oferta.get("source") or "?",
            "por_noche": pn,
            "total": tt,
        })
    fuentes.sort(key=lambda f: f["por_noche"] if f["por_noche"] is not None else 1e9)

    log.info("OTAs %s → %s: mejor %.0f €/noche (%.0f € total), %d webs",
             entrada, salida, por_noche or 0, total, len(fuentes))

    return {
        "entrada": entrada.isoformat(),
        "salida": salida.isoformat(),
        "noches": noches,
        "hotel": hotel.get("name"),
        "por_noche": por_noche,
        "total": total,
        "fuentes": fuentes[:8],
    }


def buscar(max_ventanas: int | None = None) -> list[dict]:
    """Consulta las mejores ventanas de fechas. Lista vacía si no hay clave."""
    clave = os.environ.get("SERPAPI_KEY")
    if not clave:
        log.info("OTAs: sin SERPAPI_KEY, se omite la comparativa")
        return []

    if max_ventanas is None:
        max_ventanas = int(os.environ.get("MAX_VENTANAS_OTAS", "3"))

    resultados = []
    for entrada, salida, noches in cfg.ventanas_validas()[:max_ventanas]:
        fila = _consultar(clave, entrada, salida, noches)
        if fila:
            resultados.append(fila)

    resultados.sort(key=lambda r: r["por_noche"] if r["por_noche"] is not None else 1e9)
    return resultados
