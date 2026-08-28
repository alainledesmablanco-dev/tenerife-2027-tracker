"""Vuelo directo Air Europa Bilbao ⇄ Tenerife Norte, de su propia web.

Por qué existe este módulo
--------------------------
Air Europa es, a día de hoy, la única de las tres aerolíneas de la ruta que
tiene agosto de 2027 a la venta **y** que el rastreo no podía ver:

    Volotea      vende agosto 2027   -> volotea.py (Tenerife Sur)
    Air Europa   vende agosto 2027   -> este modulo (Tenerife Norte)
    Vueling      NO vende agosto 2027 -> su calendario se corta el 13-jun-2027

Y Google Vuelos tampoco sirve de puente: rechaza cualquier fecha a más de unos
330 días ("la fecha solicitada para el vuelo es demasiado lejana"), así que
`vuelos_serp.py` devolverá vacío hasta finales de septiembre de 2026. Sin este
módulo, el panel enseña la mitad del mercado.

Lo comprobado a mano el 27-ago-2026
-----------------------------------
Buscando 2 adultos + 1 niño, Bilbao → Tenerife Norte:

    ida    mar 3-ago-2027   19:35 BIO -> 21:45 TFN   sin escalas, 3 h 10 min
                            328,44 € (Lite) / 416,44 € (Standard, con maleta)
    vuelta mar 10-ago-2027  14:35 TFN -> 18:45 BIO   sin escalas, 3 h 10 min
                            331,73 €

    Ida y vuelta directo, los tres, tarifa Lite: 660,17 €

El directo opera **martes, jueves y sábado**. El resto de días Air Europa te
manda vía Madrid, y eso se nota en el precio: 674,61 € en vez de 328,44 €.

Cómo se distingue un día con directo
------------------------------------
El calendario de tarifas da el precio más barato de cada día, pero no dice si
ese precio es de un directo o de uno con escala. En vez de abrir los 15 días
uno a uno, se lee la tarjeta del vuelo directo del día seleccionado y se marcan
como directos los días cuyo precio de calendario no pase de `MARGEN_DIRECTO`
veces ese precio. Con los datos reales el corte es nítido —328/331/371 € los
directos contra 448/604/674 € los de escala— pero es una heurística: solo el
día cuya tarjeta se ha leído de verdad lleva `directo_confirmado: True`.

Equipaje
--------
El precio que se devuelve es el de la tarifa **Lite**: bolso bajo el asiento y
maleta de mano de 10 kg por pasajero, SIN maleta facturada. La Standard, que sí
factura 23 kg por persona, costaba 88 € más por trayecto para los tres.
"""

from __future__ import annotations

import logging
import re
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

INICIO = "https://www.aireuropa.com/es/es"
AEROLINEA = "Air Europa"
DESTINO = "TFN"
DESTINO_NOMBRE = "Tenerife Norte"
FUENTE = "web de Air Europa"

# Un día se considera con vuelo directo si su precio de calendario no pasa de
# este múltiplo del precio del directo que sí se ha leído. Con los datos del
# 27-ago-2026: 328,44 x 1.2 = 394 €, que deja fuera los 448 € del primer
# vuelo con escala y dentro los 371 € de un directo de otro día.
MARGEN_DIRECTO = 1.2

PRECIO_MIN, PRECIO_MAX = 80.0, 6000.0

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# "martes, 3 de agosto de 2027\n328,44\nEUR"  /  "...\nNo disponible"
FILA_CALENDARIO = re.compile(
    r"(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo),\s*"
    r"(\d{1,2})\s+de\s+([a-zá-ú]+)\s+de\s+(\d{4})\s*\n\s*"
    r"(No disponible|[\d.,]+)",
    re.IGNORECASE,
)

HORA = r"\d{1,2}:\d{2}"
# Bloque de tarjeta de vuelo: "19:35\nBIO\nsin escalas\n3 h 10 min\n21:45\nTFN"
TARJETA_DIRECTO = re.compile(
    rf"({HORA})\s*\n\s*([A-Z]{{3}})\s*\n\s*sin\s+escalas\s*\n\s*"
    r"(\d+\s*h(?:\s*\d+\s*min)?)\s*\n\s*"
    rf"({HORA})\s*\n\s*([A-Z]{{3}})",
    re.IGNORECASE,
)
# El primer importe que aparece detrás de la tarjeta del directo.
PRECIO_TRAS_DIRECTO = re.compile(r"([\d.]+,\d{2})\s*\n?\s*EUR")


def _a_float(crudo: str) -> float | None:
    """'328,44' → 328.44 ; '1.234,56' → 1234.56."""
    txt = crudo.strip().replace(".", "").replace(",", ".")
    try:
        valor = float(txt)
    except ValueError:
        return None
    return valor if PRECIO_MIN <= valor <= PRECIO_MAX else None


