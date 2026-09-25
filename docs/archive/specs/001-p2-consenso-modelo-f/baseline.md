# Baseline B1 — 001 P2 consenso modelo F

- **Fecha:** 2026-09-23
- **Ruta sdd-engine:** brownfield-delta (fase B1, solo lectura)
- **Commit base:** `df71147`, sobre `68bc9bc` (rama `claude/consenso-modelo-f-471f7d`, fast-forward de `feature/tactical-tier-gate-df`)
- **Suite de referencia (INV-6):** `conda run -n blast_master pytest -q` → **418 passed, 1 skipped**. El skip es `tests/test_pnl_calculator.py:226`: el reporte MT5 real `ReportHistory-87050257.xlsx` no está en `tools/migration_data/`, que está en `.gitignore`. Se ejecutó en esta sesión.
- **Fuente del pedido:** `docs/prompts/2026-09-23-p2-consenso-modelo-f.md`, más la adenda `docs/prompts/2026-09-23-p2-consenso-modelo-f-adenda-1.md` (tiene prioridad donde se contradicen)
- **Corrección del 2026-09-23 (después de la adenda):** §2.8 decía que el snapshot era point-in-time, y era falso. Ver H19. También se resolvió H14 y se agregaron H20 y H21

**Convenciones del documento**
- Toda afirmación cita `archivo:línea`, o el comando que la midió.
- `[NO VERIFICADO]` marca lo que se dedujo leyendo el código y no se ejecutó.
- "Medido" significa que se ejecutó en esta sesión, en solo lectura (`sqlite3 ... ?mode=ro`, `ls`, `wc`).

---

## 1. Superficie

Superficie = archivos que el feature previsiblemente toca, más sus consumidores directos. Ningún archivo fuera de esta tabla puede modificarse sin detenerse y reportar (regla B4).

| Archivo | Rol en el feature | ¿Se toca? |
|---|---|---|
| `cli/main.py` | `flow_new_analysis` (captura de P2, cálculo del edge, guardado), los llamadores de retroactivo y clon, y la reparación (`recalculate_unified_metrics`) | Sí |
| `cli/schemas/efficiency.py` | `Direction`, `Strength`, `score` y `to_db_layers` | Probablemente no; lo decide el plan |
| `cli/schemas/tactical.py` | Probabilidades y `tactical_classification` derivados de `calc_edge` | No; consumidor |
| `tools/database.py` | ORM, `init_db` (`create_all` y el shim `ALTER TABLE`) | Sí, solo aditivo |
| `tools/p2_backtest.py` | `MODEL_F`, `MODEL_A`, snapshot, reescalado y reloj: fuente de los modelos (R7) | Solo reuso. Tocarlo requiere justificación en el plan |
| `core/p2_ground_truth.py`, `core/stats_tests.py` | Ground truth (D6) y test de signo (D5) para el reporte | No; reuso |
| `tools/edge_evaluation.py` | Referencia de lectura de solo lectura a prueba de columnas faltantes | No; referencia |
| `windows_export/export_p2_ohlc.py` | Exportador que dispara el CLI (D3) | Posible (R8); lo decide el plan |
| `config/contract_specs.py` | `SYMBOL_ALIASES`: no sirve como mapa MT5 | No |
| `.env.template` | Variables de configuración nuevas | Posible |
| `tests/test_flow_new_analysis.py`, `tests/test_repair_analysis_audits.py` | Red de seguridad (B3) | Sí, se agregan tests |
| **No tocar** (prompt, §Restricciones) | `core/math_engine.py`; `determine_market_bias` y su umbral ±0.26; los gates Tier D/F, emocional y Stop Deviation; `tools/notion_sync.py`; `core/edge_analysis.py`; P0, P1, P3 y P4 | No |

---

## 2. Comportamiento actual observado

### 2.1 `flow_new_analysis` (`cli/main.py:2492-3086`)

**Firma e inicio**
- La firma es `flow_new_analysis(backdated_timestamp=None, cloned_state: dict = None)` (`cli/main.py:2492`).
- Genera `trade_id = uuid4()` (`:2493`).
- Crea una `AnalysisSession` (`:2496`). Si hay `cloned_state`, hace `session.state.update(cloned_state)` (`:2497-2498`).

**Caché de `AnalysisSession.prompt`** (`cli/main.py:210-253`)
- Si la clave ya está en `session.state`, no pregunta: imprime `Loaded {key}` y devuelve el valor guardado (`:211-217`).
- Si no, ejecuta la función de prompt y guarda el resultado en `session.state[key]` (`:236-239`).
- `GoBackException` borra la clave previa y lanza `RestartFlowException` (`:240-253`). El `while True` externo la captura (`:3075-3076`) y vuelve a recorrer los prompts, reusando la caché.

**Orden real de captura (Step 1 y Step 2).** P2 se carga temprano, antes de P3, P1 y P4:
1. `asset` (`:2521`)
2. P0: tesis, dirección y fuerza (`:2531-2533`)
3. **P2: `p2_thesis` (`:2545`, plantilla por defecto "20EMA/200EMA/EMA/ADX/ATR/RSI/DIVERGENCE" en `:2535-2543`), `p2_dir` (`:2546`) y `p2_str` (`:2547`)**
4. P3 (`:2549-2551`)
5. Fractales P1 (`:2554-2558`), dirección y fuerza de P1, `p1_tf`, `p1_type` y nodos (`:2561-2566`)
6. P4 (`:2568-2571`)

