"""Precios del hotel en otras webs (Booking, Expedia, Agoda...) vía SerpApi.

Google Hotels ya agrega los precios de las principales OTAs para un hotel y
unas fechas concretas. SerpApi expone ese resultado como API, así que en vez de
scrapear Booking (bloqueado desde IPs de centro de datos y contra sus términos)
se consulta Google Hotels y se lee su comparativa.

Presupuesto
-----------
El plan gratuito de SerpApi son 250 búsquedas/mes. Este módulo se ejecuta una
vez al día y, con MAX_VENTANAS_OTAS=1, gasta ~30 al mes. El rastreo del hotel
sigue corriendo 2 veces al día.

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
CONSULTA = "Landmar Costa Los Gigantes Tenerife"
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


def _consultar(clave: str, entrada: date, salida: date, noches: int) -> dict | None:
    params = {
        "engine": "google_hotels",
        "q": CONSULTA,
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
        log.warning("OTAs: fallo de red en %s → %s (%s)", entrada, salida, exc)
        return None

    if r.status_code == 401:
        log.error("OTAs: SERPAPI_KEY rechazada (401). Revisa el secreto.")
        return None
    if r.status_code == 429:
        log.warning("OTAs: cuota de SerpApi agotada (429)")
        return None
    if r.status_code >= 400:
        log.warning("OTAs: HTTP %s en %s → %s", r.status_code, entrada, salida)
        return None

    try:
        datos = r.json()
    except ValueError:
        log.warning("OTAs: respuesta no es JSON en %s → %s", entrada, salida)
        return None

    if datos.get("error"):
        log.warning("OTAs: la API devuelve error: %s", datos["error"])
        return None

    props = datos.get("properties") or []
    hotel = _nuestro_hotel(props)
    if not hotel:
        # Log detallado a propósito: "no aparece" puede significar que Google
        # devolvió cero propiedades o que devolvió varias y ninguna casaba con
        # el filtro de nombre. Son causas distintas y hay que poder verlas.
        nombres = " | ".join((p.get("name") or "sin nombre") for p in props[:6])
        log.info("OTAs %s → %s: el hotel no está entre las %d propiedades "
                 "devueltas. Nombres: %s",
                 entrada, salida, len(props), nombres or "(lista vacía)")
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
