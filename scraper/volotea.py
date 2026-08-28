"""Vuelos Volotea Bilbao ⇄ Tenerife Sur, leídos de su propia web.

Por qué existe este módulo
--------------------------
Google no indexa Volotea en esta ruta. El rastreo decía "vuelos cerrados"
mientras Volotea llevaba semanas vendiendo agosto de 2027: un falso negativo
que dejaba el viaje entero sin cotizar.

El hallazgo importante
----------------------
**Volotea solo vuela esta ruta miércoles y domingos.** Eso reduce las 35
ventanas de fechas teóricas a un puñado de combinaciones reservables. De nada
sirve encontrar la estancia de hotel más barata si empieza un martes.

Cómo se lee (reescrito el 27-ago-2026)
--------------------------------------
La versión anterior avanzaba el calendario mes a mes hasta llegar a agosto de
2027, clicando una flecha localizada así:

    page.locator("[class*='calendar'] button, [class*='datepicker'] button").last

Ese `.last` no era la flecha de avanzar: era el último botón que casaba, que
según el momento podía ser el interruptor de "Solo ida" o una celda del
calendario. El módulo devolvía lista vacía en todas las pasadas.

Mirando el DOM real resulta que no hace falta navegar nada: Volotea **pinta de
una vez los 457 días** del calendario, del 1-ago-2026 al 31-oct-2027, cada uno
como

    <button volotea-calendar-day data-date="2027-08-04T00:00:00.000Z" ...>
      <time class="c-calendar-day__number">4</time>
      <p class="c-calendar-day__price">128€</p>
    </button>

Así que basta abrir el calendario una vez y leer todos los `data-date`. Se
acabaron las flechas, los nombres de mes y el contar cuántos saltos faltan.
La fecha se lee del atributo, no del número pintado, que era la otra fuente de
errores: el calendario muestra dos meses a la vez y los días de uno se colaban
como si fueran del otro.

Comprobado el 27-ago-2026 contra la web, con el calendario delante:

    1 ago dom 154€ · 4 ago mié 128€ · 8 ago dom 154€ · 11 ago mié 128€
    15 ago dom 154€ · 18 ago mié 103€ · 22 ago dom 128€ · 25 ago mié 103€

Precio por persona y por trayecto, aunque el buscador lleve 3 pasajeros.

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
from datetime import date

from typing import TYPE_CHECKING

if TYPE_CHECKING:   # Playwright solo hace falta para navegar. Dejarlo fuera
    # del import de arriba permite probar los parseadores (que son texto puro)
    # sin instalar navegadores: son la parte que de verdad se rompe cuando la
    # web cambia, y tienen que poder ejecutarse en cualquier sitio.
    from playwright.sync_api import Page

from . import config as cfg
from . import consentimiento
from . import depuracion

log = logging.getLogger(__name__)

INICIO = "https://www.volotea.com/es/"
AEROLINEA = "Volotea"
DESTINO = "TFS"
DESTINO_NOMBRE = "Tenerife Sur"
FUENTE = "web de Volotea"

# Los selectores que de verdad existen en su buscador y su calendario,
# confirmados leyendo la estructura de la pagina desde el propio runner
# (run #60). Los tres campos del buscador tienen id estable:
#
#   input #input-text_sf-origin        ph "Seleccionar aeropuerto"
#   input #input-text_sf-destination   ph "Seleccionar aeropuerto"
#   input #input-text_sf-passenger     ph "PASAJEROS"
#
# Origen y destino comparten placeholder, y ahi estaba el fallo de las
# versiones anteriores: `get_by_text("Seleccionar aeropuerto").first` abria
# el panel del ORIGEN y despues buscaba "Tenerife Sur" en una lista de
# aeropuertos de salida. Nunca aparecia, y el timeout se leia como "la web ha
# cambiado" cuando en realidad estabamos clicando el campo equivocado.
SEL_ORIGEN = "#input-text_sf-origin"
SEL_DESTINO = "#input-text_sf-destination"
SEL_DIA = "button[volotea-calendar-day][data-date]"
SEL_PRECIO = ".c-calendar-day__price"

PRECIO_CELDA = re.compile(r"(\d[\d.,]*)\s*€")
PRECIO_MIN, PRECIO_MAX = 20.0, 1500.0


@dataclass
class Tramo:
    fecha: str
    precio: float

    def dict(self) -> dict:
        return asdict(self)


def _a_float(crudo: str) -> float | None:
    """'128' → 128.0 ; '1.234,56' → 1234.56. None si no es un precio creíble."""
    txt = crudo.strip()
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    else:
        txt = txt.replace(".", "")
    try:
        valor = float(txt)
    except ValueError:
        return None
    return valor if PRECIO_MIN <= valor <= PRECIO_MAX else None


def _elegir_aeropuerto(page: Page, selector: str, ciudad: str) -> tuple[bool, str]:
    """Rellena uno de los dos campos de aeropuerto y elige la ciudad.

    Se ataca por id, no por placeholder: los dos campos comparten el texto
    "Seleccionar aeropuerto" y por eso la version anterior acababa siempre en
    el de origen.
    """
    try:
        page.wait_for_selector(selector, timeout=30_000, state="visible")
    except Exception as exc:  # noqa: BLE001
        return False, f"No aparecio el campo {selector} ({exc})"

    try:
        campo = page.locator(selector).first
        campo.click(timeout=8000)
        page.wait_for_timeout(1500)
        # Escribir filtra la lista de aeropuertos y deja una sola tarjeta,
        # que es mas fiable que buscar el nombre entre las decenas que
        # muestra el panel sin filtrar.
        campo.fill("", timeout=4000)
        campo.type(ciudad, delay=90)
        page.wait_for_timeout(2500)
        page.get_by_text(ciudad, exact=True).first.click(timeout=10_000)
        page.wait_for_timeout(2500)
        log.info("Volotea: %s = %s", selector, ciudad)
        return True, f"{ciudad} seleccionado"
    except Exception as exc:  # noqa: BLE001
        return False, f"No se pudo poner {ciudad} en {selector} ({exc})"


def _destino_ya_puesto(page: Page) -> bool:
    """True si el buscador ya muestra Tenerife como destino."""
    try:
        return "tenerife" in page.inner_text("body", timeout=5000).lower()
    except Exception:  # noqa: BLE001
        return False


def _abrir_buscador(page: Page) -> tuple[bool, str]:
    """Deja el buscador con origen Bilbao y destino Tenerife Sur.

    Devuelve (ok, motivo). El motivo viaja hasta el panel: en el run #58 este
    paso fallaba en silencio —habia un `except` que decia "el destino ya
    estaba puesto o el panel no salio" sin comprobar cual de las dos cosas
    era— y el sintoma aparecia despues, al no encontrar calendario. Un except
    que se inventa la explicacion es peor que no capturarlo.
    """
    try:
        page.goto(INICIO, wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        return False, f"No cargo la web de Volotea ({exc})"

    page.wait_for_timeout(4000)

    senal = depuracion.bloqueada(page)
    if senal:
        depuracion.volcar(page, "volotea-bloqueo")
        return False, f"Volotea bloquea la IP del runner (senal: {senal!r})"

    # Lo primero, quitar el banner de en medio: en el run #62 el panel de
    # Usercentrics interceptaba todos los clicks y Playwright reintentaba
    # diecinueve veces contra un aside invisible.
    log.info("Volotea: consentimiento -> %s", consentimiento.rechazar(page))
    page.wait_for_timeout(2000)

    for campo, ciudad in ((SEL_ORIGEN, cfg.ORIGEN_NOMBRE),
                          (SEL_DESTINO, DESTINO_NOMBRE)):
        ok, motivo = _elegir_aeropuerto(page, campo, ciudad)
        if not ok:
            if campo == SEL_DESTINO and _destino_ya_puesto(page):
                log.info("Volotea: el destino ya venia puesto en el buscador")
                return True, "destino ya puesto"
            return False, motivo
    return True, "origen y destino seleccionados"


def _abrir_calendario(page: Page) -> bool:
    """Despliega el calendario de fechas y espera a que pinte los dias."""
    if page.locator(SEL_DIA).count():
        return True
    for etiqueta in ("Ida", "Fecha de ida", "Salida", "Fecha"):
        try:
            page.get_by_text(etiqueta, exact=True).first.click(timeout=6000)
        except Exception:  # noqa: BLE001
            continue
        try:
            # Esperar al selector, no dormir un rato y cruzar los dedos: el
            # calendario tarda lo que tarde segun lo cargado que este el
            # runner, y 2,5 s fijos se quedaban cortos.
            page.wait_for_selector(SEL_DIA, timeout=15_000)
            return True
        except Exception:  # noqa: BLE001
            continue
    return bool(page.locator(SEL_DIA).count())


def leer_calendario(page: Page) -> list[Tramo]:
    """Todos los días con precio que haya pintados, leyendo `data-date`.

    No navega por meses: Volotea ya trae más de un año de calendario en el DOM.
    """
    tramos: list[Tramo] = []
    dias = page.locator(SEL_DIA)
    total = dias.count()
    for i in range(total):
        dia = dias.nth(i)
        try:
            iso = (dia.get_attribute("data-date") or "")[:10]
            if not iso:
                continue
            precio_txt = dia.locator(SEL_PRECIO)
            if not precio_txt.count():
                continue           # día sin vuelo
            crudo = precio_txt.first.inner_text(timeout=2000)
        except Exception:  # noqa: BLE001
            continue
        m = PRECIO_CELDA.search(crudo)
        if not m:
            continue
        precio = _a_float(m.group(1))
        if precio is None:
            continue
        try:
            date.fromisoformat(iso)
        except ValueError:
            continue
        tramos.append(Tramo(fecha=iso, precio=precio))
    log.info("Volotea: %d celdas de calendario, %d con precio", total, len(tramos))
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
                "fuente": FUENTE,
            })
    ofertas.sort(key=lambda o: o["precio_total"])
    return ofertas


def buscar(page: Page) -> tuple[list[dict], str]:
    """Devuelve (ofertas, detalle). Lista vacía si no se pudo leer."""
    # Las pistas son los rotulos del buscador: con ellos el log dice que
    # elemento hay que clicar de verdad, sin bajarse el HTML del artefacto.
    PISTAS = ("origen", "destino", "aeropuerto", "bilbao", "tenerife",
              "ida", "vuelta", "pasajero", "buscar")

    ok, motivo = _abrir_buscador(page)
    if not ok:
        depuracion.volcar(page, "volotea-buscador", pistas=PISTAS)
        return [], motivo

    if not _abrir_calendario(page):
        depuracion.volcar(page, "volotea-calendario", pistas=PISTAS)
        return [], f"No se pudo abrir el calendario de Volotea ({motivo})"

    tramos = leer_calendario(page)
    if not tramos:
        return [], "El calendario de Volotea no trae precios"

    mes = cfg.NOCHE_OBLIGATORIA.strftime("%Y-%m")
    del_mes = [t for t in tramos if t.fecha.startswith(mes)]
    if not del_mes:
        return [], (f"Volotea todavía no vende {mes}: su calendario llega hasta "
                    f"{max(t.fecha for t in tramos)}")

    # El mismo calendario sirve de ida y de vuelta: Volotea pinta el precio por
    # trayecto de cada día, no un precio de ida y vuelta. Antes se pinchaba un
    # día para "abrir las vueltas", un paso frágil que no aportaba nada.
    ofertas = _combinar(del_mes, del_mes)
    if not ofertas:
        return [], (f"Volotea vuela en {mes}, pero ninguna combinación cubre "
                    f"la noche del {cfg.NOCHE_OBLIGATORIA.day}")

    mejor = ofertas[0]
    detalle = (
        f"Volotea directo desde {mejor['precio_total']:.0f} € los {cfg.PASAJEROS} "
        f"({mejor['precio']:.0f} €/persona), {mejor['ida']} → {mejor['vuelta']} "
        f"({mejor['noches']} noches). Solo bolso de mano."
    )
    return ofertas, detalle
