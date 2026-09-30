# Protocolo: P2 banco v2 (F1)

- **Fecha:** 2026-09-29 (F1 y F2 hasta la pausa) y 2026-09-30 (F2b en adelante).
- **Base del código citado:** `3c557a6`. Las líneas son de ese commit.
- **Pre-registro:** `jupyter/p2_banco_v2/preregistro.md`. Las dos versiones se commitearon solas y antes de calcular
  cualquier predicción o acierto:
  - v1.0: commit `e99e69d`, 2026-09-29 11:24 GT, sha256
    `c569e760a77b4cd821afde9518aea7a2ee0678d3a2955101e70c679611719e44`;
  - v1.1: commit `c43f2b7`, 2026-09-30 06:45 GT, sha256
    `997a94165ba2918309e80c6b4d5814d5d43469fd397ff72fcb9740562529bcb6`. Agrega la limpieza de velas repetidas (§5).

## 1. Lectura obligatoria: qué se usa y de dónde

| Fuente | Qué se toma |
|---|---|
| `CLAUDE.md:87-95` | Reloj de las velas: MT5 entrega hora del servidor. `calibrate_clock_offset()` aborta si no cuadra. |
| `CLAUDE.md:119-181` | Auditoría de análisis de datos, en particular: look-ahead de la vela en formación (`:140-146`), ancla tardía (`:147-151`), lectura de DBs no migradas con columnas explícitas (`:154-157`), criterios S1/S4 (`:159-164`) y el checklist de 6 puntos (`:166-181`). |
| `docs/criterios-de-acierto.md:12-64` | Ancla `created_at − 20 min` (`:14-15`), precio de partida con vela cerrada en la TF más fina (`:16-17`), primer toque 1M→1H (`:18-24`), horizonte 2160 velas 1H (`:25-26`), retroactivos fuera (`:27-28`), S1 (`:32-39`), S4 (`:41-49`) y Overlap como etiqueta (`:51-64`). |
| `jupyter/p2_edge_evaluation.ipynb` (v2 del 2026-09-23) | El protocolo a replicar: §1.2 (celda 10), §1.3 (celda 13), §6 (celdas 28, 31, 32), §7 (celda 35), §8.1–§8.9 (celdas 39–62) y §11 (celda 69). |
| `tools/p2_backtest.py` | `ModelSpec` (`:130-143`), `MODEL_A..F` (`:146-219`), `MODELS` (`:221`), `closed_bars` (`:391-401`), `get_indicator_snapshot` (`:427-454`), `compute_score_p2_sistematico` (`:537-574`), `rescale_p2_sistematico` (`:602-617`), `evaluate_clock_entries` (`:1081-1107`), `calibrate_clock_offset` (`:1126-1168`). |
| `docs/prompts/2026-09-23-p2-consenso-modelo-f-adenda-1.md` | D14, el modelo G (`:125-145`); D15, D como modelo activo (`:146-167`). |
| `docs/archive/specs/001-p2-consenso-modelo-f/CIERRE.md` | Por qué se cerró la spec 001 (`:28-58`); H-C, el test que creaba `.data/` (`:103-121`). |
| `core/stats_tests.py` | `binomial_test_two_sided` (`:30-44`), `mcnemar_exact` (`:47-54`), `wilson_interval` (`:57-69`), `min_detectable_gap` (`:135-143`), `net_score` (`:154-162`), `permutation_max_net_test` (`:165-195`). |
| `tools/edge_evaluation.py` | `simplex_grid` (`:328-340`), `select_best` (`:362-369`), `walk_forward_folds` (`:372-379`); `initial = max(2·WF_BLOCK, n // 2)` en `:442`. |
| `windows_export/export_p2_ohlc.py` | `TIMEFRAME_MAP` (`:102-112`, 9 TF sin 2H), `ALL_EXPORT_TIMEFRAMES` (`:116`), `TIMEFRAME_MINUTES` (`:120-130`), hora del servidor por vela (`:238-299`), `compute_backward_start` (`:354-374`), `parse_timeframes_arg` (`:541-555`). |

## 2. Cómo construyó el v1 cada pieza (§6 y §8)