**Sitio 1 del edge: vista previa**
- Justo después de P4 calcula `preview_x0..x4 = get_dir_val × get_str_val` (`:2575-2579`).
- Luego `calculate_edge_score` (`:2581`), `determine_market_bias` (`:2582`) y `calculate_probabilities` (`:2583`).
- Muestra el panel "Edge Bias & Probabilities Preview" (`:2587-2605`).
- Todo eso ocurre **antes** de pedir `efficiency_timeframe` (`:2607`), `mark_price_raw` (`:2615`, opcional), `evp_raw` y `si_raw` (`:2617-2618`, opcionales), `edge_desc` (`:2661`), `bias_a` (`:2663`) y `tact_class` (`:2664`).

**Sitio 2 del edge: Step 3, revisión**
- Es un bucle `while True` (`:2667`) que relee `session.state` en cada vuelta (`:2669-2698`).
- Recalcula `x0..x4` (`:2701-2705`), `i_cd = calculate_edge_score(...)` (`:2707`) y `market_bias` (`:2708`).
- Construye `EfficiencyAnalysis(p2_direction=p2_dir, p2_strength=p2_str, ..., Calc_edge=i_cd, Market_Bias=market_bias)` (`:2712-2717`) y `TacticalAnalysis(..., calc_edge=i_cd)` (`:2718-2724`).
- El panel muestra P2 como `DIRECCIÓN | FUERZA` y la tesis (`:2749-2761`), además de `I_CD` y las probabilidades de `tactical` (`:2805-2814`).
- "Created" muestra `backdated_timestamp` o, si no hay, **`datetime.datetime.now()`** (`:2826`), que es la hora local del sistema y no GT. `[NO VERIFICADO]` qué zona tiene configurada la WSL.

**Guardado** ("Confirm & Save", `:2877-2946`), en una sola transacción:
- `UnifiedDepartment` con `market_bias`, `calc_edge=i_cd`, `long/short/no_trade_prob` de `tactical`, e `is_backdated=backdated_timestamp is not None` (`:2883-2900`).
- `mark_price`, `edge_validation_price` y `structural_invalidation` desde texto opcional. Si el decimal es inválido, quedan en `None` (`:2902-2916`).
- Retroactivo: `created_at = updated_at = backdated_timestamp` en `UnifiedDepartment` y en `EfficiencyAudit` (`:2917-2925`). En los demás casos, `created_at` toma el default del ORM, GT naive (`tools/database.py:100`).
- Capas: `for layer_dict in efficiency.to_db_layers()` (P0, P2, P3, departamento `EFFICIENCY`) y `tactical.to_db_layers()` (P4, P1, departamento `TACTICAL`) → filas `AnalysisLayer` (`:2928-2938`).
- `commit` (`:2940`). Ante una excepción: `rollback`, mensaje y `return`, y el análisis **no** se guarda (`:2942-2946`).

**Después del guardado**
- Pregunta "¿Deseas alimentar un Tactical Audit ahora…?" (`:2948-2957`).
- Si la respuesta es sí, pasa `preselected_payload` con `calc_edge=i_cd` y `Market_Bias` a `flow_pending_audits` (`:2959-2972`).

**"Edit a Field"** (`:2980-3074`)
- Las opciones de P2 son `p2_thesis`, `p2_dir` y `p2_str` (`:2995-2998`).
- Dirección y fuerza se editan con `get_enum_choice(..., Direction/Strength)` (`:3047-3050`) y se guardan en `session.state[field] = new_val` (`:3068`).
- El `continue` implícito vuelve a la parte superior del Step 3, que recalcula el edge (`:2667`, `:2701-2708`).
- **El `asset` también es editable** (`:3029-3044`), incluida la opción "Add Custom Asset".

**Salidas por excepción:** `RestartFlowException` reinicia el flujo; `PauseAuditException`, `GoBackException` y `ExitToMainMenuException` hacen `return` sin guardar (`:3075-3086`).

### 2.2 Llamadores de `flow_new_analysis`

| Llamador | Ubicación | Qué pasa |
|---|---|---|
| Menú principal (análisis nuevo) | `cli/main.py:1048` | `flow_new_analysis()` sin argumentos |
| Retroactivo | `cli/main.py:1087-1090` | `backdated_ts = get_mandatory_datetime("Enter Target Timestamp")` (`:840-866`) es un `datetime` naive parseado con `"%Y-%m-%d %H:%M"`, **sin conversión de zona**. `[NO VERIFICADO]` que el operador lo tipee en GT |
| Clon | `cli/main.py:2292-2332` | `macro_state` copia `asset`, `edge_desc`, `efficiency_timeframe`, `bias_a` y **`p2_dir`/`p2_str`/`p2_thesis` desde la fila `analysis_layer` P2 del original** (`:2306-2320`, `layers_dict` construido en `:1624-1637`), igual que P0 y P3. Con eso, el prompt de P2 **se salta** (caché, §2.1) y el operador no carga P2 a ciegas. El timestamp puede ser la hora actual (`backdated_ts=None`) o uno retroactivo (`:2295-2304`) |

### 2.3 Helpers del edge (`cli/main.py`)

