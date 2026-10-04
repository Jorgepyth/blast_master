---
spec: 002-auto-resolucion-velas
verdict: pending  # lo fija T64, con un veredicto por RF e INV
---

# Validación de la spec 002

Lo que se comprobó con datos reales y con el usuario (T60 a T63), y el recorrido final por requisito (T64).

## T60. `resolution-report` sobre las DBs reales (2026-10-04)

**Cómo se corrió.** El comando real, desde el código de la rama al día (`cf79f25`, igual al checkout principal):

```bash
ACCOUNTS_DATA_DIR=<repo>/.data CANDLE_BANK_DIR=<repo>/.data/candle_bank AUTO_EXPORT=false \
  python -m cli.main resolution-report --output-dir <scratchpad>
```

- Corrió desde una carpeta del scratchpad, no desde el worktree ni desde el checkout principal. El callback de `cli()`
  crea un `.data/` en la carpeta actual, y así no tocó ninguno de los dos.
- Con `AUTO_EXPORT=false`, para que el disparo de T54 no lanzara un export ni tocara el banco.
- Las 4 DBs y el banco quedaron sin cambios: se compararon la fecha de modificación y el tamaño de cada archivo antes
  y después.
- Duración: 21 s. Código de salida 0. El `.md` (422 líneas) quedó fuera del repo, porque tiene datos del journal.
  Corrido sin `--output-dir`, el comando lo escribe en `.data/reports/`.

**Resultado por cuenta:**

| Cuenta | Resueltos por velas | Sin resultado | S4 estricto direccional | S4 estricto, todos | S1 direccional |
|---|---|---|---|---|---|
| 000 XAU | 75 de 81 | 6 `no_levels` | 28/45 = 62% (manual 30/45) | 43/75 = 57% | 29/45 = 64% |
| 001 US500 | 0 de 10 | 10 `clock_unverified` | — | — | — |
| 002 BTC | 22 de 24 | 2 `no_levels` | 8/16 = 50% (manual 9/16) | 9/22 = 41% | 9/16 = 56% |
| 003 US100 | 0 de 8 | 8 `clock_unverified` | — | — | — |

US500 y US100 no tienen resultado porque su reloj todavía no está verificado: esperan sus propias referencias (N43).

**El S1 direccional de XAU contra el 61% de `analisis-overlap-2d.md`.** Aquel análisis (2026-09-27) daba 23/38 = 61%,
con el ancla `created_at − 20 min`, velas de 15M y 1H de los CSV de MT5, y sin retroactivos. Hoy da 29/45 = 64%. Se
repitió el script de aquel día (`analisis/overlap_2d.py`, en solo lectura) y se comparó análisis por análisis:

- **Los 38 de entonces dan hoy el mismo primer toque** (23 validation y 15 invalidation). El cambio de velas (de 15M
  y 1H a la escalera 1M/5M/15M/30M/1H del banco) no cambió ningún resultado, y el ancla es la misma, porque esos
  análisis no tienen `analysis_start_time`.
- **Se suman 7:**
  - 6 retroactivos recuperados después del DROP del 2026-07-27, que cuentan desde N50: `9fb9e581`, `45c7ffc0`,
    `f24b9653`, `7ae2bfe8`, `b867b660` y `79a818c1`. Los 6 tocaron primero el objetivo. El séptimo recuperado,
    `5e8fc526`, es Choppy y solo entra en "todos".
  - `792518cd` (2026-09-16), que el 2026-09-27 todavía no había tocado nada: los CSV de entonces terminaban el
    2026-09-22. Tocó la invalidación el 2026-09-27 a las 20:55. Es una pérdida.
- **La cuenta:** 23 + 6 = 29 ganadas sobre 38 + 7 = 45. Sin los retroactivos recuperados daría 23/39 = 59%.

**Otras lecturas del reporte:**

- **Coincidencia con el audit manual en XAU:** tipo 66 de 74, Structural Resolution 54 de 74, MAE 32 de 73 y MFE 25 de
  73. Son los conflictos que va a mostrar el backfill (T63); el MAE/MFE difiere por la ventana (N49).
- **La excepción de toque** de `4b17b903` (N46) aparece con su vela (08:06, 4369.62 contra 4370).
- **Overlaps:** 14 en XAU y 8 en BTC. Solo son una etiqueta (N36, N52).
- **Un Mark Price fuera de su vela:** `695b2b4b` (XAU), a 8.11 en 1M. No tiene `mark_price_time`, así que se usa el
  período antes de `created_at`.
