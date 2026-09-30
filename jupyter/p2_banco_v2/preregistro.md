# Pre-registro: P2 banco v2 (temporalidades cortas), v1.1

- **Fecha:** 2026-09-29 (v1.0) y 2026-09-30 (v1.1).
- **Base:** commit `3c557a6`, rama `claude/p2-banco-v2-eval-f63a69` (worktree `.claude/worktrees/p2-banco-v2-eval-f63a69`).
- **Origen:** prompt v1.2 del 2026-09-29, "P2 banco v2 (temporalidades cortas)".
- **Cuándo se fijó:** este archivo se commitea **solo, antes de calcular cualquier predicción, acierto o prueba del
  banco v2** (F0). Si hay que corregir algo, se commitea una versión nueva **antes de F3** y el reporte cita el
  sha256 de las dos.
- **Versión 1.1 (2026-09-30), commiteada también antes de F3.** Un solo cambio de regla, decidido por el usuario:
  al leer el banco se quita la copia repetida de las velas de invierno en 4H, 12H, 1D y 1W (§2).
  - La v1.0 es el commit `e99e69d`, sha256 `c569e760a77b4cd821afde9518aea7a2ee0678d3a2955101e70c679611719e44`.
  - Entre la v1.0 y la v1.1 se calculó, **sin ningún ground truth ni acierto**: el reloj, los filtros, la cobertura
    por TF, las condiciones de F2b del 2H nativo, la comparación F2d y cuántas señales y predicciones cambian con las
    velas repetidas (`00_protocolo.md` §5). Ninguna cifra de acierto existía al fijar esta versión.
  - Precisión agregada, que no cambia ningún resultado con los datos actuales: en el remuestreo, un tramo que empieza
    en la hora del cambio de horario se descarta (§6).
- **Qué ya se sabía:** los resultados del banco v1 (`jupyter/p2_edge_evaluation.ipynb`, 2026-09-23), sobre los mismos
  análisis. Por eso **todo resultado retrospectivo del banco v2 es exploratorio** (R9). La evidencia independiente son
  los análisis con `created_at` posterior al commit de este archivo (§13).

## 1. Preguntas

| | Pregunta |
|---|---|
| Q1 | ¿Algún modelo 2X supera a D? |
| Q2 | ¿Algún 2X iguala o supera a P2-DISC (tu P2 manual)? |
| Q3 | ¿Cambian Q1 y Q2 entre los análisis Direccionales y los Choppy? |
| Q4 | ¿El cambio de temporalidades, solo, mejora o empeora? (2A contra D-EQ) |
| Q5 | ¿El mejor 2X sobrevive a la corrección por haber probado la familia completa de 13 variantes? |

Decisión que informa: si algún 2X debe reemplazar a D como feedback impreso después de "Confirm & Save".

## 2. Datos

- **Cuentas:** XAUUSD (`.data/flight_account_001_xauusd.db`) y BTCUSD (`.data/flight_account_002_btcusdtp.db`) del
  checkout principal, en solo lectura (`open_readonly_session`, `mode=ro`). Nunca `init_db()`.
- **Fuera:** US100 y US500. Su reloj no está verificado en el banco (`status.json`: `clock_unverified`).
- **Velas:** `.data/candle_bank/{XAUUSD,BTCUSD}/{TF}.csv`, en solo lectura. Se cargan una vez, y todos los predictores
  usan ese mismo snapshot. El reporte registra, para cada archivo, filas, primera y última vela y sha256.
- **Velas repetidas (v1.1):** el banco tiene, en 4H, 12H, 1D y 1W, cada vela de invierno dos veces: OHLC idéntico y
  etiquetas separadas por 1 h (una del import de los CSV viejos, con offset único +3; otra del export nuevo, con +2 en
  invierno). Al leer, sin tocar el banco, se quita una copia de cada par:
  - par = dos velas consecutivas a exactamente 1 h, con los cuatro precios iguales;
  - queda la que abre en la frontera del servidor con `BROKER_DST_RULE` (1W: domingo 00:00; el resto: múltiplo de
    su duración);
  - si las dos o ninguna están en la frontera, queda la más nueva y se cuenta como "sin resolver" (hoy son 0);
  - 1H y las TF más finas no se tocan: no tienen el problema.

  Consecuencia conocida: BTC tiene 809 semanas reales, no 1081. Sus 7 primeros análisis no llegan a 800 velas 1W
  cerradas, y ahí los modelos que usan 1W (A a G y D-EQ) no opinan. Los 2X sí, porque no usan 1W.
