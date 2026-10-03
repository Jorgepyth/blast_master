# Plan técnico — Spec 002: auto-resolución con velas

- **Fecha:** 2026-09-27.
- **Fase:** F4. **Aprobado por el usuario el 2026-09-27.**
- **Base:** `6162742`, en la rama de la sesión `claude/spec-001-closure-tests-b99a6d`.
- **Fuentes:**
  - `spec.md`, con las decisiones N1–N37;
  - `docs/constitution.md`, principios 1 a 8;
  - `baseline.md`, con las citas `archivo:línea`;
  - `docs/criterios-de-acierto.md`.
- **Cómo leerlo:**
  1. módulos;
  2. dónde vive cada dato;
  3. los cálculos paso a paso;
  4. los comandos;
  5. las decisiones, cada una con su alternativa descartada;
  6. los tests;
  7. la cobertura RF por RF.

---

## 1. Estructura de módulos

Los módulos nuevos de cálculo viven en `core/`: son funciones puras, sin DB y sin consola (constitución, principio
3). La lectura y escritura de archivos y de DBs vive en `tools/`. La interfaz vive en `cli/`.

| Módulo | Responsabilidad | RF que cubre |
|---|---|---|
| `tests/conftest.py` (nuevo) | Aislamiento de la suite. Un fixture autouse lleva cada test a su `tmp_path`, y un audit hook hace fallar el test si abre algo bajo `<repo>/.data/` o `/mnt/c/` | RF-16, INV-5 |
| `tests/test_cli_report_command.py` (ajuste) | Fijar `engine_default` en el test de `:71-75` | RF-16b |
| `tests/test_wizard_safety_net.py` (nuevo) | Red de seguridad escrita **antes** de tocar los wizards: fija el orden de los prompts y los valores guardados del Efficiency y de la rama llenada del Tactical, en verde contra el código actual | INV-1, R10.1 |
| `config/auto_resolution.py` (nuevo) | Constantes versionadas: `REAL_ACCOUNTS`, `MT5_SYMBOL_MAP`, `MAX_HORIZON`, `ANCHOR_FALLBACK_MIN`, `MARK_PRICE_TOLERANCE`, `REVERT_H`, `EXPANSION_H`, `EXPANSION_R`, `SWEEP_R`, `SWEEP_H`, `OVERLAP_MIN_BARS`, `S4_WINDOW_H`, `P2_LOG_MODELS`, y los códigos de motivo como constantes | Configuración de la spec |
| `.env.template` (ajuste) | Rutas propias de la máquina: `CANDLE_BANK_DIR`, `MT5_INCOMING_DIR`, `WINDOWS_PYTHON`, `EXPORTER_WIN_PATH`, `AUTO_EXPORT`, `BROKER_DST_RULE` | RF-20, RF-15b |
| `windows_export/export_p2_ohlc.py` (ajuste) | Agregar 5M y 1M. Convertir cada vela con el desfase de su fecha según `--dst-rule`. Escribir en un directorio nuevo por corrida y de forma atómica, archivo por archivo | RF-15, RF-15b |
| `tools/p2_backtest.py` (solo agregados) | `TIMEFRAME_MINUTES` suma `5M` y `1M`. La evaluación de `calibrate_clock_offset` se extrae a `evaluate_clock_entries()`, sin cambiar su resultado | RF-2, RF-2b, RF-4f |
| `core/p2_ground_truth.py` (solo agregados) | `first_touch_detail()`: misma lógica de toque que `first_touch_direction`, pero devuelve el índice de la vela, el nivel tocado y si fue ambiguo. `first_touch_direction` no cambia | RF-4, RF-4b, RF-4f |
| `tools/candle_bank.py` (nuevo) | El banco por símbolo: import inicial, fusión atómica sin pérdidas, candado, verificación por superposición y por referencias, estado por símbolo, lectura como `CsvOHLCProvider` | RF-1, RF-1b, RF-1c, RF-1d, RF-2, RF-2b, RF-2c, RF-2d, RF-15c |
| `core/candle_resolution.py` (nuevo, puro) | Camino de varias TF desde el ancla, primer toque con refinamiento, MAE/MFE, R, revertido, expansión y sweep, MAE/MFE táctico, `could_hit_tp`, chequeo del Mark Price y códigos de motivo | RF-3, RF-3b, RF-4 a RF-4h, RF-5, RF-5b, RF-8 a RF-8d, RF-9 a RF-9d, RF-10 a RF-10d |
| `core/outcome_metrics.py` (nuevo, puro) | **El módulo de criterios de acierto:** S1, S4, la etiqueta Overlap y la exclusión de retroactivos. Lo usan el reporte y los cuadernos | RF-21, RF-17, RF-5 |
| `tools/auto_resolution.py` (nuevo) | Servicio: lee de una DB, con columnas explícitas, lo que necesita el resolvedor (el análisis y las anclas de su cuenta), abre el banco del símbolo, llama a `core/candle_resolution.py` y devuelve la propuesta | RF-4, RF-4e, RF-6b, RF-7, RF-9, RF-10 |
| `tools/resolution_report.py` (nuevo) | Reporte de comparación: los datos y el Markdown en inglés, siempre en solo lectura. El resumen en consola vive en el subcomando de `cli/main.py` (O2) | RF-6, RF-6b, RF-3b, RF-17, RF-21 |
| `tools/auto_backfill.py` (nuevo) | Arma el plan de cambios, detecta conflictos, ensaya sobre copias, chequea el backup, aplica en una transacción y escribe `backfill_history` | RF-11 a RF-11e, RF-18 |
| `cli/backfill_view.py` (nuevo) | Dibuja la vista estilo `git log --decorate --oneline --graph` con Rich y ofrece aceptar conflictos | RF-19, RF-11e |
| `tools/candle_sync.py` (nuevo, proceso aparte) | Lanza el exportador de Windows vía `powershell.exe`, espera con timeout, y después fusiona y verifica llamando a `tools/candle_bank.py`. Escribe el estado | RF-20 a RF-20f |
| `tools/database.py` (solo agregados) | Columnas nuevas, tabla `backfill_history` y el shim de `init_db` | RF-13d, RF-13e, RF-14, RF-14b, RF-18, NFR-1 |
| `cli/schemas/audit_efficiency.py` (ajuste) | `resolution_time` opcional; campos nuevos `audit_registration_time` y `resolution_time_source` | RF-7g, RF-14, RF-14b |
| `cli/main.py` (ajuste) | Horas en `flow_new_analysis`, aviso del Mark Price, disparos del export, defaults `(auto)` en los wizards, prompt "Resolution Time", subcomandos `resolution-report`, `backfill`, `candles` y `p2-model` | RF-3, RF-7 a RF-7g, RF-9, RF-10, RF-13 a RF-13e, RF-14, RF-20 a RF-20d, RF-12 |
| `tools/p2_model_feedback.py` (nuevo) | P2 de cada modelo de `P2_LOG_MODELS` (hoy solo D) sobre velas cerradas en el ancla, con registro prospectivo en `.data/p2_model_log.jsonl` (una línea por análisis y modelo) y los chequeos de RF-12e. Lo usan el hook de guardado, el catch-up de `candle_sync` y el comando `p2-model` | RF-12 a RF-12e |
| `docs/criterios-de-acierto.md` (ya existe, `6162742`) | La definición en palabras de S1, S4 y Overlap | RF-21 |