- **Demora del audit:** la mediana es 21.5 h en XAU y 39.6 h en BTC. Hay demoras negativas (el mínimo es −24.7 h en
  XAU): audits guardados antes de que el precio tocara el nivel. Ya se habían visto en la medición del 2026-10-02
  (7 casos en XAU, 6 de ellos con Overlap). El backfill (T63) mueve esas horas a `audit_registration_time` (N2), pero
  la demora sigue siendo la misma: no es un error de datos.

## T62. Export automático con MT5 abierto y cerrado (2026-10-04)

El usuario corrió `resolution-report`, `candles status` y el CLI (`trading`) en el checkout principal, con
`AUTO_EXPORT=true`. Fuente: su terminal y `.data/candle_export.log`.

**Domingo antes de las 16:00 (mercado cerrado, salvo BTC):**
- XAUUSD, US500 y USTEC fallan con `exporter_exit_1: ... Offset de servidor inferido fuera de rango: -42h.`: el último
  tick es del viernes y el exportador se niega a adivinar el reloj. El banco no cambia, y el reloj de XAUUSD sigue
  `verified` en `status.json`. El mensaje no dice que el mercado está cerrado: lo arregla T62b.
- BTCUSD (24/7) se fusiona (+1104 velas de 1M en la primera corrida; casi nada en las siguientes, minutos después).
- Abrir el CLI lanza los 4 de fondo sin esperar: `Candle export started in the background: XAUUSD, US500, BTCUSD,
  USTEC`.
- Un segundo export de BTCUSD no arranca mientras hay otro: `Candle export skipped: export_in_progress (BTCUSD)`
  (RF-20f).
- La retención (N47) borró las corridas viejas y deja 3 por símbolo.
- El reporte terminó normal en todos los casos (RF-20e, INV-2).

**Domingo 16:11 (mercado abierto):**
- XAUUSD se fusiona: `+2877` velas de 1M, reloj verificado por superposición.
- US500 y USTEC: `clock_unverified (9 reference prices in the exported range, need 10; 1 of 9 own reference prices do
  not fit the candles, so the clock of BTCUSD is not inherited)`. Es la regla estricta de N43, elegida por el usuario
  el 2026-10-02: el banco no cambia hasta tener 10 referencias propias que calcen.
- La espera de 30 s terminó con `not finished in 30s (BTCUSD, USTEC)`, y los dos siguieron de fondo: BTCUSD se fusionó
  a las 16:11:43 y USTEC dio `clock_unverified` a las 16:11:48, según el log.

**Domingo 16:15 a 16:17 (el usuario cerró MT5):**
- **Hallazgo:** con MT5 cerrado el export no falla, porque **vuelve a abrir MT5**. El exportador llama a
  `mt5.initialize()` sin argumentos (`windows_export/export_p2_ohlc.py:638`), y el paquete `MetaTrader5` lanza la
  terminal si no está corriendo. Cada símbolo es un proceso aparte, así que MT5 se reabría una vez por símbolo, y
  además en cada disparo (abrir el CLI, el reporte). El usuario lo vio al abrir `trading`: cerraba MT5 y se volvía a
  abrir.
- Así, el caso "MT5 cerrado" de RF-20e nunca se da hoy: XAUUSD se fusionó (+1 vela de 1M) con MT5 "cerrado".
- Cerrar MT5 en medio de un export sí se maneja bien: USTEC a las 16:16:26 terminó en
  `exporter_exit_1: ... (-10001, 'IPC send failed')`, como `Candle export skipped`, sin tocar el banco.
- **Decisión del usuario (N54, 2026-10-04):** que el export abra MT5 está bien. Más adelante quiere que el Tactical
  Audit coloque las órdenes en MT5, y para eso tiene que estar abierto. No se agrega el chequeo propuesto.

**Veredicto de T62:** cumplido.
- **Mercado abierto:** XAUUSD y BTCUSD se fusionan.
- **No disponible:** el mercado cerrado y MT5 cerrado a mitad de un export dan `Candle export skipped`, sin tocar el
  banco ni frenar nada.
- **El resto de los disparos** funcionó: de fondo al abrir el CLI, con espera acotada en el reporte, sin exports
  duplicados y con la retención.
- **El mensaje confuso** del offset fuera de rango quedó corregido en T62b.