- `determine_market_bias(i_cd)`: `|i_cd| < 0.26` devuelve `"Choppy / Neutral"`, `≥ 0.26` devuelve `"Bullish"` y el resto `"Bearish"` (`:681-687`).
- `get_dir_val`: LONG=1, SHORT=−1 y cualquier otro valor 0 (`:689-693`).
- `get_str_val`: STRONG=2, MID=1 y cualquier otro valor 0 (`:695-698`). Compara por enum y funciona igual con strings, porque `Direction` y `Strength` son `str+Enum` (`cli/schemas/efficiency.py:5-13`).
- `calculate_edge_score(x0..x4) = (0.30·x0 + 0.25·x1 + 0.15·x2 + 0.10·x3 + 0.20·x4) / 2` (`core/math_engine.py:61-63`). **P2 pesa 0.075 por punto**, con x2 ∈ {−2..2}.
- `calculate_probabilities(calc_edge)` lee `ICD_NO_TRADE_MAX`, `ICD_NO_TRADE_MIN` e `ICD_EDGE_CAP` del entorno (`core/math_engine.py:18-25`).
- **Efecto colateral:** `TacticalAnalysis.calculate_derived_tactics` fuerza `tactical_classification = NA` si `calc_edge == 0.0` (`cli/schemas/tactical.py:57-60`). Si P2 efectivo = 0 deja el edge total exactamente en 0.0, la clasificación táctica elegida por el operador se reemplaza por NA. `[NO VERIFICADO]` con un caso real.

### 2.4 Reparación: `recalculate_unified_metrics` (Sitio 3)

- Está **definida en `cli/main.py:5143`, anidada en `flow_repair_analysis_audits` (`:4826`)**. El prompt tiene los dos números invertidos.
- **Carga:** toma P0..P4 de `record.analysis_layers` (`:4904-4909`) y arma el diccionario `workspace` (`:5018-...`). Si falta la capa, usa `"Neutral"`/`"Weak"`.
- **Recálculo:** solo al editar una dirección o una fuerza (`:5347-5352`) recalcula `calc_edge`, `market_bias` y las probabilidades (`:5143-5163`), con la misma fórmula (`get_dir_val`, `get_str_val`, `calculate_edge_score`).
- **Guardado:** `UPDATE unified_department` (`:5376-5406`), y luego **reescribe las 5 filas de `analysis_layer`** (`UPDATE ... direction, strength, thesis, score`, o `INSERT` si falta) desde `workspace` (`:5408-5438`).
- **Consecuencia:** cualquier análisis que pase por la reparación queda con un `calc_edge` coherente con lo que diga `analysis_layer` P2. `[NO VERIFICADO]` en ejecución: si el guardado tras editar solo la tesis conserva `calc_edge` de la DB; por lectura, sí, porque `recalculate_unified_metrics` solo se invoca en `:5349`/`:5352`.

### 2.5 Schemas

- `AnalysisLayerInput` tiene `layer_name`, `direction`, `strength`, `score: int = 0` y `thesis` (`cli/schemas/efficiency.py:15-20`).
- `EfficiencyAnalysis.to_db_layers()` persiste `score = peso_dirección × peso_fuerza`, con LONG=1, SHORT=−1, NEUTRAL=0 y STRONG=2, MID=1, **WEAK=0** (`cli/schemas/efficiency.py:59-84`, P2 en `:76-79`). Por eso "Long + Weak" da score 0.
- `EfficiencyAnalysis` calcula `Long_prob`/`Short_prob` con otra fórmula (`:40-57`), pero **no se persisten**: lo que se guarda son las de `TacticalAnalysis` (`cli/main.py:2896-2898`).

### 2.6 Base de datos (`tools/database.py`)

- `AnalysisLayer` (`:80-92`): `id` UUID, `trade_id` FK con `ondelete="CASCADE"`, `department`, `layer_name`, `direction`, `strength`, `score` y `thesis`, todas nullable salvo PK y FK. **No hay columna para un "P2 original" separado del P2 persistido.**
- `UnifiedDepartment` (`:94-125`):
  - `asset` es un string libre (`:99`);
  - `created_at` con default GT naive, UTC−6 (`:100`);
  - `is_backdated` (`:118`);
  - `edge_validation_price`, `structural_invalidation` y `mark_price` nullable (`:120-122`).
- `init_db(db_url)` (`:262`):
  - `Base.metadata.create_all(engine)` (`:275`) **crea cualquier tabla nueva del ORM en la DB que se abra**;
  - el shim de `ALTER TABLE ADD COLUMN` es idempotente y va columna por columna (desde `:277`).
- **El CLI llama a `init_db` cada vez que abre una DB:** al arrancar (`cli/main.py:285`) y al activar o cambiar de sesión (`:940`, `:990`). Si una tabla o columna nueva entra al ORM, la migración se aplica sola **la primera vez que el CLI abra cada DB real**, sin un paso explícito. Deducido de `:275` + `:285/940/990`, `[NO VERIFICADO]` en ejecución. Esto afecta cómo se implementan las puertas de R12.
- `get_records_by_state` (`:567`) incluye `analysis_layers` en el payload (`:593-599`).

### 2.7 Resolución de cuenta y activo

- `get_active_engine()` (`cli/main.py:255-286`): con sesión activa devuelve `ACTIVE_ENGINE`. Si no, abre `sessions["001"]["db_name"]` de `.data/flight_sessions.json` con una **ruta relativa** `sqlite:///.data/...` (`:285`).
- `FlightSessionManager.create_session` nombra la DB `flight_account_{idx}_{nickname}.db` (`:325-337`). `delete_session` borra el archivo (`:348-357`).
- El activo de cada análisis lo elige el operador de `get_assets(engine)` (`:2503-2521`), es decir, de `asset_config`. **No hay relación entre cuenta y símbolo en el código:** el símbolo sale de `unified_department.asset`.
- **Medido** (lectura `mode=ro` de `.data/flight_sessions.json` y de las 4 DBs):

  | Sesión | Nombre | DB | `asset` | Análisis | Rango `created_at` |
  |---|---|---|---|---|---|
  | 000 | XAUUSDT.P | `flight_account_001_xauusd.db` | `XAUUSDT.P` | 81 | 2026-05-18 → 2026-09-16 |
  | 001 | US500 | `flight_account_000_us500.db` | `US500` | 3 | 2026-09-17 → 2026-09-22 |
  | 002 | BTCUSDT.P | `flight_account_002_btcusdtp.db` | `BTCUSDT.P` | 24 | 2026-07-02 → 2026-09-01 |
  | 003 | US100 | `flight_account_003_us100.db` | `US100` | 8 | 2026-08-11 → 2026-09-03 |

  - **Hay 4 cuentas reales, no 3.** US500 es una cuenta real con 3 análisis y **no figura** en `{{MT5_SYMBOL_MAP}}`. Sin un cambio de configuración, todo análisis de esa cuenta caerá en E5 (`sin_simbolo_mt5`).
  - La sesión "001" apunta a la DB de US500, así que la DB que abre `get_active_engine()` sin sesión activa es la de US500, no la de XAUUSD. Esto contradice lo que CLAUDE.md dice de `:251`. Se registra, no se toca.