---

## 2. Modelo de datos

### 2.1 Columnas nuevas en cada DB de cuenta (`.data/flight_account_*.db`)

Son cambios aditivos, vía `Base.metadata.create_all` y el shim de `ALTER TABLE ... ADD COLUMN` (patrón de
`tools/database.py:277-379`).

| Tabla | Columna | Tipo | Ejemplo | Quién la escribe |
|---|---|---|---|---|
| `unified_department` | `analysis_start_time` | `DATETIME NULL` | `2026-10-02 09:14:03` | `flow_new_analysis` al confirmar la fuerza de P0 (RF-13, 13b, 13c) |
| `unified_department` | `mark_price_time` | `DATETIME NULL` | `2026-10-02 09:26:41` | Al ingresar el Mark Price (RF-13d) |
| `unified_department` | `saved_at` | `DATETIME NULL` | `2026-10-02 09:31:10` | "Confirm & Save", con la hora real (RF-13e) |
| `efficiency_audit` | `audit_registration_time` | `DATETIME NULL` | `2026-10-04 18:02:55` | Guardado del audit (RF-14); backfill para las filas viejas (RF-11c) |
| `efficiency_audit` | `resolution_time_source` | `VARCHAR NULL` | `candles` | Wizard y backfill (RF-14b) |

Valores de `resolution_time_source`: `candles`, `corrected`, `pending_candles`, `no_history`, `clock_unverified`,
`clock_misaligned`, `ambiguous`, `no_levels`, `no_mt5_symbol`, `open`.

### 2.2 Tabla nueva `backfill_history`, en cada DB de cuenta

```sql
CREATE TABLE backfill_history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      VARCHAR(36) NOT NULL,   -- uuid4 de la corrida
  run_at      DATETIME    NOT NULL,   -- GT naive
  kind        VARCHAR     NOT NULL,   -- 'fill' | 'accepted_conflict' | 'legacy_move'
  table_name  VARCHAR     NOT NULL,   -- 'efficiency_audit' | 'tactical_audit'
  record_id   VARCHAR     NOT NULL,
  field       VARCHAR     NOT NULL,
  old_value   TEXT        NULL,       -- texto; NULL si estaba vacío
  new_value   TEXT        NULL,
  source      VARCHAR     NOT NULL    -- 'candles' | código de motivo
);
```

Ejemplo de fila: `(1, '7f3a2c1e-...', '2026-10-05 14:20:11', 'legacy_move', 'efficiency_audit', '00e2e31b-...',
'audit_registration_time', NULL, '2026-06-24 18:40:07.412', 'legacy')`.

Solo se insertan filas. El código de `tools/auto_backfill.py` nunca hace `UPDATE` ni `DELETE` sobre esta tabla, y un
test lo verifica (RF-18).

**Aclaración de T35 (2026-10-02):**
- **La DB también lo impide.** Dos triggers, `backfill_history_no_update` y `backfill_history_no_delete`, abortan con
  `backfill_history is append-only`. Se crean junto con la tabla (evento `after_create` del ORM), así que existen en
  las DBs nuevas y en las viejas que migra `init_db`. Es un agregado, como la tabla.
- **Lectores en solo lectura.** Las columnas nuevas del ORM rompen a todo código que haga `SELECT` de la entidad
  completa sobre una DB que el CLI todavía no migró, porque falla con "no such column". El único caso era
  `assemble_p2_systematic_rows` (`tools/p2_backtest.py`), que ahora pide columnas explícitas.

### 2.3 Banco de velas (nuevo)

- **Ubicación:** `${CANDLE_BANK_DIR}/{MT5_SYMBOL}/{TF}.csv`. El valor por defecto de `CANDLE_BANK_DIR` es
  `.data/candle_bank/`, resuelto a una ruta absoluta desde la raíz del repo. Nunca es relativo a la carpeta de trabajo.
- **Formato:** idéntico al de hoy (`time,open,high,low,close`, con `time` = hora de apertura en GT naive), ordenado y
  sin `time` repetido. Así, `CsvOHLCProvider(base_dir=<banco>/XAUUSD)` lo lee sin cambios (INV-6).
- **TF guardadas:** 1W, 1D, 12H, 4H, 1H, 30M, 15M, 5M y 1M (RF-15).
- **Estado por símbolo:** `${CANDLE_BANK_DIR}/{MT5_SYMBOL}/status.json`. Ejemplo:

```json
{"symbol": "XAUUSD", "clock": "verified", "verified_by": "overlap", "dst_rule": "unverified",
 "last_export": {"run_id": "20261005T142011", "result": "merged", "bars_added": {"1M": 412, "15M": 28}},
 "last_error": null,
 "verified_export": {"run_id": "20261005T142011", "verified_by": "overlap", "server": "ICMarketsSC-Demo",
                     "base_utc_offset": 2, "dst_rule": "us"}}
```

- `verified_export` (N43) describe el último export que se fusionó: cómo se verificó, y con qué servidor, desfase
  base (el de invierno) y regla de horario se convirtió. Un export que no se fusiona no lo cambia. Es lo que compara
  la herencia (§3.8, paso 4b). El import legacy lo deja con `server` y `base_utc_offset` en `null`, así que un
  símbolo importado de legacy no sirve de donante hasta su primer export nuevo.

- **Candado:** `${CANDLE_BANK_DIR}/{MT5_SYMBOL}/.lock`, con el PID y la hora. Se considera abandonado después de
  2 h, igual que `tools/backup.py:134`.

### 2.4 Llegada de los exports (lado Windows)

- **Ubicación:** `${MT5_INCOMING_DIR}/{MT5_SYMBOL}/{run_id}/{TF}.csv`, por defecto
  `/mnt/c/Users/jcifu/MT5Exports/_incoming/`. Cada corrida escribe en un directorio **nuevo**, así que nunca
  sobrescribe nada.
- Se conservan las últimas 5 corridas por símbolo, para poder auditarlas.
- Además de `RUN_ID:` y `RUN_DIR:`, el exportador imprime `SERVER:` (el servidor de la cuenta, de
  `mt5.account_info()`, que es de solo lectura) y `BASE_UTC_OFFSET:` (el desfase de invierno del servidor).
  `tools/candle_sync.py` los pasa a la fusión, para la herencia del reloj (N43).
- Los CSV actuales `MT5Exports/{SYMBOL}/{TF}.csv` **no se tocan** (INV-6). El cuaderno y `tools/edge_evaluation.py`
  los siguen leyendo.

### 2.5 Registro prospectivo del P2 sistemático (RF-12 a RF-12e, N38, N42)

