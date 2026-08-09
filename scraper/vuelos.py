"""Estado de la venta de vuelos Bilbao → Tenerife.

Historia de este módulo
-----------------------
La primera versión deducía la apertura de venta mirando si el buscador de
vuelo+hotel del propio Landmar aceptaba el origen Bilbao. La heurística era
`if "vuelo" in texto and "eur" in texto`, y como la página del hotel contiene
esas dos palabras SIEMPRE, daba "vuelos abiertos" en todas las pasadas. Era un
falso positivo puro: se comprobó a mano en Google Flights que agosto de 2027
todavía no está a la venta.

Un aviso equivocado es peor que no avisar, así que esa detección se ha
eliminado. Ahora hay dos caminos honestos:

  * Con AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET configurados, `vuelos_amadeus`
    consulta el inventario real de las aerolíneas y este módulo no se usa.
  * Sin credenciales, se devuelve "sin comprobar" y el panel lo dice tal cual.

Google Flights no tiene API pública y bloquea el scraping desde IPs de centro de
datos como las de GitHub Actions, así que no es una opción automatizable. Para
cubrir ese hueco, lo fiable es crear una alerta de precio en Google Flights y
que sea Google quien avise por email.
"""

from __future__ import annotations

import logging
from datetime import date

from playwright.sync_api import Page

from . import config as cfg

log = logging.getLogger(__name__)

MOTIVO_SIN_COMPROBAR = (
    "Sin comprobar: configura las claves de Amadeus para consultar el "
    "inventario real, o crea una alerta en Google Flights"
)


def venta_abierta(page: Page, entrada: date, salida: date) -> tuple[bool, str]:
    """Devuelve siempre (False, motivo) — ya no se deduce nada del hotel.

    Se mantiene la firma para no romper a quien la llame. El parámetro `page` no
    se usa: precisamente el problema era fiarse de lo que pintaba esa página.
    """
    log.info("Detección indirecta de vuelos desactivada (daba falsos positivos)")
    return False, MOTIVO_SIN_COMPROBAR


def resumen_aerolineas() -> list[dict]:
    """Lista de aerolíneas a revisar manualmente, con enlace directo."""
    return [{"nombre": n, "url": u} for n, u in cfg.AEROLINEAS]


def enlace_google_flights(entrada: date, salida: date) -> str:
    """URL de Google Flights ya rellenada, para comprobar a mano."""
    return (
        "https://www.google.com/travel/flights?q="
        f"Flights%20to%20TFS%20from%20BIO%20on%20{entrada.isoformat()}"
        f"%20through%20{salida.isoformat()}"
    )
