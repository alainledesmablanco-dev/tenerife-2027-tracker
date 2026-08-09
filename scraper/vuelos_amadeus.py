"""Consulta directa de vuelos Bilbao → Tenerife con la API de Amadeus.

Sustituye la detección indirecta de `vuelos.py` (que deducía la apertura de
venta por si el paquete vuelo+hotel del propio Landmar rechazaba el origen
Bilbao) por una consulta real al inventario de las aerolíneas.

Si no hay credenciales configuradas, el módulo se desactiva solo y el rastreo
sigue funcionando con el método antiguo. Nunca rompe la ejecución.

Secretos del repositorio que usa:
    AMADEUS_CLIENT_ID       API Key de developers.amadeus.com
    AMADEUS_CLIENT_SECRET   API Secret
    AMADEUS_HOST            "test" (por defecto) o "produccion"

Sobre test vs producción
------------------------
El entorno de pruebas de Amadeus responde con un subconjunto de datos cacheados
y NO sirve para saber si agosto de 2027 está a la venta de verdad. Para eso hay
que pedir las claves de producción desde el panel de Amadeus (sigue siendo
gratis hasta la cuota mensual) y poner AMADEUS_HOST=produccion.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import date

import requests

from . import config as cfg

log = logging.getLogger(__name__)

HOSTS = {
    "test": "https://test.api.amadeus.com",
    "produccion": "https://api.amadeus.com",
    "production": "https://api.amadeus.com",
}

# Tenerife Sur (Reina Sofía) es el que sirve a Los Gigantes; Tenerife Norte
# (Los Rodeos) se consulta también porque Vueling e Iberia lo usan.
DESTINOS = ("TFS", "TFN")

TIMEOUT = 30
_token_cache: dict = {"valor": None, "caduca": 0.0}


@dataclass
class Vuelo:
    origen: str
    destino: str
    salida: str
    regreso: str
    precio: float
    moneda: str
    aerolinea: str
    escalas_ida: int
    escalas_vuelta: int
    directo: bool

    def dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------- credenciales

def configurado() -> bool:
    return bool(os.environ.get("AMADEUS_CLIENT_ID")
                and os.environ.get("AMADEUS_CLIENT_SECRET"))


def _host() -> str:
    clave = os.environ.get("AMADEUS_HOST", "test").strip().lower()
    return HOSTS.get(clave, HOSTS["test"])


def _token() -> str | None:
    """Token OAuth2 con cache en memoria (Amadeus los da para ~30 minutos)."""
    if _token_cache["valor"] and time.time() < _token_cache["caduca"]:
        return _token_cache["valor"]

    try:
        r = requests.post(
            f"{_host()}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": os.environ["AMADEUS_CLIENT_ID"],
                "client_secret": os.environ["AMADEUS_CLIENT_SECRET"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Amadeus: no se pudo pedir el token (%s)", exc)
        return None

    if r.status_code != 200:
        log.error("Amadeus: credenciales rechazadas (HTTP %s). "
                  "Revisa AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET y si las "
                  "claves son de test o de producción.", r.status_code)
        return None

    datos = r.json()
    _token_cache["valor"] = datos.get("access_token")
    # Margen de 60 s para no apurar la caducidad
    _token_cache["caduca"] = time.time() + max(int(datos.get("expires_in", 1799)) - 60, 60)
    return _token_cache["valor"]


# ------------------------------------------------------------------ búsqueda

def _escalas(itinerario: dict) -> int:
    return max(len(itinerario.get("segments", [])) - 1, 0)


def _parsear(oferta: dict, origen: str, destino: str,
             salida: date, regreso: date) -> Vuelo | None:
    try:
        precio = float(oferta["price"]["grandTotal"])
    except (KeyError, TypeError, ValueError):
        return None

    itinerarios = oferta.get("itineraries", [])
    ida = _escalas(itinerarios[0]) if len(itinerarios) > 0 else 0
    vuelta = _escalas(itinerarios[1]) if len(itinerarios) > 1 else 0
    aerolineas = oferta.get("validatingAirlineCodes") or []

    return Vuelo(
        origen=origen,
        destino=destino,
        salida=salida.isoformat(),
        regreso=regreso.isoformat(),
        precio=precio,
        moneda=oferta["price"].get("currency", "EUR"),
        aerolinea=aerolineas[0] if aerolineas else "?",
        escalas_ida=ida,
        escalas_vuelta=vuelta,
        directo=(ida == 0 and vuelta == 0),
    )


def _consultar(token: str, destino: str, salida: date, regreso: date) -> list[Vuelo]:
    """Una llamada a Flight Offers Search. Lista vacía si no hay nada."""
    params = {
        "originLocationCode": cfg.ORIGEN,
        "destinationLocationCode": destino,
        "departureDate": salida.isoformat(),
        "returnDate": regreso.isoformat(),
        "adults": cfg.ADULTOS,
        "currencyCode": "EUR",
        "max": 5,
    }
    if cfg.NINOS:
        params["children"] = cfg.NINOS

    try:
        r = requests.get(
            f"{_host()}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Amadeus: fallo consultando %s→%s (%s)", cfg.ORIGEN, destino, exc)
        return []

    if r.status_code == 429:
        log.warning("Amadeus: límite de peticiones alcanzado")
        return []

    if r.status_code >= 400:
        # Cuando aún no hay inventario para esas fechas Amadeus devuelve 400
        # con un error de tipo "NO ITINERARY FOUND". No es un fallo nuestro.
        detalle = ""
        try:
            errores = r.json().get("errors", [])
            detalle = "; ".join(
                f"{e.get('code')} {e.get('title')}: {e.get('detail', '')}".strip()
                for e in errores
            )
        except Exception:  # noqa: BLE001
            detalle = r.text[:200]
        log.info("Amadeus %s→%s %s: sin resultados (%s)",
                 cfg.ORIGEN, destino, salida, detalle or f"HTTP {r.status_code}")
        return []

    ofertas = r.json().get("data", [])
    vuelos = [v for v in (_parsear(o, cfg.ORIGEN, destino, salida, regreso)
                          for o in ofertas) if v]
    log.info("Amadeus %s→%s %s → %s: %d ofertas",
             cfg.ORIGEN, destino, salida, regreso, len(vuelos))
    return vuelos


def buscar(max_ventanas: int | None = None) -> tuple[bool, str, list[dict]]:
    """Busca vuelos para las mejores ventanas de fechas.

    Devuelve (venta_abierta, detalle_legible, ofertas_ordenadas).
    Si el módulo no está configurado devuelve (False, motivo, []).
    """
    if not configurado():
        return False, "Amadeus no configurado (faltan las claves)", []

    token = _token()
    if not token:
        return False, "Amadeus: no se pudo autenticar", []

    if max_ventanas is None:
        max_ventanas = int(os.environ.get("MAX_VENTANAS_VUELOS", "3"))

    ventanas = cfg.ventanas_validas()[:max_ventanas]
    todos: list[Vuelo] = []

    for entrada, salida, _noches in ventanas:
        for destino in DESTINOS:
            todos.extend(_consultar(token, destino, entrada, salida))
            time.sleep(0.4)   # cortesía con la cuota

    if not todos:
        entorno = "test" if "test." in _host() else "producción"
        return (
            False,
            f"Amadeus ({entorno}): todavía no hay vuelos a la venta para esas fechas",
            [],
        )

    todos.sort(key=lambda v: v.precio)
    mejor = todos[0]
    directos = [v for v in todos if v.directo]

    detalle = (
        f"Mejor precio {mejor.precio:.0f} € ({mejor.aerolinea}) "
        f"{mejor.origen}→{mejor.destino}, {mejor.salida} → {mejor.regreso}"
    )
    if directos:
        d = directos[0]
        detalle += f" · directo más barato: {d.precio:.0f} € ({d.aerolinea})"
    else:
        detalle += " · sin vuelos directos todavía"

    return True, detalle, [v.dict() for v in todos]
