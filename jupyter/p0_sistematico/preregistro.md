# Pre-registro: P0 sistemático (fractales + Fibonacci), modelos A–H, v1.2

- **Fecha:** 2026-10-06 (v1.0, v1.1 y v1.2).
- **Base:** commit `76c9a53` (el prompt, commiteado solo sobre `3856296` = `main`), rama `claude/p0-sistematico`,
  worktree `.claude/worktrees/p0-sistematico`, sin `.data/` propio.
- **Origen:** prompt v1.1 del 2026-10-06, `docs/prompts/2026-10-06-p0-sistematico-fractal-fib.md`, sha256
  `5cb8841c298f0256e7cc30d73e93a60c53a8865ba4b2a5fad7b512ee1f360970`. Sus decisiones D1–D21 son el contenido de este
  archivo. Donde el prompt deja algo abierto, este archivo lo fija y lo marca **(precisión)**: son reglas operativas
  que el usuario aprobó en la puerta de F0, no variantes.
- **Cuándo se fijó:** este archivo se commitea **solo, antes de calcular cualquier predicción, acierto o prueba** (F0).
  Si una discrepancia de F1 o un dato de F2 obliga a cambiar algo, se commitea una versión nueva, sola, **antes de
  F4**, y el reporte cita el sha256 de las dos.
- **Versión 1.1 (2026-10-06), commiteada sola antes de F4.** Dos cambios, por las discrepancias de F1
  (`00_protocolo.md` §3, puntos 1 y 6):
  - **Camino de los eventos (decidido por el usuario):** el banco tiene velas de 1M a 1H solo desde el 2026-03-08
    (XAU) y el 2026-04-19 (BTC). Donde la escalera no cubre, los eventos se detectan y resuelven con la TF más fina del
    banco que no sea más gruesa que la del tramo (§7). "Solo escalera" queda como sensibilidad (§10, T11).
  - **Lectura de etiquetas (precisión):** `12T-` y `4T-` se leen como `12HT-` y `4HT-`, sin distinguir mayúsculas (§9).
  - La v1.0 es el commit `3c12d0f`, sha256 `51724e5530f0ad2fca2558707e01e284ec439a0c721036c27677251129b66931`.
  - Entre la v1.0 y la v1.1 se calculó, **sin ninguna verdad ni acierto**: la re-verificación de los hechos `[COPIA]`,
    la cobertura del banco por TF y un conteo grueso de tramos por TF (`00_protocolo.md` §2 y §3). Ninguna cifra de
    acierto ni de reacción en niveles existía al fijar esta versión.
- **Versión 1.2 (2026-10-06), commiteada sola al empezar F4, antes de calcular cualquier predicción o acierto.** Un
  solo cambio, decidido por el usuario: si el ganador de la selección es A, que no tiene gemelo placebo, "ganador ≤ su
  gemelo" se da por cumplido (Fibonacci no mejoró al mejor modelo), y la familia F3 queda en {T6, T8×2} (§10, §13).
  - La v1.1 es el commit `4f23145`, sha256 `39e748b7aa0492aa4c303f484fb52f8a46db42cdaceb0579e789f192722dbefe`.
  - Entre la v1.1 y la v1.2 se escribió y testeó el código de F3 y se contaron entradas a zonas por brazo, sin mirar
    rechazos ni rupturas (`00_protocolo.md` §5.5). Ningún acierto, predicción de análisis ni prueba existía.
- **Qué ya se sabía antes de este archivo:**
  - los hechos `[COPIA]` del prompt: conteos de análisis por sesgo, coincidencia de la dirección P0 con el sesgo final
    y menciones de niveles en las tesis;
  - el acierto de P0 contra la **etiqueta** `market_bias` × `specific_bias_compliance` (`core/edge_analysis.py`, 52%
    con Choppy como pérdida, 71% sin ese efecto), que es otra verdad;
  - los resultados de P2 (banco v1 y banco v2) sobre los mismos análisis.

  No consta en el repo ningún acierto de P0 contra el precio (S1, S4, S4 estricto) ni ninguna medición de reacción en
  niveles de Fibonacci. Igual, **todo resultado retrospectivo de los modelos es exploratorio**: "≥ P0 discrecional" se
  decide con la evidencia independiente de §14 (D19).

## 1. Preguntas