- **2H:** fuente según §6. Nunca se integra al banco.
- **Reloj:**
  - `calibrate_clock_offset` sobre el banco de cada cuenta. Si `aligned` no es `True` en alguna, **se aborta** (§12).
  - Además, `evaluate_clock_entries` en 1M y 5M, con desplazamientos de −6 a +6 h y la misma regla de margen
    (`CLOCK_MISALIGNMENT_MARGIN`). Una TF que no valida no se usa para el ground truth (§4).

## 3. Unidad y filtros

La unidad es un análisis (`unified_department`). Los filtros van en este orden, con conteo en cada paso:

1. Todas las filas de la cuenta.
2. Fuera: sin `edge_validation_price` (EVP) o sin `structural_invalidation` (SI).
3. Fuera: retroactivos (`is_backdated = 1`, R11 de `docs/criterios-de-acierto.md`).
4. **Ancla:** `created_at − 20 min`. `analysis_start_time` no existe en las DBs. El banco v1 usó `created_at − 15 min`.
5. **Precio de partida:** cierre de la última vela cerrada en el ancla (`time + duración(TF) <= ancla`), en la TF más
   fina validada que tenga una: 1M, 5M, 15M, 30M, 1H. Si no hay ninguna: fuera (`no_history`).
6. **Dirección de la tesis:** `infer_thesis_direction(precio de partida, EVP, SI)`. Si da `None` (los dos niveles del
   mismo lado): fuera (`levels_same_side`).

Lo que queda son los análisis **en alcance**.

**Segmentos (R2):** según el bias original del edge, `determine_market_bias(calc_edge)`:
- **Direccionales:** `Bullish` o `Bearish`.
- **Choppy:** `Choppy / Neutral`.
- **Todos:** los dos juntos.

Nunca se segmenta por el resultado.

## 4. Ground truth

**S1 (principal, `docs/criterios-de-acierto.md`):**
- **Camino:** velas que **abren en el ancla o después**. En cada instante se usa la TF más fina validada que cubre ese
  instante (1M, 5M, 15M, 30M, 1H). "Cubre" es estar entre la primera vela del archivo y el cierre de la última. El
  cambio de TF se hace solo en el borde de la vela más gruesa, sin huecos ni superposición.
- **Toque:** `high >= nivel de arriba` o `low <= nivel de abajo`. Las mechas cuentan. La hora del toque es la apertura de
  la vela que tocó.
- **Estados:**
  - `resolved`: tocó un solo nivel primero.
  - `ambiguous`: una misma vela tocó los dos niveles, en la TF más fina disponible en ese tramo.
  - `open`: el horizonte terminó sin toque.
  - `pending`: el banco se termina antes del horizonte, sin toque.
  - Solo `resolved` cuenta.
- **Horizonte:** 2160 velas de 1H desde el ancla (`MAX_HORIZON`).
- **Verdad para P2:** +1 si el primer nivel tocado es el de arriba, −1 si es el de abajo. P2 positivo predice que sube.

**S4 (secundario):** igual que S1, pero cuenta solo si la hora del toque es como máximo 48 h después del ancla. Siempre
se informa cuántos quedan fuera. S4 es descriptivo: **ninguna regla de decisión lo usa**.

**Overlap:** es solo una etiqueta, que nunca excluye. Un análisis A es Overlap si otro análisis B de la misma cuenta
tiene su ancla después de la de A y antes del toque de A (o antes del fin del camino disponible, si A no tocó nada). B
puede ser retroactivo, y entonces su ancla es su `created_at`.

## 5. Predictores

**Regla de señal, igual para todos los modelos:** `compute_score_p2_sistematico` y `rescale_p2_sistematico` de
`tools/p2_backtest.py`, sin cambios.
- Por TF: dirección por cadena de EMAs, filtro DI si el modelo lo pide y fuerza por ADX14.
- `MIN_BARS_PER_TF = 800` velas cerradas por TF.
- **Predicción:** signo del valor reescalado, +1, −1 o 0.
- Si falta cobertura en alguna TF del modelo, el score es `None` y la predicción es 0 ("no opina"). Se marca aparte
  como "sin cobertura".

**Snapshot por TF:** el de `CsvOHLCProvider.get_indicator_snapshot`, con velas cerradas en el ancla y EMA/ADX sobre
toda la historia del banco anterior al ancla. Para 2H es la misma lógica, con duración de 120 min, implementada en
`tools/p2_bank_v2.py` porque `TIMEFRAME_MINUTES` de `tools/p2_backtest.py` no tiene 2H y ese archivo no se toca.

