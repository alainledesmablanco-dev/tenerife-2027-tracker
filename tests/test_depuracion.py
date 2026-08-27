"""Prueba de la deteccion de paginas de bloqueo.

El texto de muestra es el que devolvio de verdad Air Europa al runner de
GitHub Actions en el run #59. Dos runs se fueron en tratar eso como un fallo
de selectores; esta prueba existe para que no vuelva a pasar.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper import depuracion   # noqa: E402

# Lo que vio el runner el 27-ago-2026, literal.
BLOQUEO_AIREUROPA = (
    "AirEuropa | Page Unavailable | Estamos actualizando la web, para mas "
    "informacion llame al 911 401 501 | Reference ID: 18.6ec83017.1787874306."
    "34f1d203 | Client IP: 172.174.221.224"
)
TITULO_AIREUROPA = "Server errors"

# Y esto es la web de Volotea cargando bien, tambien del mismo run.
OK_VOLOTEA = (
    "Check-in online | Estado de tu vuelo | Agencias de viajes | Grupos | "
    "Gift Card | Informacion antes de viajar | Asistencia | Ayuda"
)
TITULO_VOLOTEA = "VOLOTEA | Vuelos baratos, ofertas y billetes de avion"


class PaginaFalsa:
    def __init__(self, titulo, cuerpo):
        self._titulo, self._cuerpo = titulo, cuerpo

    def title(self):
        return self._titulo

    def inner_text(self, _sel, timeout=None):
        return self._cuerpo


def test_detecta_el_bloqueo_de_aireuropa():
    senal = depuracion.bloqueada(PaginaFalsa(TITULO_AIREUROPA, BLOQUEO_AIREUROPA))
    assert senal is not None, "no reconocio la pagina de bloqueo de Air Europa"
    print("OK bloqueo de Air Europa detectado, senal:", repr(senal))


def test_una_pagina_buena_no_es_bloqueo():
    assert depuracion.bloqueada(PaginaFalsa(TITULO_VOLOTEA, OK_VOLOTEA)) is None
    print("OK la portada de Volotea no se confunde con un bloqueo")


def test_no_lanza_si_la_pagina_falla():
    """La instrumentacion nunca puede tumbar el rastreo."""
    class Rota:
        def title(self):
            raise RuntimeError("navegador cerrado")

        def inner_text(self, _sel, timeout=None):
            raise RuntimeError("navegador cerrado")

    assert depuracion.bloqueada(Rota()) is None
    print("OK una pagina rota devuelve None en vez de estallar")


def test_se_limpian_las_urls():
    """Los logs de Actions llevan tokens firmados en las urls."""
    sucio = "url=https://ejemplo.com/x?token=secreto123 y algo mas"
    assert "secreto123" not in depuracion._limpiar(sucio)
    assert "<url>" in depuracion._limpiar(sucio)
    print("OK las urls no se escupen al log")


if __name__ == "__main__":
    test_detecta_el_bloqueo_de_aireuropa()
    test_una_pagina_buena_no_es_bloqueo()
    test_no_lanza_si_la_pagina_falla()
    test_se_limpian_las_urls()
    print("\nTodo correcto.")