| | Pregunta |
|---|---|
| Q1 | ¿Los niveles de P0 (0, 0.382, 0.5, 0.618, 1) producen más reacción del precio que niveles placebo? |
| Q2 | ¿La estructura sola (modelo A) predice la dirección mejor que el azar? |
| Q3 | ¿Fibonacci agrega algo sobre la estructura (B contra A)? |
| Q4 | ¿El mejor modelo de A–G iguala o supera al P0 del operador? |
| Q5 | ¿El mejor modelo sobrevive a la corrección por haber probado 7, y le gana a su gemelo placebo? |
| Q6 | ¿Cambian las respuestas entre Direccionales y Choppy, y entre XAU y BTC? |

Decisión que informa (§13, D18): continuar (el ganador se sigue midiendo con análisis nuevos), modificar, abandonar
Fibonacci, abandonar P0 sistemático o insuficiente.

## 2. Datos

- **Cuentas (D21):** XAUUSD es la principal (`.data/flight_account_001_xauusd.db`, cuenta `000` de `REAL_ACCOUNTS`) y
  BTCUSD valida con todo congelado (`.data/flight_account_002_btcusdtp.db`, cuenta `002`). Las dos del checkout
  principal, en solo lectura (`open_readonly_session`, `mode=ro`), con columnas explícitas. Nunca `init_db()`.
- **Fuera (D21):** US100 (reloj de `USTEC` sin verificar) y US500 (reloj o historia insuficientes, según el prompt).
- **Foto del banco (R5) (precisión):** en F2 se copian una sola vez `XAUUSD/` y `BTCUSD/` de `.data/candle_bank/`
  (los CSV de 1M, 5M, 15M, 30M, 1H, 4H, 12H, 1D y 1W, y `status.json`) a `.tmp/p0_foto_<fecha>/` dentro del worktree
  (`.tmp/` está en `.gitignore`; nada se escribe en `.data/`). Motivo: el export automático agrega velas al banco cada
  vez que se abre el CLI, así que leer el banco vivo cambiaría el sha256 entre corridas. `00_protocolo.md` §5 registra,
  por archivo: ruta, filas, primera y última vela y sha256. Todo cálculo lee la foto, y el cargador verifica los sha256
  al leer: si uno no coincide, se detiene (E3).
- **Velas cerradas:** en un instante τ solo existen las velas con `time + duración <= τ` (`closed_bars()`, INV-3).
- **Reloj (T0, parada):** `status.json` de cada símbolo con `clock: verified`, y `calibrate_clock_offset` con
  `aligned = True` en las dos cuentas. Si no, se detiene todo.
- **Regla horaria:** `BROKER_DST_RULE=us`, asumida y no probada. No bloquea: T0 agrega una sensibilidad (§9).

## 3. Unidad, ancla, filtros y segmentos (análisis)

La unidad de las pruebas de modelos y del operador (T1, T6–T8, T10) es un análisis (`unified_department`). Se
resuelve con `AccountResolver` de `tools/auto_resolution.py` (que usa `resolve_analysis`, `TOUCH_EXCEPTIONS` y
`RECOVERED_BACKDATED`), apuntado a la foto. Filtros, en orden y con conteo en cada paso:

1. Todas las filas de la cuenta.
2. **Retroactivos (R6):** fuera, salvo los 7 de XAU de `RECOVERED_BACKDATED` (N50), que entran con su hora tipeada
   como ancla. Se informa cuántos quedaron fuera.
3. **Ancla (R6, D14):** `analysis_anchor()` de `core/outcome_metrics.py`: `analysis_start_time` si existe; si no,
   `created_at − 20 min` (`ANCHOR_FALLBACK_MIN`).
4. **Evidencia independiente:** un análisis con ancla posterior al commit de este archivo no entra al retrospectivo;
   es evidencia de §14.
5. **Precio de partida (R6):** `start_price()` de `core/candle_resolution.py`: cierre de la última vela cerrada en el
   ancla, en la TF más fina de la escalera (1M, 5M, 15M, 30M, 1H). Sin vela: fuera (`no_history`).
6. **Niveles (E2):** sin `edge_validation_price` o sin `structural_invalidation`, o con los dos del mismo lado del
   precio de partida: fuera (`no_levels`).

Todo excluido aparece en una tabla con su motivo (`ExclusionRecord`, INV-7).

**Segmentos (R11, D13)**, según `determine_market_bias(calc_edge)` (umbral ±0.26), nunca según el resultado:
- **Todos:** cualquier sesgo;
- **Direccionales:** `Bullish` o `Bearish`;
- **Choppy:** `Choppy / Neutral`.

Se reportan siempre los tres, lado a lado.

## 4. Verdades (D12, R12)

