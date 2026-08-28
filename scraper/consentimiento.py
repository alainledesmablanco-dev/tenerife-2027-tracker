"""Cerrar el banner de consentimiento antes de tocar nada.

Por que existe este modulo
--------------------------
En el run #62 Volotea fallaba asi, y el mensaje lo decia todo:

    <aside id="usercentrics-cmp-ui"></aside> intercepts pointer events
    retrying click action, attempt #19

No era un selector equivocado: era el banner de Usercentrics tapando la pagina
entera. Playwright reintentaba el click diecinueve veces contra un panel
invisible hasta agotar el timeout, y el sintoma que llegaba al log —"no se
pudo poner Tenerife Sur como destino"— apuntaba al sitio que no era.

En un Chrome de escritorio el banner no sale porque ya se respondio una vez.
En el runner sale siempre, y es lo primero que hay que quitar de en medio.

Que se pulsa
------------
Se rechaza, no se acepta. Un rastreador de precios no necesita publicidad
personalizada, y aceptar por comodidad seria tomar por el usuario una decision
que no nos toca. Solo si no hay forma de encontrar el boton de rechazar se
retira el panel del DOM, y queda dicho en el log: es preferible a aceptar.

Detalles feos que hay que contemplar
------------------------------------
- Usercentrics pinta sus botones dentro de un shadow DOM. Los selectores CSS
  de Playwright atraviesan los shadow roots abiertos, asi que por ahi si se
  llega; get_by_role con el nombre del boton, no.
- Hay varias implantaciones (Usercentrics, OneTrust, Didomi) y cada una usa
  sus ids. Se prueban todas: sale barato y evita otro run perdido.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

# Contenedores conocidos de los gestores de consentimiento.
PANELES = (
    "#usercentrics-cmp-ui",
    "#usercentrics-root",
    "#onetrust-consent-sdk",
    "#didomi-host",
    "[id*='cookie-banner' i]",
)

# Botones de RECHAZAR, en orden de preferencia. Los data-testid son los que
# usan estas librerias por dentro y aguantan mejor los cambios de idioma.
BOTONES_RECHAZAR = (
    "[data-testid='uc-deny-all-button']",
    "#uc-deny-all-button",
    "[data-testid='uc-save-button']",
    "#onetrust-reject-all-handler",
    "#didomi-notice-disagree-button",
    "button[id*='reject' i]",
    "button[id*='deny' i]",
)

TEXTOS_RECHAZAR = (
    "Denegar", "Denegar todo", "Rechazar", "Rechazar todo", "Rechazar todas",
    "Solo las necesarias", "Aceptar solo las esenciales", "Continuar sin aceptar",
    "Deny", "Reject all",
)

# Dominios desde los que se sirven estos gestores. Bloquearlos en la red es
# mas fiable que quitar el panel del DOM: en el run #64 se retiraba al cargar
# y once segundos despues el script lo habia vuelto a inyectar, asi que el
# click en el buscador seguia interceptado. Si el script no llega, no hay
# panel que quitar ni que reaparezca.
GUIONES_CMP = (
    "**/*usercentrics*",
    "**/*onetrust*",
    "**/*cookielaw*",
    "**/*didomi*",
    "**/*cookiebot*",
    "**/*trustarc*",
)


def bloquear(page: "Page") -> None:
    """Impide que se cargue el gestor de consentimiento. Nunca lanza.

    No se cargan sus scripts, asi que tampoco se cargan las cookies de
    seguimiento que gestionan: bloquear equivale a rechazar, y ademas no
    depende de encontrar un boton dentro de un shadow DOM.
    """
    for patron in GUIONES_CMP:
        try:
            page.route(patron, lambda ruta: ruta.abort())
        except Exception as exc:  # noqa: BLE001
            log.info("Consentimiento: no se pudo bloquear %s (%s)", patron, exc)
    log.info("Consentimiento: bloqueados %d patrones de CMP", len(GUIONES_CMP))


JS_RETIRAR = """(paneles) => {
  let quitados = 0;
  for (const sel of paneles) {
    for (const nodo of document.querySelectorAll(sel)) {
      nodo.remove();
      quitados++;
    }
  }
  return quitados;
}"""


def _hay_panel(page: "Page") -> str | None:
    for sel in PANELES:
        try:
            if page.locator(sel).count():
                return sel
        except Exception:  # noqa: BLE001
            continue
    return None


def rechazar(page: "Page", espera_ms: int = 2000) -> str:
    """Cierra el banner rechazando. Devuelve que se hizo, para el log.

    Nunca lanza: si esto tumbase el rastreo estariamos rompiendo la pasada por
    un banner.
    """
    panel = _hay_panel(page)
    if not panel:
        return "sin banner"

    for sel in BOTONES_RECHAZAR:
        try:
            boton = page.locator(sel).first
            if boton.count() and boton.is_visible(timeout=1500):
                boton.click(timeout=5000)
                page.wait_for_timeout(espera_ms)
                if not _hay_panel(page):
                    log.info("Consentimiento: rechazado con %s", sel)
                    return f"rechazado con {sel}"
        except Exception:  # noqa: BLE001
            continue

    for etiqueta in TEXTOS_RECHAZAR:
        try:
            boton = page.get_by_role("button", name=etiqueta, exact=False)
            if boton.count():
                boton.first.click(timeout=5000)
                page.wait_for_timeout(espera_ms)
                if not _hay_panel(page):
                    log.info("Consentimiento: rechazado con el boton %r", etiqueta)
                    return f"rechazado con {etiqueta!r}"
        except Exception:  # noqa: BLE001
            continue

    # Ultimo recurso. Se deja constancia: no es lo mismo haber rechazado que
    # haber apartado el panel sin contestarlo.
    try:
        quitados = page.evaluate(JS_RETIRAR, list(PANELES))
        page.wait_for_timeout(1000)
        log.warning("Consentimiento: no se hallo boton de rechazar en %s; "
                    "se retiran %d paneles del DOM sin contestar", panel, quitados)
        return f"panel {panel} retirado sin contestar"
    except Exception as exc:  # noqa: BLE001
        log.warning("Consentimiento: %s sigue tapando la pagina (%s)", panel, exc)
        return f"panel {panel} sigue tapando"