**Filas y ancla** (celda 5): `assemble_p2_systematic_rows(..., include_no_execution=True, anchor_mode="analysis")`.
- Ancla: `created_at − 15 min` (`tools/p2_backtest.py:692`, `:884`).
- Retroactivos fuera (`:881-883`).
- Sin EVP o SI: fuera (`:873-878`).
- Precio de partida: último cierre cerrado en 15M, 30M o 1H (`:693`, `:705-716`).

**Predicción** (celda 31, `pred()`):
- snapshot por TF con `get_indicator_snapshot(tf, ancla)`, solo velas cerradas (`:391-401`, `:427-454`);
- `rescale_p2_sistematico(compute_score_p2_sistematico(snaps, modelo)[0])`;
- predicción = 0 si el valor es 0 o `None`; si no, su signo;
- tu P2 = signo de `p2_discrecional`, el score de la capa P2 (`:929-935`).

**Ground truth** (celda 28 y `tools/p2_backtest.py:959-962`):
- dirección de la tesis con `infer_thesis_direction` (`:940`; `core/p2_ground_truth.py:33-49`);
- camino: **velas 1H con `time > ancla`**, hasta 2160 (`:456-464`, `:102-103`);
- `first_touch_direction` (`core/p2_ground_truth.py:52-105`): si una vela toca los dos niveles, queda incompleto
  (`:96-97`);
- entran solo los resueltos. Verdad = +1 si la dirección resultante es long, −1 si es short.

**Segmentos** (celda 31):
- `choppy = bias_predicho_original == "Choppy / Neutral"`, con `bias_predicho_original =
  determine_market_bias(calc_edge)` (`tools/p2_backtest.py:937-938`; `cli/main.py:681-687`, umbral ±0.26);
- Direccionales = no Choppy; Todos = los 89.

**Métricas** (celda 31):
- aciertos/opiniones con IC de Wilson;
- neto con `net_score`;
- "Opina en" = opiniones / resueltos.

**Pruebas de §8:**

| § | Cómo se hizo en el v1 |
|---|---|
| 8.1 (celda 39) | `permutation_max_net_test` sobre 7 variantes: A–F y "C + 30M · pico 12H" (celda 35). `n_perm = 10 000`, semilla 20260923. |
| 8.2 (celda 42) | Resueltos ordenados por ancla, mitades en `len // 2`. Aciertos/opiniones y neto de F, A, C y tu P2. |
| 8.3 (celda 44) | Lo mismo, por activo. |
| 8.4 (celda 47) | Interpolación de pesos de C a F, α de −0.5 a 2 cada 0.125, más cada TF sola. |
| 8.5 (celda 51) | Reparto long/short de F y "siempre short" en los mismos análisis. |
| 8.6 (celda 54) | F contra A: test de signo sobre `val(F) − val(A)`, que **incluye** análisis donde opina uno solo. |
| 8.7 (celda 57) | Solo XAU. Momentos = apertura 1H + 30 min, entre el primer y el último ancla. Precio: último cierre 15M. Niveles: ± la mediana de tus distancias. Camino 1H. `default_rng(SEED)` por grupo, 400 sin reposición, franjas "cualquier hora" y 5–8. Modelo F. Test de dos proporciones. |
| 8.8 (celda 60) | Geometría (esperado por distancia, "siempre hacia el nivel más cercano", F hacia el nivel cercano o el lejano). Anclas a 0, 5, 10, 15, 30 y 60 min, recalculando todo. |
| 8.9 (celda 62) | Tabla ✅/⚠️/❌ sin criterios numéricos fijados de antemano. |
| §1.2 (celda 10) | `calibrate_clock_offset` por cuenta (15M), y velas con `time <` primer ancla por TF. |

## 3. Diferencias entre el v1 y este banco v2

