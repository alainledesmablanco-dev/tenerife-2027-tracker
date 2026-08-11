"""Estado de la venta de vuelos Bilbao → Tenerife, leído de Google Flights.

Historia de este módulo
-----------------------
La primera versión deducía la apertura de venta mirando si el buscador de
vuelo+hotel del propio Landmar mencionaba "vuelo" y "eur". Como esa página las
menciona siempre, daba "vuelos abiertos" en todas las pasadas: un falso
positivo puro. La segunda versión tiraba de la API de Amadeus, cuyo portal
gratuito cerró en julio de 2026. Entre una y otra el módulo quedó desactivado
devolviendo siempre "sin comprobar".

Esta versión lee Google Flights con el mismo Playwright que ya usamos para el
hotel. No necesita ninguna clave ni cuota.

Tres estados, no dos
--------------------
Devuelve (abiertos, detalle). `abiertos` solo vale True si se han leído precios
de verdad. Si Google dice que no hay vuelos, el detalle lo dice; si la lectura
falla, el detalle dice que no se pudo comprobar. Esa distinción es el motivo
de que este archivo se haya reescrito dos veces: un aviso equivocado es peor
que no avisar.

Sobre el precio que se lee
--------------------------
Google Flights sin parámetros de pasajeros cotiza UN adulto. Meter 2 adultos y
un niño obliga a construir el parámetro `tfs`, que es un protobuf serializado
en base64 y se rompe en cuanto Google le cambia un campo. Como aquí lo que
importa es "¿ya se puede comprar?" y el orden de magnitud, se cotiza un adulto
y se etiqueta como tal. Para los tres, cuenta algo menos del triple: los niños
suelen pagar tarifa de adulto en vuelo, menos la tasa de menor.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from . import config as cfg

log = logging.getLogger(__name__)

# Tenerife Sur (Reina Sofía) es el aeropuerto que sirve a Los Gigantes.
# Tenerife Norte se mira también porque Iberia y Vueling lo usan a menudo.
DESTINOS = (("TFS", "Tenerife Sur"), ("TFN", "Tenerife Norte"))

# Google escribe el precio como "€123" en inglés y "123 €" en español, así que
# se buscan las dos formas.
PRECIO_DELANTE = re.compile(r"€\s*(\d[\d.,]*)")
PRECIO_DETRAS = re.compile(r"(\d[\d.,]*)\s*€")

# Filtro de cordura: por debajo de 30 € no hay ida y vuelta peninsular-Canarias
# y por encima de 3000 € lo que hemos leído no es un billete.
PRECIO_MIN = 30.0
PRECIO_MAX = 3000.0

SIN_VUELOS = (
    "no hay vuelos",
    "ningún vuelo",
    "no encontramos vuelos",
    "no flights",
    "couldn't find any flights",
    "no se han encontrado",
)

ESPERA_MS = 25_000


def _a_float(crudo: str) -> float | None:
    """Convierte '1.234', '1,234' o '1 234' en 1234.0.

    El separador de miles cambia con el idioma de la página, así que se decide
    por la forma: si el último separador deja exactamente tres dígitos detrás,
    es de miles; si no, es decimal.
    """
    txt = crudo.strip().replace(" ", "").replace(" ", "")
    if not txt:
        return None

    ultimo = max(txt.rfind("."), txt.rfind(","))
    if ultimo == -1:
        entero, decimales = txt, ""
    elif len(txt) - ultimo - 1 == 3:
        entero, decimales = txt.replace(".", "").replace(",", ""), ""
    else:
        entero = txt[:ultimo].replace(".", "").replace(",", "")
        decimales = txt[ultimo + 1:]

    try:
        return float(f"{entero}.{decimales or 0}")
    except ValueError:
        return None


def _precios(texto: str) -> list[float]:
    """Todos los importes plausibles que aparecen en la página."""
    brutos = [m.group(1) for m in PRECIO_DELANTE.finditer(texto)]
    brutos += [m.group(1) for m in PRECIO_DETRAS.finditer(texto)]

    encontrados = []
    for bruto in brutos:
        valor = _a_float(bruto)
        if valor is not None and PRECIO_MIN <= valor <= PRECIO_MAX:
            encontrados.append(valor)
    return encontrados


def _aerolinea(texto: str, precio: float) -> str | None:
    """Busca el nombre de una aerolínea conocida cerca del precio más barato."""
    lineas = [l.strip() for l in texto.split("\n")]
    conocidas = [nombre for nombre, _url in cfg.AEROLINEAS]

    for i, linea in enumerate(lineas):
        if precio not in _precios(linea):
            continue
        ventana = " ".join(lineas[max(i - 6, 0):i + 3]).lower()
        for nombre in conocidas:
            if nombre.lower() in ventana:
                return nombre
    return None


def _url(destino: str, ida: date, vuelta: date) -> str:
    """URL de Google Flights por búsqueda en lenguaje natural.

    Se pide en inglés (hl=en) porque el analizador de `q` entiende ese formato
    sin ambigüedad, y la moneda se fuerza a euros para no leer dólares.
    """
    consulta = (
        f"Flights from {cfg.ORIGEN} to {destino} "
        f"on {ida.isoformat()} through {vuelta.isoformat()}"
    )
    parametros = {"q": consulta, "curr": "EUR", "hl": "en", "gl": "ES"}
    return "https://www.google.com/travel/flights?" + urlencode(parametros)


def _consentimiento(page) -> None:
    """Cierra el aviso de cookies eligiendo siempre la opción que menos recoge."""
    for etiqueta in ("Reject all", "Rechazar todo", "Rechazar todas"):
        try:
            boton = page.get_by_role("button", name=etiqueta)
            if boton.count():
                boton.first.click(timeout=5000)
                page.wait_for_timeout(2500)
                log.info("Consentimiento de Google resuelto (%s)", etiqueta)
                return
        except Exception:  # noqa: BLE001 - el aviso es opcional
            continue
    log.debug("Sin aviso de cookies de Google")


def _consultar(page, codigo: str, nombre: str,
               ida: date, vuelta: date) -> tuple[float | None, str]:
    """Una consulta. Devuelve (precio_mas_barato, estado).

    estado: 'ok', 'sin_vuelos' o 'no_leido'.
    """
    try:
        page.goto(_url(codigo, ida, vuelta),
                  wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        log.warning("Vuelos %s: no cargó la página (%s)", codigo, exc)
        return None, "no_leido"

    _consentimiento(page)
    page.wait_for_timeout(ESPERA_MS)

    try:
        texto = page.locator("body").inner_text(timeout=cfg.TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        log.warning("Vuelos %s: no se pudo leer el texto (%s)", codigo, exc)
        return None, "no_leido"

    precios = _precios(texto)
    if precios:
        mejor = min(precios)
        linea = _aerolinea(texto, mejor)
        log.info("Vuelos %s→%s (%s): %d precios, el mejor %.0f €%s",
                 cfg.ORIGEN, codigo, nombre, len(precios), mejor,
                 f" ({linea})" if linea else "")
        return mejor, "ok"

    bajo = texto.lower()
    if any(marca in bajo for marca in SIN_VUELOS):
        log.info("Vuelos %s→%s: Google dice que no hay vuelos", cfg.ORIGEN, codigo)
        return None, "sin_vuelos"

    log.warning("Vuelos %s→%s: ni precios ni mensaje reconocible "
                "(%d caracteres leídos)", cfg.ORIGEN, codigo, len(texto))
    return None, "no_leido"


def venta_abierta(page=None, entrada=None, salida=None) -> tuple[bool, str]:
    """¿Se pueden comprar ya los vuelos? Devuelve (abiertos, detalle).

    Los parámetros existen por compatibilidad con la versión anterior; se
    ignoran. El módulo abre su propio navegador porque main.py lo llama después
    de cerrar el que usa para el hotel.
    """
    ventanas = cfg.ventanas_validas()
    if not ventanas:
        return False, "Sin ventana de fechas que consultar"
    ida, vuelta, _noches = ventanas[0]

    resultados: list[tuple[float | None, str, str]] = []
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(
                args=["--disable-blink-features=AutomationControlled"]
            )
            contexto = navegador.new_context(
                locale="es-ES",
                timezone_id="Europe/Madrid",
                viewport={"width": 1440, "height": 1000},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            pagina = contexto.new_page()

            for codigo, nombre in DESTINOS:
                precio, estado = _consultar(pagina, codigo, nombre, ida, vuelta)
                resultados.append((precio, estado, codigo))

            contexto.close()
            navegador.close()
    except Exception as exc:  # noqa: BLE001
        log.error("Vuelos: fallo abriendo el navegador (%s)", exc)
        return False, f"No se pudo comprobar Google Flights ({exc})"

    con_precio = [(p, c) for p, estado, c in resultados if estado == "ok" and p]
    if con_precio:
        mejor, destino = min(con_precio, key=lambda r: r[0])
        detalle = (
            f"Desde {mejor:.0f} € por adulto, ida y vuelta "
            f"{ida.strftime('%d/%m')}–{vuelta.strftime('%d/%m')} "
            f"({cfg.ORIGEN}→{destino}, Google Flights)"
        )
        return True, detalle

    if any(estado == "sin_vuelos" for _p, estado, _c in resultados):
        return False, (
            "Google Flights todavía no tiene vuelos para esas fechas. "
            "Las aerolíneas suelen abrir la venta 10-12 meses antes"
        )

    return False, (
        "No se pudo leer Google Flights en esta pasada; se reintenta "
        "en la siguiente"
    )
