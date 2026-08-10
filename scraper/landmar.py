"""Lectura del motor de reservas de Landmar Hotels.

Estrategia
----------
1. URL directa con los nombres de parámetro del propio motor
   (startDate / endDate / adultsRoom1 / childrenRoom1 / agesKid1).
2. Si eso no cuaja, se conduce la interfaz: calendario, ocupación y
   "Repetir búsqueda".

Siempre se verifica que la página muestre las fechas pedidas antes de leer
precios. Si no coinciden, se descarta la lectura en vez de devolver un precio
de otras fechas.

Por qué se parsea TEXTO y no el DOM
-----------------------------------
Las versiones anteriores navegaban el HTML y fallaban por dos motivos:

  * Las cabeceras ("PAGA AHORA + UNA MODIFICACIÓN GRATIS") se ven en mayúsculas
    por CSS (text-transform), pero en el HTML están en minúscula. Un XPath con
    contains(., 'PAGA') devolvía siempre 0 resultados.
  * Las filas de precio no son hermanas de la cabecera en el árbol, así que
    buscarlas con following-sibling tampoco encontraba nada.

`body.inner_text()` devuelve el texto RENDERIZADO y en orden visual, que es
justo lo que se ve en pantalla. Recorrerlo con una máquina de estados es mucho
más estable que adivinar el anidamiento del HTML. Estructura de cada fila:

    Todo Incluido Plus            <- régimen
    Varios descuentos | -10%      <- descuento (opcional)
    315EUR                        <- €/noche antes
    283EUR                        <- €/noche
    por noche
    2208EUR                       <- total antes
    1987EUR                       <- total
    -10%
    Ahorras
    221EUR                        <- ahorro (NO es un precio)
    Total (7x Noches)
    SELECCIONAR                   <- fin de la fila
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
NOCHES_RE = re.compile(r"Total\s*\((\d+)\s*x\s*Noches?\)", re.I)
CABECERA_RE = re.compile(r"^PAGA\s+(AHORA|EN\s+EL\s+HOTEL)", re.I)
# Insensible a acentos: el motor no siempre los pinta igual.
CANCELABLE_RE = re.compile(r"CANCELACI[OÓ]N\s+GRATUITA", re.I)

FIN_FILA = "SELECCIONAR"
ANCLA_REGIMEN = ("condiciones de la reserva", FIN_FILA.lower())


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
    try:
        return float(m.group(1).replace(".", "").replace(",", "."))
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


# --------------------------------------------------------------- parseo texto

def _es_nombre_habitacion(lineas: list[str], i: int) -> bool:
    """El nombre es corto y sin punto, y justo debajo lleva su descripción larga.

    Ese segundo requisito descarta el desplegable "Seleccionar habitación", que
    también lista los nombres pero sin descripción debajo.
    """
    linea = lineas[i]
    if not linea or len(linea) > 40 or "." in linea:
        return False
    siguiente = next((l for l in lineas[i + 1:i + 5] if l), "")
    return len(siguiente) > 60


def _fila(bloque: list[str], noches_def: int) -> dict | None:
    """Convierte las líneas de una fila (régimen → SELECCIONAR) en datos."""
    try:
        idx_pn = next(i for i, l in enumerate(bloque) if l.lower() == "por noche")
    except StopIteration:
        return None

    por_noche_vals = [v for v in (_num(l) for l in bloque[:idx_pn]) if v]

    # Tras "por noche" vienen los totales. Hay que saltarse el importe que sigue
    # a "Ahorras", que es el descuento en euros y no un precio.
    totales: list[float] = []
    saltar = False
    for l in bloque[idx_pn + 1:]:
        if "ahorras" in l.lower():
            saltar = True
            continue
        v = _num(l)
        if v is None:
            continue
        if saltar:
            saltar = False
            continue
        totales.append(v)

    if not totales or not por_noche_vals:
        return None

    texto = "\n".join(bloque)
    m_noches = NOCHES_RE.search(texto)
    m_dto = re.search(r"-(\d{1,2})\s*%", texto)
    return {
        "regimen": bloque[0],
        "total": totales[-1],
        "antes": totales[0] if len(totales) > 1 else None,
        "por_noche": por_noche_vals[-1],
        "noches": int(m_noches.group(1)) if m_noches else noches_def,
        "descuento": f"-{m_dto.group(1)}%" if m_dto else None,
    }


def _parsear_tarifas(page: Page, entrada: date, salida: date, noches: int,
                     promo: str | None) -> list[Tarifa]:
    """Recorre el texto renderizado y extrae las tarifas que cumplen los filtros."""
    tarifas: list[Tarifa] = []
    texto = page.locator("body").inner_text()

    # OJO: la leyenda del calendario incluye SIEMPRE "Fecha sin disponibilidad",
    # así que buscar ese texto daba un falso positivo en todas las fechas.
    # La señal fiable es que no aparezca ningún precio.
    if "EUR" not in texto:
        log.info("Sin tarifas para %s → %s", entrada, salida)
        return tarifas

    lineas = [l.strip() for l in texto.split("\n")]
    habitacion, cancelable = "Desconocida", None
    leidas = 0
    i = 0

    while i < len(lineas):
        linea = lineas[i]

        if _es_nombre_habitacion(lineas, i):
            habitacion = linea
            cancelable = None          # cada ficha trae sus propios bloques

        elif CABECERA_RE.match(linea):
            cancelable = bool(CANCELABLE_RE.search(linea))

        elif linea.lower() in ANCLA_REGIMEN and cancelable is not None:
            j = i + 1
            while j < len(lineas) and not lineas[j]:
                j += 1
            # Tras un SELECCIONAR puede venir la cabecera del bloque siguiente o
            # el nombre de la habitación siguiente: ninguno es un régimen.
            if (j < len(lineas) and not CABECERA_RE.match(lineas[j])
                    and not _es_nombre_habitacion(lineas, j)):
                fin = j
                while fin < len(lineas) and lineas[fin] != FIN_FILA:
                    fin += 1
                if fin < len(lineas):
                    datos = _fila(lineas[j:fin], noches)
                    if datos:
                        leidas += 1
                        if _pasa_filtros(datos, cancelable):
                            tarifas.append(Tarifa(
                                entrada=entrada.isoformat(),
                                salida=salida.isoformat(),
                                habitacion=habitacion,
                                cancelable=cancelable,
                                codigo_promo=promo,
                                **datos,
                            ))
                    i = fin            # el SELECCIONAR dispara la fila siguiente
                    continue
        i += 1

    log.info("%s → %s: %s filas leídas, %s pasan los filtros",
             entrada, salida, leidas, len(tarifas))
    return tarifas


def _pasa_filtros(datos: dict, cancelable: bool) -> bool:
    if cfg.SOLO_CANCELABLE and not cancelable:
        return False
    if cfg.SOLO_TODO_INCLUIDO and cfg.REGIMEN_TI.lower() not in datos["regimen"].lower():
        return False
    return True


def consultar(page: Page, entrada: date, salida: date, noches: int,
              promo: str | None = None) -> list[Tarifa]:
    """Devuelve las tarifas válidas para una ventana de fechas concreta."""
    page.goto(_url_directa(entrada, salida, promo),
              wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
    _aceptar_cookies(page)

    # Los logs confirman que a los pocos segundos la página ya trae precios. Una
    # espera activa que se probó antes nunca casaba y costaba 45 s por fecha, lo
    # que reventaba el timeout de 30 minutos del job.
    page.wait_for_timeout(8000)

        # La SPA ignora las fechas de la URL de forma intermitente: en la misma
    # pasada unas ventanas salen bien y otras no, sin patrón por duración. Un
    # simple recargar resuelve la mayoría. Conducir el calendario a mano
    # (_fijar_por_interfaz) fallaba casi siempre con timeout en "Entrada", así
    # que ya no se usa como plan B.
    intentos = 0
    while not _cabecera_ok(page, entrada, salida):
        intentos += 1
        if intentos > 2:
            log.warning("Fechas no confirmadas para %s → %s; se descarta",
                        entrada, salida)
            return []
        log.info("La URL no fijó las fechas; recargando (intento %s)", intentos)
        page.reload(wait_until="domcontentloaded", timeout=cfg.TIMEOUT_MS)
        page.wait_for_timeout(9000)

    return _parsear_tarifas(page, entrada, salida, noches, promo)