### 2.8 Modelos P2 (`tools/p2_backtest.py`)

**Registro de modelos**
- `ModelSpec(name, label, timeframes, weights, ema_chain, use_di, description)` es inmutable, `frozen=True` (`:124-138`).
- `MODEL_A`: 1W..1H, pesos 0.30/0.25/0.20/0.15/0.10, EMA (20, 200), `use_di=False` (`:141-150`).
- `MODEL_F`: 1W..1H, pesos = los de D sin 30M reescalados a 1, EMA (20, 100, 200), `use_di=True` (`:199-214`).
- `MODELS_BY_NAME` (`:216-217`). Hay asserts de que los pesos suman 1 (`:219-221`).

**Snapshot: NO es point-in-time (corregido, ver H19)**
- `CsvOHLCProvider.get_indicator_snapshot(tf, as_of)` filtra `df["time"] < as_of` (`:412`), y `get_past_closes` hace lo mismo (`:386-391`).
- `time` es la hora de **apertura** de la vela: convención de MT5, y el CSV del exportador la conserva (`export_p2_ohlc.py:271-273`, más el fixture `tests/fixtures/p2_ohlc/1H.csv`).
- **La vela en formación en el ancla entra con su cierre, máximo y mínimo finales**, que ocurren después del ancla. Es look-ahead. En 1W, eso es ver desde un miércoles el cierre del viernes.
- El comentario "doble candado point-in-time" (`:410-411`) es incorrecto.
- Calcula EMA 20, 100 y 200, ADX14 y ±DI (`:417-435`).
- Devuelve `None` si no existe el CSV (`:407-408`) o si no hay velas previas (`:414-415`).
- El proveedor **cachea cada CSV en memoria** (`_load_tf`, `:364-384`). Un proveedor creado antes de un re-export no ve los datos nuevos. `[NO VERIFICADO]`, deducido.

**Score y reescalado**
- `compute_score_p2_sistematico(snapshots, model)` devuelve `(Σ peso_TF × bias_TF × fuerza_ADX, False)`.
- Devuelve **`(None, True)`** si falta un TF, si un TF tiene `bars_available < MIN_BARS_PER_TF = 800`, o si falta un indicador que el modelo pide (`:518-555`, constante en `:90`).
- `rescale_p2_sistematico(score) = clamp(round_half_away_from_zero(score × 2), −2, 2)` y devuelve `None` si el score es `None` (`:583-598`). Deja el score en la escala del score discrecional, {−2..2}.

**Traducción de bias y evaluación**
- `BIAS_TO_DIRECTION` (`:607-611`) y `evaluate_match` (`:633-656`): Choppy cuenta como abstención, no como fallo.

**Filas del backtest: `assemble_p2_systematic_rows`** (`:755-947`)
- Hace `select(UnifiedDepartment)` con la **entidad ORM completa** (`:795`). Si se agregan columnas a `UnifiedDepartment` en el ORM, esta función falla con "no such column" sobre cualquier DB no migrada, que es el modo de falla de US100 del 2026-09-22 (CLAUDE.md, "Auditoría"). `[NO VERIFICADO]`, deducido.
- **Ancla del backtest:** `resolve_anchor` usa el `entry_time` más temprano de `tactical_audit` y, si no hay, `created_at` (`:666-695`).
- Con `include_no_execution=True`, un análisis sin ejecución se ancla en `created_at` con `mark_price` como precio de referencia. Los retroactivos sin ejecución se excluyen (`:809-839`).

**Reloj: `calibrate_clock_offset(session, provider)`** (`:993-1046`)
- Necesita una **sesión sobre una DB con fills** y `mark_price`.
- Con menos de `CLOCK_MIN_ENTRIES = 10` puntos de referencia devuelve `aligned=None`, "Sin calibrar" (`:962`, `:1028-1029`).
- `aligned=False` si el desplazamiento 0 queda más de `0.20` por debajo del mejor desplazamiento (`:963`, `:1044-1045`).
- Una Flight Session nueva, con la DB vacía, **siempre** da `aligned=None`. `[NO VERIFICADO]`, deducido de `:1028`.
- Los `mark_price` de análisis **retroactivos no cuentan** (`is_backdated.isnot(True)`, `:1025`). Un retroactivo solo suma si tiene un fill con `entry_time` y `entry_price > 0`, porque los fills no se filtran por retroactividad (`:1010-1016`).
- **Medido** (`mode=ro`), fills + mark_price no retroactivos: XAUUSD 35 + 70 = 105; BTC 4 + 15 = 19; US100 1 + 8 = 9; US500 0 + 2 = 2.

