"""Pruebas del lector de Air Europa.

El texto de muestra NO esta inventado: es el que devolvio su buscador el
27-ago-2026 para Bilbao -> Tenerife Norte, 2 adultos y 1 nino. Si algun dia
cambian la maquetacion, lo que hay que actualizar es este fichero, y el fallo
salta aqui en vez de en una pasada de produccion.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper import aireuropa as ae   # noqa: E402

TEXTO_IDA = """Vuelos de ida
A continuación se muestra el precio más barato para cada día.
martes, 27 de julio de 2027
328,44
EUR
el precio más barato
mar, 27
miércoles, 28 de julio de 2027
674,61
EUR
mié, 28
sábado, 31 de julio de 2027
328,44
EUR
el precio más barato
sáb, 31
domingo, 1 de agosto de 2027
674,61
EUR
dom, 1
martes, 3 de agosto de 2027
328,44
EUR
el precio más barato
mar, 3
miércoles, 4 de agosto de 2027
674,61
EUR
mié, 4
jueves, 5 de agosto de 2027
328,44
EUR
el precio más barato
jue, 5
viernes, 6 de agosto de 2027
501,30
EUR
vie, 6
sábado, 7 de agosto de 2027
328,44
EUR
el precio más barato
sáb, 7
domingo, 8 de agosto de 2027
674,61
EUR
dom, 8
Resultados de vuelos en martes, 3 de agosto de 2027
19:35
BIO
sin escalas
3 h 10 min
21:45
TFN
Operado por
Air Europa
ECONOMY
desde
328,44
EUR
precio para todos los pasajeros
12:45
BIO
1escala
5 h 15 min
17:00
TFN
ECONOMY
desde
674,61
EUR
precio para todos los pasajeros
"""

TEXTO_VUELTA = """Vuelos de vuelta
A continuación se muestra el precio más barato para cada día.
martes, 3 de agosto de 2027
No disponible
mar, 3
jueves, 5 de agosto de 2027
323,73
EUR
el precio más barato
jue, 5
viernes, 6 de agosto de 2027
448,44
EUR
vie, 6
sábado, 7 de agosto de 2027
331,73
EUR
sáb, 7
martes, 10 de agosto de 2027
331,73
EUR
mar, 10
miércoles, 11 de agosto de 2027
610,44
EUR
mié, 11
jueves, 12 de agosto de 2027
331,73
EUR
jue, 12
sábado, 14 de agosto de 2027
371,73
EUR
sáb, 14
domingo, 15 de agosto de 2027
670,44
EUR
dom, 15
Resultados de vuelos en martes, 10 de agosto de 2027
14:35
TFN
sin escalas
3 h 10 min
18:45
BIO
Operado por
Air Europa
ECONOMY
desde
331,73
EUR
precio para todos los pasajeros
"""


def test_calendario():
    cal = ae.parsear_calendario(TEXTO_IDA)
    assert cal["2027-08-03"] == 328.44
    assert cal["2027-08-06"] == 501.30
    assert cal["2027-07-27"] == 328.44          # tambien lee el mes anterior
    assert len(cal) == 10
    print("OK calendario de ida:", len(cal), "dias")

    cal_v = ae.parsear_calendario(TEXTO_VUELTA)
    # "No disponible" no es un precio: ese dia no debe existir en el diccionario
    assert "2027-08-03" not in cal_v, cal_v
    assert cal_v["2027-08-10"] == 331.73
    print("OK 'No disponible' se descarta, no se cuela como 0 €")


def test_directo():
    d = ae.parsear_directo(TEXTO_IDA)
    assert d == {"horario": "19:35 – 21:45", "duracion": "3 h 10 min",
                 "origen": "BIO", "destino": "TFN", "precio": 328.44}, d
    v = ae.parsear_directo(TEXTO_VUELTA)
    assert v["horario"] == "14:35 – 18:45" and v["precio"] == 331.73, v
    print("OK tarjeta del directo:", d["horario"], d["precio"], "EUR")


def test_dias_con_directo():
    """El corte tiene que dejar fuera los vuelos con escala de Madrid."""
    cal = ae.parsear_calendario(TEXTO_IDA)
    directos = ae.dias_con_directo(cal, 328.44)
    assert set(directos) == {"2027-07-27", "2027-07-31", "2027-08-03",
                             "2027-08-05", "2027-08-07"}, sorted(directos)

    cal_v = ae.parsear_calendario(TEXTO_VUELTA)
    directos_v = ae.dias_con_directo(cal_v, 331.73)
    assert "2027-08-14" in directos_v          # 371,73 € sigue siendo directo
    assert "2027-08-06" not in directos_v      # 448,44 € ya es con escala
    assert "2027-08-11" not in directos_v      # 610,44 € via Madrid
    print("OK separacion directo/escala:", len(directos), "idas,", len(directos_v), "vueltas")


def test_combinar():
    """La mejor combinacion real del 27-ago-2026: 3 -> 10 ago, 660,17 €."""
    idas = ae.dias_con_directo(ae.parsear_calendario(TEXTO_IDA), 328.44)
    vueltas = ae.dias_con_directo(ae.parsear_calendario(TEXTO_VUELTA), 331.73)
    ofertas = ae.combinar(idas, vueltas,
                          ae.parsear_directo(TEXTO_IDA), ae.parsear_directo(TEXTO_VUELTA),
                          confirmadas=("2027-08-03", "2027-08-10"))
    assert ofertas, "no salio ninguna combinacion"
    mejor = ofertas[0]
    assert mejor["ida"] == "2027-08-03" and mejor["vuelta"] == "2027-08-10", mejor
    assert mejor["noches"] == 7
    assert mejor["precio_total"] == 660.17, mejor
    assert mejor["precio"] == 220.06          # por persona
    assert mejor["directo_confirmado"] is True

    # Toda combinacion debe cubrir la noche del 8 y durar entre 5 y 9 noches
    for o in ofertas:
        assert o["ida"] <= "2027-08-08" < o["vuelta"], o
        assert 5 <= o["noches"] <= 9, o
    print("OK mejor viaje:", mejor["ida"], "->", mejor["vuelta"],
          mejor["precio_total"], "EUR los 3 |", len(ofertas), "combinaciones")


if __name__ == "__main__":
    test_calendario()
    test_directo()
    test_dias_con_directo()
    test_combinar()
    print("\nTodo correcto.")
