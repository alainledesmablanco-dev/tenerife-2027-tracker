"""Punto de entrada del rastreo. Lo que ejecuta GitHub Actions."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import config as cfg
from . import landmar, notify, report, vuelos

RAIZ = Path(__file__).resolve().parent.parent
HISTORICO = RAIZ / "data" / "historico.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rastreo")


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

        log.info("Comprobando apertura de vuelos")
        base = cfg.NOCHE_OBLIGATORIA
        abiertos, detalle = vuelos.venta_abierta(
            page,
            base.replace(day=2),
            base.replace(day=9),
        )
        log.info("Vuelos abiertos: %s (%s)", abiertos, detalle)

        contexto.close()
        navegador.close()

    return tarifas, abiertos, detalle


def main() -> int:
    datos = cargar_historico()
    tarifas, abiertos, detalle = rastrear()

    validas = [
        t for t in tarifas
        if t["cancelable"] and cfg.REGIMEN_TI.lower() in t["regimen"].lower()
    ]
    mejor = min(validas, key=lambda t: t["total"]) if validas else None

    anterior = (datos.get("mejor_precio_historico") or {}).get("total") or cfg.PRECIO_REFERENCIA
    vuelos_antes = datos.get("vuelos_abiertos", False)

    sello = ahora_madrid()
    datos["registros"].append({
        "fecha": sello.strftime("%Y-%m-%d %H:%M"),
        "mejor_total": mejor["total"] if mejor else None,
        "mejor_detalle": mejor,
        "tarifas_encontradas": len(validas),
        "vuelos_abiertos": abiertos,
        "detalle_vuelos": detalle,
    })
    datos["vuelos_abiertos"] = abiertos
    datos["ultima_comprobacion"] = sello.strftime("%Y-%m-%d %H:%M")
    datos["tarifas_actuales"] = sorted(validas, key=lambda t: t["total"])[:20]

    # --- avisos ------------------------------------------------------
    url_hotel = cfg.HOTEL_URL

    if abiertos and not vuelos_antes:
        notify.enviar(notify.formatear_vuelos(detalle, url_hotel))

    if mejor and mejor["total"] < anterior - 0.5:
        notify.enviar(notify.formatear_bajada(mejor, anterior, url_hotel))
        datos["mejor_precio_historico"] = mejor
    elif mejor and not datos.get("mejor_precio_historico"):
        datos["mejor_precio_historico"] = mejor

    # Resumen semanal (lunes por la mañana) aunque no haya novedades
    if sello.weekday() == 0 and sello.hour < 12:
        notify.enviar(notify.formatear_resumen(
            mejor, anterior, "abiertos ✅" if abiertos else "aún cerrados"
        ))

    guardar_historico(datos)
    report.generar(datos, RAIZ / "docs" / "index.html")
    log.info("Listo. Mejor: %s", mejor["total"] if mejor else "sin datos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