- **Ubicación:** `${ACCOUNTS_DATA_DIR}/p2_model_log.jsonl` (por defecto `.data/p2_model_log.jsonl`). Es un solo
  archivo, con **una línea JSON por análisis y modelo**, y solo se le agregan líneas.
- **Qué modelos:** los de `P2_LOG_MODELS` en `config/auto_resolution.py` (nombre → fecha de alta; hoy
  `{"D": "2026-09-27"}`). La receta de cada uno se toma de `MODELS_BY_NAME` (`tools/p2_backtest.py:217`), sin copiarla.
  Un modelo entra en un análisis solo si `analysis_start_time >= fecha de alta 00:00` (hora GT).
- **Huella de la receta (`model_hash`):** los primeros 12 caracteres del SHA-256 del JSON canónico (claves ordenadas)
  de `timeframes`, `weights`, `ema_chain` y `use_di`, con `json.dumps(..., sort_keys=True, separators=(",", ":"))`.
  Para D da `20315fe7bfe2`. `label` y `description` no entran, porque son solo texto. La
  huella cubre la receta, no las funciones compartidas de cálculo (`compute_score_p2_sistematico`,
  `strength_from_adx`), que ya cuidan `tests/test_p2_models.py` y esta spec no cambia.
- **Chequeo de cada modelo de la lista, antes de registrar (RF-12e):**
  1. el nombre existe en `MODELS_BY_NAME` → si no, `unknown_model:<NAME>`;
  2. todas sus TF están en las TF del banco (§2.3) → si no, `timeframe_not_in_bank:<TF>`;
  3. su `model_hash` actual coincide con el de sus líneas ya registradas → si no, `model_recipe_changed:<NAME>`.

  El modelo que falla se saltea con un aviso de una línea; los demás se registran igual.
- **Ejemplo de línea:**

```json
{"trade_id": "a1b2c3d4-...", "account": "000", "asset": "XAUUSDT.P", "symbol": "XAUUSD",
 "anchor": "2026-10-02T09:14:03", "logged_at": "2026-10-02T11:05:40",
 "operator_p2": {"direction": "Long", "strength": "Mid", "score": 1},
 "model": "D", "model_since": "2026-09-27", "model_hash": "20315fe7bfe2",
 "model_spec": {"timeframes": ["1W", "1D", "12H", "4H", "1H", "30M"],
                "weights": {"1W": 0.08, "1D": 0.12, "12H": 0.17, "4H": 0.22, "1H": 0.26, "30M": 0.15},
                "ema_chain": [20, 100, 200], "use_di": true},
 "p2_raw": 0.41, "p2_rescaled": 1, "status": "ok",
 "by_tf": {"1W": {"bias": 1, "ema20": 2431.5, "ema100": 2388.2, "ema200": 2310.9,
                  "adx": 24.1, "plus_di": 27.0, "minus_di": 18.2, "weight": 0.08}}}
```

- Si no se puede calcular, `status` lleva el motivo (`insufficient_history:1W`, `pending_candles`) y `p2_raw` es
  `null`. Una línea `pending_candles` se **reemplaza** por la definitiva en el catch-up. Para que el archivo siga
  siendo de solo agregar, la línea nueva lleva `supersedes: <n.º de línea>`, y el lector se queda con la última
  línea de cada par (`trade_id`, `model`).
- **Cómo se suma un modelo H más adelante** (fuera de esta spec): se define `MODEL_H` en `tools/p2_backtest.py` con
  su test, se agrega a `MODELS`, y se suma `"H": "<fecha del día>"` a `P2_LOG_MODELS`. No hace falta tocar
  `tools/p2_model_feedback.py`. Si H usa indicadores que hoy no se calculan (EMA 50, RSI, ATR), antes hay que
  extender `TFIndicatorSnapshot` (`tools/p2_backtest.py:237`).

---

## 3. Algoritmos no triviales

### 3.1 Camino hacia adelante de varias TF (RF-4, N17, N18)

1. **Escalera:** `LADDER = [1M, 5M, 15M, 30M, 1H]`.
2. Se parte de `t = ancla`.
3. En cada paso se elige la TF más fina cuyo banco tiene una vela que abre en `t` o después, y cuyo tramo cubre `t`.
4. Se toman velas de esa TF hasta que se acabe su cobertura, en `e`.
5. **Empalme sin huecos:**
   - para pasar a la TF gruesa G siguiente, el tramo `[e, techo_G(e))` se completa con la TF más fina que lo cubra;
   - si ninguna lo cubre, el camino se corta en `e`, y el resultado depende de qué pasó antes (ver 3.2).
6. **Fin del recorrido:** el primer toque, o el fin del horizonte: la apertura de la vela 1H número 2160 contada
   desde el ancla (`MAX_HORIZON`).

**Aclaraciones de T25** (`forward_path`, sin cambiar la intención de los pasos):
- **Paso 3:** entre la TF base (la más fina que cubre `t`) y las más finas que todavía no lo cubren, se toma la
  vela que abre primero en `t` o después; en un empate, la más fina. Así, una TF más fina que empieza antes de la
  próxima vela de la base se usa desde su inicio, sin dejar ese tramo sin mirar.
- **Paso 5:** como G es la TF más fina que cubre `e`, ninguna más fina puede rellenar `[e, techo_G(e))`. En la
  práctica se baja solo si `e` cae en el borde de una vela de G; si no, el camino se corta en `e`, y el resultado es
  `pending_candles` hasta que un export traiga la TF que faltaba.
- **Límite conocido:** un hueco de datos dentro del tramo de una TF (velas que faltan en el banco aunque hubo
  mercado) no se distingue de un mercado cerrado, y el camino lo cruza. Puede pasar si no hubo ningún export en los
  días previos a un cambio de horario: el filtro de estación ya no deja entrar esas velas de 1H y menores.

**Casos límite:**
- el ancla es anterior a la vela más vieja del banco en todas las TF → `no_history`;
- el banco termina antes del toque y del fin del horizonte → `pending_candles`;
- el banco todavía no llega al ancla (su última vela cierra en el ancla o antes), o no tiene velas de la escalera →
  `pending_candles`, porque es temporal y lo arregla el próximo export (agregado en T24; un reloj sin verificar se
  informa antes, RF-4e);
- el ancla cae dentro de una vela de 1M → se empieza en la vela siguiente (N17).

### 3.2 Primer toque con refinamiento (RF-4, RF-4b)

1. Se recorre el camino y, en cada vela, se evalúan `toca_arriba = high >= nivel_superior` y
   `toca_abajo = low <= nivel_inferior`, con la misma lógica que `core/p2_ground_truth.py:92-103`.
2. Si en una vela se tocan los dos niveles y hay una TF más fina que cubre esa vela, se recorre ese intervalo con la
   TF más fina y se repite.
3. Si en la TF más fina disponible se siguen tocando los dos → `ambiguous`.
4. La hora del toque es la apertura de la vela que tocó (N19). Se guarda también la TF usada, que da la precisión.
5. `first_touch_detail()` devuelve `(dirección, incompleto, índice, nivel, ambiguo)`.