def parsear_calendario(texto: str) -> dict[str, float]:
    """De la tira de fechas del buscador saca {'2027-08-03': 328.44, ...}.

    Los días marcados "No disponible" no entran: no es lo mismo un día caro que
    un día sin vuelo.
    """
    precios: dict[str, float] = {}
    for dia, mes_txt, anio, importe in FILA_CALENDARIO.findall(texto):
        mes = MESES.get(mes_txt.lower())
        if not mes:
            continue
        if importe.strip().lower().startswith("no disponible"):
            continue
        valor = _a_float(importe)
        if valor is None:
            continue
        try:
            precios[date(int(anio), mes, int(dia)).isoformat()] = valor
        except ValueError:
            continue
    return precios


def parsear_directo(texto: str) -> dict | None:
    """Horario, duración y precio del primer vuelo sin escalas de la página."""
    m = TARJETA_DIRECTO.search(texto)
    if not m:
        return None
    salida, origen, duracion, llegada, destino = m.groups()
    precio = None
    resto = texto[m.end():m.end() + 900]
    for candidato in PRECIO_TRAS_DIRECTO.findall(resto):
        precio = _a_float(candidato)
        if precio is not None:
            break
    if precio is None:
        return None
    return {
        "horario": f"{salida} – {llegada}",
        "duracion": re.sub(r"\s+", " ", duracion).strip(),
        "origen": origen.upper(),
        "destino": destino.upper(),
        "precio": precio,
    }


def dias_con_directo(calendario: dict[str, float], precio_directo: float) -> dict[str, float]:
    """Se queda con los días cuyo precio encaja con el de un vuelo directo."""
    if precio_directo <= 0:
        return {}
    tope = precio_directo * MARGEN_DIRECTO
    return {f: p for f, p in calendario.items() if p <= tope}


def combinar(idas: dict[str, float], vueltas: dict[str, float],
             directo_ida: dict | None, directo_vuelta: dict | None,
             confirmadas: tuple[str | None, str | None] = (None, None)) -> list[dict]:
    """Cruza días de ida y vuelta que cubran la noche obligatoria."""
    obligatoria = cfg.NOCHE_OBLIGATORIA
    conf_ida, conf_vuelta = confirmadas
    ofertas: list[dict] = []
    for f_ida_txt, p_ida in idas.items():
        f_ida = date.fromisoformat(f_ida_txt)
        if f_ida > obligatoria:
            continue
        for f_vuelta_txt, p_vuelta in vueltas.items():
            f_vuelta = date.fromisoformat(f_vuelta_txt)
            if f_vuelta <= obligatoria:
                continue
            noches = (f_vuelta - f_ida).days
            if not (cfg.NOCHES_MIN <= noches <= cfg.NOCHES_MAX):
                continue
            total = round(p_ida + p_vuelta, 2)
            ofertas.append({
                "aerolinea": AEROLINEA,
                "destino": DESTINO,
                "destino_nombre": DESTINO_NOMBRE,
                "ida": f_ida_txt,
                "vuelta": f_vuelta_txt,
                "noches": noches,
                "precio": round(total / cfg.PASAJEROS, 2),
                "precio_total": total,
                "horario": (directo_ida or {}).get("horario"),
                "duracion": (directo_ida or {}).get("duracion"),
                "horario_vuelta": (directo_vuelta or {}).get("horario"),
                "escalas": "directo",
                "equipaje": "maleta de mano 10 kg, sin facturar (tarifa Lite)",
                "directo_confirmado": f_ida_txt == conf_ida and f_vuelta_txt == conf_vuelta,
                "fuente": FUENTE,
            })
    ofertas.sort(key=lambda o: o["precio_total"])
    return ofertas


# --------------------------------------------------------------- navegación

def _cerrar_avisos(page: Page) -> None:
    """El modal de Air Europa SUMA tapa el buscador si no se cierra."""
    for nombre in ("Cerrar", "Close", "close"):
        try:
            boton = page.get_by_role("button", name=nombre)
            if boton.count():
                boton.first.click(timeout=2500)
                page.wait_for_timeout(600)
        except Exception:  # noqa: BLE001
            continue


def _escribir_aeropuerto(page: Page, selector: str, texto: str, opcion: str) -> None:
    # Esperar a que exista, no clicar y confiar: el buscador es Angular y en el
    # runner tarda bastante mas en montarse que en un portatil. El run #58
    # murio justo aqui con "Timeout 8000ms waiting for #flight-searcher-departure".
    page.wait_for_selector(selector, timeout=40_000, state="visible")
    campo = page.locator(selector).first
    campo.click(timeout=8000)
    campo.fill("", timeout=4000)
    campo.type(texto, delay=90)
    page.wait_for_timeout(2000)
    page.get_by_text(opcion, exact=False).first.click(timeout=6000)
    page.wait_for_timeout(1200)


