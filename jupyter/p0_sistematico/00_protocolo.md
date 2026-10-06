# Protocolo: P0 sistemático (fractales + Fibonacci)

- **Fecha:** 2026-10-06 (F1).
- **Base del código citado:** `3c12d0f`. Las líneas son de ese commit.
- **Prompt:** `docs/prompts/2026-10-06-p0-sistematico-fractal-fib.md` (v1.1), commit `76c9a53`, sha256
  `5cb8841c298f0256e7cc30d73e93a60c53a8865ba4b2a5fad7b512ee1f360970`.
- **Pre-registro:** `jupyter/p0_sistematico/preregistro.md`, commiteado solo y antes de calcular cualquier predicción,
  acierto o prueba:
  - v1.0: commit `3c12d0f`, 2026-10-06 09:17:57 GT, sha256
    `51724e5530f0ad2fca2558707e01e284ec439a0c721036c27677251129b66931`;
  - v1.1: commit `4f23145`, 2026-10-06 09:35:49 GT, sha256
    `39e748b7aa0492aa4c303f484fb52f8a46db42cdaceb0579e789f192722dbefe`. Agrega el camino con respaldo grueso
    (decidido por el usuario en la puerta de F1) y la lectura de `12T-`/`4T-` (§3, puntos 1 y 6).
- **Corte de la evidencia independiente (§14 del pre-registro):** ancla posterior a 2026-10-06 09:35:49 GT (commit de
  la v1.1).

## 1. Lectura obligatoria: qué se usa y de dónde