**Aclaraciones de T26** (`resolve_first_touch`):
- **Pasos 2 y 3:** el camino de T25 ya va, en cada tramo, en la TF más fina que tiene el banco. Una vela del camino
  que toca los dos niveles ya está en la TF más fina disponible para ese tramo, así que es `ambiguous` directamente.
  "Una vela doble en 15M resuelta por 1M" se da sola: donde hay 1M, el camino ya va en 1M. Si el 1M empezara en
  medio de esa vela de 15M, tampoco sirve para mirar adentro: el pedazo sin 1M podría tener un toque anterior.
- **Paso 6 de §3.1 (horizonte):** se miran `MAX_HORIZON` velas de 1H desde el ancla, igual que el backtest
  (`FORWARD_PATH_MAX_BARS`). El horizonte termina en el **cierre** de la vela 1H número 2160 que abre en el ancla o
  después. La vela de 1H que contiene el ancla no cuenta, igual que en el camino. Si el mercado cierra justo antes del
  fin del horizonte y reabre después, el horizonte cuenta como cumplido.
- **Resultado:** códigos neutros (`confirmed`, `invalidated`, `open`, o el código de motivo). Pasarlos a los valores
  de `ResolutionType` del wizard (por ejemplo "Confirmed (A equal to B)") les toca a T30 y T40.

### 3.3 Etiqueta Overlap (RF-5, N11, N22, N36)

1. Con columnas explícitas se leen `created_at`, `is_backdated` y `analysis_start_time` de **toda** la cuenta.
2. Ancla de cada análisis: `analysis_start_time`; si no hay, `created_at` en los retroactivos y `created_at − 20 min`
   en los demás.
3. A es Overlap si existe B con `ancla(A) < ancla(B) < límite(A)`, donde `límite(A)` es el primer toque o, si no hubo
   toque, el fin de la cobertura del banco.
4. La etiqueta no modifica el primer toque ni `resolution_time` (N37).
5. Si Overlap se sabe pero el toque sigue pendiente → etiqueta Overlap y `pending_candles`.

### 3.4 MAE/MFE estructurales (RF-4, N3 del baseline)

- **Long:** MAE = mínimo de `low` y MFE = máximo de `high` en el camino, desde el ancla hasta la vela del toque
  inclusive. En un Short es al revés.
- El resultado son precios, con la precisión del CSV.

**Aclaraciones de T27** (`resolve_analysis`, `start_price`):
- **Precio de partida:** entre las TF de la escalera, la vela cerrada más reciente en el ancla; si dos cierran a la
  vez, la de la TF más fina. Casi siempre es la de 1M; si el 1M todavía no empezó (o le faltan velas justo antes),
  vale la TF que cerró más tarde. Si ninguna vela cerró todavía en el ancla, el resultado es `no_history`.
- **MAE/MFE estructurales:** solo con toque (`confirmed` o `invalidated`). En `open`, `ambiguous` o `pending_candles`
  no se proponen; el R sí se conoce siempre que haya tesis.
- **Velas con cierre:** el precio de partida es un cierre, y `OhlcBar` (de `core/p2_ground_truth.py`) no lo tiene.
  `Candle(time, open, high, low, close)` es el tipo nuevo, y sirve también para el camino y el toque. T30 arma las
  `Candle` desde los CSV del banco.

### 3.5 Reglas de Structural Resolution y Failure Reason (RF-8 a RF-8d)

Se usa R = |precio de partida − SI|.

1. **Revertido (N13):** entre `t_V` y `t_V + 24 h`, el precio vuelve al Mark Price. Por ejemplo, en un Long,
   `low <= mark_price`. Si el análisis no tiene Mark Price, se usa el precio de partida (RF-8d).
2. **Expansión (N14, N20):** el exceso a favor más allá de EVP, medido entre `t_V` y lo primero que ocurra
   (`t_V + 24 h` o el toque de SI), es `>= 0.5 R`.
3. **Mínima:** en cualquier otro caso.
4. **Sweep (N15, N21):** en un Invalidated, el precio toca EVP dentro de `t_SI + 48 h`, y el exceso más allá de SI
   entre `t_SI` y `t_V` es `<= 1 R`.
5. Las ventanas se recorren con la misma escalera de 3.1.

**Aclaraciones de T28** (`propose_structural`):
- **Ventanas medio abiertas:** una vela que abre justo a las 24 h (o a las 48 h) ya no entra.
- **Revertido:** se mira desde el cierre de la vela del toque. La vela del toque no cuenta, porque su extremo
  contrario pudo ocurrir antes del toque.
- **Expansión:** cuenta la vela del toque, porque todo lo que pasa del objetivo en ella ocurrió después de tocarlo.
  La ventana termina en lo primero que ocurra: `EXPANSION_H` horas o el toque de la invalidación, y la vela de ese
  toque no cuenta. Las dos ventanas tienen su propia duración (hoy son de 24 h las dos) y se recorre la más larga.
- **Sweep:** el exceso se mide desde la vela que cruzó la invalidación hasta la que toca el objetivo, las dos
  incluidas. De la segunda no se sabe si su mínimo fue antes o después del toque, y contarla evita proponer un
  sweep de más.
- **Ventana sin velas suficientes:** si el banco todavía no cubre la ventana que decide (24 h después del toque del
  objetivo, o 48 h después del de la invalidación), el resultado es `pending_candles`. La excepción es que ya se
  sepa: el precio ya volvió al Mark Price (que tiene prioridad), o ya tocó el objetivo después de la invalidación.
- **Códigos neutros:** `reverted`, `expansion`, `minimal`, `n/a`, `liquidity_sweep` y `overlap`. Pasarlos a los
  valores de `StructuralResolution` y `FailureReason` del wizard les toca a T30 y T40.

### 3.6 MAE/MFE táctico y `could_hit_tp` (RF-9, RF-10)

- **Casos que no se proponen:**
  - `exit_time <= entry_time` → `no_interval`;
  - `entry_price == stop_loss` → `zero_r`;
  - TP del lado contrario → `invalid_tp`.
- **MAE/MFE:** se toman las velas que se solapan con `[entry_time, exit_time]`, en la TF más fina validada (1M, 5M,
  15M). Con R = |entry − SL|, en un Long `MFE = (max(high) − entry) / R` y `MAE = (entry − min(low)) / R`. Se
  redondean a 2 decimales y se limitan a `[0, 10]`.
- **`could_hit_tp`:** se recorre desde la vela de la entrada con la escalera de 3.1. Si el TP llega antes que el SL,
  es "yes"; si el SL llega primero o no llega nada dentro de `MAX_HORIZON`, es "no". Si los dos caen en la misma vela
  aun en la TF más fina, no se propone (RF-10b).

### 3.7 Chequeo del Mark Price (RF-3, RF-3b, N33)