**Import de `cli.main`**
- `tools/p2_backtest.py:70-73` hace `from cli.main import determine_market_bias` a nivel de módulo, y el comentario reconoce que dispara efectos de módulo.
- **Riesgo 1, import circular:** si `cli/main.py` importa `tools.p2_backtest` a nivel de módulo, `cli.main` todavía está a medio inicializar cuando `p2_backtest` le pide `determine_market_bias`, que se define recién en `:681`.
- **Riesgo 2, doble import:** el CLI corre como script (`python cli/main.py`, `if __name__ == "__main__": cli()` al final del archivo), así que el módulo es `__main__`, no `cli.main`. Cualquier import de `p2_backtest` en tiempo de ejecución volvería a importar `cli/main.py` como `cli.main` y reejecutaría su código de módulo: `CLIState()`, el registro del grupo Click, etc.
- Los dos riesgos están `[NO VERIFICADO]`, deducidos. Tiene que resolverlos `plan.md`.

### 2.9 Ground truth y estadística

- `infer_thesis_direction(entry, evp, si)`: da "long" o "short" según de qué lado del precio de referencia estén los niveles, y `None` si ambos quedan del mismo lado (`core/p2_ground_truth.py:33-50`).
- `first_touch_direction(...)`: el primer nivel tocado sobre el path posterior. Si una vela toca los dos niveles, el resultado es incompleto (`core/p2_ground_truth.py:52-...`). Path: 1H con tope de 2160 velas (`tools/p2_backtest.py:97-98`, `get_forward_path` en `:437-445`, estrictamente posterior al ancla). Esto es lo que fijan las decisiones A/B/C de D6.
- `binomial_test_two_sided` (`core/stats_tests.py:30`) y `mcnemar_exact(b, c)` = binomial de dos colas sobre los discordantes (`:47-55`). `net_score` = aciertos − fallos, ignorando los 0 (`:154-163`).
- **Así se hizo el test F contra A del cuaderno** (`jupyter/p2_edge_evaluation.ipynb`):
  - `val` = +1 acierto, −1 fallo y 0 abstención (celda 32);
  - `d = val(F) − val(A)`, `up = #(d > 0)` y `dn = #(d < 0)`;
  - p = `binomial_test_two_sided(min(up, dn), up + dn)`, de **dos colas** (celda 51).
  - La predicción de P2 de un modelo es `sign(rescale(compute_score(snaps, model)))` (celda 28).
  - El universo son los análisis de XAUUSD y BTC con ground truth resuelto (celda 25).
- **Diferencia de ancla:** las cifras de referencia (F 30/42, consenso 20/24) se midieron con `r.timestamp_entry` de `assemble_p2_systematic_rows`, que es la **hora de la primera ejecución** cuando existe (celdas 25 y 28). El feature ancla en la hora del análisis (R6). El propio cuaderno midió F a la hora del análisis y lo resume como "un par de puntos menos" (celda 57, veredicto en celda 59).

### 2.10 Exportador (`windows_export/export_p2_ohlc.py`)

**Ejecución**
- Es standalone y corre con el Python de Windows. `import MetaTrader5` falla a propósito en Linux (`:69-71`).
- Por defecto `--symbol` es `XAUUSD` (`:75`, `:300-304`).
- `--out-dir`, `--min-anchor` y `--max-anchor` son **obligatorios**, con formato `"%Y-%m-%d %H:%M:%S"` en GT naive (`:305-316`, parseo en `:339-340`).
- `--server-utc-offset` es opcional (`:317-321`).

**Rango, reloj y salida**
- Rango por TF: desde `compute_backward_start(min_anchor, tf)` = `min_anchor − 800 velas × 2.0 − 7 días` (`:220-238`) hasta `max(max_anchor, ahora_GT)` (`:341-342`).
- Reloj: la hora del servidor pasa a UTC y luego a GT naive (`:150-156`, `:130-138`, `:271-273`). Excluye la vela en formación (`:207-217`, `:272`). **El CSV queda en GT naive**, igual que `created_at` y `entry_time`.
- Offset: lo detecta del último tick y **falla si el tick tiene más de 15 min de antigüedad**, es decir, con el mercado cerrado (`:171-204`).
- **Salida:** exporta los 7 TF en secuencia, 1W → 15M (`:345-355`). **Cada TF sobrescribe `{out_dir}/{TF}.csv` apenas termina** (`:285-287`). Si un TF posterior falla, el directorio queda con algunos CSV nuevos y otros viejos. `[NO VERIFICADO]` en ejecución, deducido del orden. Esto viola R8 si se usa tal cual.

**Errores y código de salida**
- `mt5.initialize()` fallido → `return 1` (`:326-328`).
- Los demás errores (`copy_rates_range` devuelve `None` o 0 velas, offset fuera de rango) son `RuntimeError` sin capturar. Con eso, el proceso sale con código ≠ 0 y traceback. `[NO VERIFICADO]`: comportamiento estándar de Python.
- Si un TF trae menos de 800 velas, solo imprime un **aviso** en stderr, no un error (`:276-283`).

### 2.11 Configuración y datos de velas (medido)