| Fuente | Qué se toma |
|---|---|
| `CLAUDE.md:21` | Nunca correr el CLI dentro del worktree, ni siquiera `<subcomando> --help`. |
| `CLAUDE.md:131-140` | Reloj de las velas: MT5 entrega hora del servidor; `calibrate_clock_offset()` valida el reloj. Los CSV viejos de `MT5Exports\` están corridos +3 h y no se usan. |
| `CLAUDE.md:141-161` | `BROKER_DST_RULE=us` asumida, no probada; ventanas ambiguas 2026-03-08–29 y 2026-10-25–11-01. |
| `CLAUDE.md:163-230` | Auditoría de análisis: look-ahead de la vela en formación (`:184-190`), ancla tardía (`:191-197`), DBs no migradas con columnas explícitas (`:198-202`), criterios de acierto (`:203-213`) y checklist de 6 puntos (`:215-230`). |
| `CLAUDE.md:232-277` | Formato de la evaluación "P2 banco v2", que este protocolo replica; "no volver a buscar pesos sobre estos mismos análisis". |
| `docs/criterios-de-acierto.md:12-64` | Ancla, precio de partida, primer toque 1M→1H, horizonte, retroactivos y N50, S4 estricto, S1. |
| `core/outcome_metrics.py` | `analysis_anchor` (`:36-46`), `AnalysisOutcome` (`:48-72`, con `hours_to_touch` en `:68`), `_strict_result` (`:137-148`), `s4_strict` (`:173-182`), `s1` (`:185-188`), `s4` (`:191-195`), `overlap_labels` (`:206-225`). |
| `core/candle_resolution.py` | `LADDER` y `LADDER_MINUTES` (`:47-51`), `bank_coverage` (`:85`), `PathBar` (`:138`), `forward_path` (`:147-204`), estados (`:207-209`), `horizon_end` (`:234-244`), `resolve_first_touch` (`:247-293`), `Candle` (`:296`), `start_price` (`:315-332`), `resolve_analysis` (`:352-395`), `_excepted_touch` (`:398`). |
| `core/p2_ground_truth.py` | `OhlcBar` (`:26`), `infer_thesis_direction` (`:33-49`), `first_touch_detail` (`:132`). |
| `tools/auto_resolution.py` | `ANALYSIS_COLUMNS` (`:62-63`), `read_analysis_rows` (`:108-128`, `mode=ro` y columnas explícitas), `load_bank_candles` (`:131-139`), `bank_clock_reason` (`:142-154`), `AccountResolver` (`:267-357`): `outcomes()` marca los 7 recuperados como no retroactivos; `propose()` aplica `TOUCH_EXCEPTIONS`. |
| `tools/p2_backtest.py` | `TIMEFRAME_MINUTES` (`:83-91`), `ModelSpec` (`:130-143`), `open_readonly_session` (`:229-238`), `CsvOHLCProvider` (`:339-454`) con `closed_bars` (`:391-401`) y `bar_containing`, `round_half_away_from_zero` (`:577-595`), `clamp` (`:598-599`), `rescale_p2_sistematico` (`:602-617`), `ExclusionRecord` (`:752-754`), `evaluate_clock_entries` (`:1087-1113`), `calibrate_clock_offset` (`:1132-1174`). |
| `core/stats_tests.py` | `binomial_test_two_sided` (`:30`), `mcnemar_exact` (`:47`), `wilson_interval` (`:57`), `holm_adjust` (`:72`), `min_detectable_gap` (`:135`), `net_score` (`:154-162`), `permutation_max_net_test` (`:165-195`). |
| `config/auto_resolution.py` | `MAX_HORIZON = 2160` (`:35`), `ANCHOR_FALLBACK_MIN = 20` (`:39`), `S4_WINDOW_H = 48` (`:64`), `REAL_ACCOUNTS` (`:71-76`), `MT5_SYMBOL_MAP` (`:79-84`), `TOUCH_EXCEPTIONS` (`:94-99`), `RECOVERED_BACKDATED` (`:104-112`), `BROKER_DST_RULE` (`:153`). |
| `tools/candle_bank.py` | `bank_csv_path` (`:62`), `read_candle_csv` (`:68`), `read_bank_status` (`:606`). |
| `cli/main.py:714-720` | `determine_market_bias`: umbral fijo ±0.26, no lee variables de entorno. |
| `tools/database.py:80-90` | `AnalysisLayer`: `department`, `layer_name`, `direction`, `strength`, `score`, `thesis`. |
| `cli/schemas/efficiency.py:5-13` | `Direction` = Long/Short/Neutral; `Strength` = Strong/Mid/Weak. |
| `tools/p2_bank_v2.py` | Referencia de cómo el banco v2 armó segmentos (`segment_of`, `:334-339`) y leyó el banco; no se importa nada de ahí. |

## 2. Re-verificación de los hechos `[COPIA]` del prompt

Todo en solo lectura (`mode=ro`), sin ninguna verdad ni acierto. Scripts en el scratchpad de la sesión.

| Hecho `[COPIA]` (copia del 2026-09-29) | Hoy (2026-10-06) | ¿Coincide? |
|---|---|---|
| P0 vive en `analysis_layer` (`layer_name='P0'`, `department='EFFICIENCY'`): dirección, fuerza y tesis; sin campos numéricos de Fibonacci | Sí: XAU 81 capas P0, BTC 24; columnas `direction`, `strength`, `score`, `thesis` | Sí |
| XAU: 81 análisis (7 retroactivos), 2026-05-18 a 2026-09-16; Bullish 18, Bearish 28, Choppy 35; 76 con ambos niveles | Igual. La columna `market_bias` coincide con `determine_market_bias(calc_edge)` en los 81 | Sí |
| BTC: 24 (0 retroactivos), 2026-07-02 a 2026-09-01; 12 / 5 / 7; 23 con ambos niveles | Igual, y la columna coincide en los 24 | Sí |
| Ningún análisis nuevo después de la copia | XAU termina el 2026-09-16 y BTC el 2026-09-01; **ninguno tiene `analysis_start_time`** | — (consecuencia en §3, punto 5) |
| P0 coincide con el sesgo final en 40/46 direccionales de XAU y 16/17 de BTC; P0 direccional en 34 Choppy de XAU (con retroactivos) | 40/46, 16/17 y 34. En BTC, P0 direccional en 6 de 7 Choppy | Sí |
| Tesis XAU sin retroactivos (74): 0.382 en 60, 0.618 en 42, 0.5 en 27, "nivel 0" en 29, "nivel 1" en 20; 0.236 y 0.786 en 0 | 0.382: 60, 0.618: 42, 0.5: 27, 0.236: 0, 0.786: 0. "nivel 0": 33 y "nivel 1": 28 | Casi: "nivel 0" y "nivel 1" dependen del patrón de búsqueda (el mío acepta "nivel 0." al final de una frase). No cambia D1: los dos se usan |
| Etiquetas por TF en 67–71 de 74 tesis XAU y 22 de 24 BTC; `==` en 63 de 74 y 19 de 24 | XAU: 1D 69, 12H 71, 4H 71, 1H 65 (71 con alguna); BTC: 22 en cada TF; `==`: 63 y 19 | Casi: 1H da 65, no 67. Variantes: 2 tesis XAU escriben `12T-`/`4T-` en vez de `12HT-`/`4HT-`, y hay `Bl` en minúscula |
| — | `==` contra la etiqueta de la TF referida: XAU 98 con la misma orientación y **5 con orientación distinta** (p. ej. `1DT-BR` y `12HT-BL: == 1DT`); BTC 28 y 0 | Hecho nuevo. T7 usa la etiqueta propia de cada TF |
| XAU 1H: 114 de 115 huecos de 2 h a las 15:00 GT de lunes a jueves; 1 anómalo (domingo 23:00 → 01:00) | 118 huecos de 2 h: 117 a las 15:00 (lunes 28, martes 30, miércoles 30, jueves 29) y 1 anómalo, el 2026-06-29 (falta la vela de las 00:00 del lunes). Además, 2 huecos de 4 h: 2026-05-25 y 2026-09-07, feriados de EE.UU. | Sí (el banco creció desde la copia) |
| `requirements.txt` sin `scipy` ni `statsmodels` | Sin ninguno de los dos | Sí |

## 3. Donde el prompt no coincide con el código o el entorno

1. **La historia intradía del banco es corta (cambia decisiones, ver §4).** Primera vela por TF:

   | TF | XAUUSD | BTCUSD |
   |---|---|---|
   | 1W | 1998-04-18 | 2011-03-19 |
   | 1D | 2021-12-26 | 2022-02-06 |
   | 12H | 2024-03-03 | 2024-04-16 |
   | 4H | 2025-08-17 | 2025-10-01 |
   | 1H | **2026-03-08** | **2026-04-19** |
   | 30M | 2026-04-07 | 2026-05-23 |
   | 15M | 2026-04-26 | 2026-06-08 |
   | 5M | 2026-05-05 | 2026-06-19 |
   | 1M | 2026-05-10 | 2026-06-24 |

   El pre-registro v1.0 (§7) detecta y resuelve los eventos solo en la escalera 1M→1H (`forward_path`). Con eso:
   - los eventos de T3–T5 existen solo desde el 2026-03-08 en XAU y el 2026-04-19 en BTC (unos 7 y 5,5 meses), no en
     "toda la historia de la foto";
   - conteo grueso de tramos A→B con fractales n = 2, sin ningún resultado (scratchpad `f1_pivots.py`):

     | TF | XAU desde la escalera | XAU toda la historia | BTC desde la escalera | BTC toda la historia |
     |---|---|---|---|---|
     | 1D | 32 | 246 | 41 | 384 |
     | 12H | 65 | 282 | 75 | 371 |
     | 4H | 192 | 370 | 209 | 480 |
     | 1H | 739 | 739 | 929 | 929 |

   - Estimación de cuántos eventos quedarían en el confirmatorio (40%) contra el mínimo de 480 por brazo. Supone que el
     precio llega a 0.382, 0.5 o 0.618 en ~1,4 niveles por tramo y a 0 o 1 en ~0,65, antes de deduplicar y de sacar
     ambiguos:
     - T3a, solo escalera: ~575, en el límite;
     - T3a con toda la historia: ~900;
     - T3b, solo escalera: ~270; con toda la historia: ~425. Insuficiente en los dos casos;
     - T4, un nivel por brazo, y T5, con los eventos partidos por segmento: muy probablemente insuficientes en los dos
       casos.
   - **Puntaje de los modelos:** un tramo de 1D (o de 1W en H) cuyo B se confirmó antes del 2026-03-08 no tiene camino
     de escalera entre la confirmación y esa fecha, así que no se sabe si hubo eventos ahí. El pre-registro v1.0 no dice
     qué puntaje lleva esa TF.
2. **`ModelSpec` no sirve tal cual para P0.** Tiene campos de P2 (`ema_chain`, `use_di`; `tools/p2_backtest.py:138-142`)
   y no tiene dónde guardar "usa Fibonacci", "jerárquico" ni el juego de niveles. Se usa un `P0ModelSpec` propio en
   `tools/p0_sistematico.py`, con los mismos campos comunes (`name`, `label`, `timeframes`, `weights`, `description`) y
   los de P0. No cambia ninguna decisión.
3. **`forward_path` solo recorre la escalera** (`core/candle_resolution.py:47`, `:171-172`, `:181-183`): termina donde ninguna TF
   de 1M a 1H cubre el instante. Cualquier camino más largo se arma componiendo segmentos alrededor de `forward_path`,
   sin reimplementarla.
4. **Rutas:** `config/auto_resolution.py` calcula `CANDLE_BANK_DIR` y `ACCOUNTS_DATA_DIR` desde la raíz del repo; en el
   worktree apuntan a un `.data/` que no existe. Todas las rutas se pasan explícitas: DBs del checkout principal y la
   foto en `.tmp/`. `AccountResolver` lee `<bank_root>/<símbolo>/{TF}.csv` y `status.json`, así que la foto copia esa
   misma estructura.
5. **`analysis_start_time` está vacío en todos los análisis de XAU y BTC.** Todas las anclas son `created_at − 20 min`,
   y los 7 recuperados, su hora tipeada. La evidencia independiente de §14 hoy son **0 análisis**: el último de XAU es
   del 2026-09-16 y el de BTC del 2026-09-01.
6. **Etiquetas de las tesis (D20):** aparecen `12T-` y `4T-` por `12HT-` y `4HT-` (2 tesis), y `Bl` en minúscula. El
   parser de RF-7 tiene que aceptarlas: es una regla de lectura, sin efecto en ninguna decisión. Los 5 `==` con
   orientación distinta de la TF referida se reportan en T7.
7. **`status.json`:** XAU y BTC tienen `clock: verified` y `dst_rule: unverified` (coherente con la regla asumida). El
   último export de XAU falló con el mercado cerrado (`export_failed`), sin efecto en el reloj.
8. **`determine_market_bias` vive en `cli/main.py:714`**, no en `core/`. Se importa sin ejecutar el CLI (importar
   `cli.main` no crea carpetas: los `os.makedirs` están dentro de funciones). El umbral ±0.26 está fijo en el código y
   no depende de `ICD_NO_TRADE_*`.
9. **N_MIN_BRAZO = 480 está bien calculado** para 50% → 58%, una cola, α = 0.05 y potencia 0.80 (sale 478), pero sin
   efecto de diseño: los eventos de un mismo tramo están correlacionados, así que la potencia real es menor. No se
   cambia (R7); se informa.

## 4. Decisiones afectadas

- **Punto 1, la historia corta del banco:** cambia qué eventos existen en T3–T5 y deja sin regla el puntaje de una TF
  sin camino. Necesita la decisión del usuario y una versión 1.1 del pre-registro, commiteada sola antes de F4.
- **Punto 6, el parser:** entra en la v1.1 como precisión de lectura, sin efecto en ninguna decisión.
- **Puntos 2 a 5 y 7 a 9:** no cambian ninguna decisión.

**Resolución (puerta de F1, 2026-10-06):** el usuario eligió "escalera + respaldo grueso" para el punto 1. Entró en la
v1.1 del pre-registro junto con el punto 6.

## 5. Datos de F2 (sin ninguna verdad ni acierto)

### 5.1 Foto del banco (R5)

- **Origen:** `.data/candle_bank/{XAUUSD,BTCUSD}/` del checkout principal, copiado con `cp -p` el 2026-10-06 a las 09:36
  GT.
- **Destino:** `.tmp/p0_foto_20261006/` del worktree (`.tmp/` está en `.gitignore`).
- **Copia fiel:** al terminar, los 20 archivos tenían el mismo sha256 que su origen.
- **Manifiesto:** `jupyter/p0_sistematico/foto_manifest.json`, con los mismos datos que la tabla de abajo. El cargador
  de `tools/p0_sistematico.py` lo verifica en cada lectura (E3).

| Archivo | Filas | Primera vela | Última vela | sha256 |
|---|---|---|---|---|
| `XAUUSD/1W.csv` | 1485 | 1998-04-18 15:00 | 2026-09-26 15:00 | `44ae21982a201c53c8073cd2899e6ce8b05b49cb0591a18c574a0d40edbc5853` |
| `XAUUSD/1D.csv` | 1232 | 2021-12-26 16:00 | 2026-10-01 15:00 | `6e48d77b54be9c834ffb0c54ddb9aa53df0436c1d0be0927a040867b6eaedefc` |
| `XAUUSD/12H.csv` | 1337 | 2024-03-03 16:00 | 2026-10-04 15:00 | `39ead389553758b7051a4c42720cab9b03efca30e0bb20e68959f4874e04cc58` |
| `XAUUSD/4H.csv` | 1755 | 2025-08-17 15:00 | 2026-10-05 07:00 | `75676594cac134b2bf4d4001a8889b798656bbf4a5323ab21a9af14b12f736e8` |
| `XAUUSD/1H.csv` | 3433 | 2026-03-08 16:00 | 2026-10-05 10:00 | `43f5689aac97dbd35dd293220a3d60647301ffa1e0b2c4d5b29b76b5a58f00ec` |
| `XAUUSD/30M.csv` | 5883 | 2026-04-07 23:00 | 2026-10-05 10:30 | `294e29d452e85fcfb8da4d5c3ec0d44bf9cd655df92239c891209e546ae78833` |
| `XAUUSD/15M.csv` | 10597 | 2026-04-26 16:00 | 2026-10-05 11:00 | `d9c553e940e89a125e6d9beba93fe8ad709692dc4149a7cf823e0f3b0f685c34` |
| `XAUUSD/5M.csv` | 29834 | 2026-05-05 17:40 | 2026-10-05 11:10 | `2c84b101114fea24caee29702b33521a977f1ec2beb7e51f2a4da519b12c735f` |
| `XAUUSD/1M.csv` | 144933 | 2026-05-10 16:00 | 2026-10-05 11:17 | `b3e545e099418f77b265e38082785e4a9f6e8a466abd30e8eb86f24483a297cc` |
| `XAUUSD/status.json` | — | — | — | `d63605caea400561ffa26ce1221873208458b2d50683ff2a7c85398bde5f8a3d` |
| `BTCUSD/1W.csv` | 810 | 2011-03-19 15:00 | 2026-09-26 15:00 | `0b92ac171f730bc488ec4a3160de7a2d10d43b25953551a77b6e81f8f138847c` |
| `BTCUSD/1D.csv` | 1702 | 2022-02-06 16:00 | 2026-10-04 15:00 | `c563802c54e854a62ce96d82c185e77680389ba4777b6a394dcef762ed86a995` |
| `BTCUSD/12H.csv` | 1805 | 2024-04-16 15:00 | 2026-10-05 15:00 | `8cc484f8f11f52a9868c44ccb82f3f6e19ea2c105946abf3f7d103ca8b73acab` |
| `BTCUSD/4H.csv` | 2215 | 2025-10-01 19:00 | 2026-10-05 23:00 | `dbcdf910442b92c3030f2652d0ad24340f4d5f5d4c213d677b5c569135190964` |
| `BTCUSD/1H.csv` | 4053 | 2026-04-19 17:00 | 2026-10-06 05:00 | `9df98b490777412ddefd287a54685e2369bff26fa5ff8aa72747ea00319c430b` |
| `BTCUSD/30M.csv` | 6488 | 2026-05-23 00:30 | 2026-10-06 05:30 | `41c6e71b29392a605d07e7c751c67bc5a74bb129d471236d37bd55dfe48258f1` |
| `BTCUSD/15M.csv` | 11362 | 2026-06-08 16:15 | 2026-10-06 06:00 | `b4b68d60db378e8ad2b8d5d31a34ea99b6a45f2ed01d7907235d8a696de1b743` |
| `BTCUSD/5M.csv` | 30852 | 2026-06-19 18:55 | 2026-10-06 06:15 | `bd376ce4de20cea4abb76aa12f3fe30bc0ed3b4c16bc5c6800c1a9f595c0f1c1` |
| `BTCUSD/1M.csv` | 147605 | 2026-06-24 05:31 | 2026-10-06 06:19 | `819a56a434284a2449041594db3be4c75e1c64991fc4c6e146ee9e9ef962f772` |
| `BTCUSD/status.json` | — | — | — | `5de500d76753bf3b6c9ab4a607c7e61c3806d8861506f638fb812b2cbbe30432` |

### 5.2 T0: reloj, duplicados y regla horaria

- **`status.json`:** XAUUSD y BTCUSD con `clock: verified` (`verified_by: overlap`), `dst_rule: unverified`; último
  export verificado con `base_utc_offset: 2.0` y `dst_rule: us` (servidor `ICMarketsSC-Demo`).
- **`calibrate_clock_offset`** sobre la foto, en 15M, con fills + `mark_price`:
  - XAUUSD: 105 referencias, mejor desplazamiento 0, `aligned = True`, 78% dentro de su vela;
  - BTCUSD: 19 referencias, mejor desplazamiento 0, `aligned = True`, 79%.
- **Duplicados:** ninguno en los 18 CSV. Se buscaron horas repetidas, horas fuera de orden y pares de velas a 1 h con
  OHLC idéntico en 4H, 12H, 1D y 1W, el problema de N44.
- **Regla horaria:** `BROKER_DST_RULE=us`, asumida. Ningún análisis del retrospectivo tiene su ancla, ni las 48 h
  siguientes, dentro de las ventanas ambiguas (2026-03-08–29, 2026-10-25–11-01). La sensibilidad de T0 sobre eventos se
  calcula en F4.
- **Velas con duración distinta de la nominal** (el banco etiqueta las velas en hora GT, que no tiene horario de
  verano, y el servidor sí):
  - una vez por año, la vela que cruza el fin del horario de verano de EE.UU. dura 1 h más que la nominal: 1W de XAU y
    BTC, y 1D, 12H y 4H de BTC (4 velas de 1D, 2 de 12H y 1 de 4H en BTC). Con la regla de vela cerrada (`time +
    duración nominal <= τ`) esa vela se da por cerrada 1 h antes de su cierre real. En XAU no importa, porque el mercado
    está cerrado el fin de semana. En BTC puede adelantar 1 h la confirmación de un pivote en 4 noviembres, todos fuera
    del período de los análisis (BTC: 2026-07-02 a 2026-09-01). Es la convención de `closed_bars()` y de INV-3, que
    miden con la duración nominal. Se deja documentado; no es un look-ahead nuevo del código;
  - en marzo pasa al revés (dura 1 h menos): la vela se da por cerrada 1 h después, lo que es conservador.

### 5.3 Huecos anómalos (E4) en 1H, 4H, 12H y 1D

Sin contar fines de semana ni la pausa diaria del oro (15:00 GT, de lunes a jueves):

| Activo | TF | Huecos | Explicación |
|---|---|---|---|
| XAU | 1H | 2026-05-25 13:00–16:00 y 2026-09-07 13:00–16:00 | Feriados de EE.UU. (Memorial Day, Labor Day) |
| XAU | 1H | 2026-06-29 00:00–01:00 | **Anómalo** (el del `[COPIA]`): falta la vela del lunes 00:00 |
| XAU | 4H, 12H, 1D | 24-25 y 31-12 / 1-01 de 2024 y 2025 | Navidad y Año Nuevo |
| BTC | 1H | 2026-05-15 15:00–16:00 y 2026-06-29 00:00–01:00 | **Anómalos**. El del 2026-06-29 es la misma hora que en XAU |
| BTC | 12H, 1D | 1 h en los fines de horario de verano | No es un hueco: es el cambio de etiqueta de §5.2 |

Los eventos de T3–T5 cuyo camino cruza un hueco anómalo se marcan en F4 (E4).

### 5.4 Análisis: filtros, segmentos y opiniones del operador

| Paso | XAU | BTC |
|---|---|---|
| 1. Todas las filas | 81 | 24 |
| 2. Retroactivos fuera (no recuperados) | 0 (los 7 retroactivos son los recuperados de N50 y entran) | 0 |
| 4. Evidencia independiente (ancla posterior a la v1.1) | 0 | 0 |
| 5. Sin precio de partida | 0 | 0 |
| 6. Sin niveles o con los dos del mismo lado | 6 | 2 |
| **En alcance** | **75** | **22** |

- **Anclas:** XAU de 2026-05-18 06:40 a 2026-09-16 11:12; BTC de 2026-07-02 07:50 a 2026-09-01 18:19. El precio de
  partida sale de 1M en los 97.
- **Segmentos (`determine_market_bias(calc_edge)`):** XAU Todos 75, Direccionales 45, Choppy 30; BTC 22 / 16 / 6.
- **P0 del operador que opina (Long o Short):** XAU 71 / 42 / 29; BTC 20 / 15 / 5.
- **Diferencia mínima detectable** (`min_detectable_gap(n, 0.5)`, 5% y potencia 80%), en puntos de acierto sobre 50%:
  - operador: XAU Todos ±16.6, Direccionales ±21.6, Choppy ±26.0; BTC ±31.3, ±36.2, ±62.6;
  - un modelo que opinara en todos (mejor caso): XAU ±16.2, ±20.9, ±25.6; BTC ±29.9, ±35.0, ±57.2;
  - la de cada modelo y la de cada par (con su n de opiniones y su n_disc) se calcula en F4, antes de mirar la verdad.
- **Choppy de BTC (6) queda siempre por debajo de 20:** "muestra insuficiente" por R17, sin conclusión.

### 5.5 Eventos del estudio de niveles: solo conteos, sin resultado (F3)

Prueba de humo del código de F3 sobre la foto. Son entradas a zonas (eventos), **sin mirar si fueron rechazo o
ruptura**. Todavía incluyen los que después salen ambiguos, abiertos o pendientes.

- Tramos por TF: XAU 1D 193, 12H 217, 4H 280, 1H 558; BTC 292, 285, 383, 724.
- Eventos: 15 081, y 13 590 después de deduplicar tramos entre TF.
- Entradas resueltas en camino grueso: 38% en total (1D 88%, 12H 77%, 4H 51%, 1H 0%).

| Parte | Activo | Retrocesos P0 | Retrocesos placebo | Extremos P0 | Extremos placebo |
|---|---|---|---|---|---|
| Exploratorio | XAU | 1117 | 1170 | 550 | 617 |
| Exploratorio | BTC | 1542 | 1583 | 725 | 837 |
| Confirmatorio | XAU | 736 | 787 | 373 | 422 |
| Confirmatorio | BTC | 1010 | 1063 | 486 | 572 |

El confirmatorio se contó como "todos menos exploratorio", sin llamar a la partición bloqueada. Lectura contra el mínimo
de 480 por brazo en el confirmatorio de XAU:
- T3a (retrocesos, 736 contra 787 entradas) probablemente llega, aun sacando ambiguos y abiertos;
- **T3b (extremos, 373 contra 422) no llega:** queda "insuficiente";
- **T4 (un nivel contra su placebo, unos 120 a 250 por brazo) no llega en ningún nivel;**
- T5 (los 10 niveles partidos en tendencia y rango) queda en el límite.

Consecuencia para D18: "Fib nulo" pide que T3a **y** T3b tengan n suficiente, así que no puede darse. "Modificar" por
T4 tampoco. Las decisiones posibles son, en la práctica: Continuar (si T3a pasa, BTC da el mismo signo y el ganador
le gana a su gemelo), Abandonar Fibonacci o Abandonar P0 sistemático (por el gemelo, con o sin T6), Modificar (por T5)
o Insuficiente. Es consecuencia de las reglas pre-registradas, no un cambio.

### 5.6 Detalles de implementación fijados en F3, antes de mirar cualquier resultado

1. **Varias zonas en una misma vela:** si una vela entra a varias zonas a la vez, el "último evento" de D8 es el más
   avanzado en el sentido de su llegada (el más bajo si llegó desde arriba).
2. **Corte 60/40:** se cuenta sobre todas las entradas de cada activo y TF, sin mirar el resultado (también las que
   después salen ambiguas, abiertas o pendientes).
3. **Bootstrap:** las remuestras en que IRLS no converge, o en que el nivel P0 queda constante, se descartan y se
   informan. El p unilateral usa las que quedan: (1 + #{β* ≤ 0}) / (n_ok + 1).
4. **Referencia de TF en la regresión:** 1D o, si el subconjunto no tiene eventos de 1D, la primera TF presente. Si no,
   las dummies sumarían el intercepto.
5. **Velas nominales:** el estado, los pivotes y el camino usan la duración nominal de cada TF (§5.2).
6. **El tramo de τ y sus eventos:** se usan todos los intervalos en que ese tramo fue el vigente hasta τ. En cada uno,
   el lado inicial sale del cierre de la vela de la TF que cerró al empezar el intervalo, y la extensión se mira desde
   la confirmación de B.

### 5.7 Suite (F3)

`conda run -n blast_master pytest -q -p no:cacheprovider`: **1277 passed, 3 skipped** en 62 s (la suite base tenía
1220 passed y 3 skipped; los 57 nuevos son `tests/test_p0_structure.py`, `tests/test_p0_fib.py` y
`tests/test_p0_sistematico.py`). No se creó `.data/` en el worktree.