- **Con `mark_price_time`:** se busca la vela que contiene esa hora, subiendo por 1M, 5M y 15M. Apenas
  `low <= mark_price <= high` → OK, y se registra en qué TF coincidió. Si ni la vela de 15M lo contiene con ±0.1% →
  advertencia de una línea.
- **Sin `mark_price_time`** (solo en el reporte): se toma el rango de precios de `[created_at − 20 min, created_at]`
  en 1M, si no en 5M, si no en 15M. Si el Mark Price queda fuera por más de 0.1%, se lista.

**Aclaraciones de T31** (`check_mark_price`, `check_mark_price_in_period`):
- La tolerancia del 0.1% se aplica en la última TF que tiene vela a esa hora, normalmente la de 15M; en 1M y 5M el
  chequeo es exacto.
- Una hora justo en el cierre de una vela pertenece a la siguiente.
- En la versión por período, la vela que abre justo en `created_at` no entra: sus precios son posteriores al guardado.
- Si ninguna vela contiene esa hora, no se chequea. El motivo es `no_history` (antes del banco), `pending_candles`
  (después) o `no_candle` (adentro, por ejemplo con el mercado cerrado).
- El resultado guarda en qué TF coincidió y a qué distancia quedó del rango, para el reporte (T32).

### 3.8 Fusión y verificación del banco (RF-1, RF-1b, RF-2, RF-2d, RF-15c, N29, N32)

1. Se toma el candado del símbolo. Si ya está tomado → se cancela con un mensaje y el banco no se toca.
2. Se leen los CSV que llegaron y el banco.
3. **Verificación por superposición:** en 1H, 30M, 15M, 5M y 1M, se toman las velas con `time` en los dos lados.
   - Si hay `>= OVERLAP_MIN_BARS` en alguna TF y **todas** coinciden en `open`, `high`, `low` y `close` (con una
     tolerancia relativa de 1e-9) → verificado.
   - Si alguna difiere → `clock_misaligned`, y no se fusiona.
4. **Verificación por referencias**, si no hubo superposición: se juntan las referencias de las cuentas de
   `REAL_ACCOUNTS` que mapean al símbolo, en solo lectura. Son los fills (`entry_time`, `entry_price`) y los Mark
   Price no retroactivos (`mark_price_time` si existe, si no `created_at`).
   - Si al menos 10 caen dentro del rango exportado **y** `evaluate_clock_entries()` da `aligned=True` → verificado.
   - Si `evaluate_clock_entries()` da el reloj corrido → `clock_misaligned`. Si faltan referencias → paso 4b.
4b. **Herencia (RF-2e, N43):** se busca un donante entre los otros símbolos del banco. Sirve el que tiene
   `clock = "verified"` y un `verified_export` verificado por superposición o por referencias (no heredado), con el
   mismo `server`, `base_utc_offset` y `dst_rule` que este export. Si hay varios, el primero en orden alfabético.
   - Con donante, se toman las referencias propias que caen en una vela del export, en la TF de
     `CLOCK_TIMEFRAME_PREFERENCE` (igual que el paso 4). Si son al menos `INHERIT_MIN_OWN_REFERENCES` y **todas**
     calzan sin desplazamiento → verificado, con `verified_by = "inherited:<donante>"`.
   - Si no → `clock_unverified`, con el motivo: sin donante, pocas referencias propias, o cuántas no calzan.
   - Si el exportador no informó `SERVER:` o `BASE_UTC_OFFSET:`, no hay herencia posible.
5. **Estación de horario (RF-15c, N39):** mientras `BROKER_DST_RULE` no esté verificado, en 1H y en las TF menores
   solo se fusionan las velas de la misma estación que el momento del export. 4H, 12H, 1D y 1W se fusionan
   completas, porque el desfase de 1 h es despreciable para EMA y ADX, y los modelos de `P2_LOG_MODELS` (hoy D)
   necesitan 800 velas.
6. **Fusión:** es una unión por `time`. Si un `time` ya existe, se conserva la vela del banco. Se escribe a un archivo
   temporal y después `os.replace`, archivo por archivo.
7. **Chequeo final:** los `time` del banco nuevo contienen todos los del banco viejo, y sus velas son idénticas. Si
   no, se restaura desde el temporal anterior y se informa un error.
8. Se actualiza `status.json` y se libera el candado.

**Import inicial (RF-2c):** la misma verificación por referencias se aplica a `MT5Exports/{SYMBOL}/*.csv`, sin copiar
`XAUUSD/5M.csv`. El filtro de estación del paso 5 se aplica a **todas** las TF, no solo a 1H y menores (N44): los CSV
legacy tienen un solo desfase para todo el año, así que sus velas de 4H o más de la otra estación quedarían duplicadas,
corridas 1 h, junto a las del primer export nuevo.

**Limpieza única (N44):** `tools/dedup_candle_bank.py` busca en 4H, 12H, 1D y 1W los pares de velas con el mismo
`open`, `high`, `low` y `close` a exactamente 1 h, y quita la de 1 h antes. En esas TF dos velas reales nunca están a
1 h, así que un par así solo puede ser la misma vela con dos etiquetas. Por defecto solo informa; con `--apply` toma el
candado del símbolo, copia los archivos afectados a `.data/archives/`, reescribe cada CSV de forma atómica y verifica
que no quede ningún par y que el resto de las velas no cambió. No toca 1H ni las TF menores.

### 3.9 Conversión con horario de verano en el exportador (RF-15b, N34)

1. `offset(vela) = offset_base + (1 si la fecha de la vela cae en el período de horario de verano de la regla)`.
2. `offset_base` sale del offset detectado del último tick (`export_p2_ohlc.py:171-204`), menos 1 si "ahora" está en
   horario de verano.
3. **Reglas:**
   - `us`: del segundo domingo de marzo al primer domingo de noviembre;
   - `eu`: del último domingo de marzo al último domingo de octubre;
   - `none`: un solo offset, como hoy.
4. Las velas que caen dentro de la hora del cambio se descartan y se informa cuántas fueron.

### 3.10 S1 y S4 (RF-21, N35)

- **S1:** sobre los análisis no retroactivos con primer toque (VALIDATION o INVALIDATION), la proporción de
  VALIDATION. Se informan n y los excluidos.
- **S4:** lo mismo, contando solo los toques con `h_toque <= 48`. Se informa cuántos quedan fuera.
- Las dos se calculan para todos los análisis y para los direccionales (Bullish/Bearish).
- La función recibe una lista de resultados del resolvedor; no sabe de DBs ni de velas.

### 3.11 Plan del backfill (RF-11 a RF-11e)

Para cada análisis de cada cuenta de `REAL_ACCOUNTS` y cada campo propuesto:

| Valor actual | Propuesta | Resultado |
|---|---|---|
| NULL (o `"Open"` con `real_bias_b` NULL, N9) | Sí | `fill` |
| Igual a la propuesta | — | `unchanged` |
| Distinto de la propuesta | — | `conflict` |