def _rellenar(page: Page, ida: date, vuelta: date) -> tuple[bool, str]:
    """Origen, destino, fechas y pasajeros. Devuelve (ok, motivo)."""
    try:
        consentimiento.bloquear(page)
        page.goto(INICIO, wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
        page.wait_for_timeout(6000)

        # Antes de tocar un solo selector: comprobar que esto es la web de Air
        # Europa y no su pagina de bloqueo. En el run #59 no lo era, y los 40
        # segundos de espera al buscador se los pasó esperando a un cartel de
        # "Page Unavailable" que nunca iba a traer un formulario.
        senal = depuracion.bloqueada(page)
        if senal:
            depuracion.volcar(page, "aireuropa-bloqueo")
            return False, (
                "Air Europa bloquea la IP del runner: su CDN devuelve una "
                f"pagina de error (senal: {senal!r}). No es un fallo de "
                "selectores y no se arregla desde GitHub Actions"
            )

        log.info("Air Europa: consentimiento -> %s", consentimiento.rechazar(page))
        _cerrar_avisos(page)

        _escribir_aeropuerto(page, "#flight-searcher-departure", cfg.ORIGEN_NOMBRE, cfg.ORIGEN)
        _escribir_aeropuerto(page, "#flight-searcher-arrival", DESTINO_NOMBRE, DESTINO)

        # Las fechas se teclean en formato dd/mm/aaaa; si el campo no admite
        # escritura habrá que ir al calendario, pero entonces es mejor que
        # falle aquí y lo diga que no que invente un precio de otras fechas.
        for etiqueta, valor in (("Fecha de ida", ida), ("Fecha de vuelta", vuelta)):
            campo = page.get_by_label(etiqueta).first
            campo.click(timeout=8000)
            campo.fill(valor.strftime("%d/%m/%Y"), timeout=4000)
            page.wait_for_timeout(800)
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)

        page.get_by_role("button", name=re.compile("Buscar vuelos", re.I)).first.click(timeout=8000)
        page.wait_for_url(re.compile(r"digital\.aireuropa\.com/booking/availability"), timeout=60_000)
        page.wait_for_timeout(6000)
        return True, "busqueda lanzada"
    except Exception as exc:  # noqa: BLE001
        log.warning("Air Europa: no se pudo lanzar la búsqueda (%s)", exc)
        depuracion.volcar(page, "aireuropa-buscador",
                          pistas=("origen", "destino", "fecha", "buscar"))
        return False, f"No se pudo consultar Air Europa ({exc})"


def _seguir_a_la_vuelta(page: Page) -> bool:
    """Selecciona la tarifa más barata del directo para ver el tramo de vuelta."""
    try:
        page.get_by_role("button", name=re.compile("continuar", re.I)).first.click(timeout=10_000)
        page.wait_for_url(re.compile(r"availability/1"), timeout=45_000)
        page.wait_for_timeout(6000)
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("Air Europa: no se pudo pasar al tramo de vuelta (%s)", exc)
        depuracion.volcar(page, "aireuropa-vuelta")
        return False


def buscar(page: Page, ida: date, vuelta: date) -> tuple[list[dict], str]:
    """Cotiza el directo BIO⇄TFN alrededor de (ida, vuelta).

    Con una sola búsqueda se leen los ~15 días de calendario de cada tramo, así
    que no hace falta repetirla por cada ventana de fechas del hotel.
    """
    ok, motivo = _rellenar(page, ida, vuelta)
    if not ok:
        return [], motivo

    texto_ida = page.inner_text("body", timeout=20_000)
    cal_ida = parsear_calendario(texto_ida)
    directo_ida = parsear_directo(texto_ida)
    if not cal_ida:
        depuracion.volcar(page, "aireuropa-ida")
        return [], "Air Europa no devolvió calendario de precios para la ida"
    if not directo_ida:
        return [], (f"Air Europa vende esas fechas (desde {min(cal_ida.values()):.0f} € "
                    f"los {cfg.PASAJEROS}), pero ningún vuelo directo")

    if not _seguir_a_la_vuelta(page):
        return [], "Se leyó la ida de Air Europa pero no la vuelta"

    texto_vuelta = page.inner_text("body", timeout=20_000)
    cal_vuelta = parsear_calendario(texto_vuelta)
    directo_vuelta = parsear_directo(texto_vuelta)
    if not (cal_vuelta and directo_vuelta):
        return [], "Air Europa no devolvió vuelos directos de vuelta"

    idas = dias_con_directo(cal_ida, directo_ida["precio"])
    vueltas = dias_con_directo(cal_vuelta, directo_vuelta["precio"])
    log.info("Air Europa: %d días con directo de ida, %d de vuelta",
             len(idas), len(vueltas))

    ofertas = combinar(idas, vueltas, directo_ida, directo_vuelta,
                       confirmadas=(ida.isoformat(), vuelta.isoformat()))
    if not ofertas:
        return [], ("Air Europa tiene directos, pero ninguna combinación cubre "
                    f"la noche del {cfg.NOCHE_OBLIGATORIA.day}")

    mejor = ofertas[0]
    detalle = (
        f"Air Europa directo {mejor['precio_total']:.0f} € los {cfg.PASAJEROS}, "
        f"{mejor['ida']} → {mejor['vuelta']} ({mejor['noches']} noches), "
        f"{mejor['horario'] or ''} a {DESTINO_NOMBRE}. Tarifa Lite, sin facturar."
    )
    return ofertas, detalle