- `.env` real (solo nombres de clave): `P2_SYSTEMATIC_OHLC_DIR=/mnt/c/Users/jcifu/MT5Exports/XAUUSD`. Apunta a **una sola** carpeta de símbolo. `load_ohlc_provider_from_csv()` la usa si no se le pasa `base_dir` (`tools/p2_backtest.py:448-465`).
- `.env.template:21-28` documenta `P2_SYSTEMATIC_OHLC_DIR` con un valor placeholder.
- `config/contract_specs.py:52-56`: `SYMBOL_ALIASES` mapea `asset` a la **clave de contrato**, con `US100 → NAS100` en `:55`. El símbolo MT5 de US100 es `USTEC`, que es el nombre de la carpeta exportada. Hace falta un mapa propio.
- `/mnt/c/Users/jcifu/MT5Exports/` contiene `XAUUSD/`, `BTCUSD/`, `USTEC/` y `US100/` (este último vacío). Además, `XAUUSD/5M.csv` es del 2026-09-22 10:50, anterior a la corrección del reloj (+3h), y ningún modelo lo usa.
- **Cobertura actual de cada CSV** (referencia de INV-5; filas excluyendo el encabezado; primera → última vela, GT naive):

  | Símbolo | 1W | 1D | 12H | 4H | 1H | 30M | 15M |
  |---|---|---|---|---|---|---|---|
  | XAUUSD | 1483 · 1998-04-18 → 2026-09-12 | 1224 · 2021-12-26 → 2026-09-21 | 1320 · 2024-03-03 → 2026-09-22 | 1700 · 2025-08-17 → 2026-09-22 | 3250 · 2026-03-05 → 2026-09-22 17:00 | 5470 · 2026-04-08 → | 9793 · 2026-04-26 → |
  | BTCUSD | 808 · 2011-03-19 → | 1655 · 2022-03-12 → | 1710 · 2024-05-20 → | 1930 · 2025-11-04 → | 2915 · 2026-05-23 → 2026-09-22 17:00 | 4217 · 2026-06-25 → | 6876 · 2026-07-12 → |
  | USTEC | **737** · 2012-08-04 → | 1165 · 2022-03-20 → | 1204 · 2024-05-26 → | 1334 · 2025-11-10 → | 1875 · 2026-05-31 → 2026-09-22 17:00 | 2678 · 2026-07-02 → | 4318 · 2026-07-19 → |

  - Todas terminan el 2026-09-22 entre las 15:00 y las 18:15 GT.
  - **USTEC tiene 737 velas 1W en total, menos que las 800 que exige cualquier modelo.** Con la historia disponible, todo análisis de US100 caerá en E3 (`historia_insuficiente:1W`), tanto para F como para A.
- **Interop (medido):** `command -v powershell.exe` → `/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe`. No se verificó un export lanzado desde WSL: queda para el spike de R10.

### 2.12 Entorno de ejecución de este worktree

- El worktree **no tiene `.env` ni `.data/`**, porque los dos están en `.gitignore`. Verificado con `ls -a` en la raíz del worktree.
- `get_active_engine()` y `init_db()` usan rutas relativas `.data/...` (`cli/main.py:285`, `tools/database.py:264-265`). Correr el CLI desde el worktree crearía un `.data/` vacío ahí mismo. `[NO VERIFICADO]`, deducido.
- El checkout principal tiene cambios sin commitear en `cli/ui_manager.py`: se agregó `active_db_name` en `CLIState`, visto con `git diff` en el checkout principal. Tocar `cli/ui_manager.py` en este feature chocaría con esos cambios.

---

## 3. Contratos públicos de la zona

| Contrato | Forma actual | Consumidores |
|---|---|---|
| `flow_new_analysis(backdated_timestamp=None, cloned_state: dict = None)` (`cli/main.py:2492`) | Sin retorno. Persiste `UnifiedDepartment`, `EfficiencyAudit` y 5 `AnalysisLayer` | Menú principal, `:1090`, `:2331`, tests |
| Tabla `analysis_layer`, fila P2 (`EFFICIENCY`/`P2`) | `direction` ∈ {Long, Short, Neutral}, `strength` ∈ {Strong, Mid, Weak} y `score` ∈ {−2..2} | Ver §4 |
| `unified_department.calc_edge`, `market_bias` y `*_prob` | Derivados de las 5 capas en el guardado | Notion (`tools/notion_sync.py`, payload de Efficiency), reportes, backtests, `flow_pending_audits` |
| `determine_market_bias(i_cd) -> str` (`cli/main.py:681`) | Umbral ±0.26 | 3 sitios del edge, `tools/p2_backtest.py:73`, `tools/edge_evaluation.py`, cuaderno |
| `MODEL_F`, `MODEL_A`, `compute_score_p2_sistematico`, `rescale_p2_sistematico`, `CsvOHLCProvider`, `calibrate_clock_offset`, `open_readonly_session` | Ver §2.8 | `tools/edge_evaluation.py`, cuaderno, tests `test_p2_*` |
| CLI del exportador | Ver §2.10. Salida: 7 CSV `time,open,high,low,close` en GT naive | `CsvOHLCProvider` |

---

## 4. Consumidores de la fila P2 de `analysis_layer`

Son los que se ven afectados si P2 efectivo ≠ P2 del operador. Alimentan la aclaración de "cómo se representa P2 = 0".

| Consumidor | Qué lee | Ubicación |
|---|---|---|
| Vista de detalle ("Review History") | `direction` y `strength` por capa. **Recalcula los scores y muestra la descomposición de la fórmula del edge** con pesos hardcodeados | `cli/main.py:1568-1574` (query), `:1624-1637` (`layers_dict`), `:1755-1766` (fórmula) |
| Clon | `direction`, `strength` y `thesis` de P2 → `cloned_state` | `cli/main.py:2316-2318` |
| Reparación | Carga las capas y **reescribe las 5** al guardar | `cli/main.py:4904-4909`, `:5408-5438` |
| Layout del wizard | Capas del registro mostrado en el panel lateral | `cli/ui_manager.py:598-602`. `[NO VERIFICADO]` de qué registro son |
| `get_records_by_state` | Payload con `analysis_layers` | `tools/database.py:593-599` |
| Reportes / LLM export | Pivot de `p0..p4_{direction,strength,score}` | `tools/report_data.py:54-60`, `:86` |
| `core/edge_analysis.py` (no se toca) | `analysis_layer.score` pivoteado | `core/edge_analysis.py:7` (docstring) |
| Backtests de P2 | `analysis_layer.score` de P2 como `p2_discrecional` | `tools/p2_backtest.py:841-847` |
| Cuadernos | `analysis_layer` | `jupyter/p2_edge_evaluation.ipynb`, `custom_analysis.ipynb`, `custom_analysis.tmp.ipynb`, `data_analysis.ipynb` (grep) |
| `tools/notion_sync.py` | **No lee capas** (`grep -n "p2\|P2\|layers" tools/notion_sync.py` → 0 coincidencias) | — |