- **Regla geométrica (D12):** un P0 Long (operador o modelo) acierta si el precio toca primero el más alto de los dos
  niveles del análisis; un P0 Short, si toca primero el más bajo; Neutral es abstención. No importa cuál nivel se llamó
  objetivo. El camino, los toques, `TOUCH_EXCEPTIONS`, los estados (`confirmed`, `invalidated`, `ambiguous`, `open`,
  `pending_candles`) y el horizonte (2160 velas de 1H) son los de `core/candle_resolution.py`.
- **Conversión (precisión):** con la dirección de la tesis de `resolve_analysis` (`long` si el objetivo está arriba),
  "tocó primero el de arriba" = (`long` y `confirmed`) o (`short` y `invalidated`). Para cada predictor se arma un
  `AnalysisOutcome` con `confirmed` si su dirección coincide con el primer toque e `invalidated` si no, y se mide con
  `s4_strict`, `s1` y `s4` de `core/outcome_metrics.py`, sin reimplementarlas.
- **Comparaciones pareadas (operador, B contra A, ganador contra gemelo):** **S4 estricto** principal (N51), S1 y S4 al
  lado. Para el neto y las permutaciones, la verdad S4 estricto de un análisis es +1 (tocó primero el de arriba dentro
  de 48 h), −1 (el de abajo dentro de 48 h) o 0 (tardío: toda opinión pierde). Con esa codificación, `net_score` y
  `permutation_max_net_test` cuentan un tardío como fallo de quien opine.
- **Habilidad contra el azar (T1, T6):** **S1** contra el nulo geométrico (§5), porque ese nulo no existe con la
  ventana de 48 h. Al lado, S4 estricto contra "siempre long" y "siempre short" en los mismos análisis.
- El cuaderno lo dice en §2.
- **Overlap:** solo etiqueta (N36). Nunca excluye.

## 5. Nulo geométrico (D15)

Para cada análisis resuelto en S1 en el que el predictor opina, con d(·) = distancia del precio de partida al nivel:
- P0 Long: `p_i = d(inferior) / (d(superior) + d(inferior))`;
- P0 Short: `p_i = d(superior) / (d(superior) + d(inferior))`.

Aciertos observados contra la Poisson-binomial de los p_i, calculada exacta por convolución (numpy). p unilateral =
P(X ≥ observados). Es la versión por análisis de "8.8 geometría" del banco v2.

## 6. Motor de estructura (D3, D4, D5)

Por TF k ∈ {1W, 1D, 12H, 4H, 1H}, con las velas de la foto.

- **Fractal (D3), n = 2:** superior en t si `H_t ≥ max(H_{t−1..t−n})` y `H_t > max(H_{t+1..t+n})`; inferior,
  simétrico con mínimos (`L_t ≤ min(L_{t−1..t−n})` y `L_t < min(L_{t+1..t+n})`). Vale desde el **cierre** de la vela
  t+n (`time_{t+n} + duración`), nunca antes (R8, INV-4). **(precisión)** Si una vela es fractal superior e inferior
  a la vez, se registran los dos, y en la secuencia alterna entra primero el del tipo opuesto al último pivote.
- **Secuencia alterna (D4):** dos techos seguidos conservan el más alto; dos pisos, el más bajo. **(precisión)** Con
  empate exacto se conserva el anterior. El reemplazo ocurre en el instante de confirmación del nuevo.
- **Etiquetas (D4):** cada techo contra el techo anterior de la secuencia (HH si es mayor, LH si es menor); cada piso
  contra el piso anterior (HL si es mayor, LL si es menor). **(precisión)** Un empate exacto hereda la etiqueta del
  pivote anterior del mismo tipo; si ese no tiene etiqueta, queda sin etiqueta.
- **Estado en τ:** con el último techo y el último piso confirmados: HH + HL = alcista; LH + LL = bajista; HH + LL o
  LH + HL = rango. Menos de dos techos y dos pisos confirmados, o un último pivote sin etiqueta = historia insuficiente
  (E1, motivo `insufficient_history`).
- **Invalidación (D4):** alcista, cuando una vela de la TF k **cierra** debajo del último HL; bajista, cuando cierra
  encima del último LH. Desde el cierre de esa vela el estado es rango. **(precisión)** Vuelve a evaluarse con la regla
  normal cuando, después de la invalidación, se confirmaron un techo nuevo y un piso nuevo. La invalidación por mecha
  es sensibilidad (T11).
