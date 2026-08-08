"""Avisos por Telegram."""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


def enviar(texto: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Sin TELEGRAM_TOKEN / TELEGRAM_CHAT_ID: no se envía aviso")
        print(texto)
        return False

    try:
        r = requests.post(
            API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        r.raise_for_status()
        log.info("Aviso enviado a Telegram")
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Fallo enviando a Telegram: %s", exc)
        return False


def formatear_bajada(mejor: dict, anterior: float, url: str) -> str:
    ahorro = anterior - mejor["total"]
    return (
        f"🔻 <b>Baja el precio — Tenerife agosto 2027</b>\n\n"
        f"<b>{mejor['total']:.0f} €</b> "
        f"(antes {anterior:.0f} €, ahorras {ahorro:.0f} €)\n"
        f"{mejor['habitacion']} · {mejor['regimen']}\n"
        f"{mejor['entrada']} → {mejor['salida']} ({mejor['noches']} noches)\n"
        f"{mejor['por_noche']:.0f} €/noche · cancelación gratuita\n\n"
        f"<a href='{url}'>Abrir la web del hotel</a>"
    )


def formatear_vuelos(detalle: str, url: str) -> str:
    return (
        f"🔓 <b>¡Ya se pueden comprar los vuelos!</b>\n\n"
        f"Bilbao → Tenerife, agosto 2027.\n{detalle}\n\n"
        f"Compra cuanto antes: los primeros días suelen tener el mejor precio.\n"
        f"<a href='{url}'>Abrir el buscador</a>"
    )


def formatear_resumen(mejor: dict | None, referencia: float, vuelos: str) -> str:
    if not mejor:
        return (
            "ℹ️ <b>Tenerife 2027</b> — no se pudo leer ninguna tarifa esta vez.\n"
            f"Vuelos: {vuelos}"
        )
    delta = mejor["total"] - referencia
    signo = "sin cambios" if abs(delta) < 1 else (f"+{delta:.0f} €" if delta > 0 else f"{delta:.0f} €")
    return (
        f"📊 <b>Tenerife agosto 2027</b>\n\n"
        f"Mejor Todo Incluido cancelable: <b>{mejor['total']:.0f} €</b> ({signo})\n"
        f"{mejor['habitacion']} · {mejor['entrada']} → {mejor['salida']}\n"
        f"Vuelos: {vuelos}"
    )
