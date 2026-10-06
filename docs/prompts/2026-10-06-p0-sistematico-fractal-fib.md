# v1.1 — 2026-10-06 · Prompt de evaluación: P0 sistemático (fractales + Fibonacci), modelos A–H

> Cómo usarlo: copiá este archivo a `docs/prompts/` en el repo de WSL, commitealo solo (R2) y, en una sesión
> **nueva** de Claude Code abierta en la raíz de `blast_master`, escribí:
> `Leé y ejecutá @docs/prompts/2026-10-06-p0-sistematico-fractal-fib.md`
>
> Changelog:
> - v1.0 (2026-10-02): versión inicial, como spec 003 con `/sdd-engine`. No se ejecutó.
> - v1.1 (2026-10-06): tras el cierre de la spec 002.
>   - Pasa del formato spec al formato de evaluación de "P2 banco v2" (`jupyter/p2_banco_v2/`): pre-registro
>     commiteado solo antes de calcular, protocolo de discrepancias, worktree y ningún cambio al CLI. La spec 003 queda
>     libre para la idea de que el Tactical Audit coloque órdenes en MT5.
>   - Criterio principal: **S4 estricto** (N51), con S1 y S4 al lado. Implementación única: `core/outcome_metrics.py`.
>   - Retroactivos: fuera, salvo los 7 de XAU de `RECOVERED_BACKDATED` (N50).
>   - Ancla: `analysis_start_time` si existe (análisis desde el 2026-10-03); si no, `created_at − 20 min`.
>   - Regla horaria: `BROKER_DST_RULE=us` está asumida, no probada. No bloquea: se agrega una sensibilidad (T0).
>   - Evidencia independiente: se re-ejecuta con los análisis posteriores al pre-registro, como §13 del banco v2. No
>     hace falta ningún registro en sombra ni cambio de código.
>
> Origen: plan "Plan P0 — Fractales + Fibonacci (v1.1)" (documento de claude.ai) y las sesiones de análisis del
> 2026-10-02 y 2026-10-06. Los hechos de `<contexto>` marcados `[COPIA]` salen de una copia del repo y se re-verifican
> en F1.

<instrucciones>

## Rol

Sos el evaluador de UNA pregunta de investigación sobre un repo existente. Construís funciones puras, tests y un
cuaderno, y ejecutás un protocolo pre-registrado. No es un feature: no cambiás el CLI, el wizard, `calc_edge`, los
pesos, el umbral ±0.26 ni ningún gate. No escribís en `.data/` bajo ninguna circunstancia.

## Objetivo

Responder, con reglas fijadas antes de mirar resultados, si **P0** (tendencia + Fibonacci en 1D, 12H, 4H y 1H, hoy
manual) se puede sistematizar con un acierto mayor o igual al del P0 discrecional. Preguntas:

| | Pregunta |
|---|---|
| Q1 | ¿Los niveles de P0 (0, 0.382, 0.5, 0.618, 1) producen más reacción del precio que niveles placebo? |
| Q2 | ¿La estructura sola (modelo A) predice la dirección mejor que el azar? |
| Q3 | ¿Fibonacci agrega algo sobre la estructura (B contra A)? |
| Q4 | ¿El mejor modelo de A–G iguala o supera al P0 del operador? |
| Q5 | ¿El mejor modelo sobrevive a la corrección por haber probado 7, y le gana a su gemelo placebo? |
| Q6 | ¿Cambian las respuestas entre Direccionales y Choppy, y entre XAU y BTC? |

Decisión que informa: continuar (el ganador se sigue midiendo con análisis nuevos), modificar, abandonar Fibonacci o
abandonar P0 sistemático (D18).

## Entregable

1. `jupyter/p0_sistematico/preregistro.md`: las decisiones de este prompt, commiteado **solo** antes de calcular
   cualquier predicción, acierto o prueba (F0).
2. `jupyter/p0_sistematico/00_protocolo.md`: lectura obligatoria, discrepancias entre este prompt y el código, y datos
   de F2.
3. `core/p0_structure.py` y `core/p0_fib.py`: funciones puras, sin I/O (motores de estructura y Fibonacci).
4. `tools/p0_sistematico.py`: registro de modelos, puntaje, ensamblaje por análisis y exclusiones con motivo.
5. `tests/test_p0_*.py`, con la suite completa en verde.
6. `jupyter/p0_sistematico.ipynb` y su HTML, con las secciones de RF-9.
7. `jupyter/p0_sistematico/resultados.csv`.