| Aspecto | v1 (`p2_edge_evaluation.ipynb`) | v2 (este prompt y el pre-registro) | Por qué |
|---|---|---|---|
| Ancla | `created_at − 15 min` | `created_at − 20 min` | `docs/criterios-de-acierto.md:14-15`, decidido el 2026-09-27, después del v1 |
| Velas | CSV de `MT5Exports/{XAUUSD,BTCUSD}` | Banco `.data/candle_bank`, un solo snapshot con sha256 | Prompt, F2 |
| Precio de partida | Último cierre en 15M/30M/1H | TF más fina validada: 1M, 5M, 15M, 30M, 1H | Criterios `:16-17` |
| Camino del ground truth | 1H con `time > ancla`: la vela 1H en formación y la que abre justo en el ancla quedan fuera, y el primer tramo (hasta ~1 h) no se observa | Velas que abren en el ancla o después, en la TF más fina que cubre cada instante (1M en la práctica) | Criterios `:18-20`; spec 002 RF-4, N17 y N18 |
| Vela que toca los dos niveles | Incompleto, en 1H | `ambiguous`, en la TF más fina (1M) | Criterios `:23-24` |
| Sin toque | Un solo estado, "incompleto" | `open` (horizonte cumplido) y `pending` (el banco no llega) | Spec 002 RF-4c y RF-5 |
| S4 | No existía | 48 h, al lado de S1, descriptivo | Criterios `:41-49` |
| Overlap | No existía | Etiqueta informativa, nunca excluye | Criterios `:51-64` |
| Temporalidades | 1W … 15M | Se suman 2H y 5M | Prompt, `<configuracion>` |
| Familia de 8.1 | 7 variantes, incluida "C + 30M · pico 12H" | 13: A, B, C, D, E, F, G, D-EQ, 2A–2E. C30 no entra, 2F va aparte | Prompt, `<configuracion>` |
| Comparación pareada | 8.6: test de signo sobre `val` (entran análisis donde opina uno solo) | R1: solo donde **los dos** opinan, con McNemar exacto | Prompt, R1 |
| p del modelo en 8.1 | Solo el del máximo | p "single-step max-T" de cada modelo (mismo nulo) | Hace falta para aplicar R7 a cada 2X |
| Regla de decisión | Ninguna fija para P2 (todo exploratorio) | R7, fija en el pre-registro | Prompt, R7 |
| Criterios ✅/⚠️/❌ | Cualitativos | Numéricos, fijados en el pre-registro §9 | Evita la lectura a posteriori |
| 8.4 | Interpolación C→F | Vecinos ±0.05 en la grilla | Prompt, R5 |
| 8.7 | F; precio 15M; camino 1H | Mejor 2X (y D de referencia); precio 1M; camino S1 | Consistencia con S1 |
| 8.8, ancla | 0–60 min (15 la usada) | 0–60 min, más 20 (20 la usada) | Nueva ancla |
| Pesos fuera de muestra | Sobre P0–P4 del edge (`run_question_2`) | 2F: grilla de 10 626 sobre las 5 TF de P2, walk-forward + LOO, prior 2A | Prompt, R6 |
| MDD | Solo para el edge (§3) | Por predictor y por par, sin usar la verdad | Prompt, R8 |
| Semilla | 20260923 | 20260929 | Pre-registro §14 |
| Retroactivos | Fuera | Fuera, con conteo | Igual |
| Segmento | `determine_market_bias(calc_edge)` | Igual | Igual |
| Neto | `net_score` | Igual | Igual |
| Predicción | Signo de `rescale(score)`; `None` → 0 | Igual, con "sin cobertura" marcado aparte | Igual |

## 4. Donde el prompt no coincide con el código o el entorno (manda el código)

1. **Worktree.** El prompt pide `.claude/worktrees/p2-banco-v2`, rama `claude/p2-banco-v2`. La sesión ya corre en
   `.claude/worktrees/p2-banco-v2-eval-f63a69`, rama `claude/p2-banco-v2-eval-f63a69`, creada desde `HEAD` = `3c557a6`.
   Se usa esa: cumple lo mismo (worktree nuevo desde `HEAD`, sin `.data/`, sin tocar el de la spec 002).
2. **`TIMEFRAME_MINUTES` no tiene 2H** (`tools/p2_backtest.py:83-91`), y `closed_bars` y `bar_containing` lo usan
   (`:401`, `:423`). Se resuelve en `tools/p2_bank_v2.py` con una subclase del proveedor que usa el mismo filtro con 120
   min. `tools/p2_backtest.py` no se toca.
3. **El test existente del exportador usa "2H" como ejemplo de temporalidad desconocida**
   (`tests/test_export_p2_ohlc.py:257-260`), y otro exige que `ALL_EXPORT_TIMEFRAMES` y `TIMEFRAME_MAP` tengan las
   mismas TF (`:234-237`). Al agregar 2H hay que ajustar esas dos aserciones:
   - el ejemplo de TF desconocida pasa a "3H";
   - el invariante pasa a decir que `TIMEFRAME_MAP` es la unión de las 9 de siempre más las "bajo pedido" (2H).

   2H **no** entra en `ALL_EXPORT_TIMEFRAMES`: el export por defecto (el que usa `candle_sync` para el banco) no cambia.
