"""Suma hotel + vuelos para saber cuánto cuesta el viaje entero.

Hasta ahora el panel enseñaba dos números que no se podían sumar: el mejor
€/noche del hotel salía de una estancia de 8 o 9 noches, y el precio de vuelo
de una ventana de 7. Aquí se cruzan por fechas para que cada total corresponda
a un viaje que se puede reservar de verdad.

Y el resultado no es obvio: la estancia más barata POR NOCHE no suele ser el
viaje más barato. Cada noche extra suma hotel pero no suma billete, así que
alargar la estancia baja el €/noche y sube el total.

Cómo se calcula
---------------
    total = precio del hotel (los 3, con Todo Incluido)
          + precio del vuelo por adulto x 3

Google Flights cotiza un pasajero. El niño de 5 años paga tarifa de adulto en
avión salvo promociones puntuales, así que multiplicar por tres se queda del
lado seguro: el total real será ese o algo menos, nunca más. El panel lo
etiqueta como estimación por ese motivo.

Solo se combinan fechas para las que hay vuelo Y hotel. Si para una estancia
no se ha cotizado vuelo, esa fila no aparece: es preferible enseñar menos
opciones que un total inventado.
"""

from __future__ import annotations

import logging

from . import config as cfg

log = logging.getLogger(__name__)


def _mejor_vuelo_por_fechas(ofertas: list[dict]) -> dict[tuple[str, str], dict]:
    """El vuelo más barato de cada ventana de fechas."""
    mejores: dict[tuple[str, str], dict] = {}
    for o in ofertas:
        ida, vuelta = o.get("ida"), o.get("vuelta")
        if not ida or not vuelta:
            continue
        clave = (ida, vuelta)
        actual = mejores.get(clave)
        if actual is None or o["precio"] < actual["precio"]:
            mejores[clave] = o
    return mejores


def calcular(tarifas: list[dict], ofertas_vuelos: list[dict],
             tope: int = 10) -> list[dict]:
    """Cruza tarifas de hotel con vuelos por fechas. Ordenado de más barato."""
    vuelos = _mejor_vuelo_por_fechas(ofertas_vuelos)
    if not vuelos:
        log.info("Combinado: sin vuelos cotizados, no se puede sumar el viaje")
        return []

    filas = []
    for t in tarifas:
        vuelo = vuelos.get((t["entrada"], t["salida"]))
        if not vuelo:
            continue
        vuelos_total = round(vuelo["precio"] * cfg.PASAJEROS, 2)
        filas.append({
            "entrada": t["entrada"],
            "salida": t["salida"],
            "noches": t["noches"],
            "habitacion": t["habitacion"],
            "hotel_total": t["total"],
            "hotel_por_noche": t["por_noche"],
            "aerolinea": vuelo["aerolinea"],
            "destino": vuelo["destino"],
            "vuelo_por_adulto": vuelo["precio"],
            "vuelos_total": vuelos_total,
            "total": round(t["total"] + vuelos_total, 2),
        })

    filas.sort(key=lambda f: f["total"])

    if filas:
        m = filas[0]
        log.info("Combinado: mejor viaje %.0f EUR (%s: hotel %.0f + vuelos %.0f) "
                 "%s -> %s, %d noches, %s",
                 m["total"], m["habitacion"], m["hotel_total"], m["vuelos_total"],
                 m["entrada"], m["salida"], m["noches"], m["aerolinea"])
    else:
        log.info("Combinado: hay vuelos y hay hotel, pero para fechas distintas")

    return filas[:tope]
