"""Pruebas de vuelos_serp y combinar sin tocar la red.

Se falsea la respuesta de SerpApi porque el objetivo es comprobar NUESTRA
lectura, no la de Google: si un dia cambia el JSON, lo que hay que actualizar
es este fichero y el modulo, y conviene que el fallo salte aqui y no en una
pasada de produccion.
"""
import json
import os
import sys
import types
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SERPAPI_KEY"] = "clave-de-prueba"

from scraper import combinar, vuelos_serp   # noqa: E402

# Un dia en el que agosto de 2027 ya esta dentro del horizonte de Google.
DENTRO = date(2027, 1, 15)

RESPUESTA_OK = {
    "best_flights": [{
        "flights": [{
            "departure_airport": {"name": "Bilbao", "id": "BIO",
                                  "time": "2027-08-08 07:10"},
            "arrival_airport": {"name": "Tenerife Norte", "id": "TFN",
                                "time": "2027-08-08 10:20"},
            "airline": "Vueling", "flight_number": "VY 3920",
            "duration": 190,
        }],
        "total_duration": 190,
        "price": 612,
        "type": "Round trip",
    }],
    "other_flights": [
        {
            "flights": [{
                "departure_airport": {"id": "BIO", "time": "2027-08-08 09:40"},
                "arrival_airport": {"id": "TFN", "time": "2027-08-08 12:55"},
                "airline": "Air Europa", "flight_number": "UX 7007",
                "duration": 195,
            }],
            "total_duration": 195,
            "price": 738,
        },
        {   # con escala: debe descartarse porque SOLO_VUELOS_DIRECTOS
            "flights": [
                {"departure_airport": {"id": "BIO", "time": "2027-08-08 06:00"},
                 "arrival_airport": {"id": "MAD", "time": "2027-08-08 07:10"},
                 "airline": "Iberia", "duration": 70},
                {"departure_airport": {"id": "MAD", "time": "2027-08-08 09:00"},
                 "arrival_airport": {"id": "TFN", "time": "2027-08-08 11:30"},
                 "airline": "Air Europa", "duration": 150},
            ],
            "total_duration": 330,
            "price": 450,
        },
        {   # precio absurdo: el filtro de cordura debe tirarlo
            "flights": [{
                "departure_airport": {"id": "BIO", "time": "2027-08-08 20:00"},
                "arrival_airport": {"id": "TFS", "time": "2027-08-08 23:10"},
                "airline": "Vueling", "duration": 190,
            }],
            "total_duration": 190,
            "price": 99999,
        },
    ],
}

SIN_RESULTADOS = {"error": "Google Flights hasn't returned any results for this query."}


class RespuestaFalsa:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _parchear(payload):
    llamadas = []

    def get(url, params=None, timeout=None):
        llamadas.append(params)
        return RespuestaFalsa(payload)

    vuelos_serp.requests = types.SimpleNamespace(get=get)
    return llamadas


def test_lectura_normal():
    llamadas = _parchear(RESPUESTA_OK)
    ofertas, estados = vuelos_serp.buscar(
        [(date(2027, 8, 8), date(2027, 8, 15), 7)], hoy=DENTRO)

    assert estados == ["ok"], estados
    assert len(ofertas) == 2, [o["aerolinea"] for o in ofertas]

    mejor = ofertas[0]
    assert mejor["aerolinea"] == "Vueling"
    assert mejor["precio_total"] == 612.0          # los tres, tal cual lo da Google
    assert mejor["precio"] == 204.0                # 612 / 3, por persona
    assert mejor["destino"] == "TFN"
    assert mejor["destino_nombre"] == "Tenerife Norte"
    assert mejor["escalas"] == "directo"
    assert mejor["horario"] == "07:10 – 10:20"
    assert mejor["duracion"] == "3 h 10 min"
    assert mejor["ida"] == "2027-08-08" and mejor["vuelta"] == "2027-08-15"

    p = llamadas[0]
    assert p["adults"] == "2" and p["children"] == "1"   # la familia real
    assert p["type"] == "1" and p["stops"] == "1"        # ida y vuelta, directos
    assert p["arrival_id"] == "TFS,TFN"                  # una llamada, dos aeropuertos
    assert p["currency"] == "EUR"
    print("OK lectura normal:", json.dumps(mejor, ensure_ascii=False))


def test_sin_resultados_no_es_error():
    _parchear(SIN_RESULTADOS)
    ofertas, estados = vuelos_serp.buscar(
        [(date(2027, 8, 8), date(2027, 8, 15), 7)], hoy=DENTRO)
    assert ofertas == []
    assert estados == ["sin_vuelos"], estados
    print("OK 'todavia no hay vuelos' se distingue de 'fallo la consulta'")


def test_no_gasta_cuota_fuera_de_horizonte():
    """Google rechaza fechas a mas de ~330 dias; preguntar seria tirar cuota."""
    llamadas = _parchear(RESPUESTA_OK)
    ofertas, estados = vuelos_serp.buscar(
        [(date(2027, 8, 8), date(2027, 8, 15), 7)], hoy=date(2026, 8, 27))
    assert ofertas == []
    assert estados == ["fuera_de_horizonte"], estados
    assert llamadas == [], "se ha llamado a SerpApi para nada"

    # El 20-sep-2026 el 15-ago-2027 ya cae dentro y si se pregunta.
    llamadas = _parchear(RESPUESTA_OK)
    _, estados = vuelos_serp.buscar(
        [(date(2027, 8, 8), date(2027, 8, 15), 7)], hoy=date(2026, 9, 25))
    assert estados == ["ok"], estados
    assert len(llamadas) == 1
    print("OK no se consulta a Google antes de que alcance las fechas")


def test_combinar_no_triplica_el_total():
    """El fallo que se busca: multiplicar por 3 un precio ya cotizado para 3."""
    tarifas = [{"entrada": "2027-08-08", "salida": "2027-08-15", "noches": 7,
                "habitacion": "Suite", "total": 2008.0, "por_noche": 286.0}]
    vuelo_serp = {"aerolinea": "Vueling", "destino": "TFN", "ida": "2027-08-08",
                  "vuelta": "2027-08-15", "precio": 204.0, "precio_total": 612.0,
                  "fuente": "Google Flights (SerpApi)"}
    filas = combinar.calcular(tarifas, [vuelo_serp])
    assert filas[0]["vuelos_total"] == 612.0, filas[0]
    assert filas[0]["total"] == 2620.0, filas[0]

    # Y una oferta antigua, sin precio_total, sigue multiplicandose por 3.
    vuelo_viejo = {"aerolinea": "Vueling", "destino": "TFN", "ida": "2027-08-08",
                   "vuelta": "2027-08-15", "precio": 204.0}
    filas = combinar.calcular(tarifas, [vuelo_viejo])
    assert filas[0]["vuelos_total"] == 612.0, filas[0]
    print("OK el total del viaje no se triplica dos veces")


if __name__ == "__main__":
    test_lectura_normal()
    test_sin_resultados_no_es_error()
    test_no_gasta_cuota_fuera_de_horizonte()
    test_combinar_no_triplica_el_total()
    print("\nTodo correcto.")