Los entregables 6 y 7 traen datos del journal y el repo tiene remoto en GitHub: van en **un commit propio**, que el
usuario decide si conserva (igual que `0816ed9` del banco v2).

## Procedimiento

Trabajá en un worktree nuevo desde `HEAD` de la rama principal: `.claude/worktrees/p0-sistematico`, rama
`claude/p0-sistematico`, sin `.data/` propio. Las DBs y el banco se leen del checkout principal, en solo lectura.

| Fase | Qué | Salida | Puerta humana |
|---|---|---|---|
| F0 | Prerrequisitos (R1–R3) y pre-registro | `preregistro.md` commiteado solo, con su sha256 | El usuario lee y aprueba el pre-registro **antes** del commit |
| F1 | Lectura obligatoria y discrepancias | `00_protocolo.md` §1–§4: qué se usa y de dónde (`archivo:línea`), re-verificación de todo `[COPIA]`, y cada punto donde este prompt no coincide con el código | El usuario lee las discrepancias que cambien una decisión |
| F2 | Datos, **sin ninguna verdad ni acierto** | Foto del banco con sha256 (R5), reloj, cobertura por TF, conteos por filtro, T0, diferencia mínima detectable; todo en `00_protocolo.md` §5 | — |
| F3 | Motores, registro y tests | Código + tests + salida de la suite, en el orden de R15 | Revisión del diff |
| F4 | Exploratorio | Cuaderno §1–§10 y §12, sin el 40% confirmatorio de T3–T5 | El usuario lee los resultados exploratorios |
| F5 | Confirmatorio | Cuaderno §11, ejecutada **una sola vez** | Aprobación escrita antes de correrla (R10) |
| F6 | Verificación final | `00_protocolo.md` §6–§7: suite, sha256 del pre-registro y de la foto, resultado en una línea | — |

Si una discrepancia de F1 cambia una decisión de `<decisiones>`, se corrige con una versión nueva del pre-registro,
commiteada sola **antes de F4**, y el reporte cita el sha256 de las dos (como la v1.1 del banco v2).

## Reglas de decisión

- **R1 — CLAUDE.md es intocable.** No modifiques `CLAUDE.md` ni el symlink `AGENTS.md`. Antes de cerrar cada turno,
  `git diff --stat CLAUDE.md` debe salir vacío y `readlink AGENTS.md` debe devolver `CLAUDE.md`.
- **R2 — Base commiteada.** Este prompt tiene que estar commiteado antes de F0. Si no lo está, detenete y pedile al
  usuario el commit con la lista explícita de archivos.
- **R3 — Base correcta.** Verificá en la rama principal que la spec 002 esté integrada:
  `specs/002-auto-resolucion-velas/validation.md` con `verdict: pass`, `core/outcome_metrics.py` y
  `config/auto_resolution.py` presentes. Si falta algo, detenete y consultá.