---

## 5. Cobertura de tests en la zona

| Test | Qué prueba | Qué **no** prueba |
|---|---|---|
| `tests/test_flow_new_analysis.py` (2 tests, `:49`, `:87`) | El wizard completo con mocks (`InquirerPy.inquirer.select`/`text`, `cli.main.get_mandatory_text`/`get_mandatory_int`, `handle_visual_lesson_assignment`, `flow_pending_audits`) hasta el guardado; que exista 1 `UnifiedDepartment` y el traspaso a `flow_pending_audits` | **Ningún** valor de `calc_edge`, `market_bias`, probabilidades ni filas `analysis_layer` (P2 incluida); tampoco el retroactivo (`created_at`), el clon, "Edit a Field" ni la vista previa |
| `tests/test_repair_analysis_audits.py:286` | Recálculo del edge en la reparación y guardado SQL de `unified_department` | `[NO VERIFICADO]` qué combinaciones de P2 cubre; hay que leerlo en B3 |
| `tests/test_p2_models.py` (41 tests recolectados, 23 funciones) | Registro de modelos, pesos, cadenas EMA, DI, F = D sin 30M (`:190`) | — |
| `tests/test_p2_pipeline.py` (30 tests) | Score, incompletos, anclas, filtro de alcance, `include_no_execution`. "No look-ahead" (`:273`) solo verifica que el snapshot se pida **en** el ancla, con un `FakeProvider`; `:288` verifica que el path posterior excluya la vela del ancla | **Que el snapshot use solo velas cerradas** (H19); anclaje en la hora de inicio del análisis |
| `tests/test_p2_ohlc_csv_loader.py` (8 tests) | Snapshot point-in-time con fixtures, CSV inválido, variable de entorno faltante | Recarga del proveedor tras un re-export |
| `tests/test_p2_clock.py` (6 tests) | Reloj alineado, +3h desalineado, mark_price, retroactivos ignorados | Una DB sin fills (`aligned=None`) |
| `tests/test_p2_rescale.py` (10), `tests/test_p2_ground_truth.py` (5), `tests/test_stats_tests.py` (28 recolectados) | Redondeo, clamp, primer toque, binomial, McNemar, net_score | — |
| `tests/test_export_p2_ohlc.py` (19 tests recolectados) | Conversiones de reloj, vela en formación, `compute_backward_start`, inferencia del offset | `main()` completo (necesita MT5), escritura atómica, código de salida |
| `tests/test_edge_evaluation.py` | Pesos y umbral iguales a producción, baselines point-in-time | — |

- Los tests de DB usan SQLite `:memory:` con `create_all` (`tests/test_flow_new_analysis.py:14-19`). **Ninguno ejercita el `ALTER TABLE` real del shim** (CLAUDE.md, "Comandos").
- **Brecha para B3:** no hay ningún test que fije el `calc_edge`, `market_bias` o las filas `analysis_layer` que produce hoy `flow_new_analysis` (nuevo, retroactivo, clonado, editado). Tampoco uno que fije lo que persiste hoy la reparación para P2.

---

## 6. Hallazgos que la spec o el plan tienen que resolver (no son decisiones)

