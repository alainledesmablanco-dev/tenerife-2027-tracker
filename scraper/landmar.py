"""Lectura del motor de reservas de Landmar Hotels.

El motor es una SPA que ignora los parámetros de fecha de la URL en la mayoría
de los casos, así que hay dos estrategias:

  1. URL directa con los nombres de parámetro que usa el propio motor
     (startDate / endDate / adultsRoom1 / childrenRoom1 / agesKid1).
  2. Si eso no cuaja, se conduce la interfaz: calendario, ocupación y
     "Repetir búsqueda".

Siempre se verifica que la cabecera muestre las fechas pedidas antes de leer
precios. Si no coinciden, se descarta la lectura en vez de devolver un precio
de otras fechas.

Nota sobre selectores
---------------------
Las cabeceras de bloque ("PAGA AHORA + UNA MODIFICACIÓN GRATIS") se ven en
mayúsculas por CSS (text-transform), pero en el HTML están en minúscula. Un
XPath con contains(., 'PAGA') no encuentra nada, porque XPath lee el DOM crudo.
Hay que usar get_by_text de Playwright, que trabaja sobre el texto renderizado.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from datetime import date

from playwright.sync_api import Page, TimeoutError as PWTimeout

from . import config as cfg

log = logging.getLogger(__name__)

PRECIO_RE = re.compile(r"([\d.]+(?:,\d+)?)\s*EUR")
NOCHES_RE = re.compile(r"Total\s*\((\d+)x\s*Noches?\)", re.I)
CABECERA_RE = re.compile(r"PAGA\s+(AHORA|EN\s+EL\s+HOTEL)", re.I)


@dataclass
class Tarifa:
    entrada: str
    salida: str
    noches: int
    habitacion: str
    regimen: str
    total: float
    por_noche: float
    antes: float | None
    descuento: str | None
    cancelable: bool
    codigo_promo: str | None = None

    def dict(self) -> dict:
        return asdict(self)


def _num(txt: str) -> float | None:
    m = PRECIO_RE.search(txt or "")
    if not m:
        return None
    crudo = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(crudo)
    except ValueError:
        return None


def _aceptar_cookies(page: Page) -> None:
    for etiqueta in ("Rechazar todas", "Reject all", "Rechazar"):
        try:
            boton = page.get_by_role("button", name=etiqueta)
            if boton.count():
                boton.first.click(timeout=4000)
                log.info("Cookies rechazadas (%s)", etiqueta)
                return
        except PWTimeout:
            continue
        except Exception:  # noqa: BLE001 - el banner es opcional
            continue
    log.debug("Sin banner de cookies")


def _url_directa(entrada: date, salida: date, promo: str | None) -> str:
    params = {
        "namespace": cfg.NAMESPACE,
        "language": "SPANISH",
        "numRooms": "1",
        "startDate": entrada.strftime("%d/%m/%Y"),
        "endDate": salida.strftime("%d/%m/%Y"),
        "adultsRoom1": str(cfg.ADULTOS),
        "childrenRoom1": str(cfg.NINOS),
        "agesKid1": f"{cfg.EDAD_NINO};",
    }
    if promo:
        params["promocode"] = promo
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{cfg.BOOKING_URL}?{query}"


def _cabecera_ok(page: Page, entrada: date, salida: date) -> bool:
    """Comprueba que el motor muestra realmente las fechas que hemos pedido."""
    try:
        cabecera = page.locator("body").inner_text(timeout=cfg.TIMEOUT_MS)
    except PWTimeout:
        return False
    return (
        entrada.strftime("%d/%m/%Y") in cabecera
        and salida.strftime("%d/%m/%Y") in cabecera
    )


def _fijar_por_interfaz(page: Page, entrada: date, salida: date) -> bool:
    """Plan B: conducir el calendario y el selector de ocupación a mano."""
    meses = [
        "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
        "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
    ]
    objetivo = f"{meses[entrada.month - 1]} {entrada.year}"

    try:
        page.get_by_text("Entrada", exact=True).first.click(timeout=8000)
        page.wait_for_timeout(800)

        # Avanzar mes a mes hasta ver el objetivo (tope de seguridad: 30 saltos)
        for _ in range(30):
            if objetivo in page.locator("body").inner_text():
                break
            page.locator("a[title='Siguiente'], a:has-text('Siguiente')").first.click(timeout=5000)
            page.wait_for_timeout(350)
        else:
            log.warning("No se alcanzó %s en el calendario", objetivo)
            return False

        _clic_dia(page, entrada)
        page.wait_for_timeout(900)
        _clic_dia(page, salida)
        page.wait_for_timeout(900)

        # Ocupación
        page.get_by_text("Ocupación", exact=True).first.click(timeout=8000)
        page.wait_for_timeout(600)
        selects = page.locator("select")
        for i in range(selects.count()):
            sel = selects.nth(i)
            etiqueta = (sel.get_attribute("aria-label") or "") + (sel.get_attribute("name") or "")
            if "kid" in etiqueta.lower() or "niñ" in etiqueta.lower() or "edad" in etiqueta.lower():
                sel.select_option(str(cfg.EDAD_NINO))
        page.get_by_role("button", name=re.compile("GUARDAR", re.I)).first.click(timeout=6000)
        page.wait_for_timeout(1200)

        page.get_by_role("button", name=re.compile("Repetir búsqueda", re.I)).first.click(timeout=8000)
        page.wait_for_timeout(6000)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Fallo conduciendo la interfaz: %s", exc)
        return False


def _clic_dia(page: Page, dia: date) -> None:
    """Pulsa el número de día dentro del calendario abierto."""
    celda = page.locator(
        f"td:not(.disabled):not(.off) >> text=/^{dia.day}$/"
    ).first
    celda.click(timeout=6000)


def _parsear_tarifas(page: Page, entrada: date, salida: date, noches: int,
                     promo: str | None) -> list[Tarifa]:
    """Recorre las tarjetas de habitación y extrae las tarifas relevantes."""
    tarifas: list[Tarifa] = []

    texto = page.locator("body").inner_text()
    # OJO: la leyenda del calendario incluye SIEMPRE "Fecha sin disponibilidad",
    # así que buscar ese texto suelto daba un falso positivo en todas las fechas.
    # La señal fiable es que no aparezca ningún precio en la página.
    if "EUR" not in texto:
        log.info("Sin tarifas para %s → %s", entrada, salida)
        return tarifas

    # get_by_text trabaja sobre el texto RENDERIZADO, así que ve las mayúsculas
    # que aplica el CSS. Con XPath sobre el DOM crudo esto daba siempre 0.
    cabeceras = page.get_by_text(CABECERA_RE)
    total_cabeceras = cabeceras.count()
    log.info("%s bloques de tarifas detectados", total_cabeceras)

    for i in range(total_cabeceras):
        cabecera = cabeceras.nth(i)
        try:
            etiqueta = (cabecera.inner_text(timeout=3000) or "").upper()
        except Exception:  # noqa: BLE001
            continue
        cancelable = cfg.BLOQUE_CANCELABLE in etiqueta
        if cfg.SOLO_CANCELABLE and not cancelable:
            continue

        habitacion = _habitacion_de(cabecera)

        filas = cabecera.locator(
            "xpath=ancestor-or-self::*[parent::*][1]/following-sibling::*[position()<=8]"
        )
        for j in range(filas.count()):
            try:
                txt = filas.nth(j).inner_text(timeout=3000)
            except Exception:  # noqa: BLE001
                continue
            if "EUR" not in txt:
                continue
            if CABECERA_RE.search(txt):
                break  # hemos llegado al siguiente bloque
            if cfg.SOLO_TODO_INCLUIDO and cfg.REGIMEN_TI.lower() not in txt.lower():
                continue

            tarifa = _tarifa_de_fila(txt, entrada, salida, noches,
                                     habitacion, cancelable, promo)
            if tarifa:
                tarifas.append(tarifa)

    log.info("%s → %s: %s tarifas leídas", entrada, salida, len(tarifas))
    return tarifas


def _tarifa_de_fila(txt: str, entrada: date, salida: date, noches: int,
                    habitacion: str, cancelable: bool,
                    promo: str | None) -> Tarifa | None:
    """Convierte el texto de una fila de régimen en una Tarifa."""
    precios = [p for p in (_num(x) for x in txt.split("\n")) if p]
    if len(precios) < 2:
        return None

    m_noches = NOCHES_RE.search(txt)
    n = int(m_noches.group(1)) if m_noches else noches

    ordenados = sorted(precios)
    total = ordenados[-1]
    antes = None
    if len(ordenados) >= 2 and ordenados[-2] != ordenados[-1]:
        antes = ordenados[-1]
        total = ordenados[-2]

    por_noche = round(total / n, 2) if n else 0.0
    m_dto = re.search(r"-(\d{1,2})\s*%", txt)
    regimen = "Todo Incluido Plus" if "Plus" in txt else cfg.REGIMEN_TI

    return Tarifa(
        entrada=entrada.isoformat(),
        salida=salida.isoformat(),
        noches=n,
        habitacion=habitacion,
        regimen=regimen,
        total=total,
        por_noche=por_noche,
        antes=antes,
        descuento=f"-{m_dto.group(1)}%" if m_dto else None,
        cancelable=cancelable,
        codigo_promo=promo,
    )


def _habitacion_de(bloque) -> str:
    """Busca hacia arriba el título de la habitación a la que pertenece el bloque."""
    for xpath in (
        "xpath=preceding::h2[1]",
        "xpath=preceding::h3[1]",
        "xpath=ancestor::*[self::section or self::article][1]//h2[1]",
    ):
        try:
            nodo = bloque.locator(xpath)
            if nodo.count():
                nombre = (nodo.first.inner_text(timeout=2000) or "").strip()
                if nombre and len(nombre) < 60:
                    return nombre
        except Exception:  # noqa: BLE001
            continue
    return "Desconocida"


def consultar(page: Page, entrada: date, salida: date, noches: int,
              promo: str | None = None) -> list[Tarifa]:
    """Devuelve las tarifas válidas para una ventana de fechas concreta."""
    page.goto(_url_directa(entrada, salida, promo),
              wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
    _aceptar_cookies(page)

    # Los logs confirman que a los pocos segundos la página ya tiene precios.
    # No hace falta la espera activa que se probó antes: nunca casaba y costaba
    # 45 s por fecha, lo que reventaba el timeout de 30 min del job.
    page.wait_for_timeout(8000)

    if not _cabecera_ok(page, entrada, salida):
        log.info("La URL directa no fijó las fechas; conduciendo la interfaz")
        if not _fijar_por_interfaz(page, entrada, salida):
            return []
        if not _cabecera_ok(page, entrada, salida):
            log.warning("Fechas no confirmadas para %s → %s; se descarta", entrada, salida)
            return []

    return _parsear_tarifas(page, entrada, salida, noches, promo)
