"""Volcado de la pagina cuando un lector falla.

Por que hace falta
------------------
Los selectores de `volotea.py` y `aireuropa.py` se sacaron mirando el DOM en un
Chrome de escritorio con sesion abierta y cookies de busquedas anteriores. El
runner de GitHub Actions llega en frio: banner de consentimiento por encima,
nada preseleccionado y el JavaScript todavia montandose.

Que ha aportado ya
------------------
En el run #59 este modulo resolvio de un golpe una pregunta que llevaba dos
runs disfrazada de "fallo de selector":

    Volcado depuracion-aireuropa-buscador | titulo 'Server errors'
    texto visible: AirEuropa | Page Unavailable | ... Reference ID: 18.6ec8...
    Client IP: 172.174.221.224

Eso no es la web de Air Europa: es la pagina de bloqueo de su CDN, y esa IP es
de Azure, o sea el runner. No habia ningun selector que arreglar. De ahi sale
`bloqueada()`, para que un bloqueo por IP se llame bloqueo desde la primera
pasada y no se confunda nunca mas con un timeout.

Y de ahi sale tambien `estructura()`: cuando la pagina SI carga —el caso de
Volotea— lo que hace falta es saber que campos hay de verdad en pantalla, con
sus ids y sus etiquetas. Sacarlo al log evita el ciclo de descargar un
artefacto por cada intento.

Los ficheros van a la raiz del repo con prefijo `depuracion-`, que es lo que
recoge el paso "Subir capturas" del workflow, y estan en .gitignore para que no
acaben commiteados por el paso que guarda el historico.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

PREFIJO = "depuracion-"

# Frases con las que las CDN anuncian que te han cerrado la puerta. No son
# errores de la web: son la web negandose a servirte por quien pareces.
SENALES_BLOQUEO = (
    "page unavailable",
    "access denied",
    "reference id",
    "request blocked",
    "server errors",
    "attention required",
    "unusual traffic",
    "are you a robot",
    "verifying you are human",
    "403 forbidden",
)

# Cuantos elementos y cuanto texto se sacan al log. Un volcado que no se puede
# leer de un vistazo no sirve de nada.
MAX_ELEMENTOS = 45
MAX_TEXTO = 70

JS_ESTRUCTURA = """() => {
  const visible = e => {
    const r = e.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const corta = (s, n) => (s || '').toString().replace(/\\s+/g, ' ').trim().slice(0, n);
  const campos = [...document.querySelectorAll(
      'input, select, textarea, button, [role=button], [role=combobox], [contenteditable=true]')]
    .filter(visible)
    .map(e => [e.tagName.toLowerCase(),
               e.id ? '#' + e.id : '',
               e.name ? 'name=' + e.name : '',
               e.placeholder ? 'ph=' + corta(e.placeholder, 30) : '',
               e.getAttribute('aria-label') ? 'aria=' + corta(e.getAttribute('aria-label'), 30) : '',
               corta(e.className, 35),
               corta(e.innerText, 30)].filter(Boolean).join(' | '));
  return campos;
}"""

JS_PISTAS = """(claves) => {
  const visible = e => {
    const r = e.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const corta = s => (s || '').toString().replace(/\\s+/g, ' ').trim().slice(0, 60);
  const salida = [];
  for (const e of document.querySelectorAll('*')) {
    if (e.children.length > 3 || !visible(e)) continue;
    const t = corta(e.innerText).toLowerCase();
    if (!t || t.length > 60) continue;
    if (!claves.some(k => t.includes(k))) continue;
    salida.push(e.tagName.toLowerCase()
      + (e.id ? '#' + e.id : '')
      + ' [' + corta(e.className).slice(0, 35) + '] '
      + corta(e.innerText));
    if (salida.length > 25) break;
  }
  return salida;
}"""


def _limpiar(txt: str) -> str:
    """Quita urls y parametros: los logs de Actions llevan tokens firmados."""
    return re.sub(r"https?://\S+", "<url>", txt or "")


def bloqueada(page: "Page") -> str | None:
    """Devuelve la senal de bloqueo encontrada, o None si la pagina es real.

    Se mira el titulo y el principio del texto: las paginas de bloqueo son
    cortas y lo dicen arriba del todo.
    """
    try:
        titulo = (page.title() or "").lower()
        cuerpo = page.inner_text("body", timeout=8000)[:1200].lower()
    except Exception:  # noqa: BLE001
        return None
    for senal in SENALES_BLOQUEO:
        if senal in titulo or senal in cuerpo:
            return senal
    return None


def estructura(page: "Page", pistas: tuple[str, ...] = ()) -> None:
    """Saca al log los campos visibles de la pagina, con sus ids y etiquetas.

    Es lo que hace falta para corregir un selector sin bajarse el HTML entero:
    los mismos datos que se miran a mano en las herramientas de desarrollo.
    """
    try:
        campos = page.evaluate(JS_ESTRUCTURA)
    except Exception as exc:  # noqa: BLE001
        log.info("No se pudo leer la estructura (%s)", exc)
        return
    log.info("Estructura: %d campos visibles", len(campos))
    for campo in campos[:MAX_ELEMENTOS]:
        log.info("  campo: %s", _limpiar(campo))

    if not pistas:
        return
    try:
        encontrados = page.evaluate(JS_PISTAS, list(pistas))
    except Exception as exc:  # noqa: BLE001
        log.info("No se pudieron buscar las pistas (%s)", exc)
        return
    log.info("Pistas %s: %d coincidencias", list(pistas), len(encontrados))
    for pista in encontrados[:MAX_ELEMENTOS]:
        log.info("  pista: %s", _limpiar(pista))


def volcar(page: "Page", nombre: str, pistas: tuple[str, ...] = ()) -> None:
    """Guarda captura y HTML, y describe la pagina en el log.

    Nunca lanza: si el volcado tumbase el rastreo estariamos rompiendo el
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
        log.info("Volcado %s | titulo=%r", base, page.title())
        texto = page.inner_text("body")[:600].replace("\n", " | ")
        log.info("Volcado %s | texto visible: %s", base, _limpiar(texto))
    except Exception as exc:  # noqa: BLE001
        log.info("No se pudo describir %s (%s)", base, exc)

    senal = bloqueada(page)
    if senal:
        # Si nos han bloqueado, la estructura no aporta nada: los campos que
        # saldrian son los de la pagina de error.
        log.warning("%s: parece una pagina de BLOQUEO (senal: %r)", base, senal)
        return
    estructura(page, pistas)