`resolution_time` tiene una regla propia (RF-11c):
- si `audit_registration_time` está vacío → `legacy_move` (la hora vieja pasa a `audit_registration_time`) y `fill`
  del `resolution_time` de las velas, o NULL con motivo;
- si no → se compara como cualquier otro campo.

La salida es un plan en memoria. Con `--apply` y las puertas de R9 se aplica en una transacción por cuenta, y cada
cambio se inserta en `backfill_history`.

---

## 4. Contrato de interfaz

| Entrada | Salida | Código de salida o error |
|---|---|---|
| `python cli/main.py resolution-report [--output-dir DIR] [--account ID ...]` | `resolution_report_<YYYYmmdd_HHMM>.md` (inglés) y resumen en consola. Nunca escribe en la DB. Sin `--output-dir`, el archivo va a `reports/` dentro de `ACCOUNTS_DATA_DIR` (`.data/reports/`): tiene datos del journal y `.data/` está fuera de git (O3). `--account` se repite para varias cuentas; sin él, todas las de `REAL_ACCOUNTS` | 0 si termina bien. 1 si una cuenta no existe, si falta una DB de `REAL_ACCOUNTS` o el banco: en ese caso no escribe el reporte |
| `python cli/main.py backfill [--account ID ...]` | Vista previa estilo git-graph (dry-run). No escribe nada | 0 |
| `python cli/main.py backfill --apply [--account ID ...]` | 1) ensayo sobre copias temporales; 2) chequeo de backup; 3) aceptación de conflictos uno por uno; 4) confirmación escribiendo `APPLY`; 5) escritura con historial | 0 si aplica. 3 si falla el ensayo (E6). 4 si no hay backup de las últimas 24 h. 5 si el usuario cancela |
| `python cli/main.py candles import-legacy` | Import inicial del banco (RF-2c) | 0. 2 con `clock_unverified` o `clock_misaligned` por símbolo, informado |
| `python cli/main.py candles export --symbol XAUUSD [--wait N]` | Export manual con fusión y verificación | 0 si fusiona. 2 si no se verifica. 6 con `export_failed` |
| `python cli/main.py candles status` | Tabla con el estado del banco por símbolo | 0 |
| `python tools/candle_sync.py --symbol S [--wait-seconds N]` | El proceso que usan los disparos automáticos. Escribe `status.json` | Igual que `candles export` |
| `python cli/main.py p2-model --trade-id ID [--model NAME]` | Muestra las líneas registradas del análisis, una por modelo. Las que faltan (o `--model` con un modelo del registro fuera de la lista) se calculan a pedido y se muestran como `not logged`, sin escribirlas (RF-12d) | 0. 1 si no existe el análisis o el modelo |
| Exportador de Windows: nuevos `--timeframes`, `--dst-rule {us,eu,none}` y `--out-dir` único | CSV en `_incoming/{SYMBOL}/{run_id}/` | Igual que hoy (`export_p2_ohlc.py:325-327`) |
| **Wizard de Efficiency** | Los prompts de siempre, con el valor propuesto como default y la marca `(auto)`. Prompt nuevo al final: `Resolution Time (YYYY-MM-DD HH:MM) (auto: 2026-08-21 09:15, ±1 min) >`. Enter acepta, vacío deja el campo vacío | — |
| **Wizard de Tactical** | Defaults `(auto)` en `Could hit TP?`, `MAE` y `MFE`. Si no hay propuesta, una línea con el motivo | — |
| **Avisos de una línea** (inglés) | `Mark price outside its candles (1M/5M/15M ±0.1%)`, `Still open according to candles`, `Candle export skipped: <reason>` | Nunca bloquean |

---

## 5. Decisiones técnicas

