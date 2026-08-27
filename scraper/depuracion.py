"""Volcado de la pagina cuando un lector falla.

Por que hace falta
------------------
Los selectores de `volotea.py` y `aireuropa.py` se sacaron mirando el DOM en un
Chrome de escritorio con sesion abierta y cookies de busquedas anteriores. El
runner de GitHub Actions llega en frio: banner de consentimiento por encima,
nada preseleccionado y el JavaScript de la pagina todavia montandose. En el
run #58 eso se tradujo en dos timeouts y cero informacion sobre lo que habia
de verdad en pantalla.

Adivinar selectores a ciegas, un run por intento, es la peor forma de
depurar esto. Asi que cuando algo falla se guarda lo que el navegador estaba
viendo —captura y HTML— y el workflow lo sube como artefacto. Se mira una vez
y se corrige contra la pagina real.

Los ficheros van a la raiz del repo con prefijo `depuracion-`, que es lo que
recoge el paso "Subir capturas" del workflow, y estan en .gitignore para que
no acaben commiteados por el paso que guarda el historico.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

PREFIJO = "depuracion-"


def volcar(page: "Page", nombre: str) -> None:
    """Guarda captura y HTML de la pagina. Nunca lanza: es instrumentacion.

    Si el volcado fallara y tumbase el rastreo estariamos rompiendo el
    programa por intentar depurarlo.
    """
    base = f"{PREFIJO}{nombre}"
    try:
        page.screenshot(path=f"{base}.png", full_page=True)
    except Exception as exc:  # noqa: BLE001
        log.info("No se pudo capturar %s (%s)", base, exc)
    try:
        with open(f"{base}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as exc:  # noqa: BLE001
        log.info("No se pudo guardar el HTML de %s (%s)", base, exc)
    try:
        titulo, url = page.title(), page.url
        texto = page.inner_text("body")[:600].replace("\n", " | ")
        log.info("Volcado %s | titulo=%r | url=%s", base, titulo, url)
        log.info("Volcado %s | texto visible: %s", base, texto)
    except Exception as exc:  # noqa: BLE001
        log.info("No se pudo describir %s (%s)", base, exc)
