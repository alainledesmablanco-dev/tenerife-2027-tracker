"""Vuelos Volotea Bilbao ⇄ Tenerife Sur, leídos de su propia web.

Por qué existe este módulo
--------------------------
Google Flights no lista Volotea en esta ruta. El rastreo decía "vuelos
cerrados" mientras Volotea llevaba semanas vendiendo agosto de 2027: un falso
negativo que dejaba el viaje entero sin cotizar. Ryanair tiene el mismo
problema por decisión propia, así que la lección es general: en rutas con
low-cost hay que ir a la web de la aerolínea.

El hallazgo importante
----------------------
**Volotea solo vuela esta ruta miércoles y domingos.** Eso reduce las 35
ventanas de fechas teóricas a un puñado de combinaciones reservables. De nada
sirve encontrar la estancia de hotel más barata si empieza un martes.

Cómo se lee
-----------
No se recorre el flujo de reserva entero, que es largo y frágil. Basta el
calendario: con 1 pasajero muestra el precio por persona de cada día con vuelo.
Se lee el mes de ida, se pincha un día y se lee el mes de vuelta. Dos lecturas
y salen todas las combinaciones.

Verificado el 15-ago-2026 contra la web: ida mié 4-ago-2027 127,47 € + vuelta
mié 11-ago-2027 102,79 € = 230,26 €/persona, 690,79 € para 2 adultos y 1 niño.
El calendario mostraba 128 € y 103 €, así que redondea al euro por arriba.

Aviso sobre el equipaje
-----------------------
La tarifa estándar incluye SOLO un bolso pequeño de mano. Para una semana con
un niño habrá que sumar maletas facturadas, que Volotea cobra aparte. El precio
que devuelve este módulo es el del billete pelado.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from datetime import date, timedelta

from playwright.sync_api import Page, TimeoutError as PWTimeout

from . import config as cfg

log = logging.getLogger(__name__)

INICIO = "https://www.volotea.com/es/"
AEROLINEA = "Volotea"
DESTINO = "TFS"
DESTINO_NOMBRE = "Tenerife Sur"

# Volotea escribe "128€" en las celdas del calendario.
PRECIO_CELDA = re.compile(r"(\d[\d.,]*)\s*€")

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre")

PRECIO_MIN, PRECIO_MAX = 20.0, 1500.0
MAX_SALTOS = 30


@dataclass
class Tramo:
    fecha: str
    precio: float

    def dict(self) -> dict:
        return asdict(self)


def _a_float(crudo: str) -> float | None:
    txt = crudo.strip()
    # "1.234,56" → 1234.56 ; "128" → 128.0
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    else:
        txt = txt.replace(".", "")
    try:
        valor = float(txt)
    except ValueError:
        return None
    return valor if PRECIO_MIN <= valor <= PRECIO_MAX else None


def _rechazar_cookies(page: Page) -> None:
    for etiqueta in ("Aceptar solo las esenciales", "Rechazar", "Reject"):
        try:
            boton = page.get_by_role("button", name=etiqueta)
            if boton.count():
                boton.first.click(timeout=4000)
                return
        except Exception:  # noqa: BLE001 - el banner es opcional
            continue


def _abrir_buscador(page: Page) -> bool:
    """Deja el buscador con origen Bilbao y destino Tenerife Sur."""
    try:
        page.goto(INICIO, wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
        page.wait_for_timeout(4000)
        _rechazar_cookies(page)
        page.wait_for_timeout(1500)

        # El destino abre un panel con tarjetas por aeropuerto.
        page.get_by_text("Seleccionar aeropuerto").first.click(timeout=8000)
        page.wait_for_timeout(2500)
        page.get_by_text(DESTINO_NOMBRE, exact=True).first.click(timeout=8000)
        page.wait_for_timeout(4000)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Volotea: no se pudo preparar el buscador (%s)", exc)
        return False


def _avanzar_hasta(page: Page, objetivo: date) -> bool:
    """Adelanta el calendario hasta que se vea el mes objetivo."""
    etiqueta = f"{MESES[objetivo.month - 1]} {objetivo.year}"
    for _ in range(MAX_SALTOS):
        try:
            visible = page.locator("body").inner_text(timeout=8000).lower()
        except PWTimeout:
            return False
        if etiqueta in visible:
            return True
        try:
            # La flecha de avanzar mes; sin nombre accesible fiable, se toma
            # el último botón de navegación de la cabecera del calendario.
            page.locator("[class*='calendar'] button, [class*='datepicker'] button"
                         ).last.click(timeout=5000)
        except Exception:  # noqa: BLE001
            return False
        page.wait_for_timeout(500)
    return False


def _leer_mes(page: Page, mes: date) -> list[Tramo]:
    """Devuelve los días del mes con vuelo y su precio por persona."""
    tramos: list[Tramo] = []
    dias = page.locator("td:has-text('€'), [role='gridcell']:has-text('€')")
    for i in range(min(dias.count(), 62)):
        try:
            txt = dias.nth(i).inner_text(timeout=2500)
        except Exception:  # noqa: BLE001
            continue
        m_dia = re.match(r"\s*(\d{1,2})\b", txt)
        m_precio = PRECIO_CELDA.search(txt)
        if not (m_dia and m_precio):
            continue
        precio = _a_float(m_precio.group(1))
        if precio is None:
            continue
        numero = int(m_dia.group(1))
        try:
            dia = mes.replace(day=numero)
        except ValueError:
            continue
        tramos.append(Tramo(fecha=dia.isoformat(), precio=precio))
    # El calendario pinta dos meses; nos quedamos con los del mes pedido.
    return tramos


def _combinar(idas: list[Tramo], vueltas: list[Tramo]) -> list[dict]:
    """Cruza idas y vueltas que cubran la noche obligatoria."""
    obligatoria = cfg.NOCHE_OBLIGATORIA
    ofertas: list[dict] = []
    for ida in idas:
        f_ida = date.fromisoformat(ida.fecha)
        if f_ida > obligatoria:
            continue                      # llegaría tarde a la fiesta
        for vuelta in vueltas:
            f_vuelta = date.fromisoformat(vuelta.fecha)
            noches = (f_vuelta - f_ida).days
            # La vuelta debe ser al menos el día siguiente a la noche clave.
            if f_vuelta <= obligatoria:
                continue
            if not (cfg.NOCHES_MIN <= noches <= cfg.NOCHES_MAX):
                continue
            por_persona = round(ida.precio + vuelta.precio, 2)
            ofertas.append({
                "aerolinea": AEROLINEA,
                "destino": DESTINO,
                "destino_nombre": DESTINO_NOMBRE,
                "ida": ida.fecha,
                "vuelta": vuelta.fecha,
                "noches": noches,
                "precio": por_persona,
                "precio_total": round(por_persona * cfg.PASAJEROS, 2),
                "escalas": "directo",
                "equipaje": "solo bolso de mano",
                "fuente": "web de Volotea",
            })
    ofertas.sort(key=lambda o: o["precio"])
    return ofertas


def buscar(page: Page) -> tuple[list[dict], str]:
    """Devuelve (ofertas, detalle). Lista vacía si no se pudo leer."""
    if not _abrir_buscador(page):
        return [], "No se pudo abrir el buscador de Volotea"

    mes = cfg.NOCHE_OBLIGATORIA.replace(day=1)
    if not _avanzar_hasta(page, mes):
        return [], f"No se alcanzó {MESES[mes.month - 1]} {mes.year} en Volotea"

    idas = _leer_mes(page, mes)
    if not idas:
        return [], "Volotea no muestra vuelos para ese mes"
    log.info("Volotea: %d días con vuelo de ida", len(idas))

    # Al pinchar una ida, el calendario pasa a mostrar precios de vuelta.
    referencia = min(idas, key=lambda t: abs(
        (date.fromisoformat(t.fecha) - cfg.NOCHE_OBLIGATORIA).days))
    try:
        page.get_by_text(str(date.fromisoformat(referencia.fecha).day),
                         exact=True).first.click(timeout=6000)
        page.wait_for_timeout(3000)
    except Exception as exc:  # noqa: BLE001
        log.warning("Volotea: no se pudo abrir el calendario de vuelta (%s)", exc)
        return [], "No se pudieron leer las vueltas de Volotea"

    vueltas = _leer_mes(page, mes)
    siguiente = (mes + timedelta(days=32)).replace(day=1)
    vueltas += _leer_mes(page, siguiente)

    ofertas = _combinar(idas, vueltas)
    if not ofertas:
        return [], "Volotea vuela ese mes, pero ninguna combinación cubre la noche del 8"

    mejor = ofertas[0]
    detalle = (
        f"Volotea directo desde {mejor['precio']:.0f} €/persona "
        f"({mejor['precio_total']:.0f} € los {cfg.PASAJEROS}), "
        f"{mejor['ida']} → {mejor['vuelta']} ({mejor['noches']} noches). "
        f"Solo bolso de mano."
    )
    return ofertas, detalle