- **Tramo A→B (D5):**

  | Estado | B | A |
  |---|---|---|
  | Alcista | último techo | último piso antes de B |
  | Bajista | último piso | último techo antes de B |
  | Rango | último pivote (cualquiera) | pivote opuesto anterior |

  **(precisión)** La identidad de un tramo es (TF, pivote A, pivote B): el mismo par sigue siendo el mismo tramo aunque
  cambie el estado. El tramo de τ es función de los pivotes confirmados y del estado en τ; el precio solo lo cambia a
  través de una invalidación.
- **Orientación:** el tramo sube si B > A (B es un techo) y baja si B < A.
- **Extendido (D5) (precisión):** desde la primera vela del camino (§7) cuyo máximo supera B (tramo que sube) o cuyo
  mínimo perfora B (tramo que baja). Desde ahí el tramo no genera eventos nuevos; los ya empezados siguen hasta su
  resultado.

## 7. Niveles, zonas, eventos y resultados (D1, D2, D6, D7)

- **Niveles (D1):** `L_r = B − r · (B − A)`, con r ∈ {0, 0.382, 0.5, 0.618, 1}. 0 = B (fin del impulso), 1 = A.
- **Placebo (D2):** r ∈ {0.300, 0.441, 0.559} (retrocesos) y {0.10, 0.90} (extremos).
- **Zona (D6):** `[L_r − δ·|B−A|, L_r + δ·|B−A|]`, δ = 0.025, igual para P0 y placebo. Ninguna zona se solapa.
- **Camino (v1.1):** desde el instante de confirmación de B, vela por vela, sin huecos ni superposición:
  - donde la escalera cubre el instante, `forward_path()` de `core/candle_resolution.py` (1M → 5M → 15M → 30M → 1H, la
    más fina que cubre cada instante);
  - donde ninguna TF de la escalera lo cubre, la siguiente vela de la TF más fina del banco entre 4H, 12H, 1D y 1W que
    cubre ese instante **y no es más gruesa que la del tramo** (un tramo de 4H nunca usa velas de 12H). Esa vela se
    usa entera, y desde su cierre se vuelve a mirar si la escalera ya cubre;
  - "cubre" es lo mismo que en `forward_path`: estar entre la apertura de la primera vela del archivo y el cierre de la
    última. Si ninguna TF permitida cubre el instante, el camino termina (motivo `no_path`, contado);
  - cada evento guarda la TF de cada vela de su camino, de la entrada al resultado. Un evento es "grueso" si alguna de
    esas velas es de 4H o más.
  - Es el mismo camino para las pruebas de niveles (T3–T5) y para el puntaje de los modelos (§8), siempre cortado en τ
    para el puntaje.
- **Evento (D6):** la primera entrada a la zona (la mecha cuenta) en el camino, después de confirmarse B, mientras el
  tramo está vigente (su vela de entrada abre antes de que el tramo cambie y no después de la vela de extensión).
  Máximo un evento por (tramo, nivel). **(precisión)** Entrar es tocar la zona viniendo de afuera: el lado de llegada es
  el de la última posición entera fuera de la zona (el cierre de la vela t+n de B, o la última vela del camino que no
  tocó la zona). Si ese cierre ya está dentro de la zona, el nivel se arma recién cuando una vela del camino queda
  entera de un lado.
- **Resultado (D7):** barreras simétricas `D = 0.10·|B−A|` desde `L_r`.
  - Rechazo: el precio se aleja D en sentido contrario a su llegada.
  - Ruptura: avanza D en el sentido de su llegada.
  - **(precisión)** En la vela de entrada solo cuenta la barrera de ruptura (el extremo del lado de llegada puede ser
    anterior a la entrada); desde la vela siguiente cuentan las dos.
  - Ambiguo: una vela del camino toca las dos barreras (el camino ya va en la TF más fina disponible) → excluido y
    contado.
  - Abierto: ninguna barrera en `MAX_HORIZON` desde la entrada → excluido y contado. **(v1.1)** Como el camino puede
    tener velas de varias TF, el horizonte se mide en horas de velas recorridas: termina cuando la suma de las
    duraciones de las velas del camino, desde la entrada, llega a 2160 h (en 1H son exactamente 2160 velas). Sin velas
    suficientes todavía: `pending`, excluido y contado.
  - Una ruptura en 1 que coincide con la invalidación del tramo cuenta igual: el resultado se sigue hasta la barrera
    aunque el tramo cambie.
- **Huecos (E4):** un hueco de velas no explicado por el fin de semana ni por la pausa diaria del oro se reporta en §1
  del cuaderno con fecha y TF, y los eventos que lo cruzan se marcan.

## 8. Puntaje, modelos y gemelos (D8, D9, D10)