4. **`EXPORTER_WIN_PATH` del `.env` apunta al exportador del checkout principal**
   (`\\wsl.localhost\Ubuntu\home\jorgecg\projects\trading\blast_master\windows_export\export_p2_ohlc.py`), que no va a
   tener 2H hasta que esta rama se integre. El comando de F2a apunta al exportador de este worktree.
5. **El 1H del banco no puede ir antes del 2026-03-08.** `filter_by_export_season` (`tools/candle_bank.py:498-517`)
   solo fusiona 1H y las TF más finas de la misma estación de horario que el export (verano, desde el 2026-03-08 con la
   regla `us`). Por eso el 1H de XAU empieza exactamente el 2026-03-08 16:00, y un 2H remuestreado (F2c) no puede tener
   historia anterior.
6. **R16 de la spec 002 ya está implementado** en `3c557a6`: `tests/conftest.py` cambia el cwd de cada test a un
   `tmp_path` y un audit hook bloquea `.data/` del repo y `/mnt/c/`. En un worktree, ese `.data/` es el del worktree
   (que no existe); los tests usan rutas relativas, que caen en `tmp_path`. Igual se verifica antes y después de la suite
   que el `.data/` del checkout principal no cambió.
7. **Cobertura del banco**, verificada el 2026-09-29 contra los archivos: coincide con el `<contexto>` del prompt
   (1H XAU desde 2026-03-08, 30M XAU desde 2026-04-07, 5M XAU desde 2026-05-05, 5M BTC desde 2026-06-19, sin 2H). Además:
   30M BTC desde 2026-05-23, 1M XAU desde 2026-05-10 y 1M BTC desde 2026-06-24.
8. **`analysis_start_time` no existe** en ninguna de las dos DBs (`PRAGMA table_info(unified_department)`), así que el
   ancla es `created_at − 20 min`, como dice el prompt.
9. **No hay análisis nuevos desde el v1:** el último `created_at` es 2026-09-16 (XAU) y 2026-09-01 (BTC). Las 81 + 24
   filas son las mismas que usó el v1, así que todo resultado de este banco es exploratorio (R9).
10. **"Funciones puras" en `tools/p2_bank_v2.py`.** Ninguna función del módulo abre una DB ni un archivo, ni escribe
    nada: reciben la sesión de solo lectura, el proveedor de velas y los DataFrames ya cargados. Abrir las DBs
    (`mode=ro`), leer el CSV del 2H y calcular los sha256 lo hace el cuaderno. La única lectura de disco que queda en el
    módulo es la de `BankProvider`, heredada de `CsvOHLCProvider`: es la subclase que hace falta para el 2H (punto 2).
11. **F2d y F2b.3 se pisan con un 2H nativo al que le faltan días.** Si le falta más del 1% de las velas, el "% de
    fronteras alineadas" de F2d baja del 99% y la regla fijada es detenerse y reportar, no caer en silencio al
    remuestreo. F2b.3 decide solo cuando faltan 2 días seguidos que son menos del 1% de las velas. Con los datos reales
    no aplica: F2d dio 100%.
12. **La prueba de reloj distingue mal un corrimiento de 1 h con velas de 2H.** En BTC, con 19 precios de referencia,
    un corrimiento de −1 h cuadra en 19 y sin corrimiento en 18. La regla de `calibrate_clock_offset` lo da por válido
    (diferencia menor a 0.20). La prueba que decide es F2d, contra el 2H armado desde el 1H verificado.
13. **El walk-forward de R6 usa resultados que todavía no se conocían.** `walk_forward_folds`
    (`tools/edge_evaluation.py:372-379`) entrena con los análisis anteriores por fecha de ancla. El resultado de un
    análisis se conoce recién en su primer toque (mediana de 11 h), que puede ser posterior al ancla del análisis que se
    predice. Pasa en 9 de los 45 análisis de prueba (18 usos). Es el procedimiento fijado, el mismo del cuaderno v1, así
    que el resultado de 2F se reporta con él. Como control, `run_2f_known_outcomes` lo repite dejando fuera esos
    resultados: mismo neto (+7) y p = 0.047. No cambia ninguna etiqueta ni la acción. No afecta a 2A–2E ni a D, que no
    eligen nada con resultados.