| # | Hallazgo | Evidencia | Afecta a |
|---|---|---|---|
| H1 | P2 se captura **antes** de P3, P1 y P4. "Al terminar de cargar P2" (RF-1) ocurre en mitad del Step 1 | `cli/main.py:2545-2551` | RF-1, RF-3 |
| H2 | El clon **copia P2 del original** y salta su prompt, así que no hay captura a ciegas en un clon | `cli/main.py:2316-2318`, `:211-217` | RF-8, R3 |
| H3 | Las fuentes de verdad de P2 (`analysis_layer`) las leen 9 consumidores, y la reparación **reescribe las capas y recalcula el edge desde ellas** | §4, `cli/main.py:5408-5438` | Aclaración de la representación de P2 = 0, RF-4 |
| H4 | Un `calc_edge == 0.0` fuerza `tactical_classification = NA` | `cli/schemas/tactical.py:59-60` | RF-4 (efecto lateral de P2 = 0) |
| H5 | Import circular y doble import de `cli.main` si el CLI importa `tools.p2_backtest` | `tools/p2_backtest.py:70-73`, `cli/main.py:681` y el final del archivo | Plan |
| H6 | `calibrate_clock_offset` necesita ≥10 fills o mark_price en la **DB de la cuenta**. En una DB con pocos, o en una Flight Session, da `aligned=None` ("sin calibrar"), un caso que E2 no cubre (E2 solo contempla `aligned=False`) | `tools/p2_backtest.py:962`, `:1028-1029` | RF-2, E2, demo en Flight Session |
| H7 | El exportador sobrescribe TF por TF, así que un fallo a mitad de camino deja una mezcla de CSV viejos y nuevos. Su cobertura depende de `--min-anchor` | `export_p2_ohlc.py:285-287`, `:345-355`, `:220-238` | R8, INV-5 |
| H8 | `--min-anchor`/`--max-anchor` son obligatorios. Para no reducir la cobertura, el CLI tiene que pasar un `min-anchor` ≤ al más antiguo que cubre hoy cada CSV | §2.10, tabla §2.11 | R8, RF-1 |
| H9 | Con el mercado cerrado, la detección automática del offset falla. BTC opera los fines de semana y XAU y US100 no | `export_p2_ohlc.py:171-204` | E1, frescura (aclaración del prompt) |
| H10 | **4 cuentas reales.** US500 no tiene símbolo MT5, así que cae siempre en E5. US100 tiene 737 velas 1W, así que cae siempre en E3. Con la historia actual, el consenso solo opera en XAUUSD y BTC | §2.7, §2.11 | RF-1, RF-9, reporte |
| H11 | Agregar columnas al ORM de `UnifiedDepartment` o `AnalysisLayer` rompe `assemble_p2_systematic_rows` (usa la entidad completa) sobre cualquier DB no migrada | `tools/p2_backtest.py:795`, CLAUDE.md "Auditoría" | Plan (tabla nueva contra columnas nuevas), R12 |
| H12 | `init_db` corre `create_all` y el shim **cada vez que el CLI abre una DB**: la "migración" real ocurre al abrir el CLI, no con un comando | `tools/database.py:275`, `cli/main.py:285/940/990` | R12 (cuándo corren las 3 puertas) |
| H13 | Las cifras de referencia se midieron anclando en la **primera ejecución**, no en la hora del análisis. El reporte prospectivo tiene que fijar ancla y precio de referencia del ground truth: `mark_price`, `evp` y `si` son opcionales en el wizard (`cli/main.py:2615-2618`), así que un análisis sin ellos nunca "se resuelve" | §2.9; `tools/p2_backtest.py:802-839` | RF-10, D5, D6 |
| H14 | **Resuelto:** la WSL corre en `CST (−0600)` (medido con `date '+%Z (%z)'`), así que `datetime.now()` (`cli/main.py:2826`) y el default GT de `created_at` (`tools/database.py:100`) dan la misma hora. El usuario confirmó que todo se maneja en hora de Guatemala, incluidas las fechas retroactivas | — | — |
| H15 | `CsvOHLCProvider` cachea los DataFrames, así que después de un export hay que crear un proveedor nuevo | `tools/p2_backtest.py:364-384` | RF-2 |
| H16 | El `asset` es editable en "Edit a Field" (incluido un activo custom). Si cambia después del consenso, el símbolo MT5 usado ya no corresponde | `cli/main.py:3029-3044` | RF-6 |
| H17 | El worktree no tiene `.env` ni `.data/`, y el CLI usa rutas relativas. El spike, las demos y la migración R12 tienen que correr contra el checkout principal o apuntarle explícitamente | §2.12 | Plan, R12, R13 |
| H18 | El prompt cita `recalculate_unified_metrics` en `:4826` "anidada en `:5143`": es al revés, y `SYMBOL_ALIASES` empieza en `:52`, con US100 en `:55` | §2.4, §2.11 | Solo corrección de referencias |
| H19 | **Look-ahead en `CsvOHLCProvider`:** el snapshot y `get_past_closes` incluyen la vela en formación con sus valores finales. La regla correcta es usar solo velas cerradas, `time + duración(TF) <= ancla` (adenda, C1). El fix con su test va en la rama base, fuera de esta sesión, y llega por fast-forward | §2.8; `tools/p2_backtest.py:386-391`, `:406-415` | RF-2, RF-14, reporte, todas las cifras previas (adenda, C2) |
| H20 | **No existe la hora de inicio del análisis.** `created_at` se fija al guardar (`cli/main.py:2881-2900`, default ORM `tools/database.py:100`), después de P2, Mark Price, niveles y Edge Description. El ancla de D13 exige registrar un timestamp nuevo al entrar en `flow_new_analysis` (`:2492`), fuera del `while True` (`:2500`), para que un `RestartFlowException` no lo mueva | `cli/main.py:2492-2500`, `:2877-2900` | RF-12, RF-13, D13 |
| H21 | Con el ancla dentro de una vela de 1H (por ejemplo 06:15), `get_forward_path` usa `time > as_of` (`tools/p2_backtest.py:441`), así que el path empieza en la vela de 07:00: el tramo de 06:15 a 07:00 no se observa. Hoy pasa lo mismo con cualquier ancla que no caiga en hora exacta | `tools/p2_backtest.py:437-445` | RF-10 (ground truth del reporte) |

---

## 7. Resumen de `[NO VERIFICADO]`

1. ~~Zona horaria de `backdated_timestamp` y de la WSL~~: **resuelto**. WSL en `CST −0600` (medido) y el usuario confirmó GT para todo (H14).
2. Que la migración se aplique sola al abrir el CLI (H12). Deducido del código, no ejecutado.
3. Que `calc_edge == 0.0` por P2 = 0 ocurra con datos reales (H4).
4. El import circular y el doble import (H5).
5. `aligned=None` en una Flight Session vacía (H6).
6. Que el exportador deje una mezcla de CSV ante un fallo intermedio, y su código de salida ante un `RuntimeError` (H7).
7. Un export completo lanzado desde WSL vía `powershell.exe`, con su duración real: queda para el spike de R10.
8. Qué combinaciones de P2 cubre `tests/test_repair_analysis_audits.py:286`.
9. De qué registro muestra capas `cli/ui_manager.py:598-602`.
