"""Precios del hotel en otras webs (Booking, Expedia, Agoda...) vía SerpApi.

Google Hotels ya agrega los precios de las principales OTAs para un hotel y
unas fechas concretas. SerpApi expone ese resultado como API, así que en vez de
scrapear Booking (bloqueado desde IPs de centro de datos y contra sus términos)
se consulta Google Hotels y se lee su comparativa.

Por qué se busca por zona y no por el nombre del hotel
-----------------------------------------------------
La primera versión consultaba "Landmar Costa Los Gigantes Tenerife" y devolvía
SIEMPRE cero propiedades, sin error de la API. El motivo: cuando la búsqueda
identifica un hotel concreto, Google no devuelve un listado sino la ficha de
ese hotel, y en ese modo el campo `properties` viene vacío.

La solución es buscar por zona y localizar nuestro hotel entre los resultados.

Por qué hacen falta DOS llamadas
--------------------------------
La búsqueda por zona sí trae el hotel y su precio agregado, pero cada propiedad
del listado viene sin el campo `prices`, que es el desglose por web:

    OTAs ... -> 20 propiedades
    OTAs: hotel localizado
    OTAs 2026-09-02 -> 2026-09-09: mejor 425 EUR/noche (2972 EUR total), 0 webs

Había precio y no había desglose, y el panel, que solo pintaba filas por web,
mostraba "Google Hotels todavía no publica precios" teniendo el dato delante.

El desglose vive en la ficha del hotel, que se pide con su `property_token`.
De ahí la segunda llamada. Y si esa segunda llamada falla, se guarda igual el
precio agregado: es mejor enseñar "Google Hotels: 425 EUR/noche" que nada.

Presupuesto
-----------
El plan gratuito de SerpApi son 250 búsquedas/mes. Con MAX_VENTANAS_OTAS=1, una
consulta de zona (normalmente la primera acierta) más la ficha, salen 2 al día
≈ 60 al mes. El tope absoluto, si fallaran las dos primeras zonas, son 4.

Secreto que usa:
    SERPAPI_KEY    clave de serpapi.com (plan gratuito)
"""

from __future__ import annotations

import logging
import os
from datetime import date

import requests

from . import config as cfg

log = logging.getLogger(__name__)

ENDPOINT = "https://serpapi.com/search.json"

# Se prueban en orden hasta que una localice el hotel. De zona pequeña a zona
# grande, para que salga lo más arriba posible entre los resultados.
CONSULTAS = (
    "hoteles en Puerto de Santiago Tenerife",
    "hoteles en Los Gigantes Tenerife",
    "hoteles en Santiago del Teide Tenerife",
)

# Para identificar nuestro hotel entre los resultados: todas estas palabras
# deben aparecer en el nombre devuelto por Google.
CLAVES_NOMBRE = ("landmar", "gigantes")
TIMEOUT = 40


def configurado() -> bool:
    return bool(os.environ.get("SERPAPI_KEY"))


def _extraer(bloque: dict | None) -> float | None:
    """Saca el número de un {"lowest": "123 €", "extracted_lowest": 123}."""
    if not isinstance(bloque, dict):
        return None
    valor = bloque.get("extracted_lowest")
    return float(valor) if isinstance(valor, (int, float)) else None


def _nuestro_hotel(propiedades: list[dict]) -> dict | None:
    for prop in propiedades:
        nombre = (prop.get("name") or "").lower()
        if all(clave in nombre for clave in CLAVES_NOMBRE):
            return prop
    return None