- **Puntaje por TF en τ (D8)**, con dir = +1 alcista, −1 bajista:

  | Situación en τ | A | B–H y gemelos |
  |---|---|---|
  | Tendencia, último evento en un retroceso o en 1 = Rechazo | dir | 2·dir |
  | Tendencia, último evento en un retroceso o en 1 = Ruptura | dir | 0 |
  | Tendencia, último evento en 0 = Rechazo | dir | 0 |
  | Tendencia, último evento en 0 = Ruptura | dir | 2·dir |
  | Tendencia, sin evento todavía | dir | dir |
  | Rango o historia insuficiente | 0 | 0 |

  **(precisión)** "Último evento" = el de entrada más reciente del tramo vigente en τ (entrada cerrada en τ), entre los
  niveles del modelo. Si su resultado no se conoce en τ (la vela de la barrera no cerró), o fue ambiguo, la TF cuenta
  como "sin evento todavía". En los gemelos, "retroceso" es 0.300/0.441/0.559, "1" es 0.90 y "0" es 0.10.
- **P0 del modelo:** `S = Σ w_k · s_k`; `x0 = clamp(round_half_away_from_zero(2·S / S_max), −2, 2)`, con `S_max = 1`
  en A y 2 en B–H y gemelos; el redondeo es el de `rescale_p2_sistematico`. Signo de x0 = dirección (Neutral si 0);
  |x0| = fuerza (1 Mid, 2 Strong). Si todas las TF valen 0, el modelo no opina.
- **Registro (D9)**, nombres `P0_MODEL_A..H`:

  | Modelo | Regla | Pesos |
  |---|---|---|
  | A | Solo estructura (control) | 1D 0.25 · 12H 0.25 · 4H 0.25 · 1H 0.25 |
  | B | Estructura + Fib | 1D 0.25 · 12H 0.25 · 4H 0.25 · 1H 0.25 |
  | C | Estructura + Fib | 1D 0.40 · 12H 0.30 · 4H 0.20 · 1H 0.10 |
  | D | Estructura + Fib | 1D 0.20 · 12H 0.30 · 4H 0.30 · 1H 0.20 |
  | E | Estructura + Fib | 1D 0.10 · 12H 0.20 · 4H 0.30 · 1H 0.40 |
  | F | Estructura + Fib, jerárquico | 1D 0.30 · 12H 0.30 · 4H 0.20 · 1H 0.20 |
  | G | Estructura + Fib, pesos del operador | 1D 0.20 · 12H 0.20 · 4H 0.30 · 1H 0.30 |
  | H | Estructura + Fib, con 1W (experimental) | 1W 0.20 · 1D 0.20 · 12H 0.20 · 4H 0.20 · 1H 0.20 |

  - F: si `s_1D + s_12H = 0`, no opina (x0 = 0); si no, `s_4H` y `s_1H` cuentan solo si tienen el mismo signo que
    `s_1D + s_12H`, y valen 0 en otro caso.
  - H: fuera de la selección (§11) y de la decisión (§13); se reporta al lado.
- **Gemelos (D10):** `P0_MODEL_Bp..Hp`, idénticos a su modelo con los niveles placebo.
- Cada receta lleva un test que la fija (INV-5).

## 9. P0 del operador y etiquetas

- **P0 del operador:** la capa `analysis_layer` con `layer_name = 'P0'` (`department = 'EFFICIENCY'`): `direction`
  (`Long`, `Short`, `Neutral`) y `strength`. Long/Short opinan; Neutral o vacío es abstención. La fuerza no entra en el
  acierto.
- **Etiquetas de tesis (D20, T7):** en `thesis`, `T-BL` = tramo alcista y `T-BR` = bajista, por TF (`1DT-B…`,
  `12HT-B…`, `4HT-B…`, `1HT-B…`); `12HT-BR == 1DT` = el tramo de 12H es el mismo que el de 1D. Una tesis sin ninguna
  etiqueta queda fuera de T7. **(v1.1)** Al leer: sin distinguir mayúsculas (`Bl` = `BL`), `12T-` = `12HT-` y
  `4T-` = `4HT-`, con espacios o `:` opcionales; si una TF aparece etiquetada dos veces, vale la primera.
- **"Fractal" (R14):** solo el fractal de precio de n velas de §6. Ningún campo de `tactical_audit` se usa ni se cruza.

## 10. Pruebas (D16) y partición (D17)

