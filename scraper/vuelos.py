"""Vuelos Bilbao → Tenerife, leídos de Google Flights.

Historia de este módulo
-----------------------
La primera versión deducía la apertura de venta mirando si el buscador de
vuelo+hotel del propio Landmar mencionaba "vuelo" y "eur". Como esa página las
menciona siempre, daba "vuelos abiertos" en todas las pasadas: un falso
positivo puro. La segunda versión tiraba de la API de Amadeus, cuyo portal
gratuito cerró en julio de 2026. Entre una y otra el módulo quedó desactivado.

Esta versión lee Google Flights con el mismo Playwright que ya usamos para el
hotel. No necesita ninguna clave ni cuota.

Qué devuelve
------------
(abiertos, detalle, ofertas). `abiertos` solo vale True si se han leído precios
de verdad. Si Google dice que no hay vuelos, el detalle lo dice; si la lectura
falla, el detalle dice que no se pudo comprobar. Esa distinción es el motivo de
que este archivo se haya reescrito: un aviso equivocado es peor que no avisar.

`ofertas` es la lista de vuelos concretos, cada uno con aerolínea, precio,
horario, duración y escalas.

Cómo se leen las tarjetas
-------------------------
La primera versión buscaba precios sueltos por toda la página y se quedaba con
el mínimo. Servía para saber si había venta, pero perdía a qué vuelo
correspondía cada precio: salió "63 €" sin aerolínea.

La segunda intentó agrupar por líneas, anclando en un horario tipo
"7:15 AM - 10:05 AM" al principio de línea. No casó ni una vez: Google no
maquetó el texto como yo había supuesto. El log lo dijo tal cual —"12 precios
sueltos pero ninguna tarjeta reconocible"— y de ahí sale esta tercera versión.

Ahora no se adivina el formato del texto: se toma cada `<li>` de la página, que
es como Google agrupa cada resultado, y se lee dentro de ese trozo. La
estructura HTML es mucho más estable que el orden de las líneas. Si aun así no
se reconoce ninguna tarjeta, se vuelca una muestra al log para poder mirarla
en vez de seguir suponiendo.

Sobre el precio
---------------
Google Flights sin parámetros de pasajeros cotiza UN adulto. Meter 2 adultos y
un niño obliga a construir el parámetro `tfs`, que es un protobuf serializado
en base64 y se rompe en cuanto Google le cambia un campo. Como aquí importa
"¿ya se puede comprar?" y el orden de magnitud, se cotiza un adulto y se
etiqueta como tal. Para los tres, cuenta algo menos del triple.
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

# Google escribe el precio como "€123" en inglés y "123 €" en español.
PRECIO_DELANTE = re.compile(r"€\s*(\d[\d.,]*)")
PRECIO_DETRAS = re.compile(r"(\d[\d.,]*)\s*€")

# El horario ya no es el ancla, solo un dato más, así que se busca en
# cualquier posición y se aceptan los varios guiones que usa Google.
HORA_RE = re.compile(
    r"\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[–—\-−]\s*\d{1,2}:\d{2}\s*(?:AM|PM)?", re.I
)
UNA_HORA_RE = re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)?$", re.I)
DURACION_RE = re.compile(r"\b(\d+)\s*hr\b(?:\s*(\d+)\s*min)?|\b(\d+)\s*h\s*(\d+)?", re.I)
DIRECTO_RE = re.compile(r"\bnonstop\b|\bdirecto\b", re.I)
ESCALAS_RE = re.compile(r"\b(\d+)\s*(?:stops?|escalas?)\b", re.I)
# re.M para que valga tanto sobre una línea suelta como buscándola dentro de
# la tarjeta entera; sin él, el ^...$ nunca casaba en el bloque multilínea.
AEROPUERTOS_RE = re.compile(r"^[A-Z]{3}\s*[–—\-−]\s*[A-Z]{3}$", re.M)

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
MAX_LI = 150


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
    """Todos los importes plausibles que aparecen en un texto."""
    brutos = [m.group(1) for m in PRECIO_DELANTE.finditer(texto)]
    brutos += [m.group(1) for m in PRECIO_DETRAS.finditer(texto)]

    encontrados = []
    for bruto in brutos:
        valor = _a_float(bruto)
        if valor is not None and PRECIO_MIN <= valor <= PRECIO_MAX:
            encontrados.append(valor)
    return encontrados


def _es_aerolinea(linea: str) -> bool:
    """Descarta las líneas de la tarjeta que no son el nombre de la compañía."""
    if not linea or len(linea) > 60:
        return False
    if _precios(linea) or HORA_RE.search(linea) or UNA_HORA_RE.match(linea):
        return False
    if AEROPUERTOS_RE.match(linea) or DIRECTO_RE.search(linea):
        return False
    if ESCALAS_RE.search(linea) or DURACION_RE.search(linea):
        return False
    if not any(c.isalpha() for c in linea):
        return False
    descartes = ("separate", "price", "self transfer", "round trip", "ida y",
                 "emisiones", "emissions", "avg", "typical", "co2",
                 "select", "seleccionar", "más barato", "cheapest", "best",
                 "unavailable", "no disponible")
    bajo = linea.lower()
    return not any(bajo.startswith(d) for d in descartes)


def _escalas(bloque: str) -> str:
    if DIRECTO_RE.search(bloque):
        return "directo"
    m = ESCALAS_RE.search(bloque)
    if m:
        n = int(m.group(1))
        return f"{n} escala" if n == 1 else f"{n} escalas"
    return "?"


def _duracion(bloque: str) -> str | None:
    m = DURACION_RE.search(bloque)
    if not m:
        return None
    horas = m.group(1) or m.group(3)
    minutos = m.group(2) or m.group(4) or "0"
    if not horas:
        return None
    return f"{int(horas)}h {int(minutos):02d}m"


def _parece_vuelo(bloque: str) -> bool:
    """¿Este <li> es un resultado de vuelo y no un filtro o un anuncio?

    Se exige precio y al menos dos señales propias de un itinerario. Sin este
    filtro entran las tarjetas de "ordenar por precio" y similares.
    """
    if not _precios(bloque):
        return False
    señales = (
        DIRECTO_RE.search(bloque) is not None,
        ESCALAS_RE.search(bloque) is not None,
        DURACION_RE.search(bloque) is not None,
        HORA_RE.search(bloque) is not None,
        AEROPUERTOS_RE.search(bloque) is not None,
    )
    return sum(señales) >= 2


def _horario(bloque: str) -> str | None:
    """El horario, venga en una línea o partido en dos."""
    m = HORA_RE.search(bloque)
    if m:
        return re.sub(r"\s+", " ", m.group(0)).strip()

    sueltas = [l.strip() for l in bloque.split("\n") if UNA_HORA_RE.match(l.strip())]
    if len(sueltas) >= 2:
        return f"{sueltas[0]} – {sueltas[1]}"
    return None


def _ofertas(tarjetas: list[str], destino: str) -> list[dict]:
    """Extrae una oferta por tarjeta de resultado."""
    ofertas: list[dict] = []

    for bloque in tarjetas:
        if not _parece_vuelo(bloque):
            continue

        lineas = [l.strip() for l in bloque.split("\n") if l.strip()]
        aerolinea = next((l for l in lineas if _es_aerolinea(l)), "?")

        ofertas.append({
            "aerolinea": aerolinea,
            "precio": min(_precios(bloque)),
            "horario": _horario(bloque),
            "duracion": _duracion(bloque),
            "escalas": _escalas(bloque),
            "destino": destino,
        })

    # Los <li> anidados y la lista de "mejores vuelos" repiten tarjetas.
    unicas: dict[tuple, dict] = {}
    for o in ofertas:
        clave = (o["aerolinea"], o["precio"], o["horario"])
        unicas.setdefault(clave, o)

    return sorted(unicas.values(), key=lambda o: o["precio"])


def _tarjetas(page) -> list[str]:
    """El texto de cada <li> de la página, que es como Google agrupa cada vuelo.

    Se limita a los primeros MAX_LI porque Google pinta también los <li> de
    menús y filtros, y a los que caben en 900 caracteres: por encima de eso no
    es una tarjeta suelta sino un contenedor con varias dentro.
    """
    trozos: list[str] = []
    try:
        lis = page.locator("li")
        total = min(lis.count(), MAX_LI)
    except Exception as exc:  # noqa: BLE001
        log.warning("Vuelos: no se pudieron listar las tarjetas (%s)", exc)
        return trozos

    for i in range(total):
        try:
            txt = lis.nth(i).inner_text(timeout=2000)
        except Exception:  # noqa: BLE001
            continue
        if txt and 20 < len(txt) < 900:
            trozos.append(txt)
    return trozos


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
               ida: date, vuelta: date) -> tuple[list[dict], str]:
    """Una consulta. Devuelve (ofertas, estado).

    estado: 'ok', 'sin_vuelos' o 'no_leido'.
    """
    try:
        page.goto(_url(codigo, ida, vuelta),
                  wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        log.warning("Vuelos %s: no cargó la página (%s)", codigo, exc)
        return [], "no_leido"

    _consentimiento(page)
    page.wait_for_timeout(ESPERA_MS)

    try:
        texto = page.locator("body").inner_text(timeout=cfg.TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        log.warning("Vuelos %s: no se pudo leer el texto (%s)", codigo, exc)
        return [], "no_leido"

    tarjetas = _tarjetas(page)
    ofertas = _ofertas(tarjetas, codigo)
    if ofertas:
        mejor = ofertas[0]
        log.info("Vuelos %s->%s (%s): %d vuelos leídos de %d tarjetas, "
                 "el mejor %.0f EUR (%s, %s)",
                 cfg.ORIGEN, codigo, nombre, len(ofertas), len(tarjetas),
                 mejor["precio"], mejor["aerolinea"], mejor["escalas"])
        return ofertas, "ok"

    # Hay precios pero no hemos sabido leer las tarjetas: el diseño de Google
    # ha cambiado. Es un fallo nuestro, no de Google, así que se vuelca una
    # muestra para poder arreglarlo mirando en vez de suponiendo.
    sueltos = _precios(texto)
    if sueltos:
        log.warning("Vuelos %s->%s: %d precios sueltos y %d tarjetas, pero "
                    "ninguna reconocible como vuelo",
                    cfg.ORIGEN, codigo, len(sueltos), len(tarjetas))
        for i, muestra in enumerate(tarjetas[:3]):
            log.warning("Muestra de tarjeta %d: %r", i + 1, muestra[:400])
        if not tarjetas:
            corte = texto.find("€")
            log.warning("Muestra del texto junto al primer precio: %r",
                        texto[max(corte - 250, 0):corte + 250])
        return ([{"aerolinea": "?", "precio": min(sueltos), "horario": None,
                  "duracion": None, "escalas": "?", "destino": codigo}], "ok")

    bajo = texto.lower()
    if any(marca in bajo for marca in SIN_VUELOS):
        log.info("Vuelos %s->%s: Google dice que no hay vuelos", cfg.ORIGEN, codigo)
        return [], "sin_vuelos"

    log.warning("Vuelos %s->%s: ni precios ni mensaje reconocible "
                "(%d caracteres leídos)", cfg.ORIGEN, codigo, len(texto))
    return [], "no_leido"


def venta_abierta(page=None, entrada=None, salida=None) -> tuple[bool, str, list[dict]]:
    """¿Se pueden comprar ya los vuelos? Devuelve (abiertos, detalle, ofertas).

    Los parámetros existen por compatibilidad con la versión anterior; se
    ignoran. El módulo abre su propio navegador porque main.py lo llama después
    de cerrar el que usa para el hotel.
    """
    ventanas = cfg.ventanas_validas()
    if not ventanas:
        return False, "Sin ventana de fechas que consultar", []
    ida, vuelta, _noches = ventanas[0]

    todas: list[dict] = []
    estados: list[str] = []
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
                ofertas, estado = _consultar(pagina, codigo, nombre, ida, vuelta)
                todas.extend(ofertas)
                estados.append(estado)

            contexto.close()
            navegador.close()
    except Exception as exc:  # noqa: BLE001
        log.error("Vuelos: fallo abriendo el navegador (%s)", exc)
        return False, f"No se pudo comprobar Google Flights ({exc})", []

    if todas:
        todas.sort(key=lambda o: o["precio"])
        for o in todas:
            o["ida"] = ida.isoformat()
            o["vuelta"] = vuelta.isoformat()
        mejor = todas[0]
        detalle = (
            f"Desde {mejor['precio']:.0f} € por adulto con {mejor['aerolinea']} "
            f"({mejor['escalas']}), ida y vuelta "
            f"{ida.strftime('%d/%m')}-{vuelta.strftime('%d/%m')} "
            f"{cfg.ORIGEN}-{mejor['destino']}"
        )
        return True, detalle, todas[:20]

    if "sin_vuelos" in estados:
        return False, (
            "Google Flights todavía no tiene vuelos para esas fechas. "
            "Las aerolíneas suelen abrir la venta 10-12 meses antes"
        ), []

    return False, (
        "No se pudo leer Google Flights en esta pasada; se reintenta "
        "en la siguiente"
    ), []