def _pedir(clave: str, entrada: date, salida: date, **extra) -> dict | None:
    """Una llamada a la API. Devuelve el JSON completo, o None si falló."""
    params = {
        "engine": "google_hotels",
        "check_in_date": entrada.isoformat(),
        "check_out_date": salida.isoformat(),
        "adults": cfg.ADULTOS,
        "currency": "EUR",
        "gl": "es",
        "hl": "es",
        "api_key": clave,
    }
    if cfg.NINOS:
        params["children"] = cfg.NINOS
        params["children_ages"] = str(cfg.EDAD_NINO)
    params.update(extra)

    etiqueta = "ficha del hotel" if extra.get("property_token") else extra.get("q", "?")

    try:
        r = requests.get(ENDPOINT, params=params, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        log.warning("OTAs: fallo de red con '%s' (%s)", etiqueta, exc)
        return None

    if r.status_code == 401:
        log.error("OTAs: SERPAPI_KEY rechazada (401). Revisa el secreto.")
        return None
    if r.status_code == 429:
        log.warning("OTAs: cuota de SerpApi agotada (429)")
        return None
    if r.status_code >= 400:
        # Se incluye el cuerpo: SerpApi explica ahí qué parámetro falta, y sin
        # eso un 400 obliga a adivinar (nos costó una pasada entera).
        log.warning("OTAs: HTTP %s con '%s' - %s",
                    r.status_code, etiqueta, r.text[:200])
        return None

    try:
        datos = r.json()
    except ValueError:
        log.warning("OTAs: respuesta no es JSON con '%s'", etiqueta)
        return None

    if datos.get("error"):
        log.warning("OTAs: la API devuelve error con '%s': %s", etiqueta, datos["error"])
        return None

    return datos


def _coherentes(por_noche: float | None, total: float | None,
                noches: int) -> tuple[float | None, float | None, bool]:
    """Corrige el caso en que Google da el mismo numero como noche y total.

    Pasaba de verdad: el panel enseño meses "618 €/noche · 618 € total" para
    una estancia de 7 noches, y despues "328 € y 328 €". Un total de siete
    noches no puede coincidir con el precio de una, asi que uno de los dos
    campos viene mal.

    Cual? El bueno es el precio por noche: 328 € encaja con lo que cobra este
    hotel (270-320 €/noche) y como total de una semana seria absurdo. Asi que
    se conserva el por_noche y se recalcula el total, marcando la fila como
    estimada para que el panel no lo presente como dato de Google.
    """
    if not noches or noches <= 1 or por_noche is None or total is None:
        return por_noche, total, False
    # Un total creible tiene que acercarse a por_noche * noches. Si se queda
    # por debajo de dos noches, no es un total.
    if total < por_noche * 2:
        return por_noche, round(por_noche * noches, 2), True
    return por_noche, total, False


def _fuentes_de(bloques: list[dict], noches: int) -> list[dict]:
    """Convierte la lista `prices` de Google en filas web/precio."""
    fuentes = []
    for oferta in bloques or []:
        pn = _extraer(oferta.get("rate_per_night"))
        tt = _extraer(oferta.get("total_rate"))
        if tt is None and pn is not None and noches:
            tt = round(pn * noches, 2)
        if pn is None and tt is not None and noches:
            pn = round(tt / noches, 2)
        pn, tt, estimado = _coherentes(pn, tt, noches)
        if pn is None and tt is None:
            continue
        fuentes.append({
            "web": oferta.get("source") or "?",
            "por_noche": pn,
            "total": tt,
            "total_estimado": estimado,
        })
    return fuentes


def _desglose(clave: str, token: str, consulta: str, entrada: date,
              salida: date, noches: int) -> list[dict]:
    """Segunda llamada: la ficha del hotel, que sí trae el precio por web.

    OJO con `q`: el motor google_hotels lo exige SIEMPRE, también cuando se
    pide una ficha concreta con property_token. La primera versión mandaba
    solo el token y SerpApi respondía 400 sin más explicación.
    """
    datos = _pedir(clave, entrada, salida, q=consulta, property_token=token)
    if not datos:
        return []

    fuentes = _fuentes_de(datos.get("prices"), noches)
    fuentes += _fuentes_de(datos.get("featured_prices"), noches)

    # La misma web puede venir en las dos listas; nos quedamos con la barata.
    mejores: dict[str, dict] = {}
    for f in fuentes:
        actual = mejores.get(f["web"])
        if actual is None or (f["por_noche"] or 1e9) < (actual["por_noche"] or 1e9):
            mejores[f["web"]] = f

    orden = sorted(mejores.values(),
                   key=lambda f: f["por_noche"] if f["por_noche"] is not None else 1e9)
    log.info("OTAs: la ficha del hotel devuelve %d webs (%s)",
             len(orden), ", ".join(f["web"] for f in orden[:6]) or "ninguna")
    return orden


def _consultar(clave: str, entrada: date, salida: date, noches: int) -> dict | None:
    hotel = None
    consulta_buena = CONSULTAS[0]
    for consulta in CONSULTAS:
        datos = _pedir(clave, entrada, salida, q=consulta)
        if datos is None:
            # Error de red o de la API: no tiene sentido gastar más consultas.
            return None
        props = datos.get("properties") or []
        log.info("OTAs '%s' -> %d propiedades", consulta, len(props))
        if not props:
            continue
        hotel = _nuestro_hotel(props)
        if hotel:
            consulta_buena = consulta
            log.info("OTAs: hotel localizado con la consulta '%s'", consulta)
            break
        nombres = " | ".join((p.get("name") or "?") for p in props[:6])
        log.info("OTAs: no está entre los resultados. Primeros: %s", nombres)

    if not hotel:
        log.info("OTAs %s -> %s: ninguna consulta localizó el hotel", entrada, salida)
        return None

    total = _extraer(hotel.get("total_rate"))
    por_noche = _extraer(hotel.get("rate_per_night"))
    if total is None and por_noche is not None:
        total = round(por_noche * noches, 2)
    if por_noche is None and total is not None:
        por_noche = round(total / noches, 2) if noches else None
    por_noche, total, total_estimado = _coherentes(por_noche, total, noches)
    if total_estimado:
        log.info("OTAs %s -> %s: Google daba el mismo importe como noche y "
                 "como total; se recalcula el total (%.0f x %d noches)",
                 entrada, salida, por_noche, noches)
    if total is None:
        log.info("OTAs: encontrado '%s' pero sin precio para %s -> %s",
                 hotel.get("name"), entrada, salida)
        return None

    fuentes = []
    token = hotel.get("property_token")
    if token:
        fuentes = _desglose(clave, token, consulta_buena, entrada, salida, noches)
    else:
        log.warning("OTAs: el hotel viene sin property_token; sin desglose por web")

    log.info("OTAs %s -> %s: mejor %.0f EUR/noche (%.0f EUR total), %d webs",
             entrada, salida, por_noche or 0, total, len(fuentes))

    return {
        "entrada": entrada.isoformat(),
        "salida": salida.isoformat(),
        "noches": noches,
        "hotel": hotel.get("name"),
        "por_noche": por_noche,
        "total": total,
        "total_estimado": total_estimado,
        "fuentes": fuentes[:8],
    }


def buscar(max_ventanas: int | None = None) -> list[dict]:
    """Consulta las mejores ventanas de fechas. Lista vacía si no hay clave."""
    clave = os.environ.get("SERPAPI_KEY")
    if not clave:
        log.info("OTAs: sin SERPAPI_KEY, se omite la comparativa")
        return []

    if max_ventanas is None:
        max_ventanas = int(os.environ.get("MAX_VENTANAS_OTAS", "3"))

    resultados = []
    for entrada, salida, noches in cfg.ventanas_validas()[:max_ventanas]:
        fila = _consultar(clave, entrada, salida, noches)
        if fila:
            resultados.append(fila)

    resultados.sort(key=lambda r: r["por_noche"] if r["por_noche"] is not None else 1e9)
    return resultados
