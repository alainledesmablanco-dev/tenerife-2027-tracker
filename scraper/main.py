"""Punto de entrada del rastreo. Lo que ejecuta GitHub Actions.

Se compara SIEMPRE por €/noche, no por total. Con la duración flexible (5 a 9
noches) los totales no son comparables entre sí: una estancia de 5 noches sale
más barata que una de 7 sin que el hotel haya bajado un céntimo. Comparar por
total hacía que el histórico marcase "bajadas" que no existían.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import config as cfg
from . import landmar, notify, otas, report, vuelos

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


def rastrear() -> tuple[list[dict], bool, str]:
    """Devuelve (tarifas, vuelos_abiertos, detalle_vuelos)."""
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
                log.info("%s → %s (%dn): %d tarifas",
                         entrada, salida, noches, len(encontradas))
            except Exception as exc:  # noqa: BLE001
                log.warning("Error en %s → %s: %s", entrada, salida, exc)
            time.sleep(cfg.PAUSA_ENTRE_BUSQUEDAS)

        contexto.close()
        navegador.close()

    abiertos, detalle = vuelos.venta_abierta()

    return tarifas, abiertos, detalle


def _toca_otas(datos: dict, sello: datetime) -> bool:
    """Una sola consulta de OTAs por día natural, la primera pasada que toque.

    El plan gratuito de SerpApi son 250 búsquedas/mes y cada consulta gasta 3
    (una por ventana de fechas). Así se queda en ~90 al mes.
    """
    ultimo = (datos.get("otas_actualizado") or "")[:10]
    return ultimo != sello.strftime("%Y-%m-%d")


def main() -> int:
    datos = cargar_historico()
    tarifas, abiertos, detalle = rastrear()

    validas = [
        t for t in tarifas
        if t["cancelable"] and cfg.REGIMEN_TI.lower() in t["regimen"].lower()
    ]
    # El mejor es el de menor precio POR NOCHE, no el de menor total.
    mejor = min(validas, key=lambda t: t["por_noche"]) if validas else None

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
        "tarifas_encontradas": len(validas),
        "vuelos_abiertos": abiertos,
        "detalle_vuelos": detalle,
    })
    datos["vuelos_abiertos"] = abiertos
    datos["referencia_noche"] = round(REFERENCIA_NOCHE, 2)
    datos["ultima_comprobacion"] = sello.strftime("%Y-%m-%d %H:%M")
    datos["tarifas_actuales"] = sorted(validas, key=lambda t: t["por_noche"])[:20]

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
    log.info("Listo. Mejor: %s €/noche",
             f"{mejor['por_noche']:.0f}" if mejor else "sin datos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