**Modelos 2X.** Orden de TF: [1D, 2H, 1H, 30M, 5M]. Reglas de `MODEL_D`: `ema_chain = (20, 100, 200)`, `use_di = True`.

| Modelo | 1D | 2H | 1H | 30M | 5M |
|---|---|---|---|---|---|
| 2A | 0.20 | 0.20 | 0.20 | 0.20 | 0.20 |
| 2B | 0.10 | 0.25 | 0.25 | 0.20 | 0.20 |
| 2C | 0.20 | 0.20 | 0.20 | 0.30 | 0.10 |
| 2D | 0.15 | 0.30 | 0.25 | 0.20 | 0.10 |
| 2E | 0.10 | 0.25 | 0.30 | 0.25 | 0.10 |
| 2F | procedimiento de §10; ninguna combinación fijada a mano |  |  |  |  |

**Controles:**

| Control | Definición |
|---|---|
| D | `MODEL_D`: 1W .08, 1D .12, 12H .17, 4H .22, 1H .26, 30M .15; EMA 20/100/200 + DI |
| A | `MODEL_A`: 1W .30, 1D .25, 12H .20, 4H .15, 1H .10; EMA 20/200, sin DI |
| G | D14: reglas de `MODEL_E` (EMA 20/100/200 + DI); 1W, 1D, 12H y 4H .10; 1H, 30M y 15M .20 |
| D-EQ | TF de D (1W, 1D, 12H, 4H, 1H, 30M), 1/6 cada una; reglas de D |
| P2-DISC | signo de `analysis_layer.score` de la capa `P2`; 0 o nulo = no opina |

**Familia de comparación (R5-8.1), 13 variantes:** {A, B, C, D, E, F, G, D-EQ, 2A, 2B, 2C, 2D, 2E}. B, C, E y F son
`MODEL_B`, `MODEL_C`, `MODEL_E` y `MODEL_F` del registro, sin cambios. **2F queda fuera de la familia**, porque su
resultado ya es fuera de muestra (§10).

**Pesos duplicados (R3):** si dos modelos tienen los mismos TF, reglas y pesos, se tratan como un solo modelo y se
declara. Hoy ninguno se repite. Si los pesos finales de 2F coinciden con los de un 2A–2E, se declara.

**Mejor 2X:** entre 2A y 2E, el de mayor neto en Todos (S1). Empate: más aciertos; si sigue el empate, el primero en
orden alfabético. 2F no compite por "mejor 2X": se evalúa aparte.

## 6. Fuente del 2H (F2), por activo

**F2a.** Se agrega `"2H": mt5.TIMEFRAME_H2` (120 min) a `windows_export/export_p2_ohlc.py`, solo bajo pedido con
`--timeframes 2H`:
- el export por defecto sigue siendo el de las 9 TF de siempre;
- el banco no se entera.

Se pide al usuario el export en Windows y se espera.

**F2b.** Se usa el H2 nativo de un activo solo si cumple las tres condiciones:
1. **Reloj:** `evaluate_clock_entries` en 2H, con desplazamientos de −6 a +6 h, da `aligned` con la regla de
   `calibrate_clock_offset` (mejor desplazamiento 0, o tasa en 0 ≥ mejor tasa − 0.20).
2. **Historia:** hay ≥ 800 velas 2H cerradas antes del ancla del primer análisis en alcance de ese activo que D puede
   medir (D con cobertura completa).
3. **Huecos:** no falta más de 1 día hábil seguido en el período de los análisis.
   - Día hábil de referencia: una fecha (hora GT) con al menos una vela 1H en el banco.
   - Período: del primer al último ancla en alcance del activo.
   - Hay hueco si 2 o más días hábiles de referencia seguidos no tienen ninguna vela 2H.

**F2c.** "No se puede" es cualquiera de estos casos:
- el export falla;
- falla alguna condición de F2b;
- el usuario no responde o responde "no se puede".

En ese caso, se remuestrea desde el 1H del banco:
- **Hora del servidor:** la de cada vela, con `BROKER_DST_RULE` (hoy `us`) y offset base UTC+2, UTC+3 en verano. Ese
  offset se verifica con las etiquetas 1D y 4H del banco antes de usarlo.
- **Tramos:** de 2 h que empiezan en hora par del servidor.
- **OHLC:** apertura de la primera 1H, máximo y mínimo del tramo, cierre de la última 1H. Si el tramo tiene una sola
  1H, esa vela.
