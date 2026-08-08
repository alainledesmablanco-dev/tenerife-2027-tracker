"""Detección de la apertura de venta de vuelos Bilbao → Tenerife.

No intenta sacar precios de las aerolíneas (cada web tiene su propio circo de
anti-bots). Lo que hace es responder a una única pregunta, que es la que de
verdad importa ahora mismo:

    ¿ya se puede comprar un vuelo directo BIO → TFS/TFN para agosto de 2027?

El indicador más fiable y menos intrusivo que encontramos es el propio buscador
de vuelo+hotel de Landmar: mientras los vuelos no estén cargados, rechaza el
origen BIO con un aviso de "la búsqueda aplica restricciones". En cuanto deje de
rechazarlo, la venta está abierta.
"""

from __future__ import annotations

import logging
from datetime import date

from playwright.sync_api import Page

from . import config as cfg

log = logging.getLogger(__name__)

SENALES_CERRADO = (
    "aplica restricciones",
    "calendario de precios",
    "calendario de disponibilidad",
    "no hay vuelos",
    "sin vuelos disponibles",
)


def venta_abierta(page: Page, entrada: date, salida: date) -> tuple[bool, str]:
    """Devuelve (abierta, detalle)."""
    url = (
        f"{cfg.BOOKING_URL}?namespace={cfg.NAMESPACE}&language=SPANISH"
        f"&numRooms=1&startDate={entrada.strftime('%d/%m/%Y')}"
        f"&endDate={salida.strftime('%d/%m/%Y')}"
        f"&adultsRoom1={cfg.ADULTOS}&childrenRoom1={cfg.NINOS}"
        f"&agesKid1={cfg.EDAD_NINO};&flight_hotel={cfg.ORIGEN}"
    )
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
        page.wait_for_timeout(9000)
        texto = page.locator("body").inner_text().lower()
    except Exception as exc:  # noqa: BLE001
        return False, f"No se pudo comprobar ({exc.__class__.__name__})"

    for senal in SENALES_CERRADO:
        if senal in texto:
            return False, f"El paquete rechaza {cfg.ORIGEN}: «{senal}»"

    if "vuelo" in texto and "eur" in texto:
        return True, "El paquete vuelo+hotel ya devuelve resultados con origen Bilbao"

    return False, "Respuesta ambigua del buscador de paquetes"


def resumen_aerolineas() -> list[dict]:
    """Lista de aerolíneas a revisar manualmente, con enlace directo."""
    return [{"nombre": n, "url": u} for n, u in cfg.AEROLINEAS]