**Partición (D17, R10):** las pruebas de niveles (T3–T5, y su réplica en BTC de T10) usan los eventos de **toda la
historia de la foto**, por activo y por TF del tramo, ordenados por hora de entrada: el 60% más antiguo es
exploratorio y el 40% más reciente es confirmatorio. **(precisión)** El corte es la hora de entrada del evento número
`floor(0.60·n)` (base 0): los de antes son exploratorios, los de esa hora o después, confirmatorios. El código no
calcula el confirmatorio sin el flag explícito `allow_confirmatory=True` (INV-6), que se activa una sola vez en §11,
después de la aprobación escrita del usuario. Las pruebas de modelos (T1, T6–T8) usan todos los análisis del
retrospectivo y son exploratorias por naturaleza (§14 decide).

**Deduplicación (D5) (precisión):** un mismo tramo detectado en varias TF cuenta una vez en las pruebas agregadas. Dos
tramos de TF distintas son el mismo si tienen la misma orientación, el mismo precio de A y de B, y las velas de A y de
B de la TF más fina caen dentro de las velas de A y de B de la más gruesa. Queda el de la TF más gruesa.

**Regresión (T3–T5, R16, RF-8):** logística por IRLS en numpy. Cada evento es una fila con y = 1 si Rechazo y 0 si
Ruptura (ambiguos, abiertos y pendientes fuera). Covariables **(precisión)**: profundidad = r (continua); nivel P0
(1 = nivel de P0, 0 = placebo); TF del tramo (dummies, referencia 1D); estado de la TF en la entrada (tendencia =
alcista o bajista, referencia; rango). OR = exp(coeficiente de nivel P0). IC95 percentil y p de un **bootstrap por
tramo** (se remuestrean tramos con reemplazo, con todos sus eventos), 5000 remuestras, semilla
20261006. **(precisión)** p unilateral (H1: OR > 1) = (1 + #{β* ≤ 0}) / (5000 + 1). Las remuestras en las que IRLS no
converge se cuentan y se informan.

| ID | Hipótesis | Datos y modelo | Falla si |
|---|---|---|---|
| T0 | Foto sin duplicados ni velas corridas; reloj verificado; regla horaria registrada | Velas repetidas (mismo `time`, o pares a 1 h con OHLC idéntico en 4H, 12H, 1D, 1W); `status.json`; `calibrate_clock_offset`. Sensibilidad: todo recalculado sin eventos ni análisis cuyo intervalo (entrada→resultado; ancla→ancla+48 h o primer toque) toque 2026-03-08–2026-03-29 o 2026-10-25–2026-11-01 | Un duplicado o un reloj sin verificar → parada. Si la sensibilidad cambia el signo de una conclusión, queda "pendiente de la regla horaria" |
| T1 | El P0 del operador supera el nulo geométrico | S1 observado vs Poisson-binomial (§5); Todos, Direccionales y Choppy; XAU (BTC al lado) | p ≥ 0.05 → no se distingue de la geometría de sus niveles |
| T2 | Sin look-ahead | Para cada análisis, x0 de todos los modelos y estado de cada TF en τ, con la foto entera y con la foto cortada en τ | Una sola diferencia → parada |
| T3a | Rechazo en 0.382/0.5/0.618 > en 0.300/0.441/0.559 | Logística con los 6 niveles: y ~ r + nivel P0 + TF + estado; OR | IC95 inferior ≤ 1 o p Holm ≥ 0.05, en el confirmatorio |
| T3b | Rechazo en 0 y 1 > en 0.10 y 0.90 | Igual, con los 4 niveles | Igual que T3a |
| T4 | Cada nivel contra su placebo | **(precisión)** Pares en orden: 0.382↔0.300, 0.5↔0.441, 0.618↔0.559, 0↔0.10, 1↔0.90. Por par: y ~ nivel P0 + TF + estado (sin r, que sería colineal) | Ninguno sobrevive a Holm |
| T5 | Efecto en tendencia y en rango, por separado | **(precisión)** Los 10 niveles: y ~ r + extremo + nivel P0 + TF, ajustado dentro de tendencia y dentro de rango; la interacción (nivel P0 × rango), descriptiva | Los dos con IC95 inferior ≤ 1 |
| T6 | A acierta más que el nulo | XAU, Todos: S1 vs nulo geométrico de A; al lado, S4 estricto de A vs "siempre long" y "siempre short" en los mismos análisis, y cobertura (% que opina) | p Holm ≥ 0.05 |
| T7 | Los modelos anclan como el operador (descriptiva) | κ de Cohen por TF: orientación del tramo del modelo en τ vs etiqueta BL/BR de la tesis; `==` del operador vs "mismo tramo" del modelo, descriptivo | κ < 0.4 en 1D y 12H |
| T8 | Ganador ≥ operador; B > A; ganador > su gemelo | XAU, Todos, S4 estricto, pares donde los dos opinan y S4 estricto cuenta: McNemar exacto, d con IC95 (§12) | Ganador inferior al operador, o ganador ≤ su gemelo |
| T10 | Generalización a BTC, todo congelado | T3–T5 con eventos de BTC (misma partición); T1, T6–T8 con análisis de BTC y el ganador elegido en XAU | Efecto de signo opuesto al de XAU |
| T11 | Sensibilidad | n = 3 y 5; ancho por ATR14 (precisión: semiancho = c·ATR14 de la TF al confirmarse B, con c tal que la mediana del semiancho sea la de δ·\|B−A\| en esa TF y activo; las zonas pueden solaparse); invalidación por mecha; ancla a 0, 10, 20, 30 y 60 min antes de `created_at` (los que tienen `analysis_start_time` no cambian); **(v1.1)** solo escalera: sin eventos gruesos, y en el puntaje una TF sin camino de escalera desde su B vale 0. Sobre el OR exploratorio de T3a/T3b y el neto y acierto de A–G | El signo cambia → frágil; no se elige variante |

**Familias de Holm (α = 0.05):** F1 = {T3a, T3b, T4×5}; F2 = {T5×2}; F3 = {T6, T8×3}. **(v1.2)** Si el ganador
es A, no existe "ganador contra su gemelo" y F3 = {T6, T8×2}.

**Mínimos:** T3–T5: 480 eventos por brazo (P0 y placebo) en el confirmatorio; por debajo es "insuficiente", no "falla".
Pruebas de análisis: menos de 20 opiniones o pares en un segmento = "muestra insuficiente", sin conclusión (R17).

## 11. Selección (D11, R13)

- **Candidatos:** A–G. H y los gemelos no participan.
- **Neto:** +1 acierto, 0 abstención, −1 fallo, con la verdad S4 estricto de §4, en XAU, Todos.
- **Ganador:** el de mayor neto. **(precisión)** Empate: más aciertos; si sigue, el primero en orden alfabético.
- **Castigo:** `permutation_max_net_test` con los 7 modelos, 10 000 mezclas, semilla 20261006. El p ajustado del
  modelo j es (#{mezclas cuyo máximo nulo ≥ neto_j} + 1) / (10 000 + 1), el "single-step max-T" de §7 del banco v2.
  Para el ganador coincide con el p de la función.

## 12. Comparaciones pareadas y etiquetas de T8

- Entre X e Y: solo análisis donde S4 estricto cuenta y **los dos opinan**; n de pares reportado.
- b = acertó X y falló Y; c = acertó Y y falló X. Neto pareado = 2(b − c). McNemar exacto (`mcnemar_exact(b, c)`).
- d = (b − c) / n, IC95 condicional: Wilson sobre b / (b + c), escalado por (b + c) / n; si b + c = 0, [0, 0].
- **Etiquetas (como §8 del banco v2)**, con el p de McNemar ajustado por Holm en F3 **(precisión)**:
  - menos de 20 pares: **no concluyente**;
  - b > c, McNemar p < 0.05 y, si X es el ganador, p de R13 < 0.05: **supera**;
  - b < c y McNemar p < 0.05: **inferior**;
  - el IC95 de d contiene 0: **equivalente** (dentro del ruido de esta muestra, no una prueba formal);
  - cualquier otro caso: **no distinguible**.
- **Diferencia mínima detectable (F2, sin verdad):** por predictor, `min_detectable_gap(n opiniones, 0.5)`; por par,
  con n_disc = pares en que las dos predicciones difieren, `min_detectable_gap(n_disc, 0.5)` en b / (b + c), y
  2 · esa brecha · n_disc / n en d.

## 13. Decisión (D18), una sola

Condiciones, todas en el **confirmatorio** para T3–T5 y en XAU salvo que se diga otra cosa:
- **Fib positivo:** T3a o T3b pasa: ≥ 480 eventos por brazo, OR > 1, IC95 inferior > 1 y p Holm (F1) < 0.05.
- **Fib nulo:** T3a y T3b tienen ≥ 480 eventos por brazo y ninguno pasa.
- **BTC mismo signo:** para cada prueba que pasó en XAU, el OR de BTC (confirmatorio, punto) es > 1.
- **Gemelo (precisión):** "ganador > su gemelo" = b > c con ≥ 20 pares (S4 estricto, Todos); "ganador ≤ su gemelo" =
  b ≤ c con ≥ 20 pares. Con menos de 20 pares no vale ninguna de las dos. **(v1.2)** Si el ganador es A (sin gemelo),
  "ganador ≤ su gemelo" se da por cumplido: ningún modelo con Fibonacci le ganó a la estructura sola.
- **T6 falla:** p Holm (F3) de T6 ≥ 0.05.
- **(precisión)** Un nivel de T4 o un segmento de T5 "pasa" con ≥ 480 eventos por brazo, OR > 1, IC95 inferior > 1 y p
  Holm (F1 para T4, F2 para T5) < 0.05.

**Orden (precisión): la primera fila que se cumple es la decisión.**

| # | Decisión | Condición | Acción |
|---|---|---|---|
| 0 | (parada) | T0 o T2 fallan | Se reporta y no hay decisión |
| 1 | Continuar | Fib positivo, BTC mismo signo y ganador > su gemelo | El ganador se sigue midiendo con análisis nuevos (§14) |
| 2 | Modificar | El efecto aparece solo en un segmento (T5: exactamente uno de los dos pasa) o solo en un nivel (T4: exactamente uno pasa) | Pre-registro nuevo y restringido, medido solo con análisis posteriores |
| 3 | Abandonar P0 sistemático | (Fib nulo o ganador ≤ su gemelo) y T6 falla | P0 sigue manual; no se buscan más variantes con estos análisis |
| 4 | Abandonar Fibonacci | Fib nulo o ganador ≤ su gemelo | Queda A solo (T6 pasa) |
| 5 | Insuficiente | Cualquier otro caso (incluye n < mínimo y señales que se contradicen, por ejemplo Fib positivo en XAU con BTC de signo opuesto) | Sin decisión; acumular. El cuaderno dice cuál condición faltó |

H y los gemelos no cuentan para ninguna condición salvo "gemelo del ganador".

## 14. Evidencia independiente (D19)

- Son los análisis de XAU y BTC con ancla posterior al commit de este archivo.
- Se evalúan re-ejecutando el cuaderno con **exactamente** estas reglas, los mismos modelos y el ganador ya elegido,
  sin re-elegir ninguno, con una foto nueva del banco (registrada igual que la primera).
- El modelo nunca se muestra al operador: su P0 sigue a ciegas sin cambiar el CLI.
- "≥ P0 discrecional" se decide con esta evidencia, no con el retrospectivo.

## 15. Parámetros

| Parámetro | Valor |
|---|---|
| Semilla (permutaciones, bootstrap) | 20261006 |
| `N_PERM` | 10 000 |
| `N_BOOT` | 5 000 |
| Fracción exploratoria por TF (T3–T5) | 0.60 |
| `N_MIN_BRAZO` (confirmatorio) | 480 eventos por brazo |
| `N_MIN_PARES` | 20 |
| `MAX_HORIZON` (eventos y S1) | 2160 velas de 1H |
| Ventana S4 / S4 estricto | 48 h (`S4_WINDOW_H`) |
| Ancla | `analysis_start_time`; si no, `created_at − 20 min`; retroactivos recuperados: hora tipeada |
| Fractal | n = 2 (n = 3 y 5, sensibilidad) |
| δ (semiancho de zona) | 0.025·\|B−A\| |
| Barrera D | 0.10·\|B−A\| |
| Camino de los eventos (v1.1) | escalera 1M→1H; donde no cubre, la TF más fina del banco que no sea más gruesa que la del tramo |
| `BROKER_DST_RULE` | `us` (asumida) |

## 16. Paradas

Se detiene todo y se reporta, sin continuar, si:
- falla el reloj de XAUUSD o BTCUSD (`status.json` no verificado, o `calibrate_clock_offset` con `aligned` distinto de
  `True`);
- este archivo no estaba commiteado antes de F4;
- cambió el sha256 de la foto (E3);
- aparece un look-ahead nuevo (T2);
- hace falta tocar algo de "No toques" del prompt o escribir en `.data/`;
- un resultado contradice un hecho de `<contexto>` del prompt;
- `CLAUDE.md` muestra diff;
- un test falla (E5).

**Qué no se hace:** no se toca `cli/`, `core/math_engine.py`, `determine_market_bias`, los gates,
`core/edge_analysis.py`, `tools/notion_sync.py`, los modelos de P2 ni `.data/p2_model_log.jsonl`; no se escribe en
ninguna DB ni en `.data/`; no se agrega ninguna dependencia; asociación no es causa.