## 5. Datos de F2

**Reloj del banco: OK en las dos cuentas.** `calibrate_clock_offset` elige 15M:

| Cuenta | Precios de referencia | 15M sin desplazar | Mejor desplazamiento en 1M / 5M / 15M / 30M / 1H |
|---|---|---|---|
| XAUUSD | 105 | 78% | 0 / 0 / 0 / 0 / 0 |
| BTCUSD | 19 | 79% | 0 / 0 / 0 / 0 / 0 |

**Offset del servidor (para F2c).** Con la regla `us` y base UTC+2, el 100% de las velas de verano del banco abren en
la frontera del servidor (1D 00:00, 12H, 4H). En invierno no se puede verificar así: esas TF tienen velas repetidas
(ver abajo).

**Filtros (pre-registro §3):**

| Cuenta | Filas | Sin niveles | Retroactivos | Sin historia | Niveles del mismo lado | En alcance | Direccionales / Choppy |
|---|---|---|---|---|---|---|---|
| XAUUSD | 81 | 5 | 7 | 0 | 1 | 68 | 39 / 29 |
| BTCUSD | 24 | 1 | 0 | 0 | 1 | 22 | 16 / 6 |

- El precio de partida sale de 1M en los 90 análisis.
- Los anclas van de 2026-05-18 06:40 a 2026-09-16 11:12 en XAU, y de 2026-07-02 07:50 a 2026-09-01 18:19 en BTC.

**Hallazgo: velas repetidas en el banco (4H, 12H, 1D y 1W).**
- **Qué pasa:** cada vela de invierno está dos veces, con OHLC idéntico y etiquetas separadas por 1 h. Por ejemplo,
  XAU 1D 2021-12-26 15:00 y 16:00.
- **Por qué:** una copia viene del import de los CSV viejos, exportados con un solo offset (+3). La otra viene del
  export nuevo con `--dst-rule us` (+2 en invierno). La fusión deduplica por `time`, así que no las ve.
- **Por qué no pasa en 1H y las más finas:** el banco solo guarda las de la estación del export
  (`filter_by_export_season`).
- **Cuántos pares** (todos se resuelven: exactamente una de las dos copias abre en la frontera del servidor):

  | Cuenta | 1W | 1D | 12H | 4H |
  |---|---|---|---|---|
  | XAUUSD | 509 | 407 | 362 | 528 |
  | BTCUSD | 272 | 505 | 504 | 737 |

- **Efecto, medido sin mirar aciertos:** con las reglas de D, la señal por TF cambia en 19 de 68 análisis de XAU en 1D,
  15 en 1W y 10 en 12H. La predicción cambia, en los 90 análisis, en 20 de A, 13 de B, 12 de C, 6 de D, 2 de E y 6 de F.
- **Decisión del usuario (2026-09-30):** quitar la copia repetida al leer, sin tocar el banco. Quedó en la v1.1 del
  pre-registro (§2), commiteada antes de F3. La hace `drop_relabeled_duplicates` de `tools/p2_bank_v2.py`.
- **Pendiente fuera de este trabajo:** corregir la fusión del banco (`tools/candle_bank.py`, spec 002). Mientras tanto,
  cualquier cálculo que lea 4H, 12H, 1D o 1W del banco ve cada vela de invierno dos veces.

**Cobertura si no hubiera 2H nativo (con el 2H remuestreado de F2c), calculada antes de la pausa:**

| | Banco tal cual | Banco sin duplicados |
|---|---|---|
| Análisis que D mide | 90 (68 XAU + 22 BTC) | 83 (68 + 15): 7 de BTC pierden 1W, porque el broker tiene 809 semanas reales y las 800 cerradas recién se alcanzan el 2026-07-25 |
| Pierde el 2H remuestreado | 15 de XAU (2026-05-18 a 2026-06-10) = 16.7% | los mismos 15 = 18.1% |
| ¿Parada por más de 25%? | No | No |

- El 2H remuestreado llega a 800 velas cerradas recién el 2026-06-10 en XAU y el 2026-06-25 en BTC, porque el 1H del
  banco empieza el 2026-03-08 (XAU) y el 2026-04-19 (BTC). El 2H nativo podría recuperar esos 15.