- **Vela cerrada:** solo si `time + 120 min <= ancla`.
- **Hora del cambio de horario (v1.1):** un tramo que empieza en la hora del cambio se descarta, igual que el
  exportador descarta la vela nativa de esa hora.

**F2d.** Si existen las dos series (nativa y remuestreada), se comparan donde se superponen:
- % de velas con OHLC idéntico (tolerancia 1e-9);
- % de horas de apertura presentes en las dos (fronteras alineadas).

Si alguna de las dos cifras queda por debajo del 99%, se reporta **antes de seguir** (§12).

**Control extra del remuestreo, con cualquier fuente:** agregar el 2H remuestreado a 4H tiene que reproducir el 4H del
banco (≥ 99% de velas idénticas donde se superponen).

**Tests obligatorios (`tests/test_p2_bank_v2.py`), con cualquier fuente:**
- (a) ninguna vela 2H usada contiene datos ≥ ancla;
- (b) si se remuestreó, el OHLC 2H es la agregación exacta de sus 1H;
- (c) semana de cambio de horario (DST): fronteras en hora par del servidor antes y después del cambio, sin mezclar
  horas de los dos lados.

**Cobertura:** tabla por activo × TF, con la primera fecha con ≥ 800 velas cerradas y cuántos análisis pierde cada
modelo, con el motivo (la TF que falta).

## 7. Métricas

- **Por predictor × segmento (R4):**
  - aciertos/opina y %, con IC95 de Wilson;
  - neto (`net_score`: aciertos − fallos, abstenciones 0);
  - % en que opina, sobre los análisis resueltos (S1) del segmento;
  - binomial exacto de dos colas contra 50%;
  - S4 al lado.
  - Un segmento con menos de 20 opiniones dice **"muestra insuficiente"** y no lleva conclusión.
- **Comparación pareada (R1):** entre dos predictores X e Y, solo donde S1 resolvió y **los dos opinan** (n de pares
  reportado).
  - b = acertó X y falló Y; c = acertó Y y falló X.
  - Neto pareado de X menos el de Y = 2(b − c).
  - McNemar exacto (`mcnemar_exact(b, c)`).
  - Diferencia de acierto d = (b − c) / n, con IC95 condicional: Wilson sobre b / (b + c), escalado por (b + c) / n.
    Si b + c = 0, el IC es [0, 0].
- **Diferencia mínima detectable (R8), antes de los resultados, sin usar la verdad:**
  - por predictor: `min_detectable_gap(n opiniones, 0.5)`;
  - por par: con n_disc = pares donde las dos predicciones difieren (cuando los dos opinan y difieren, exactamente uno
    acierta), la brecha detectable en b / (b + c) es `min_detectable_gap(n_disc, 0.5)`, y en diferencia de acierto
    2 · esa brecha · n_disc / n.
- **p ajustado por familia (8.1):** `permutation_max_net_test` con los 13 modelos de la familia sobre Todos (S1),
  `n_perm = 10 000`, semilla 20260929. El p del modelo j es (nulos con máximo ≥ neto_j + 1) / (n_perm + 1), el
  "single-step max-T". Para el mejor de la familia coincide con el p de la función.

## 8. Reglas de decisión (R7), fijas

**2X contra D** (2A–2E; 2F con §10), en Todos:
1. Menos de 20 pares: **no concluyente**.
2. b > c, McNemar p < 0.05 y p de 8.1 < 0.05: **supera**.
3. b < c y McNemar p < 0.05: **inferior**.
4. Cualquier otro caso: **no distinguible**. Incluye b = c: con empate se mantiene D.

**2X contra P2-DISC:**
1. Menos de 20 pares: **no concluyente**.
2. b > c, McNemar p < 0.05 y p de 8.1 < 0.05: **supera**.
3. b < c y McNemar p < 0.05: **inferior**.
4. El IC95 de d contiene 0: **equivalente** (es el "iguala" de Q2). Significa "la diferencia está dentro del ruido de
   esta muestra", no una prueba formal de equivalencia. El reporte lo aclara.
5. Cualquier otro caso: **no distinguible**.

**Q4, 2A contra D-EQ:** una sola comparación fijada de antemano, sin p de 8.1:
- menos de 20 pares: **no concluyente**;
- b > c y McNemar p < 0.05: **supera** (mejora);
- b < c y McNemar p < 0.05: **inferior** (empeora);
- cualquier otro caso: **no distinguible**.

