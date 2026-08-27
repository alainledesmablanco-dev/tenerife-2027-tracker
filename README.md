# Rastreo Tenerife · agosto 2027

Vigila el precio del **Landmar Costa Los Gigantes** para agosto de 2027 y avisa por
Telegram cuando baje o cuando se abra la venta de vuelos desde Bilbao.

Corre en GitHub Actions dos veces al día, así que no depende de que tengas el
ordenador encendido.

**Viaje:** Bilbao → Tenerife · 2 adultos + 1 niño (5 años) · 7 noches (flexible 5–9)
· obligatorio pasar la noche del **8 de agosto** · **Todo Incluido** · **solo tarifas
con cancelación gratuita**.

**Precio de referencia a batir:** 1.987 € — Suite, Todo Incluido Plus, 2–9 agosto 2027,
283 €/noche, cancelable hasta el 31-jul-2027.

---

## Puesta en marcha

### 1. Crear el repositorio

Descomprime esta carpeta y súbela a un repo **público** (Actions ilimitado y gratis):

```bash
cd tenerife-2027-tracker
git init -b main
git add .
git commit -m "Rastreo Tenerife 2027"
git remote add origin https://github.com/TU_USUARIO/tenerife-2027-tracker.git
git push -u origin main
```

### 2. Crear el bot de Telegram (2 minutos)

1. Abre Telegram y habla con **@BotFather**.
2. Envía `/newbot` y sigue las instrucciones. Te dará un **token** con esta pinta:
   `7712345678:AAF-xxxxxxxxxxxxxxxxxxxxxxxxxxxx`
3. Busca tu bot recién creado y envíale un mensaje cualquiera (`hola`). Este paso es
   obligatorio: un bot no puede escribirte primero.
4. Abre en el navegador, cambiando el token:
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   Busca `"chat":{"id":123456789` — ese número es tu **chat id**.

### 3. Guardar los secretos

En el repo: **Settings → Secrets and variables → Actions → New repository secret**

| Nombre | Valor |
|---|---|
| `TELEGRAM_TOKEN` | el token de BotFather |
| `TELEGRAM_CHAT_ID` | tu chat id |

### 4. Activar Actions y Pages

- **Settings → Actions → General → Workflow permissions** → marca *Read and write permissions*.
- **Settings → Pages → Source** → *GitHub Actions*.

### 5. Primera ejecución

**Actions → Rastreo Tenerife 2027 → Run workflow.** Tarda unos 10 minutos.
Al terminar tendrás el panel en `https://TU_USUARIO.github.io/tenerife-2027-tracker/`.

---

## Cómo funciona

```
scraper/
├── config.py       Fechas, ocupación, filtros. Todo lo ajustable está aquí.
├── landmar.py      Lee el motor de reservas del hotel con Playwright.
├── vuelos.py       Orquesta la cotización de vuelos.
├── aireuropa.py    Air Europa, leída de su propia web (directo a TFN).
├── volotea.py      Volotea, leída de su propia web (directo a TFS).
├── vuelos_serp.py  Google Vuelos vía SerpApi, dormido hasta septiembre 2026.
├── otas.py         Booking, Expedia... vía Google Hotels (SerpApi).
├── combinar.py     Suma hotel + vuelos por fechas para dar el viaje entero.
├── notify.py       Avisos por Telegram.
├── report.py       Genera el panel HTML de GitHub Pages.
└── main.py         Orquesta todo y guarda el histórico.
```

### Vuelos: quién vuela a dónde, y quién vende ya

Comprobado a mano en las webs de las aerolíneas el **27-ago-2026**:

| Aerolínea | Aeropuerto | Días de directo | ¿Agosto 2027 a la venta? | Al hotel |
|---|---|---|---|---|
| **Volotea** | Tenerife Sur | mié y dom | **Sí** | ~45 min |
| **Air Europa** | Tenerife Norte | mar, jue y sáb | **Sí** | ~1 h 15 |
| Vueling | Tenerife Norte | a diario | **No** (su calendario acaba el 13-jun-2027) | ~1 h 15 |

Precios medidos ese día para 2 adultos + 1 niño, ida y vuelta, directo:

- **Air Europa 660 €** — 3→10 ago, sale 19:35 llega 21:45, vuelve 14:35→18:45.
  Tarifa Lite: maleta de mano de 10 kg, sin facturar.
- **Volotea 768 €** — 4→11 ago. Solo bolso bajo el asiento.

O sea que Air Europa sale más barata **y** con más equipaje, a cambio de media
hora más de coche desde el aeropuerto. Y como su directo va martes, jueves y
sábado, la entrada al hotel encaja el **sábado 7** o el **jueves 5**, no el
domingo 8.