| # | Decisión | Alternativa descartada | Motivo |
|---|---|---|---|
| T1 | Banco nuevo en `.data/candle_bank/`, en el mismo formato CSV | (a) Fusionar dentro de `MT5Exports/{SYMBOL}/`. (b) Guardarlo en SQLite | (a) El `5M.csv` contaminado ya vive ahí (RF-2c pide no importarlo), el exportador lo sobrescribe, y los lectores de INV-6 recibirían datos distintos sin aviso. (b) INV-6 pide que `CsvOHLCProvider` y los cuadernos lo lean sin cambios |
| T2 | El exportador escribe en un directorio nuevo por corrida (`_incoming/{run_id}/`) | Sobrescribir `{out_dir}/{TF}.csv`, como hoy | Hoy la escritura no es atómica y un fallo a mitad deja CSV mezclados (baseline H14) |
| T3 | La fusión y la verificación corren del lado de WSL, en `tools/candle_bank.py` | Hacerlas dentro del exportador de Windows | Verificar exige leer las DBs de cuenta y el banco, que viven en WSL. El exportador queda como un extractor simple |
| T4 | Solo entran al banco velas verificadas. El resolvedor confía en el banco y lee el estado en `status.json` | Calibrar el reloj en cada resolución | Sería lento y necesitaría referencias de la DB en cada consulta. Una Flight Session no tiene referencias, así que nunca podría resolver |
| T5 | Resolvedor puro en `core/`, con el acceso a datos en `tools/` | Poner la lógica en `cli/main.py` | Constitución, principio 3; además se puede testear sin simular el teclado |
| T6 | `first_touch_detail()` nueva en `core/p2_ground_truth.py` | Cambiar el retorno de `first_touch_direction` | RF-4f: sus llamadores actuales (`tools/p2_backtest.py`, `tools/edge_evaluation.py`, el cuaderno) no deben cambiar |
| T7 | Extraer `evaluate_clock_entries()` de `calibrate_clock_offset` sin cambiar su resultado | (a) Copiar el bucle. (b) Cambiar la firma de `calibrate_clock_offset` | (a) Dos copias terminan divergiendo. (b) Rompe a los llamadores. `tests/test_p2_clock.py` cuida que el resultado siga igual |
| T8 | El export automático corre como proceso aparte (`tools/candle_sync.py`), igual que el sync a Notion (`cli/main.py:1060`) | Un hilo dentro del CLI | Un hilo muere al cerrar el CLI y mezcla su salida con la de Rich |
| T9 | Candado por símbolo con archivo, PID y vencimiento de 2 h, como `tools/backup.py:134` | `fcntl.flock` | `flock` se libera solo si el proceso muere, pero es invisible para el usuario y para el lado Windows. El archivo se puede inspeccionar, y es el mismo patrón que ya usa el repo |
| T10 | Aislamiento de tests con `conftest.py`: `chdir(tmp_path)` autouse y un audit hook sobre `open`/`sqlite3.connect` que falla en `<repo>/.data/` y en `/mnt/c/` | (a) Arreglar solo el test conocido. (b) Hacer absolutas las rutas de `get_active_engine` | (a) R16 exige un test que falle ante **cualquier** violación. (b) Cambia producción, fuera de RF-16b |
| T11 | Agregar el parámetro `default` a `get_enum_choice`, `get_mandatory_float` y a un `get_optional_datetime` nuevo | Crear prompts aparte para los valores automáticos | Duplicaría la lógica de los prompts y cambiaría su orden (INV-1). InquirerPy 0.3.4 ya acepta `default` en `select` y `text` (medido) |
| T12 | "Resolution Time" es un prompt opcional al final de Efficiency | Hacerlo obligatorio | RF-7g: puede quedar vacío, con el motivo |
| T13 | `backfill_history` es una tabla en cada DB de cuenta | (a) Un archivo JSONL. (b) Una DB central | El usuario pidió historial "de cada base". Viaja con los backups de cada DB y se puede consultar con SQL |
| T14 | Las puertas de R9 las hace cumplir el comando: ensayo automático sobre una copia temporal, chequeo de que haya backup de las últimas 24 h, y confirmación escribiendo `APPLY` | Dejarlas como checklist manual | Una checklist manual es fácil de saltear. El comando falla con un código distinto en cada puerta |
| T15 | Reporte en Markdown y en inglés, más un resumen en consola | (a) Sumarlo al comando `report` que ya existe. (b) HTML | (a) El comando `report` tiene otra fuente y otro propósito. (b) Markdown se lee y se versiona fácil. Inglés por N30 |
| T16 | Constantes de reglas en `config/auto_resolution.py` y rutas de la máquina en `.env` | (a) Todo en `.env`. (b) Todo fijo en el código | (a) Las reglas (48 h, 0.5R) son decisiones del proyecto y deben quedar versionadas. (b) Las rutas cambian de una máquina a otra |
| T17 | El import inicial solo trae velas de la estación de horario actual mientras `BROKER_DST_RULE` no esté verificado, en **todas** las TF (T16 y T16b lo aplicaron solo a 1H y menores; corregido en T16c, N44) | (a) Importar todo con un solo offset. (b) Reetiquetar según una regla supuesta | (a) Las velas de invierno tendrían 1 h de error. (b) Sería inventar la regla. Los análisis de mayo a septiembre caen todos en la estación actual |
| T18 | Las propuestas del Tactical se calculan cuando se llega a cada prompt, con los valores ya cargados en `session.state` | Precalcularlas al abrir el wizard | `entry_time`, `exit_time` y los precios se cargan en el mismo wizard, antes de esos prompts |
| T19 | El banco **no** entra en `tools/backup.py` en esta spec. **Decidido por el usuario el 2026-09-27**: no es crítico por ahora, y no quiere pagar almacenamiento extra en Backblaze. Queda como pendiente en `CLAUDE.md` (sección Backup 3-2-1) | Agregarlo al backup | El riesgo (MT5 guarda poco historial de 1M) queda anotado para analizarlo en otra implementación |
| T20 | En la superposición, las velas se comparan con una tolerancia relativa de 1e-9 | Compararlas como texto exacto | El formato de los números en el CSV puede cambiar entre versiones de pandas sin que cambie el precio |
| T21 | El P2 sistemático se registra en un JSONL aparte, al que solo se le agregan líneas | Una columna en `unified_department` | D3 pide explícitamente que no vaya a la DB, y así el registro no se mezcla con los datos del operador ni con el edge |
| T22 | El registro ocurre al guardar, si hay velas, y además en un catch-up después de cada fusión del banco | Solo al guardar | Como el export es manual o va en segundo plano, al guardar casi nunca hay velas del momento. Sin catch-up, casi ningún análisis quedaría registrado |
| T23 | Los modelos registrados salen de `P2_LOG_MODELS` (nombre → fecha de alta), con una línea por análisis y modelo, la receta completa y su huella en cada línea (N42, decidido por el usuario el 2026-09-28) | Dejar `MODEL_D` fijo en `tools/p2_model_feedback.py` | Cambiar o sumar un modelo (por ejemplo H) sería editar código y spec. Con la lista, D y H se registran en paralelo sobre los mismos análisis; la fecha de alta evita evaluar H sobre los datos con los que se diseñó, y la huella impide mezclar dos recetas con el mismo nombre |
| T24 | La herencia (N43) compara el servidor, el desfase base y la regla de horario del export con el `verified_export` del donante, y exige 5 referencias propias que calcen todas | (a) Heredar sin referencias propias. (b) Exigir que el donante se haya exportado en la misma tanda | (a) No detectaría un símbolo mal mapeado ni un reloj corrido propio. (b) El desfase base y la regla ya fijan la conversión de cada vela; la hora del export no agrega nada |

**Dependencias nuevas:** ninguna (constitución, principio 1). Se usan pandas, SQLAlchemy, Rich, InquirerPy, Click y la
biblioteca estándar, que ya están en `requirements.txt` o en el entorno.

---

## 6. Estrategia de tests

**Reglas para toda la suite:**
- Se corre en la fotocopia de esta sesión, sin `.data/` ni `.env` reales, con `.data/` verificado antes de cada
  corrida y listado después (R14).
- Ningún test toca MT5, `/mnt/c` ni `.data/`: se usa `tmp_path`, `:memory:` y velas de fixture. Lo garantiza T10.

**Unidad:**
- **`core/candle_resolution.py`**, con velas sintéticas: toque de un nivel, de otro, vela doble resuelta por
  refinamiento y `ambiguous`, horizonte, `pending_candles` contra `no_history`, empalme 1M → 5M → 1H sin huecos,
  MAE/MFE, los bordes de revertido (24 h), expansión (0.5R) y sweep (1R, 48 h), `no_interval`, `zero_r`,
  `invalid_tp`, el tope de 10R, y la escalera del Mark Price.
- **`core/outcome_metrics.py`:** S1 y S4, exclusión de retroactivos con conteo, que Overlap **nunca** excluya, y el
  corte direccional.
- **`tools/candle_bank.py`:**
  - la fusión nunca pierde velas; es un test que falla si una vela desaparece o cambia;
  - un `time` duplicado conserva la vela vieja;
  - superposición que coincide o no coincide;
  - menos de 10 referencias en el rango → `clock_unverified`;
  - candado tomado → se cancela;
  - `status.json`, y el import inicial que excluye el 5M.
- **Exportador**, con el stand-in de `MetaTrader5` que ya usa `tests/test_export_p2_ohlc.py:25`: la conversión por
  vela con las reglas `us`, `eu` y `none`, la hora del cambio descartada, 5M/1M en el mapa y el directorio por
  corrida.
- **`tools/database.py`:** el shim agrega las columnas nuevas sobre una DB de archivo en `tmp_path`, `backfill_history`
  solo acepta inserciones, y `resolution_time` opcional en pydantic.
- **`evaluate_clock_entries()`** da lo mismo que `calibrate_clock_offset` antes del cambio. Se reusan los casos de
  `tests/test_p2_clock.py`.

**Integración:**
- **Red de seguridad (primero):** orden de los prompts y valores guardados del wizard de Efficiency y de la rama
  llenada del Tactical, en verde contra el código **sin modificar**.
- **Wizards con propuestas**, con mocks de los prompts: se verifica que el default llega a cada prompt, la marca
  `(auto)`, lo que se persiste (`resolution_time_source` = `candles` o `corrected`) y los motivos cuando no hay
  propuesta.