**Q3:** las reglas de Q1 y Q2, dentro de Direccionales y dentro de Choppy, con el p de 8.1 recalculado dentro de cada
segmento.
- **Sí:** para algún 2X, la etiqueta cambia entre segmentos, con ≥ 20 pares en los dos.
- **No:** las etiquetas coinciden en todos los casos con ≥ 20 pares en los dos segmentos.
- **No concluyente:** ninguna comparación llega a 20 pares en los dos segmentos.

**Q5:** el p de 8.1 del mejor 2X < 0.05: **sobrevive**. Si no, **no sobrevive**.

Etiquetas permitidas: supera, equivalente, no distinguible, inferior, no concluyente.

## 9. Sobreajuste (R5): pruebas sobre el mejor 2X y criterios de la tabla ✅/⚠️/❌

| Prueba | Qué se hace | ✅ | ⚠️ | ❌ |
|---|---|---|---|---|
| Entrenamiento | ¿Se ajustó algo con los resultados? 2A–2E fijados acá; 2F solo fuera de muestra | nada ajustado dentro de muestra | — | algún parámetro ajustado |
| Look-ahead | Velas cerradas + tests (a)–(c) | tests en verde | — | look-ahead nuevo (se detiene todo, §12) |
| 8.1 | Familia de 13, p ajustado del mejor 2X | p < 0.05 | — | p ≥ 0.05 |
| 8.2 | Mitades temporales de Todos (S1) ordenado por ancla, h = n // 2 | neto > 0 en las dos | en una | en ninguna |
| 8.3 | Por activo (XAU, BTC) | neto > 0 en los dos | en uno | en ninguno |
| 8.4 | Vecinos en la grilla: todo punto a distancia L1 = 0.10 (una TF +0.05, otra −0.05, pesos ≥ 0), neto dentro de muestra en Todos | ≥ 50% de vecinos con neto ≥ neto del mejor − 2 | 25–50% | < 25% |
| 8.5 | "Siempre long" y "siempre short" en los análisis donde opina el mejor 2X | aciertos del mejor > el mayor de los dos | iguales | menos |
| 8.7a | 400 momentos al azar de XAU en la franja 5:00–8:59 GT: acierto contra 50% | > 50% con p < 0.05 | — | si no |
| 8.7b | Tus momentos (XAU) contra momentos al azar de la misma franja: dos proporciones, dos colas | diferencia > 0 con p < 0.05 | diferencia > 0, p ≥ 0.05 | diferencia ≤ 0 |
| 8.8 geometría | Acierto del mejor 2X cuando apunta al nivel más lejano | ≥ 50% | < 50% | — |
| 8.8 ancla | Ancla a 0, 5, 10, 15, 20, 30 y 60 min antes de `created_at`, todo recalculado | todas a ±5 pts del valor a 20 min | a ±10 | más de 10 |

**Detalle de 8.7 (réplica de §8.7 del v1):**
- **Momentos:** XAU solamente. Candidatas: velas 1H del banco que abren entre el primer y el último ancla en alcance de
  XAU. Momento = apertura + 30 min.
- **Grupos:** a cualquier hora, y con hora de apertura entre las 5 y las 8 (GT).
- **Sorteo:** `default_rng(20260929)` nuevo para cada grupo; min(400, candidatas) sin reposición.
- **Niveles:** EVP = p0 + banda y SI = p0 − banda.
  - p0 es el precio de partida con la regla de §3.5.
  - banda = mediana, en los análisis de XAU en alcance, de (|EVP − p0| + |SI − p0|) / 2. Solo usa niveles y precios,
    no resultados.
- **Verdad:** la de §4.
- **Predictores:** el mejor 2X y, como referencia, D.
- Se reporta también el grupo a cualquier hora.

**Detalle de 8.8 geometría:**
- esperado por pura distancia: media de |SI − p0| / (|EVP − p0| + |SI − p0|);
- real: fracción de tesis confirmadas;
- "siempre hacia el nivel más cercano";
- mejor 2X cuando apunta al nivel más cercano y cuando apunta al más lejano.

## 10. 2F (R6)

- **Datos:** Todos, S1 resuelto, ordenado por ancla.
- **Grilla:** `simplex_grid(step=0.05, k=5)` de `tools/edge_evaluation.py`, 10 626 combinaciones sobre
  [1D, 2H, 1H, 30M, 5M].
  - Cada combinación es un modelo con las reglas de D y **siempre las 5 TF**: una TF con peso 0 sigue exigiendo su
    cobertura, así toda la grilla tiene el mismo conjunto de análisis elegibles que 2A–2E.
  - Predicción de cada combinación: la regla de §5. La versión vectorizada debe coincidir exactamente con
    `compute_score_p2_sistematico` + `rescale_p2_sistematico` (test).