### Por qué Google Vuelos no vale (todavía)

Google no cotiza más allá de unos **330 días**: el 27-ago-2026 aceptaba el
21-jul-2027 y rechazaba el 24-jul-2027 con "la fecha solicitada para el vuelo
es demasiado lejana". Agosto de 2027 entra en su rango hacia el **20-sep-2026**.

Por eso `vuelos_serp.py` no gasta ni una búsqueda hasta entonces
(`HORIZONTE_DIAS`), y mientras tanto los precios salen de las webs de las
aerolíneas. Cuando Google alcance las fechas, SerpApi entra solo y traerá
además a Vueling.

En cada pasada consulta las **35 combinaciones de fechas** que incluyen la noche del
8 de agosto (estancias de 5 a 9 noches), se queda solo con Todo Incluido cancelable
y compara con el mínimo histórico guardado en `data/historico.json`.

### Cuándo te escribe

| Situación | Aviso |
|---|---|
| Precio por debajo del mínimo histórico | 🔻 inmediato, con el ahorro |
| Se abre la venta de vuelos | 🔓 inmediato |
| Lunes por la mañana | 📊 resumen semanal aunque no haya novedades |

El resto de ejecuciones son silenciosas: guardan el dato y actualizan el panel.

---

## Ajustes habituales

**Cambiar la frecuencia** — en `.github/workflows/rastreo.yml`, las líneas `cron`
están en **UTC**. España peninsular en verano es UTC+2 y en invierno UTC+1.

**Aceptar también media pensión** — en `config.py`:

```python
SOLO_TODO_INCLUIDO = False
```

**Cambiar la duración** — en `config.py`, `NOCHES_MIN` y `NOCHES_MAX`.

**Ir más rápido** — la variable de entorno `MAX_VENTANAS` en el workflow limita
cuántas combinaciones se consultan (van ordenadas de más a menos parecidas a las
7 noches ideales).

**Probar solo los vuelos** — en **Actions → Run workflow** marca *solo_vuelos*.
Se salta el rastreo del hotel (que son ~10 minutos), reutiliza sus últimas
tarifas, cotiza los vuelos saltándose el límite diario de SerpApi y no manda
ningún aviso ni toca el histórico. Es la forma rápida de comprobar un cambio en
`vuelos_serp.py` o en `volotea.py`.

**Cuidado con la cuota de SerpApi** — el plan gratuito son 250 búsquedas al mes.
El reparto actual: ~60 de OTAs (1 al día) + ~90 de vuelos (`MAX_VENTANAS_VUELOS`
= 3, una vez al día) ≈ 150. Subir `MAX_VENTANAS_VUELOS` o quitar el límite
diario se come el margen deprisa.

---

## Limitaciones honestas

- El motor de Landmar es una SPA y **puede cambiar de maquetación sin avisar**. Si un
  día deja de leer precios, revisa los selectores de `landmar.py`. El script está
  hecho para fallar en silencio y avisar de que no leyó nada, no para inventarse
  un número.
- **Solo rastrea la web oficial del hotel.** Booking, eDreams, Logitravel y compañía
  tienen anti-bots serios y meterlos aquí daría más falsos positivos que otra cosa.
  Para esos, la comparativa manual sigue mereciendo la pena de vez en cuando.
- **Las dos aerolíneas se leen raspando sus webs**, y eso es frágil por
  definición. Si un calendario cambia de maquetación, el módulo devuelve lista
  vacía y lo dice en el log, pero no inventa precios. Los selectores concretos
  están documentados en la cabecera de cada módulo.
- En Air Europa, **qué días tienen vuelo directo se deduce del precio**: su
  calendario da el más barato de cada día sin decir si es directo. Se marcan
  como directos los días que no pasan de `MARGEN_DIRECTO` veces el precio del
  directo que sí se ha leído. Con los datos reales el corte es nítido (328 € el
  directo contra 674 € vía Madrid), pero solo el día cuya tarjeta se ha leído
  lleva `directo_confirmado`.
- El raspado de Google Flights con Playwright se conserva pero **desde GitHub
  Actions no funcionó ni una sola vez** en 52 pasadas: Google bloquea las IPs
  de centro de datos. Solo sirve ejecutando el rastreo en un portátil.
- El precio de vuelo **no incluye equipaje facturado**. Con Volotea la tarifa
  base es solo bolso de mano; con Vueling y Air Europa depende de la tarifa.
  Tampoco entra el coche de alquiler, que sale algo más caro desde el Norte.
- Los precios que veas son orientativos. **Confírmalos siempre en la web del hotel
  antes de reservar.**