- **R4 — Solo lectura total.**
  - DBs con `mode=ro` (`open_readonly_session`), columnas explícitas, nunca `init_db()`.
  - Velas **solo** de la foto del banco (R5), con `closed_bars()`. Nunca los CSV viejos de `MT5Exports\`.
  - Nada escribe en `.data/`. Los tests usan `:memory:`, `tmp_path` y fixtures.
  - **Nunca corras el CLI dentro del worktree**, ni siquiera `--help` de un subcomando (`CLAUDE.md`, "Comandos").
- **R5 — Foto fija del banco.** En F2, cargá los CSV de `XAUUSD` y `BTCUSD` una sola vez y registrá en
  `00_protocolo.md`, por archivo: ruta, filas, primera y última vela y sha256, más el `status.json` de cada símbolo.
  Todos los cálculos usan esa carga, y el cuaderno cita los sha256 en §1. Si al re-ejecutar algún sha256 cambió,
  detenete (E3).
- **R6 — Ancla, partida y retroactivos.**
  - Ancla: `analysis_start_time` si existe; si no, `created_at − 20 min`. Igual que `core/outcome_metrics.py`.
  - Precio de partida: cierre de la última vela cerrada en el ancla, en la TF más fina validada (1M, 5M, 15M, 30M, 1H).
  - Retroactivos fuera, salvo los 7 de XAU de `RECOVERED_BACKDATED` (N50), que entran con su ancla tipeada. Informá
    cuántos quedaron fuera.
- **R7 — Pre-registro congelado.** Los valores de D1–D10 no se ajustan, no se "mejoran" y no se agregan variantes. Cada
  `ModelSpec` lleva un test que fija su receta. Un cambio es una versión nueva del pre-registro, aprobada por el
  usuario, y se mide solo con análisis posteriores.
- **R8 — Latencia del fractal.** Un pivote vale desde el cierre de la vela t+n, nunca desde t. Ningún cálculo en τ usa
  una vela con `time + duración > τ` (T2).
- **R9 — Placebo obligatorio.** Todo resultado positivo de niveles o de modelos va junto a su placebo. Sin placebo, un
  positivo no cuenta.
- **R10 — Confirmatorio cerrado.** En T3–T5, el 60% más antiguo de los eventos de cada TF es exploratorio y el 40% más
  reciente es confirmatorio. El código no calcula el confirmatorio sin un flag explícito, y no lo activás hasta que el
  usuario lo apruebe por escrito, después de leer el exploratorio. Se ejecuta **una sola vez**.
- **R11 — Segmentos y muestras, siempre lado a lado.** Según el sesgo del edge (`determine_market_bias(calc_edge)`),
  nunca según el resultado:
  - **Todos:** cualquier sesgo, con P0 Long o Short y un nivel a cada lado del precio de partida;
  - **Direccionales:** Bullish o Bearish;
  - **Choppy:** `Choppy / Neutral`.
- **R12 — Verdades, según la pregunta.**
  - Comparaciones pareadas (modelo contra operador, B contra A, ganador contra gemelo): **S4 estricto** principal
    (estándar del repo, N51), con S1 y S4 al lado.
  - Habilidad contra el azar (T1, T6): **S1** contra el nulo geométrico de D15, porque ese nulo no existe con ventana de
    48 h. Al lado, S4 estricto contra "siempre long" y "siempre short".
  - Lo dice el cuaderno en §2, como pide `CLAUDE.md` cuando una prueba no usa solo la cifra principal.
- **R13 — Selección con castigo.** El mejor de A–G por neto (+1 acierto, 0 abstención, −1 fallo) lleva su p ajustado
  con `permutation_max_net_test` (7 modelos, `{{N_PERM}}` mezclas, semilla `{{SEED}}`), single-step max-T como §7 del
  banco v2. H y los gemelos placebo no participan de la selección.
- **R14 — Terminología.** "Fractal" es solo el fractal de precio de n velas usado como pivote. No uses, no cruces y no
  relaciones los campos de `tactical_audit` que mencionan fractales: son otro concepto.
- **R15 — Orden del trabajo en F3.**
  1. Tests sintéticos primero: latencia del fractal, empates, secuencia alterna, estados, invalidación, tramos, zonas,
     barreras y el test de look-ahead T2.
  2. Motores puros.
  3. Registro de modelos con sus tests de receta.
  4. Ensamblaje por análisis con `ExclusionRecord`.
- **R16 — Sin dependencias nuevas.** `requirements.txt` no tiene `scipy` ni `statsmodels` `[COPIA]`. La regresión
  logística de T3 se implementa con numpy (IRLS), con un test contra un caso de valores conocidos, y sus intervalos
  salen de un bootstrap por tramo con semilla fija. Una dependencia nueva es una pregunta para el usuario
  (constitución §1).
- **R17 — Mínimo para concluir.** Menos de `{{N_MIN_PARES}}` opiniones o pares en un segmento = "muestra insuficiente",
  sin conclusión (como el banco v2).

## Restricciones

- No inventes APIs, rutas, funciones ni resultados. Si algo no consta, declaralo como dependencia y detenete.
- Reutilizá, no reimplementes: `core/outcome_metrics.py` (S4 estricto, S1, S4, Overlap), `CsvOHLCProvider` y
  `closed_bars()`, `core/p2_ground_truth.py`, `core/stats_tests.py` (`binomial_test_two_sided`, `mcnemar_exact`,
  `wilson_interval`, `holm_adjust`, `permutation_max_net_test`, `min_detectable_gap`, `net_score`), el redondeo de
  `rescale_p2_sistematico`, `ModelSpec` y `ExclusionRecord` de `tools/p2_backtest.py`, y `config/auto_resolution.py`
  (`RECOVERED_BACKDATED`, `TOUCH_EXCEPTIONS`).
- **No toques:** `cli/` (ningún flujo), `core/math_engine.py`, `determine_market_bias`, los gates, `core/edge_analysis.py`,
  `tools/notion_sync.py`, los modelos de P2 ni `.data/p2_model_log.jsonl`.
- Tests: `conda run -n blast_master pytest -q`, nunca `pytest` a secas.
- Git: nada de `git add -A`, `git add .` ni `git commit -a`. Commits con archivos explícitos y solo cuando el usuario lo
  pida, salvo el commit solo del pre-registro de F0, que el usuario aprueba en esa puerta.
- Idioma (constitución §6): código, identificadores y salidas, en inglés. Pre-registro, protocolo, cuaderno y
  comunicación con el usuario, en español.

## Paradas

Detenete y reportá, sin continuar, si:
- R2 o R3 fallan;
- falla el reloj de XAUUSD o BTCUSD (`status.json` no verificado, o `calibrate_clock_offset` con `aligned` distinto de
  `True`);
- el pre-registro no estaba commiteado antes de F4;
- el sha256 de la foto cambió (E3);
- aparece un look-ahead nuevo;
- necesitás tocar algo de "No toques" o escribir en `.data/`;
- un resultado contradice un hecho de `<contexto>`: reportalo antes de seguir;
- `CLAUDE.md` muestra diff.

## Condiciones de finalización

- Suite completa en verde, con la salida mostrada.
- Pre-registro commiteado antes de F4, con su sha256 citado en el cuaderno y en `00_protocolo.md`.
- Cuaderno con todas las secciones de RF-9; §11 ejecutada una sola vez tras la puerta R10.
- §12 aplica D18 sin reinterpretarla y da **una sola** decisión.
- `00_protocolo.md` §7: el resultado en una línea.

</instrucciones>

<decisiones>
Tomadas por el usuario el 2026-10-02 y el 2026-10-06. Son el contenido del pre-registro, no preguntas abiertas.

- **D1. Niveles de P0.** 0, 0.382, 0.5, 0.618 y 1. **0 = B** (donde termina el impulso) y **1 = A** (donde empezó).
  Fuera: 0.236 y 0.786 (el operador no los usa: 0 menciones en las tesis P0 `[COPIA]`). Para ambos sentidos:
  `L_r = B − r · (B − A)`.
- **D2. Niveles placebo**, fijados antes de ver resultados:
  | Grupo | Niveles de P0 | Placebo |
  |---|---|---|
  | Retrocesos | 0.382, 0.5, 0.618 | 0.300, 0.441, 0.559 |
  | Extremos | 0 (B), 1 (A) | 0.10, 0.90 |
- **D3. Fractal.** n = 2. Superior en t si `H_t ≥ max(H_{t−1..t−n})` y `H_t > max(H_{t+1..t+n})` (empate permitido
  solo del lado viejo); inferior, simétrico con mínimos. Se conoce al cierre de t+n. n = 3 y 5 solo como sensibilidad
  (T11).
- **D4. Estructura.**
  - Secuencia alterna techo–piso: dos techos seguidos conservan el más alto; dos pisos, el más bajo.
  - Cada techo se compara con el anterior (HH o LH) y cada piso con el anterior (HL o LL). Un empate exacto no cambia
    la etiqueta.
  - Estado en τ, con el último techo y el último piso confirmados: HH + HL = alcista; LH + LL = bajista; HH + LL o
    LH + HL = rango.
  - Invalidación: alcista, cuando una vela **cierra** debajo del último HL; bajista, cuando cierra encima del último LH.
    Pasa a rango hasta que una nueva pareja de pivotes confirme otra tendencia. Mecha = sensibilidad (T11).
  - Menos de dos techos y dos pisos confirmados antes de τ = historia insuficiente (E1).
- **D5. Tramo A→B por TF**, solo con pivotes confirmados:
  | Estado | B | A |
  |---|---|---|
  | Alcista | último techo | último piso antes de B |
  | Bajista | último piso | último techo antes de B |
  | Rango | último pivote (cualquiera) | pivote opuesto anterior |

  El tramo cambia solo cuando se confirma un pivote nuevo. Si el precio supera B antes, queda "extendido" y no genera
  eventos. Un mismo tramo detectado en varias TF cuenta una vez en las pruebas agregadas.
- **D6. Zona y evento.** Zona = `[L_r − δ·|B−A|, L_r + δ·|B−A|]`, **δ = 0.025** (mismo ancho para P0 y placebo; ninguna
  zona se solapa). Evento = primera entrada a la zona (la mecha cuenta) después de confirmarse B, con el tramo vigente.
  Máximo un evento por tramo y nivel.
- **D7. Resultado de un evento**, con barreras simétricas **D = 0.10·|B−A|** desde L_r, según el sentido de llegada:
  - Rechazo: se aleja D en sentido contrario a su llegada.
  - Ruptura: avanza D en el sentido de su llegada.
  - Ambiguo: una vela toca ambas barreras, también en la TF más fina disponible (1M → 5M → 15M → 30M → 1H) → excluido
    y reportado.
  - Abierto: ninguna barrera dentro de `{{MAX_HORIZON}}` → excluido y reportado.

  Una ruptura en 1 puede coincidir con la invalidación del tramo: el evento cuenta igual.
- **D8. Puntaje por TF y P0 del modelo.** dir = +1 alcista, −1 bajista.
  | Situación en τ | A | B–H y gemelos |
  |---|---|---|
  | Tendencia, último evento en un retroceso o en 1 = Rechazo | dir | 2·dir |
  | Tendencia, último evento en un retroceso o en 1 = Ruptura | dir | 0 |
  | Tendencia, último evento en 0 = Rechazo | dir | 0 |
  | Tendencia, último evento en 0 = Ruptura | dir | 2·dir |
  | Tendencia, sin evento todavía | dir | dir |
  | Rango o historia insuficiente | 0 | 0 |

  `S = Σ w_k · s_k`; `x0 = clamp(round_half_away_from_zero(2·S / S_max), −2, 2)`, con `S_max = 1` en A y `2` en B–H.
  Signo de x0 = dirección (Neutral si 0); |x0| = fuerza (1 Mid, 2 Strong).
- **D9. Registro de modelos**, todos definidos el 2026-10-02, antes de ver datos de P0:
  | Modelo | Regla | Pesos | Qué aísla |
  |---|---|---|---|
  | A | Solo estructura (control) | 1D 0.25 · 12H 0.25 · 4H 0.25 · 1H 0.25 | ¿La estructura sola predice? |
  | B | Estructura + Fib | 1D 0.25 · 12H 0.25 · 4H 0.25 · 1H 0.25 | B contra A = aporte de Fibonacci |
  | C | Estructura + Fib | 1D 0.40 · 12H 0.30 · 4H 0.20 · 1H 0.10 | Pico en 1D |
  | D | Estructura + Fib | 1D 0.20 · 12H 0.30 · 4H 0.30 · 1H 0.20 | Pico en 12H y 4H |
  | E | Estructura + Fib | 1D 0.10 · 12H 0.20 · 4H 0.30 · 1H 0.40 | Pico en 1H |
  | F | Estructura + Fib, jerárquico | 1D 0.30 · 12H 0.30 · 4H 0.20 · 1H 0.20 | Lectura de arriba hacia abajo |
  | G | Estructura + Fib, pesos del operador | 1D 0.20 · 12H 0.20 · 4H 0.30 · 1H 0.30 | Intuición del operador |
  | H | Estructura + Fib, con 1W (**experimental**) | 1W 0.20 · 1D 0.20 · 12H 0.20 · 4H 0.20 · 1H 0.20 | H contra B = aporte de 1W |

  - F: si `s_1D + s_12H = 0`, no opina (x0 = 0); si no, `s_4H` y `s_1H` cuentan solo si tienen el mismo signo que
    `s_1D + s_12H`, y valen 0 en otro caso.
  - H es un experimento: fuera de la selección (R13) y de la decisión (D18); se reporta al lado.
  - Nombres en código: `P0_MODEL_A..H`, para no chocar con `MODEL_A..G` de P2.
- **D10. Gemelos placebo** `P0_MODEL_Bp..Hp`: idénticos a su modelo, con los niveles placebo de D2.
- **D11. Selección.** Gana el de mejor neto entre A–G (S4 estricto, Todos), con el castigo de R13. No hay modelo
  primario. El ganador es el que se sigue midiendo con análisis nuevos (D19).
- **D12. Cómo se mide P0** (operador y modelos). P0 Long acierta si el precio toca primero el más alto de los dos
  niveles del análisis (`edge_validation_price`, `structural_invalidation`); P0 Short, si toca primero el más bajo; P0
  Neutral es abstención. La regla es geométrica: no depende de cuál nivel se llamó objetivo. El camino, los toques, las
  excepciones (`TOUCH_EXCEPTIONS`), los estados y las ventanas son los de `core/outcome_metrics.py`.
- **D13. Segmentos** según R11.
- **D14. Ancla** según R6.
- **D15. Nulo geométrico (S1).** Desde el precio de partida: si P0 dice Long, `p_i = d(inferior) / (d(superior) +
  d(inferior))`; si dice Short, `p_i = d(superior) / (d(superior) + d(inferior))`. Se compara el acierto observado
  contra la Poisson-binomial de los p_i. Es la versión por análisis de "8.8 geometría" del banco v2.
- **D16. Pruebas.**
  | ID | Hipótesis | Métrica | Fallo |
  |---|---|---|---|
  | T0 | Foto del banco sin duplicados ni velas corridas; reloj verificado; regla horaria registrada | Conteos sobre la foto; `status.json`; sensibilidad sin las ventanas ambiguas de horario (2026-03-08 a 2026-03-29 y 2026-10-25 a 2026-11-01) | Un duplicado o un reloj sin verificar → parada. Si la sensibilidad cambia el signo de una conclusión, esa conclusión queda "pendiente de la regla horaria" |
  | T1 | El acierto de P0 del operador supera el nulo geométrico | S1 observado vs Poisson-binomial(p_i), en Todos, Direccionales y Choppy | p ≥ 0.05 → la referencia no se distingue de la geometría de sus niveles |
  | T2 | Sin look-ahead | Salida en τ idéntica con y sin velas posteriores a τ | Una sola diferencia |
  | T3a | Rechazo en 0.382/0.5/0.618 > en sus placebo | Logística: rechazo ~ profundidad + nivel P0 + TF + estado; OR | IC95 inferior ≤ 1 o p Holm ≥ 0.05 en el confirmatorio |
  | T3b | Rechazo en 0 y 1 > en 0.10 y 0.90 | Igual que T3a | Igual que T3a |
  | T4 | Por nivel: cada uno de los 5 contra su placebo más cercano | OR por nivel | Ninguno sobrevive a Holm |
  | T5 | Efecto en tendencia y en rango, por separado | OR por segmento e interacción | Ambos con IC95 inferior ≤ 1 |
  | T6 | A acierta más que el nulo | S1 vs nulo geométrico; S4 estricto vs siempre long/short; cobertura | p Holm ≥ 0.05 |
  | T7 | Los modelos anclan como el operador (descriptiva) | κ de Cohen por TF: orientación del tramo vs etiqueta BL/BR | κ < 0.4 en 1D y 12H |
  | T8 | Ganador ≥ operador; B > A; ganador > su gemelo | McNemar exacto pareado (S4 estricto), con d e IC95 como §7 del banco v2; p ajustado de R13 | Ganador inferior al operador (b < c, p < 0.05), o ganador ≤ su gemelo |
  | T10 | Generalización a BTC con parámetros congelados | Mismas métricas que T3–T8 | Efecto de signo opuesto al de XAU |
  | T11 | Sensibilidad: n = 3 y 5, ancho por ATR14, mecha vs cierre, ancla a 0/10/20/30/60 min | Signo y tamaño del efecto | El signo cambia → frágil; no se elige variante |

  Etiquetas para T8, como §8 del banco v2: supera (b > c, McNemar p < 0.05 y p de R13 < 0.05), inferior, equivalente
  (IC95 de d contiene 0; dentro del ruido, no una prueba formal), no distinguible, no concluyente (< `{{N_MIN_PARES}}`
  pares).

  Holm por familia, α = 0.05: F1 = {T3a, T3b, T4×5}; F2 = {T5×2}; F3 = {T6, T8×3}. n mínimo en T3–T5: `{{N_MIN_BRAZO}}`
  eventos por brazo en el confirmatorio (detecta 50% → 58%, una cola, potencia 0.80); por debajo es "insuficiente", no
  "falla". Errores e intervalos agrupados por tramo. La diferencia mínima detectable de T6 y T8 se calcula en F2, sin
  verdad (`min_detectable_gap`).
- **D17. Partición.** Por TF y por tiempo: 60% exploratorio (lo más antiguo) y 40% confirmatorio (R10). Nada se ajusta,
  así que no hay train.
- **D18. Decisión** (§12 del cuaderno), una sola:
  | Decisión | Condición | Acción |
  |---|---|---|
  | Continuar | T0 y T2 pasan; T3a o T3b confirmatorio con OR > 1 y p Holm < 0.05; mismo signo en BTC; ganador > su gemelo | El ganador se sigue midiendo con análisis nuevos (D19) |
  | Modificar | El efecto aparece solo en un segmento (T5) o en un nivel (T4) | Pre-registro nuevo y restringido, medido solo con análisis posteriores |
  | Abandonar Fibonacci | T3a y T3b sin efecto con n ≥ mínimo, o ganador ≤ su gemelo | Queda A solo si T6 pasa |
  | Abandonar P0 sistemático | Lo anterior y T6 falla | P0 sigue manual; no se buscan más variantes con estos análisis |
  | Insuficiente | n < mínimo | Sin decisión; acumular |
- **D19. Evidencia independiente.** Son los análisis de XAU y BTC con ancla posterior al commit del pre-registro. Se
  evalúan re-ejecutando el cuaderno con **exactamente** estas reglas y los mismos modelos, sin re-elegir ninguno. El
  modelo nunca se muestra al operador, así que su P0 sigue siendo a ciegas sin cambiar el CLI. "≥ P0 discrecional" se
  decide con esta evidencia, no con el retrospectivo.
- **D20. Etiquetas de las tesis P0 (T7).** `T-BR` = tramo identificado como bajista; `T-BL` = alcista.
  `12HT-BR == 1DT` = 12H comparte el tramo de 1D. Plantilla: `1DT-B…`, `12HT-B…`, `4HT-B…`, `1HT-B…`. Tesis con la
  plantilla vacía = sin etiqueta, fuera de T7.
- **D21. Activos.** XAUUSD es el principal; BTCUSD valida con parámetros congelados (T10). US100 y US500 fuera (reloj o
  historia insuficientes).
</decisiones>

<requisitos_semilla>
Invariantes (cada uno con su test):
- INV-1: Ninguna función escribe en `.data/` ni abre una DB en modo escritura.
- INV-2: Con la misma foto, las mismas DBs y la misma semilla, toda salida es idéntica.
- INV-3: Ningún cálculo en τ usa una vela con `time + duración > τ` (T2).
- INV-4: Un pivote no existe antes del cierre de su vela t+n.
- INV-5: Las recetas de `P0_MODEL_A..H` y sus gemelos coinciden con D9 y D10.
- INV-6: El confirmatorio no se calcula sin el flag de R10.
- INV-7: Todo análisis excluido aparece con su motivo.
- INV-8: Todo resultado del cuaderno cita el sha256 de la foto y del pre-registro.

Requisitos funcionales:
- RF-1: Fractales de n velas con la regla de D3 y su tiempo de confirmación.
- RF-2: Secuencia alterna, HH/HL/LH/LL, estado e invalidación según D4.
- RF-3: Tramo A→B por TF según D5, incluido "extendido".
- RF-4: Niveles de D1 y D2, zonas de D6 y eventos con resultado según D7.
- RF-5: Puntaje y x0 según D8 para cada modelo de D9 y D10, con la regla jerárquica de F.
- RF-6: Ensamblaje por análisis: ancla, precio de partida, x0 de cada modelo, P0 del operador, verdad (D12), p_i (D15),
  segmento (R11) y motivo de exclusión.
- RF-7: Parseo de etiquetas BL/BR y `==` (D20).
- RF-8: Regresión logística por IRLS con bootstrap por tramo (R16).
- RF-9: Cuaderno con: §1 datos, ancla, foto y T0; §2 qué es P0 sistemático y qué verdad usa cada prueba (R12); §3 nulo
  geométrico (T1); §4 P0 del operador en Todos, Direccionales y Choppy; §5 niveles contra placebo (T3–T5, exploratorio);
  §6 modelos A–G contra el operador (T6–T8); §7 ablación (Fibonacci: A vs B; pesos: B vs C–G; 1W: H vs B); §8
  sobreajuste (R13, mitades temporales, por activo, siempre long/short, gemelos placebo); §9 BTC (T10); §10 sensibilidad
  (T0 horario, T11); §11 confirmatorio; §12 decisión (D18).
</requisitos_semilla>

<errores>
- E1 — Historia insuficiente en una TF (D4). → Esa TF vale 0, motivo `insufficient_history`. Si todas valen 0, el
  modelo no opina.
- E2 — Análisis sin los dos niveles, o con los dos del mismo lado del precio de partida. → Fuera, con motivo.
- E3 — Cambió el sha256 de la foto. → Parada; no recalcular sobre otra versión sin aprobación.
- E4 — Hueco anómalo de velas (no explicado por fin de semana ni por la pausa diaria del oro). → Reportarlo en §1 con
  fecha y TF; los eventos que lo crucen se marcan.
- E5 — Un test falla. → No seguir; mostrar la salida y detenerse.
</errores>

<contexto>
Por qué existe: P0 pesa 0.30 en `calculate_edge_score`, el mayor de los cinco parámetros, y hoy es manual. P0 Strong
aporta 0.30 al `i_cd`, más que el umbral ±0.26: decide el sesgo solo. El usuario quiere saber si fractales + Fibonacci
pueden sistematizarlo, con el mismo rigor que P2. "No supera al discrecional" es un resultado válido y probable.

Hechos de la spec 002 (`CLAUDE.md` del 2026-10-06):
- Spec 002 cerrada el 2026-10-05, `verdict: pass`.
- Criterio principal: S4 estricto (N51), con S1 y S4 al lado. Implementación única: `core/outcome_metrics.py`.
  Definiciones en `docs/criterios-de-acierto.md`.
- `analysis_start_time` existe desde el 2026-10-03; las filas viejas lo tienen vacío.
- Los 7 retroactivos de XAU de `RECOVERED_BACKDATED` cuentan (N50).
- Banco: los 3.824 pares de velas de invierno repetidas en 4H, 12H, 1D y 1W se quitaron el 2026-09-30 (N44). Los CSV
  viejos de `MT5Exports\` siguen corridos.
- Relojes verificados: XAUUSD, BTCUSD y US500. USTEC no.
- `BROKER_DST_RULE=us` está asumida, no probada. Se confirma entre el 2026-10-26 y el 2026-10-30, o del 2027-03-15 al
  2027-03-26. Si resultara `eu`, hay que rehacer las velas del 2026-03-08 al 2026-03-29 y del 2026-10-25 al 2026-11-01.

Lecciones de "P2 banco v2" (`CLAUDE.md`, `jupyter/p2_banco_v2/`):
- El formato de este prompt viene de ahí: pre-registro commiteado solo antes de calcular, protocolo de discrepancias,
  foto del banco con sha256, worktree, resultados con datos del journal en un commit propio.
- "No volver a buscar pesos sobre estos mismos análisis": los pesos de A–H se fijaron antes de ver datos de P0 y no se
  re-eligen. La evidencia que decide es la de D19.
- D (P2) acierta 25/38 = 66%, compatible con un acierto real de 50% a 79%. Confirmar un 66% real pediría unos 175
  análisis. P0 va a tener una limitación de muestra del mismo orden.

`[COPIA]` Hechos medidos en la copia del 2026-09-29 (re-verificar en F1):
- P0 vive en `analysis_layer` (`layer_name = 'P0'`, `department = 'EFFICIENCY'`): dirección, fuerza y tesis en texto
  libre. No hay campos numéricos de Fibonacci.
- XAU: 81 análisis (7 retroactivos), del 2026-05-18 al 2026-09-16; Bullish 18, Bearish 28, Choppy 35; 76 con ambos
  niveles. BTC: 24 (0 retroactivos), del 2026-07-02 al 2026-09-01; 12 / 5 / 7; 23 con ambos niveles. El banco v2
  dejó 90 en alcance (68 XAU, 22 BTC).
- La dirección P0 coincide con el sesgo final en 40 de 46 direccionales de XAU y 16 de 17 de BTC. P0 es direccional en
  34 Choppy de XAU (contando retroactivos).
- Las tesis nombran 0.382 (XAU 60 de 74 sin retroactivos), 0.618 (42), 0.5 (27), "nivel 0" (29) y "nivel 1" (20);
  0.236 y 0.786, 0 veces. Etiquetas por TF en 67–71 de 74 tesis XAU y 22 de 24 BTC; `==` en 63 de 74 y 19 de 24.
- En XAU 1H, 114 de 115 huecos de 2 h caen a las 15:00 hora de Guatemala, de lunes a jueves (pausa diaria del oro);
  1 es anómalo (domingo 23:00 → 01:00).
- `requirements.txt` no tiene `scipy` ni `statsmodels`.
</contexto>

<configuracion>
| Variable | Significado | Valor |
|---|---|---|
| `{{SEED}}` | Semilla de permutaciones y bootstrap | `20261006` |
| `{{N_PERM}}` | Mezclas de la permutación del mejor de 7 | `10000` |
| `{{N_BOOT}}` | Remuestras del bootstrap por tramo | `5000` |
| `{{SPLIT_EXPLORATORIO}}` | Fracción exploratoria por TF en T3–T5 | `0.60` |
| `{{N_MIN_BRAZO}}` | Eventos mínimos por brazo en el confirmatorio | `480` |
| `{{N_MIN_PARES}}` | Opiniones o pares mínimos para concluir | `20` |
| `{{MAX_HORIZON}}` | Horizonte de eventos y de S1 | `2160` velas de 1H |
</configuracion>