- **Selección:** `select_best(neto de entrenamiento, grilla, prior = 2A)`: máximo neto; los empates se resuelven hacia 2A.
- **Fuera de muestra, principal:** `walk_forward_folds(n, initial = max(20, n // 2), block = 10)`. Cada análisis de
  prueba se predice con los pesos elegidos en su ventana de entrenamiento. **El resultado de 2F es este, nunca el máximo
  dentro de muestra.**
- **Secundario, descriptivo:** leave-one-out.
- **2F en R7 y contra P2-DISC:** se compara en los análisis de prueba del walk-forward. Su "p de 8.1" es un test de
  permutación del procedimiento entero: se mezcla la verdad de todos los análisis, se repite la selección de cada fold
  y se compara el neto fuera de muestra, 10 000 veces, semilla 20260929.
- **Se reporta:**
  - pesos finales: `select_best` sobre todos los análisis, con prior 2A. Quedan **congelados** para la evaluación
    prospectiva (§13);
  - pesos elegidos en cada fold y rango por TF;
  - percentil dentro de muestra de 2A–2E entre las 10 626, como rango medio: 100 · (#menores + 0.5 · #iguales) / 10 626;
  - histograma de los 10 626 netos con 2A–2E marcados.

## 11. Acción recomendada (una sola), fijada de antemano

1. **Reemplazar D por un 2X**, si algún 2X (2A–2E o 2F) **supera** a D en Todos. Si hay varios, el de menor p de
   McNemar contra D; si empatan, el de mayor neto pareado.
2. **Cerrar la línea**, si ningún 2X supera a D **y** ningún predictor sistemático muestra acierto sobre el azar:
   - D en Todos, binomial p ≥ 0.05;
   - el mejor de la familia, 8.1 p ≥ 0.05;
   - 2F fuera de muestra, permutación p ≥ 0.05.
3. **Mantener D como feedback**, en cualquier otro caso.

"Cerrar la línea" significa no seguir buscando variantes de P2 sistemático con los datos actuales. El banco de velas
igual permite evaluar después los análisis nuevos con estas mismas reglas (§13), sin implementar nada.

## 12. Paradas

Se detiene todo y se reporta, sin continuar, si:
- falla el reloj de alguna cuenta (§2);
- el 2H de la fuente elegida deja fuera más del 25% de los análisis que D sí mide. Se cuenta sobre XAU + BTC juntos:
  análisis en alcance con cobertura completa de D y sin 800 velas 2H cerradas en el ancla, sin mirar S1;
- F2d o el control 2H → 4H dan menos del 99%;
- este archivo no estaba commiteado antes de F3;
- aparece un look-ahead nuevo.

**Pausa esperada, que no es un error:** F2a, hasta que el usuario corra el export de 2H en Windows o responda "no se
puede".

## 13. Evidencia independiente (R9)

- Son los análisis de XAU y BTC con `created_at` posterior al commit de la v1.1 de este archivo. Hoy son 0: el último
  es XAU 2026-09-16 y BTC 2026-09-01.
- Se evalúan con **exactamente** estas reglas: los mismos 2A–2E, los pesos de 2F congelados en §10, las mismas
  etiquetas y ningún modelo re-elegido.

## 14. Parámetros

| Parámetro | Valor |
|---|---|
| Semilla (permutaciones, momentos al azar) | 20260929 |
| `n_perm` | 10 000 |
| Mínimo de opiniones o pares para concluir | 20 |
| Umbral de F2d y del control 2H → 4H | 99% |
| Umbral de parada por cobertura 2H | 25% |
| Horizonte S1 | 2160 velas de 1H |
| Ventana S4 | 48 h |
| Ancla | `created_at − 20 min` |
| `BROKER_DST_RULE` | `us` (del `.env`) |
| Limpieza de velas repetidas (v1.1) | 4H, 12H, 1D y 1W, al leer |

**Qué no se hace:**
- no se toca ningún modelo existente, `tools/p2_backtest.py`, `tools/edge_evaluation.py`, `core/*`, `cli/*` ni
  `.data/candle_bank/`;
- no se escribe en ninguna DB;
- asociación no es causa (R10): "por qué funciona una TF" se presenta solo como hipótesis.