- **Controles del remuestreo, contra el 4H del banco:**
  - XAU: 2H → 4H coincide en 872 de 872 velas (fronteras 100%, OHLC 100%).
  - BTC: 976 de 976 fronteras (100%) y 975 de 976 con OHLC idéntico (99.9%).
  - La única distinta es la primera vela 4H (2026-04-19 15:00): sus dos primeras 1H son anteriores al inicio del 1H
    del banco (17:00). Es un efecto de borde, no un error del remuestreo.

**Fuente del 2H: nativo (F2b) en los dos activos.** El usuario exportó el 2H el 2026-09-29 (corridas
`20260929T174616` de XAUUSD y `20260929T174627` de BTCUSD, en `C:\Users\jcifu\MT5Exports\p2_banco_v2\`). Se lee directo
de esa carpeta y no se integra al banco.

| Condición | XAUUSD | BTCUSD |
|---|---|---|
| Velas del export | 2326, desde 2025-12-29 | 2742, desde 2026-02-12 |
| F2b.1 Reloj del 2H | valida: 95% sin desplazar, mejor desplazamiento 0 | valida: 95% sin desplazar, mejor −1 h (19 de 19 contra 18 de 19; ver §4.12) |
| F2b.2 Velas cerradas antes del primer análisis que D mide | 1177 (2026-05-18 06:40) | 2079 (2026-08-05 06:38) |
| F2b.3 Días hábiles seguidos sin velas | 0 | 0 |
| F2d Nativo contra remuestreado: fronteras | 100% | 100% |
| F2d Nativo contra remuestreado: OHLC idéntico | 1743 de 1743 | 1946 de 1946 |
| Control 2H → 4H contra el 4H del banco | 1164 de 1164 | 1373 de 1375 |

- Las 2 velas distintas del control de BTC son de borde: la primera del export (su tramo de 4H empieza antes que el
  export) y la del día del cambio de horario (el exportador descarta la vela 2H de esa hora).
- En invierno el 2H nativo abre en hora par de GT, y en verano en hora impar: es lo que corresponde a un servidor en
  UTC+2 y UTC+3 con la regla `us`.
- **Parada por cobertura:** el 2H nativo deja fuera 0 de los 83 análisis que D mide. Los 2X miden los 90 en alcance.

## 6. Verificación final (2026-09-30)

- **Suite completa:** 708 passed, 3 skipped, corrida en este worktree, que no tiene `.data/` (y no lo creó). El
  `.data/` del checkout principal quedó igual antes y después: ninguno de sus archivos cambió de tamaño ni de fecha, y
  el sha256 de las 4 DBs de cuenta y de `flight_sessions.json` es el mismo.
- **Tres cifras del HTML elegidas al azar** (`random.Random(20260930)`, sobre las 57 cifras de las tablas de §4 y §5.1),
  recalculadas desde `resultados.csv` solo con pandas:
  - §4, G en Choppy: 4/9 en el HTML y 4/9 en el CSV;
  - §4, A en Direccionales: 21/35 y 21/35;
  - §4, E en Direccionales: 19/26 y 19/26.

  Además coinciden las 57 cifras de ese universo.
- **El primer toque no depende de la temporalidad:** medido con velas de 15M o de 1H, da la misma dirección que con 1M
  en los 90 análisis.
- **Overlap:** 21 análisis con la etiqueta (13 de XAU y 8 de BTC), los mismos 21 que encontró
  `specs/002-auto-resolucion-velas/analisis/overlap_2d.py`.
- **El dictamen aplica R7 tal como se fijó:** lo calculan `label_vs_d`, `label_vs_disc` y `label_single_comparison`,
  con tests.
- **Paradas del pre-registro §12:** ninguna se activó. El reloj valida; el 2H deja fuera 0% de los análisis que D mide;
  F2d da 100%; las dos versiones del pre-registro se commitearon antes de F3. La única fuga de información encontrada
  es la del walk-forward de 2F (§4.13): es parte del procedimiento fijado, está medida y no cambia ningún resultado.

## 7. Resultado en una línea

Ningún 2X supera a D: donde los dos opinan, nunca apuntan para lados distintos. La acción que sale de la regla fijada
es mantener D como feedback, por un margen mínimo (el p de 2F es 0.047). El detalle está en `jupyter/p2_banco_v2.html`.
