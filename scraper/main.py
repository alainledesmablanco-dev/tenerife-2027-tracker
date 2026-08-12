"""Punto de entrada del rastreo. Lo que ejecuta GitHub Actions.

Se compara SIEMPRE por €/noche, no por total. Con la duración flexible (5 a 9
noches) los totales no son comparables entre sí: una estancia de 5 noches sale
más barata que una de 7 sin que el hotel haya bajado un céntimo. Comparar por
total hacía que el histórico marcase "bajadas" que no existían.

Orden de la pasada
------------------
Primero el hotel, porque de sus mejores opciones salen las fechas para las que
merece la pena cotizar vuelos. Antes los vuelos se pedían siempre para la
ventana de 7 noches, y como las estancias baratas son de 8 y 9, el precio del
vuelo nunca correspondía a la estancia buena y no se podía sumar el viaje.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import config as cfg
from . import combinar, landmar, notify, otas, report, vuelos

RAIZ = Path(__file__).resolve().parent.parent
HISTORICO = RAIZ / "data" / "historico.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rastreo")

# Referencia en €/noche: 1.987 € por 7 noches = 283,86 €/noche
REFERENCIA_NOCHE = cfg.PRECIO_REFERENCIA / cfg.NOCHES_OBJETIVO


def ahora_madrid() -> datetime:
    """Hora local peninsular sin depender de tzdata del runner."""
    return datetime.now(timezone.utc) + timedelta(hours=2)


def cargar_historico() -> dict:
    if HISTORICO.exists():
        return json.loads(HISTORICO.read_text(encoding="utf-8"))
    return {
        "mejor_precio_historico": None,
        "vuelos_abiertos": False,
        "registros": [],
    }


def guardar_historico(datos: dict) -> None:
    HISTORICO.parent.mkdir(parents=True, exist_ok=True)
    HISTORICO.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def rastrear_hotel() -> list[dict]:
    """Todas las tarifas del hotel para las ventanas de fechas válidas."""
    tarifas: list[dict] = []
    max_ventanas = int(os.environ.get("MAX_VENTANAS", "35"))
    ventanas = cfg.ventanas_validas()[:max_ventanas]

    with sync_playwright() as p:
        navegador = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        contexto = navegador.new_context(
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = contexto.new_page()

        log.info("Consultando %d ventanas de fechas", len(ventanas))
        for entrada, salida, noches in ventanas:
            try:
                encontradas = landmar.consultar(page, entrada, salida, noches)
                for t in encontradas:
                    tarifas.append(t.dict())
                log.info("%s -> %s (%dn): %d tarifas",
                         entrada, salida, noches, len(encontradas))
            except Exception as exc:  # noqa: BLE001
                log.warning("Error en %s -> %s: %s", entrada, salida, exc)
            time.sleep(cfg.PAUSA_ENTRE_BUSQUEDAS)

        contexto.close()
        navegador.close()

    return tarifas


def _toca_otas(datos: dict, sello: datetime) -> bool:
    """Una sola consulta de OTAs por día natural, la primera pasada que toque.

    El plan gratuito de SerpApi son 250 búsquedas/mes y cada consulta gasta una
    por ventana de fechas. Con MAX_VENTANAS_OTAS=1 se queda en ~60 al mes.
    """
    ultimo = (datos.get("otas_actualizado") or "")[:10]
    return ultimo != sello.strftime("%Y-%m-%d")


def _mejores_por_grupo(tarifas: list[dict], tope: int = 15) -> list[dict]:
    """Una fila por combinación de duración y habitación.

    Con 35 ventanas de fechas la tabla salían 20 filas casi idénticas: el mismo
    precio por noche repetido para cada fecha de entrada posible. Agrupando se
    ve lo que de verdad cambia —cuántas noches y qué habitación— sin dejar de
    rastrear ninguna combinación.
    """
    mejores: dict[tuple, dict] = {}
    for t in tarifas:
        clave = (t["noches"], t["habitacion"])
        actual = mejores.get(clave)
        if actual is None or t["por_noche"] < actual["por_noche"]:
            mejores[clave] = t
    return sorted(mejores.values(), key=lambda t: t["por_noche"])[:tope]


def _ventanas_de(tarifas: list[dict], tope: int) -> list[tuple[date, date, int]]:
    """Fechas de las mejores tarifas, sin repetir, para ir a cotizar vuelos."""
    ventanas: list[tuple[date, date, int]] = []
    vistas: set[tuple[str, str]] = set()
    for t in tarifas:
        clave = (t["entrada"], t["salida"])
        if clave in vistas:
            continue
        vistas.add(clave)
        ventanas.append((date.fromisoformat(t["entrada"]),
                         date.fromisoformat(t["salida"]),
                         t["noches"]))
        if len(ventanas) >= tope:
            break
    return ventanas


def main() -> int:
    datos = cargar_historico()

    tarifas = rastrear_hotel()
    validas = [
        t for t in tarifas
        if t["cancelable"] and cfg.REGIMEN_TI.lower() in t["regimen"].lower()
    ]
    # El mejor es el de menor precio POR NOCHE, no el de menor total.
    mejor = min(validas, key=lambda t: t["por_noche"]) if validas else None
    mejores = _mejores_por_grupo(validas)

    # Los vuelos se cotizan para las fechas de las mejores estancias, no para
    # una ventana fija: así el total del viaje corresponde a algo reservable.
    log.info("Cotizando vuelos para las mejores fechas del hotel")
    abiertos, detalle, ofertas_vuelos = vuelos.buscar(
        _ventanas_de(mejores, cfg.MAX_VENTANAS_VUELOS)
    )
    log.info("Vuelos: %s (%s)", abiertos, detalle)

    combinaciones = combinar.calcular(mejores, ofertas_vuelos)

    historico = datos.get("mejor_precio_historico") or {}
    anterior = historico.get("por_noche") or REFERENCIA_NOCHE
    vuelos_antes = datos.get("vuelos_abiertos", False)

    sello = ahora_madrid()

    # Comparativa con otras webs (Booking, Expedia...) vía Google Hotels.
    if not otas.configurado():
        log.info("Sin SERPAPI_KEY: no se comparan otras webs")
    elif _toca_otas(datos, sello):
        log.info("Consultando precios en otras webs")
        encontradas = otas.buscar()
        # Se deja constancia de la comprobación aunque no haya resultados, para
        # que el panel distinga "falta la clave" de "Google todavía no publica
        # precios para esas fechas". Son cosas muy distintas y antes se
        # mostraban con el mismo mensaje, que era engañoso.
        datos["otas_comprobado"] = sello.strftime("%Y-%m-%d %H:%M")
        if encontradas:
            datos["ofertas_otas"] = encontradas
            datos["otas_actualizado"] = sello.strftime("%Y-%m-%d %H:%M")
    else:
        log.info("Las OTAs ya se consultaron hoy; se omite")

    datos["registros"].append({
        "fecha": sello.strftime("%Y-%m-%d %H:%M"),
        "mejor_por_noche": mejor["por_noche"] if mejor else None,
        "mejor_total": mejor["total"] if mejor else None,
        "mejor_detalle": mejor,
        "mejor_viaje": combinaciones[0]["total"] if combinaciones else None,
        "tarifas_encontradas": len(validas),
        "vuelos_abiertos": abiertos,
        "detalle_vuelos": detalle,
    })
    datos["vuelos_abiertos"] = abiertos
    datos["ofertas_vuelos"] = ofertas_vuelos
    datos["vuelos_actualizado"] = sello.strftime("%Y-%m-%d %H:%M")
    datos["combinaciones"] = combinaciones
    datos["pasajeros"] = cfg.PASAJEROS
    datos["referencia_noche"] = round(REFERENCIA_NOCHE, 2)
    datos["ultima_comprobacion"] = sello.strftime("%Y-%m-%d %H:%M")
    datos["tarifas_actuales"] = mejores

    # --- avisos ------------------------------------------------------
    url_hotel = cfg.HOTEL_URL

    if abiertos and not vuelos_antes:
        notify.enviar(notify.formatear_vuelos(detalle, url_hotel))

    if mejor and mejor["por_noche"] < anterior - 0.5:
        notify.enviar(notify.formatear_bajada(mejor, anterior, url_hotel))
        datos["mejor_precio_historico"] = mejor
    elif mejor and not datos.get("mejor_precio_historico"):
        datos["mejor_precio_historico"] = mejor

    # Resumen semanal (lunes por la mañana) aunque no haya novedades
    if sello.weekday() == 0 and sello.hour < 12:
        notify.enviar(notify.formatear_resumen(mejor, anterior, detalle))

    guardar_historico(datos)
    report.generar(datos, RAIZ / "docs" / "index.html")
    log.info("Listo. Mejor hotel: %s EUR/noche. Mejor viaje completo: %s EUR",
             f"{mejor['por_noche']:.0f}" if mejor else "sin datos",
             f"{combinaciones[0]['total']:.0f}" if combinaciones else "sin datos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