- **`flow_new_analysis`:** `analysis_start_time` al confirmar P0, sin cambiar con un `RestartFlowException`; la hora
  tipeada en retroactivos y clones [2]; la hora de elección en clones [1]; `mark_price_time` y `saved_at`; el aviso del
  Mark Price.
- **Backfill** sobre DBs de fixture en `tmp_path`: el dry-run no escribe, `--apply` escribe con historial,
  `legacy_move` solo con `audit_registration_time` vacío, correrlo dos veces no pisa nada, aceptación de conflictos, y
  los códigos de salida de las puertas.
- **Reporte** sobre DBs y velas de fixture: S1/S4, Overlap listado, retroactivos excluidos y columnas ausentes (US100
  sin migrar).
- **`candle_sync`** con un "exportador" falso, que es un script en `tmp_path`: éxito, timeout, "MT5 no disponible" y
  candado tomado.
- **Meta-test de aislamiento:** un test de prueba que escribe `.data/` en la carpeta de trabajo tiene que hacer fallar
  la suite. Se corre en un subproceso de `pytest` sobre un archivo temporal.

**Sin cobertura automática, y por qué:**
- **El export real desde MT5 y la llamada WSL → `powershell.exe` → Python de Windows:** necesitan Windows con MT5 y
  sesión abierta. Lo cubren el spike (tarea manual con resultado documentado) y las dos demos de RF-20.
- **Que `BROKER_DST_RULE` sea la regla real del broker:** lo cubren el spike y, después, la calibración con fills de
  las dos estaciones.
- **La migración y el backfill sobre las DBs reales:** los cubren las 3 puertas de R9, a mano y con tu aprobación.
- **La estética de la vista git-graph:** se testea el contenido del texto, no los colores ni el aspecto.
- **El rendimiento con bancos grandes de 1M:** se mide a mano en el spike y en la demo.

---

## 7. Orden de trabajo (base para `tasks.md`, R10)

0. **Aislamiento de tests** (RF-16, RF-16b). Lo pediste como primera tarea (R16).
1. **Red de seguridad** de los wizards (R10.1).
2. **Banco y reloj:**
   - agregados en `p2_backtest`: `TIMEFRAME_MINUTES`, `evaluate_clock_entries`;
   - `candle_bank` e import inicial;
   - exportador con 5M/1M, horario de verano y directorio por corrida;
   - **spike manual** de interoperabilidad, que fija `WINDOWS_PYTHON` y `BROKER_DST_RULE`;
   - comando `candles`.
3. **Resolvedor, métricas y reporte**, en solo lectura: `first_touch_detail`, `candle_resolution`,
   `outcome_metrics`, `auto_resolution` y `resolution-report`.
4. **Columnas nuevas y horas de `flow_new_analysis`:** shim, schema, RF-13 a RF-13e y RF-3.
5. **Propuestas en el Efficiency Audit**, con el prompt "Resolution Time".
6. **Propuestas en el Tactical Audit.**
7. **Backfill:** plan, vista, historial, puertas y aplicación tras R9.
8. **Export automático** (RF-20), solo si el spike funcionó.
9. **Registro prospectivo del P2 sistemático** (RF-12 a RF-12e): cálculo y registro por cada modelo de
   `P2_LOG_MODELS` (N42), hook al guardar, catch-up después de cada fusión (solo análisis nuevos, N40, y desde la
   fecha de alta de cada modelo), y el comando `p2-model`.
10. **Datos reales:** integrar la rama; **migración con las 3 puertas antes de usar el CLI nuevo** (N41); banco
    real; reporte; demos; backfill; y validación.

---

## 8. Cobertura RF

| RF / INV | Dónde |
|---|---|
| INV-1 | Red de seguridad (§6); T11 y T12 mantienen el orden |
| INV-2 | Todos los caminos de `auto_resolution` y `candle_sync` devuelven un motivo y nunca lanzan hacia el wizard; tests de integración |
| INV-3 | `auto_backfill`: solo `fill`, más `legacy_move` y conflictos aceptados, con historial |
| INV-4 | No se tocan los archivos de "No toques"; T6 y T7 son solo agregados |
| INV-5 | §6; `conftest.py` |
| INV-6 | T1; los CSV de `MT5Exports` quedan intactos |
| INV-7 | `flow_new_analysis` registra horas sin prompts nuevos (§1, `cli/main.py`) |
| INV-8 | Ningún módulo propone campos de juicio |
| RF-1, 1b, 1c, 1d | `tools/candle_bank.py`, §3.8, T2, T9 |
| RF-2, 2b, 2c, 2d, 2e | `tools/candle_bank.py` y `evaluate_clock_entries`, §3.8 (paso 4b para RF-2e), §2.3, §2.4, T4, T7, T20, T24 |
| RF-3, 3b | `core/candle_resolution.py` §3.7; `cli/main.py`; `tools/resolution_report.py` |
| RF-4, 4b, 4c, 4d, 4e, 4f, 4g, 4h | `core/candle_resolution.py` §3.1, §3.2, §3.4; `tools/auto_resolution.py`; T4, T6 |
| RF-5, 5b | §3.3; `core/outcome_metrics.py`; wizard |
| RF-6, 6b | `tools/resolution_report.py`, T15 |
| RF-7, 7e, 7f, 7g | `cli/main.py` (Efficiency), T11, T12; `cli/schemas/audit_efficiency.py` |
| RF-8, 8b, 8c, 8d | §3.5 |
| RF-9, 9b, 9c, 9d | §3.6, T18 |
| RF-10, 10b, 10c, 10d | §3.6; RF-10c: no se toca `session` |
| RF-11, 11b, 11c, 11d, 11e | `tools/auto_backfill.py` §3.11, T13, T14; `cli/backfill_view.py` |
| RF-12, 12b, 12c, 12d, 12e | `tools/p2_model_feedback.py` §2.5; hook en `cli/main.py`; catch-up en `tools/candle_sync.py`; `P2_LOG_MODELS` en `config/auto_resolution.py`; T21, T22, T23 |
| RF-13, 13b, 13c, 13d, 13e | `cli/main.py` (`flow_new_analysis` y el clon); `tools/database.py` |
| RF-14, 14b | `cli/main.py` (Efficiency), schema y DB |
| RF-15, 15b, 15c | Exportador §3.9; `candle_bank` §3.8 paso 5; T17 |
| RF-16, 16b | `tests/conftest.py`, T10 |
| RF-17 | `core/outcome_metrics.py` |
| RF-18 | `backfill_history` §2.2, T13 |
| RF-19 | `cli/backfill_view.py` |
| RF-20, 20b, 20c, 20d, 20e, 20f | `tools/candle_sync.py`, T8; disparos en `cli/main.py` |
| RF-21 | `core/outcome_metrics.py` y `docs/criterios-de-acierto.md` |

Los 70 RF y los 8 INV aparecen al menos una vez en esta tabla.
