"""Prueba de la coherencia entre precio por noche y total en las OTAs.

Los numeros son los que el panel enseño de verdad: 618 y 618 durante semanas,
y despues 328 y 328, para estancias de siete noches. Un total de una semana no
puede coincidir con el precio de una noche.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.otas import _coherentes   # noqa: E402


def test_corrige_el_total_repetido():
    pn, tt, estimado = _coherentes(328.0, 328.0, 7)
    assert (pn, tt, estimado) == (328.0, 2296.0, True)

    pn, tt, estimado = _coherentes(618.0, 618.0, 7)
    assert (pn, tt, estimado) == (618.0, 4326.0, True)
    print("OK el total repetido se recalcula")


def test_no_toca_un_total_creible():
    assert _coherentes(328.0, 2296.0, 7) == (328.0, 2296.0, False)
    # Aunque no cuadre al centimo: las OTAs aplican tasas y descuentos.
    assert _coherentes(300.0, 1980.0, 7) == (300.0, 1980.0, False)
    print("OK un total creible se respeta tal cual")


def test_una_noche_no_es_incoherente():
    """Con una sola noche, que coincidan es lo correcto."""
    assert _coherentes(328.0, 328.0, 1) == (328.0, 328.0, False)
    print("OK una estancia de una noche no se toca")


def test_sin_datos_no_inventa():
    assert _coherentes(None, 328.0, 7) == (None, 328.0, False)
    assert _coherentes(328.0, None, 7) == (328.0, None, False)
    assert _coherentes(328.0, 328.0, 0) == (328.0, 328.0, False)
    print("OK sin datos suficientes no se inventa nada")


if __name__ == "__main__":
    test_corrige_el_total_repetido()
    test_no_toca_un_total_creible()
    test_una_noche_no_es_incoherente()
    test_sin_datos_no_inventa()
    print("\nTodo correcto.")
