"""Estado de la venta de vuelos Bilbao → Tenerife.

Por qué este módulo no hace nada
--------------------------------
La primera versión deducía la apertura de venta mirando si el buscador de
vuelo+hotel del propio Landmar aceptaba el origen Bilbao. La heurística era
`if "vuelo" in texto and "eur" in texto`, y como la página del hotel contiene
esas dos palabras SIEMPRE, daba "vuelos abiertos" en todas las pasadas. Un
falso positivo puro.

Se comprobó a mano que ninguna fuente sirve todavía: Google Flights rechaza
agosto de 2027 por estar a más de 11 meses, y el portal Self-Service gratuito
de Amadeus se cerró en julio de 2026. Sencillamente, las aerolíneas aún no han
cargado ese inventario.

Un aviso equivocado es peor que no avisar, así que se devuelve "sin comprobar"
y el panel lo dice tal cual. Mientras tanto, una tarea programada revisa cada
lunes si Google Flights ya acepta esas fechas.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MOTIVO_SIN_COMPROBAR = (
    "Sin comprobar: las aerolíneas no han abierto la venta de agosto 2027. "
    "Se revisa cada lunes si Google Flights ya acepta esas fechas"
)


def venta_abierta(page=None, entrada=None, salida=None) -> tuple[bool, str]:
    """Devuelve siempre (False, motivo). Los parámetros se ignoran."""
    log.info("Detección de vuelos desactivada: no hay fuente fiable todavía")
    return False, MOTIVO_SIN_COMPROBAR
