"""Configuración del rastreo. Todo lo ajustable vive aquí."""

from datetime import date, timedelta

# ---------------------------------------------------------------- viaje
HOTEL_URL = "https://www.landmarhotels.com/es/landmar-costa-los-gigantes.html"
BOOKING_URL = "https://www.landmarhotels.com/booking1"
NAMESPACE = "landmar-gigantes"

ORIGEN = "BIO"           # Bilbao
ORIGEN_NOMBRE = "Bilbao"

ADULTOS = 2
NINOS = 1
EDAD_NINO = 5

# Noche que hay que pasar sí o sí en el hotel (fiesta del hotel)
#
# El 12-ago-2026 esto estuvo unas horas en date(2026, 9, 8) para validar con
# datos reales las dos partes que agosto de 2027 no permite comprobar: Google
# Flights y Google Hotels no publican nada a 12 meses vista, así que sin la
# prueba era imposible saber si el código funcionaba o si simplemente no había
# datos. La prueba encontró y arregló dos fallos (el desglose por web pedía
# `q` junto al property_token, y las tarjetas de vuelo se leen del DOM y no
# adivinando el formato del texto). Si hay que repetirla, basta con volver a
# poner una fecha cercana aquí.
NOCHE_OBLIGATORIA = date(2027, 8, 8)

# Se rastrean estancias de 5 a 9 noches (35 combinaciones de fechas). Las de 8
# y 9 noches suelen salir más baratas por noche, así que merece la pena
# mirarlas. Para que el panel no se llene de filas repetidas, la tabla agrupa
# por duración y tipo de habitación en main.py.
NOCHES_OBJETIVO = 7
NOCHES_MIN = 5
NOCHES_MAX = 9

# Filtros duros: si una tarifa no los cumple, no entra en el informe
SOLO_CANCELABLE = True
SOLO_TODO_INCLUIDO = True

# Texto con el que el motor marca cada bloque de tarifas
BLOQUE_CANCELABLE = "CANCELACIÓN GRATUITA"
BLOQUE_PREPAGO = "PAGA AHORA"
REGIMEN_TI = "Todo Incluido"

# Mínimo histórico de partida (9-ago-2026, Suite TI Plus, 2-9 ago 2027)
PRECIO_REFERENCIA = 1987.0

# ------------------------------------------------------------- vuelos
# Aerolíneas con ruta directa Bilbao-Tenerife que hay que vigilar.
# La venta de agosto 2027 abre previsiblemente entre sep y nov de 2026.
AEROLINEAS = [
    ("Vueling", "https://www.vueling.com/es"),
    ("Ryanair", "https://www.ryanair.com/es/es"),
    ("Binter Canarias", "https://www.bintercanarias.com/esp/"),
    ("Iberia", "https://www.iberia.com/es/"),
    ("Air Europa", "https://www.aireuropa.com/es/es"),
    ("Volotea", "https://www.volotea.com/es/"),
]

# --------------------------------------------------------------- otros
TIMEOUT_MS = 45_000
PAUSA_ENTRE_BUSQUEDAS = 2.0   # segundos, por cortesía con el servidor
CODIGOS_PROMO_A_PROBAR: list[str] = []   # se rellena desde promo.py


def ventanas_validas() -> list[tuple[date, date, int]]:
    """Todas las combinaciones entrada/salida que incluyen la noche obligatoria.

    Devuelve (entrada, salida, noches) ordenado por cercanía a NOCHES_OBJETIVO,
    de modo que las estancias más parecidas a la ideal se consultan primero.

    Por construcción la entrada nunca cae a más de NOCHES_MAX-1 días antes de
    la noche obligatoria, así que no hace falta filtrar por mes (antes había un
    filtro fijo a agosto que dejaba la lista vacía con cualquier otra fecha).
    """
    ventanas = []
    for noches in range(NOCHES_MIN, NOCHES_MAX + 1):
        for offset in range(noches):
            entrada = NOCHE_OBLIGATORIA - timedelta(days=offset)
            salida = entrada + timedelta(days=noches)
            ventanas.append((entrada, salida, noches))
    ventanas.sort(key=lambda v: (abs(v[2] - NOCHES_OBJETIVO), v[0]))
    return ventanas
