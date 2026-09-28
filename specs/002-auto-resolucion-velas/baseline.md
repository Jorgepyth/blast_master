# Baseline B1 — 002 Auto-resolución con velas

- **Fecha:** 2026-09-25.
- **Ruta sdd-engine:** brownfield-delta, fase B1, solo lectura.
- **Commit base:** `158869b`. Incluye la base de R2 (`bd5c987` y `db971a2`) y el archivo de la spec 001.
- **Dónde se escribió:** en el worktree de esta sesión, `.claude/worktrees/spec-001-closure-tests-b99a6d`, que
  está en el mismo commit y no tiene `.data/` ni `.env`. La protección de la sesión no permite escribir en el
  worktree `.claude/worktrees/spec-002-auto-resolucion-velas`. La ubicación final queda a decisión del usuario.
- **Suite de referencia (INV-5):** `conda run --cwd <worktree 002> -n blast_master pytest -q` da
  **429 passed, 1 skipped**. Se corrió el 2026-09-24 sobre `db971a2`; `158869b` solo agrega Markdown. Son los
  418 del baseline de 001 más los 11 tests de `tests/test_p2_closed_bars.py`. Esa corrida creó `.data/` en ese
  worktree (ver §2.13).
- **Fuente del pedido:** `docs/prompts/2026-09-24-auto-resolucion-velas.md` (v1.2), más las reglas R14–R17 y
  los hallazgos H-A a H-C que el usuario dio el 2026-09-24. Donde se contradicen, mandan las reglas del usuario.
- **Baseline reutilizado:** `docs/archive/specs/001-p2-consenso-modelo-f/baseline.md`, re-verificado línea por
  línea. Ver §8.

**Convenciones del documento**
- Toda afirmación cita `archivo:línea`, o el comando que la midió.
- `[NO VERIFICADO]` marca lo que se dedujo leyendo el código y no se ejecutó.
- "Medido" significa ejecutado en esta sesión y en solo lectura:
  - DBs reales con `?mode=ro&immutable=1`, o con `?mode=ro` si hay un `-wal` presente (solo US500);
  - CSV de velas leídos como archivos.

---

## Resumen para leer primero

Estos son los hallazgos que cambian lo que el prompt da por hecho. El detalle está en §6.

1. **`resolution_time` no es la hora en que se resolvió el precio.** El wizard lo guarda con
   `datetime.datetime.now()` al hacer el audit (`cli/main.py:3222`), y nunca lo pregunta. Comparar la hora
   automática del toque contra este campo mide cuánto tardó el operador en auditar, no si acertó. (H1)
2. **El wizard no deja elegir `Open`, y la DB pone `Open` por defecto.** Cada `efficiency_audit` nace con
   `resolution_type = "Open"` al guardar el análisis, así que ese campo nunca está en NULL. La regla "el backfill
   solo llena NULL" nunca lo alcanzaría. (H2)
3. **Dos MAE/MFE distintos:**
   - `structural_mae`/`structural_mfe` son **precios**;
   - los tácticos son **R de 0 a 10**. En pydantic se llaman `mae`/`mfe` y en la DB `mae_adverse`/`mfe_favorable`.
   (H3, H4)
4. **`session` ya se calcula sola** a partir de `entry_time` (`cli/schemas/audit_tactical.py:437-448`). En RF-10
   no hay nada que proponer para ese campo. (H5)
5. **El resolvedor que hay que reutilizar no alcanza.** `first_touch_direction` no devuelve la hora del toque ni
   MAE/MFE, y devuelve lo mismo para "no tocó nada" que para "una vela tocó los dos niveles". (H9)
6. **El camino hacia adelante es solo de 1H y arranca en la vela siguiente al ancla**, así que H21 de 001 sigue
   vigente. Además, `5M` no existe en el código. (H10)
7. **El único `5M.csv` (XAU) es anterior a la corrección del reloj**, y el exportador no genera 5M. (H11)
8. **Faltan velas en varios casos:**
   - US500 no tiene ninguna vela;
   - todos los CSV terminan el 2026-09-22;
   - la 15M de BTC empieza 10 días después de su primer análisis;
   - USTEC no alcanza a calibrar el reloj (9 precios de referencia, el mínimo es 10). (H12, H13)
9. **Faltan tests:** ningún test recorre el Efficiency Audit ni la rama "orden llenada" del Tactical Audit, que
   es donde se piden MAE, MFE y `could_hit_tp`. (H18)
10. **H-C está confirmado**, y hay un segundo archivo con ruta relativa, `.data/paused_audits.json`, que la
    corrección de R16 también tiene que cubrir. (H19)

---

## 1. Superficie

Ningún archivo fuera de esta tabla puede modificarse sin detenerse y reportar (regla B4).

| Archivo | Rol en el feature | ¿Se toca? |
|---|---|---|
| `cli/main.py` | Wizard de Efficiency (`flow_pending_audits`, rama `"eff"`, `:3162-3309`), wizard de Tactical (rama `"tac"`, `:3311-`), guardado de `flow_new_analysis` (`:2877-2946`, RF-3), helpers de prompt (`:375-867`), `AuditSession` (`:97-`), `get_active_engine` (`:255-286`, H-C) | Sí |
| `cli/schemas/audit_efficiency.py` | Enums y `EfficiencyAudit` (`:16-81`) | Probablemente no; lo decide el plan |
| `cli/schemas/audit_tactical.py` | `TacticalAudit` (`:206-450`): `mae`/`mfe`, `could_hit_tp` y `session` automática | Probablemente no |
| `tools/p2_backtest.py` | `CsvOHLCProvider` (`:334-460`), `calibrate_clock_offset` (`:1076-1129`), `open_readonly_session` (`:224-234`), ancla de análisis (`:681-712`, `:872-890`) | Sí, solo agregando. Los modelos A–G y su score no se tocan |
| `core/p2_ground_truth.py` | `infer_thesis_direction` (`:33-49`) y `first_touch_direction` (`:52-105`) | Posible (H9); lo decide el plan |
| `windows_export/export_p2_ohlc.py` | Exportador de velas (R4, RF-1, RF-2) | Posible; lo decide el plan |
| `tools/database.py` | ORM, `init_db` (`:262-382`), `get_records_by_state` (`:567-`), `get_unified_created_at` (`:539`) | Solo agregando, si el plan lo pide |
| `tests/` | Red de seguridad (R10.1) y aislamiento (R16) | Sí, se agregan tests |
| Nuevos: el banco de velas, el resolvedor, el reporte de comparación y el backfill | RF-1, RF-4 a RF-6 y RF-11 | Nuevos |
| **No tocar** (prompt, "Restricciones") | `core/math_engine.py`, `determine_market_bias` y su umbral, los gates Tier D/F, emocional y Stop Deviation, `tools/notion_sync.py`, `core/edge_analysis.py`, P0–P4 y los modelos A–G de `tools/p2_backtest.py` | No |

---

## 2. Comportamiento actual observado

### 2.1 Creación del análisis: `flow_new_analysis` (`cli/main.py:2492-3086`)

- `cli/main.py` no cambió entre `fc639e8` y `158869b` (`git diff --stat fc639e8 158869b -- cli/` vacío). Por eso
  las citas de §2.1 del baseline de 001 siguen valiendo línea por línea.
- **Mark Price, Edge Validation Price y Structural Invalidation** se piden en texto libre y **opcional**
  (`:2615`, `:2617-2618`).
  - Se guardan como `Decimal`, o `None` si están vacíos o no son numéricos (`:2902-2916`).
  - El wizard los pide **después** de la vista previa del edge (`:2575-2605`) y antes de `edge_desc` (`:2661`).
- **Guardado en una sola transacción** (`:2877-2946`): primero `UnifiedDepartment` (`:2883-2900`), luego la fila
  inicial de `efficiency_audit` (`ModelEfficiencyAudit(id, bias_a, efficiency_timeframe)`, `:2922`), después las
  capas y el `commit` (`:2940`). Si algo falla, hace `rollback` y el análisis no se guarda (`:2942-2946`).
- **`created_at`:**
  - en un análisis normal lo pone el default del ORM al guardar, en GT naive (UTC−6)
    (`tools/database.py:100`), o sea, el instante de "Confirm & Save";
  - en un retroactivo se sobrescribe con la hora que eligió el operador, en `unified_department` y en
    `efficiency_audit` (`cli/main.py:2917-2925`).
  - **No existe la columna `backdated_timestamp`** (`grep` en `tools/database.py`, 0 coincidencias). La marca de
    retroactivo es `is_backdated` (`:2899`; `tools/database.py:118`).
- **Después de guardar**, el wizard pregunta si se quiere alimentar un Tactical Audit (`:2948-2972`). Si la
  respuesta es sí, pasa a `flow_pending_audits` con `preselected_choice="tac"`.

### 2.2 Efficiency Audit (`flow_pending_audits`, rama `"eff"`, `cli/main.py:3162-3309`)

- **Entrada:** el `payload` de `get_records_by_state` (`tools/database.py:567-613`) o el `preselected_payload`
  (`cli/main.py:3097-3099`).
  - Trae el `asset`, los tres precios (`Edge_Validation_Price`, `Structural_Invalidation`, `Mark_Price`,
    `tools/database.py:585-587`) y `audit_efficiency` con `resolution_time` (`:612`).
  - **No trae `created_at` ni `is_backdated`**, que son justo el ancla y la marca de R11. Hoy se consultan
    aparte con `get_unified_created_at` (`tools/database.py:539`), como hace Entry Time (`cli/main.py:3851`).
- **Qué muestra:** Bias A, timeframe, los tres precios y la Edge Description (`:3163-3183`).
- **Qué pide, en este orden** (INV-1), dentro de un `while True` con `AuditSession(trade_id, "eff")`
  (`:3190-3191`):
  1. `real_bias_b` — `StructuralBias` (`:3193`);
  2. `res_type` — `ResolutionType` **sin `OPEN`** (`exclude=[ResolutionType.OPEN]`, `:3194`);
  3. `struct_res` — `StructuralResolution` (`:3195`);
  4. `fail_reason` — `FailureReason` (`:3196`);
  5. `lesson_eff` — texto opcional (`:3197`);
  6. `structural_mae_raw` — texto libre, "peor precio alcanzado en contra de la tesis", opcional (`:3199-3202`);
  7. `structural_mfe_raw` — texto libre, "mejor precio alcanzado a favor de la tesis", opcional (`:3203-3206`).
- **Construcción:**
  - `EfficiencyAudit(..., resolution_time=datetime.datetime.now(), ...)` (`:3215-3226`). **`resolution_time` es
    la hora del guardado del audit, y nunca se le pregunta al operador.**
  - Si el MAE o el MFE no se pueden convertir a decimal, **los dos** pasan a `None`, con un mensaje (`:3207-3213`).
- **Revisión:** panel "Review: Efficiency Audit Staging Payload" (`:3228-3244`) y acciones Confirm & Save, Edit
  a Field o Discard (`:3246-3303`).
  - "Edit a Field" vuelve a pedir el campo, otra vez sin `OPEN` (`:3287`), y el `while True` reconstruye todo.
  - Discard lanza `PauseAuditException` (`:3263`), que el `except` de `:3306-3309` atrapa: imprime "Audit
    Paused. Progress saved." y sale.
- **Guardado:** `new_payload["audit_efficiency"] = audit_eff.model_dump()` y `session.clear_state()`
  (`:3257-3261`). La persistencia final ocurre más abajo, en `update_record_state` [NO VERIFICADO: no se leyó el
  tramo final].
- **Derivados** del schema (`cli/schemas/audit_efficiency.py:60-81`):
  - `specific_bias_compliance` = "Valid" si `bias_a == real_bias_b` y "Invalid" si no (`:62-66`);
  - `false_regime_rate` (`:68-79`).
  - Los dos son de juicio y quedan fuera del alcance (R8).

### 2.3 Tactical Audit (`flow_pending_audits`, rama `"tac"`, camino principal)

- **Antes de la orden:** gates y confirmaciones, Tier D/F, SL, Entry, Size y TP (`cli/main.py:3787-3790`), el
  cuadro de P&L, Stop Deviation (`:3838-3845`) y `entry_time` (`:3848-3861`). Este último combina la fecha de
  `created_at` (`get_unified_created_at`, `:3851`) con una hora que tipea el operador.
- **Emociones y clasificación:** emociones (`:3864-3869`), gate emocional (`:3883`), `market_state`, `setup_t` y
  `cost` (`:3890-3892`).
- `order_filled` (`:3895`). Si es "no": `skip_reason`, lección y visual (`:3897-3901`), y no se pide nada de
  salida.
- **Orden llenada, en este orden** (INV-1):
  1. `mid_trade_emotions` y `post_trade_emotions` (`:3945-3946`);
  2. **`exit_time`** — `get_mandatory_datetime`, formato `%Y-%m-%d %H:%M` (`:3947`; helper `:840-866`);
  3. `exit_type` (`:3948`);
  4. **`close_p`** (`:3949`);
  5. **`could_hit_tp`** — select `"yes"`/`"no"` (`:3950`; helper `:3477-3484`);
  6. `f_plan` y `behav_errors` (`:3951-3952`);
  7. **`mae`** y **`mfe`** — `get_mandatory_float(..., min_val=0, max_val=10)`, con las etiquetas
     "MAE (0 <= MAE <= 10)" y "MFE (0 <= MFE <= 10)", que no dicen la unidad (`:3953-3954`);
  8. lección y visual (`:3955-3956`).
- **Construcción:** `TacticalAudit(..., exit_time, closing_price, could_hit_tp, mae, mfe, ...)` (`:3958-3990`).

### 2.4 Helpers de prompt: no aceptan un valor por defecto

- `get_enum_choice(prompt_text, enum_class, exclude=None)` (`cli/main.py:420-434`): `inquirer.select` sin
  `default`.
- `get_mandatory_float(prompt_text, min_val=None, max_val=None)` (`:754-772`): `inquirer.text` sin `default`.
- `get_mandatory_datetime(prompt_text, allow_cancel=False)` (`:840-866`): sin `default`.
- El MAE y el MFE estructurales se piden con `inquirer.text` directo, sin `default` (`:3199-3206`, `:3295-3303`).
- Solo `get_mandatory_text` acepta `default=""` (`:456`).
- **Consecuencia para R6:** para proponer un valor editable hay que agregar un `default` a estos helpers o a sus
  llamadas. Los tests existentes mockean estos helpers con `side_effect` en orden (por ejemplo,
  `tests/test_stop_deviation_journal.py:86-120`).

### 2.5 Schemas

**`EfficiencyAudit`** (`cli/schemas/audit_efficiency.py:36-81`)
- `resolution_type: ResolutionType`. Los valores y sus textos exactos (`:16-20`) son:
  - `"Open"`;
  - `"Overlap Invalidation (New Bias before resolution)"`;
  - `"Invalidated (B not equal to A)"`;
  - `"Confirmed (A equal to B)"`.
- `structural_resolution: StructuralResolution` (`:22-26`) y `failure_reason: FailureReason` (`:28-34`). Sus
  textos largos son el valor que se persiste.
- `resolution_time: datetime` es obligatorio en pydantic (`:43`).
- `structural_mae`/`structural_mfe` son `Optional[Decimal]` (`:57-58`). El comentario los define como "precio real
  alcanzado en contra/a favor de la tesis entre la creación del análisis y su resolución. Entrada manual (no hay
  feed de precio en el sistema)" (`:53-56`).

**`TacticalAudit`** (`cli/schemas/audit_tactical.py:206-450`)
- `mae` y `mfe` son `Optional[float] = Field(ge=0.0, le=10.0)` (`:261-262`).
- **Están en R** [NO VERIFICADO en ejecución, deducido de la fórmula]: `captured_mfe = r_multiple / mfe` y
  `captured_mae = |r_multiple| / mae` (`:415-425`) solo tienen sentido si `mfe` y `mae` están en la misma unidad
  que `r_multiple`, que es R (`:405-413`).
- `could_hit_tp: Optional[str]` (`:254`).
- **`session` es automática** (`:437-448`): `entry_time + 6 h` da la hora UTC, y con ella:
  - 13–16 → London/NY Overlap;
  - 16–21 → New York;
  - 8–13 → London;
  - cualquier otra → Asia/Off.

  El comentario de `:427` dice que `entry_time` es "naive UTC", pero el código le suma 6 h, o sea, lo trata como
  GT. El comentario es inexacto; el código es coherente con el resto del sistema, que maneja todo en GT naive.
- `trade_duration` también es automática (`:429-435`).

### 2.6 Base de datos (`tools/database.py`)

- **`unified_department`** (`:94-126`): `asset` (`:99`), `created_at` (`:100`), `is_backdated` (`:118`) y
  `edge_validation_price`, `structural_invalidation` y `mark_price`, nullable (`:120-122`).
- **`efficiency_audit`** (`:128-154`):
  - **`resolution_type` es nullable con `default="Open"` (`:133`).** La fila que crea `flow_new_analysis` al
    guardar (`cli/main.py:2922`) queda en `"Open"` hasta que se audita.
  - `resolution_time` es nullable (`:140`).
  - `structural_mae`/`structural_mfe` son `Numeric(18,8)`. El comentario dice "Precio real (no score 0-10)"
    (`:143-150`).
- **`tactical_audit`** (`:156-`):
  - `entry_time` y `exit_time` (`:164-165`);
  - `session` (`:195`);
  - `entry_price`, `closing_price`, `could_hit_tp`, `take_profit` y `stop_loss` (`:222-226`);
  - **`mae_adverse` y `mfe_favorable`** (`:230`, `:234`).
- **Traducción de nombres entre pydantic y la DB:** `mae` pasa a `mae_adverse` y `mfe` a `mfe_favorable`
  (`:495-498`). En la lectura, `get_records_by_state` los devuelve como `"mae"` y `"mfe"` (`:658-659`).
- **`init_db(db_url)`** (`:262-382`):
  - hace `makedirs` de `.data` si la URL empieza con `sqlite:///.data` (`:264-265`);
  - pone `PRAGMA journal_mode=WAL` en cada conexión (`:268-273`);
  - corre `create_all` (`:275`) y el shim aditivo de `ALTER TABLE` (`:277-379`);
  - **asigna `engine_default` solo si la URL es exactamente `sqlite:///.data/flight_account_001_xauusd.db`**
    (`:380-381`).
- `get_unified_created_at(trade_id)` (`:539`) y `get_unified_structural_invalidation(trade_id)` (`:554`) son
  lecturas puntuales por `trade_id`.

### 2.7 Cuentas y símbolos (medido)

| Sesión | Activo (`asset`) | DB | Análisis | Con EVP **y** SI | Retroactivos | `mark_price > 0` no retroactivo | Fills (`entry_time`, `entry_price > 0`) | `created_at` |
|---|---|---|---|---|---|---|---|---|
| 000 | `XAUUSDT.P` | `flight_account_001_xauusd.db` | 81 | 76 | 7 | 70 | 35 | 2026-05-18 → 2026-09-16 |
| 001 | `US500` | `flight_account_000_us500.db` | 5 | 5 | 0 | 4 | 0 | 2026-09-17 → **2026-09-25 06:52** |
| 002 | `BTCUSDT.P` | `flight_account_002_btcusdtp.db` | 24 | 23 | 0 | 15 | 4 | 2026-07-02 → 2026-09-01 |
| 003 | `US100` | `flight_account_003_us100.db` | 8 | 8 | 0 | 8 | 1 | 2026-08-11 → 2026-09-03 |

- **Total: 118 análisis, 112 con los dos niveles.** Faltan 6: 5 de XAU y 1 de BTC.
  - El "6 de 113" del prompt es el total sin US500: 81 + 24 + 8 = 113.
  - US500 pasó de 3 análisis (baseline de 001) a 5, y el último se cargó hoy a las 06:52.
- **Mapa de sesiones:** `.data/flight_sessions.json` asigna la sesión "001" a US500 (`flight_account_000_us500.db`)
  y la "000" a XAUUSDT.P. Leído en solo lectura.
- **No hay relación entre cuenta y símbolo MT5 en el código.** El símbolo sale de `unified_department.asset`. El
  mapa `{{MT5_SYMBOL_MAP}}` del prompt es configuración nueva. `config/contract_specs.py:SYMBOL_ALIASES` mapea a
  claves de contrato (US100 → NAS100), no a símbolos MT5 (baseline 001 §2.11, sin cambios).
- **Esquema sin migrar:** a `tactical_audit` de US100 le faltan `stop_slippage_r`, `emotional_gate_override_reason`
  y `stop_deviation_reason` (medido con `PRAGMA table_info`). Las otras tres cuentas están completas.

### 2.8 Velas: `CsvOHLCProvider` (`tools/p2_backtest.py:334-460`)

- **Formato de entrada:** un CSV por temporalidad en `{base_dir}/{TF}.csv` (`:364-367`), con las columnas
  requeridas `time,open,high,low,close` (`:106`, `:370-377`). `time` se convierte con `pd.to_datetime` y se
  ordena (`:379-380`).
- **Caché:** cada TF se cachea en memoria (`:365-366`, `:381`). Un proveedor creado antes de un re-export no ve
  las velas nuevas [NO VERIFICADO, deducido; es H15 de 001].
- **`closed_bars(tf, as_of)`** (`:386-396`) devuelve las velas con `time + TIMEFRAME_MINUTES[tf] <= as_of`.
- **`TIMEFRAME_MINUTES`** (`:83-86`) solo tiene 1W, 1D, 12H, 4H, 1H, 30M y 15M. **No tiene `5M`**, así que
  `closed_bars("5M")` y `bar_containing("5M", t)` lanzarían `KeyError` [NO VERIFICADO, deducido de `:396` y
  `:418`].
- **`bar_containing(tf, t)`** (`:409-420`) devuelve `(high, low)` de la vela que contiene `t`.
- **`get_forward_path(as_of, max_bars=2160)`** (`:451-460`):
  - usa **solo** `FORWARD_PATH_TIMEFRAME = "1H"` (`:97`);
  - toma las velas con `time > as_of` y **devuelve solo `time`, `high` y `low`** (`OhlcBar`,
    `core/p2_ground_truth.py:26-30`);
  - el tope es `FORWARD_PATH_MAX_BARS = 2160` (`:98`).
- **`load_ohlc_provider_from_csv(base_dir=None)`** (`:462-479`): sin `base_dir`, usa `P2_SYSTEMATIC_OHLC_DIR` del
  `.env` (`:104`, `:472-478`). **Es un solo directorio.** El `.env` real apunta a
  `/mnt/c/Users/jcifu/MT5Exports/XAUUSD` (medido; solo se leyó esa clave).
- **`open_readonly_session(db_path)`** (`:224-234`) abre SQLite con `mode=ro` y no usa `init_db`.

### 2.9 Reloj: `calibrate_clock_offset(session, provider)` (`tools/p2_backtest.py:1076-1129`)

- **Qué temporalidad usa:** la primera que exista entre `15M`, `30M` y `1H` (`:1044`, `:1088-1089`). **No mira
  5M.**
- **Precios de referencia:**
  - las órdenes llenadas con `entry_time` y `entry_price > 0` (`:1093-1099`);
  - si `include_mark_price`, también `created_at` + `mark_price` de los análisis **no retroactivos**
    (`:1100-1110`).
- **Resultado:**
  - con menos de `CLOCK_MIN_ENTRIES = 10` referencias devuelve `aligned=None`, "Sin calibrar" (`:1045`,
    `:1111-1112`);
  - `aligned=False` si el desplazamiento 0 queda más de `0.20` por debajo del mejor (`:1046`, `:1127-1128`).
- **Recibe una sola sesión de DB y un solo proveedor**, o sea, una cuenta y un símbolo por llamada.
- **Referencias por símbolo (medido):** XAUUSD 105, BTCUSD 19, USTEC 9 y US500 4. **USTEC y US500 dan `aligned=None`.**
- **Es el mismo mecanismo que RF-3:** el `mark_price` contra la vela que contiene `created_at`.

### 2.10 Resolución geométrica existente

- **`infer_thesis_direction(entry_price, evp, si)`** (`core/p2_ground_truth.py:33-49`) devuelve "long" o "short"
  según de qué lado del precio de referencia están los niveles, y `None` si los dos caen del mismo lado.
- **`first_touch_direction(thesis, path, entry, evp, si)`** (`:52-105`):
  - devuelve `(dirección, incompleto)`;
  - **no devuelve la hora del toque, ni el precio más adverso, ni el más favorable**;
  - **devuelve `(None, True)` tanto si no se tocó ningún nivel (`:105`) como si una vela tocó los dos
    (`:96-97`)**, así que el caller no puede distinguir "Open" de "ambiguo".
- **Ancla de análisis del backtest:**
  - `ANCHOR_MODE_ANALYSIS` usa `created_at − ANALYSIS_ANCHOR_LEAD` (15 min) y excluye los retroactivos
    (`tools/p2_backtest.py:687`, `:876-879`);
  - el precio de partida es el cierre de la última vela cerrada en 15M, 30M o 1H (`:688`, `:700-712`).
  - **El prompt 002 (RF-4) ancla en `created_at` a secas** (ver H8).

### 2.11 Exportador (`windows_export/export_p2_ohlc.py`)

- **Temporalidades:** `TIMEFRAME_MAP` solo tiene 1W, 1D, 12H, 4H, 1H, 30M y 15M (`:82-90`). **No exporta 5M.**
  El bucle de `main` recorre esas 7 (`:345`).
- **Escritura:** `export_timeframe` escribe `{out_dir}/{TF}.csv` con `to_csv` y **sobrescribe** el archivo, sin
  escritura atómica ni fusión (`:286-287`). Las TF se exportan en secuencia (`:345-355`). Si una falla a mitad
  de camino, el directorio queda mezclado: unos CSV nuevos y otros viejos [NO VERIFICADO; es H7 de 001].
- **Argumentos:** `--out-dir`, `--min-anchor` y `--max-anchor` son obligatorios (`:305-316`).
- **Reloj:** el offset del servidor se detecta del último tick y falla con el mercado cerrado (`:171-204`); se
  puede pasar a mano con `--server-utc-offset` (`:317-321`).
- **Salida:** los CSV quedan en GT naive (`:271-273`) y la vela en formación se excluye (`:207-217`, `:272`).
- **Errores:** `mt5.initialize()` fallido devuelve `1` (`:326-328`). Los demás errores son `RuntimeError`
  (`:253-268`).

### 2.12 Datos medidos

**Velas disponibles** (`/mnt/c/Users/jcifu/MT5Exports/`, filas sin contar el encabezado; primera → última vela,
en GT naive):

| Símbolo | 1W | 1D | 12H | 4H | 1H | 30M | 15M | 5M |
|---|---|---|---|---|---|---|---|---|
| XAUUSD | 1483 · 1998-04-18 → 09-12 | 1224 · 2021-12-26 → 09-21 | 1320 · 2024-03-03 → 09-22 | 1700 · 2025-08-17 → 09-22 | 3250 · 2026-03-05 → 09-22 17:00 | 5470 · 2026-04-08 → 09-22 17:30 | 9793 · 2026-04-26 → 09-22 18:00 | 25514 · 2026-05-14 → **09-22 10:45**, mtime 09-22 10:50 |
| BTCUSD | 808 · 2011-03-19 → | 1655 · 2022-03-12 → | 1710 · 2024-05-20 → | 1930 · 2025-11-04 → | 2915 · 2026-05-23 → 09-22 17:00 | 4217 · 2026-06-25 → | 6876 · **2026-07-12** → 09-22 18:15 | — |
| USTEC | **737** · 2012-08-04 → | 1165 · 2022-03-20 → | 1204 · 2024-05-26 → | 1334 · 2025-11-10 → | 1875 · 2026-05-31 → 09-22 17:00 | 2678 · 2026-07-02 → | 4318 · 2026-07-19 → 09-22 18:15 | — |
| US500 | — | — | — | — | — | — | — | — |

- No existe la carpeta `US500/`, y `US100/` está vacía.
- Todos los CSV, salvo el 5M, se escribieron el 2026-09-22 entre las 18:28 y las 18:38, después de la corrección
  del reloj de ese día (CLAUDE.md, "Reloj de las velas exportadas").

**`efficiency_audit`:**

| Cuenta | Filas | `resolution_time` | `structural_mae`/`mfe` | `real_bias_b` | `resolution_type` |
|---|---|---|---|---|---|
| XAU | 81 | 80 | 74 / 74 | 80 | Confirmed 52 · Invalidated 25 · Overlap 3 · Open 1 |
| US500 | 5 | 0 | 0 / 0 | 0 | Open 5 |
| BTC | 24 | 24 | 15 / 15 | 24 | Confirmed 12 · Invalidated 9 · Overlap 3 |
| US100 | 8 | 5 | 5 / 5 | 5 | Confirmed 4 · Invalidated 1 · Open 3 |

- **`structural_resolution` en XAU:** expansión significativa 28, mínima 14, inmediatamente revertido 10, N/A 28 y
  NULL 1.
- **`failure_reason` en XAU:** N/A 53, Reversal 15, Liquidity Sweep 4, Overlap 3, Regime Decay 3, Range Expansion
  1 y NULL 1. Hay además **1 fila con el valor `"Reversal"` a secas**, que no es un valor válido del enum
  (`cli/schemas/audit_efficiency.py:31`).
- **`resolution_time` en XAU es un sello automático:**
  - 79 de 80 valores tienen segundos o microsegundos distintos de cero. Una hora tipeada con
    `%Y-%m-%d %H:%M` tendría segundos en 0.
  - Entre `created_at` y `resolution_time` pasan como mínimo 1.2 h, con una mediana de 36.3 h y un máximo de
    768 h.

**`tactical_audit`:**

| Cuenta | Filas | Llenadas | Llenadas con `entry_time` / `exit_time` | `mae_adverse` / `mfe_favorable` no NULL | `could_hit_tp` (llenadas) |
|---|---|---|---|---|---|
| XAU | 82 | 35 | 35 / 35 | 63 / 63 | no 26 · yes 9 |
| US500 | 0 | 0 | — | 0 | — |
| BTC | 24 | 4 | 4 / 4 | 7 / 7 | no 3 · yes 1 |
| US100 | 6 | 1 | 1 / 1 | 4 / 4 | yes 1 |

- En XAU, las llenadas tienen `mae_adverse` entre 0 y 4.27 y `mfe_favorable` entre 0 y 6.26.
- Las no llenadas con valor son 28 de 47, y **26 de ellas valen 0**. Coincide con el default `0.0` que escribe la
  reparación (`cli/main.py:4984-4985`) [NO VERIFICADO que ese sea el origen].
- `session` no está vacía en ninguna fila llenada de ninguna cuenta.

### 2.13 Entorno de ejecución y aislamiento de tests (R15)

**Import sospechoso de H-C: no es la causa.**
- `tools/p2_backtest.py:73` hace `from cli.main import determine_market_bias`.
- El comentario de `:70-72` dice que eso dispara `get_active_engine()`, y **es falso**. Al importarse,
  `cli/main.py` solo ejecuta a nivel de módulo:
  - constantes: `CACHE_FILE` (`:65`), `ACTIVE_SESSION` y `ACTIVE_ENGINE = None` (`:252-253`) y
    `FLIGHT_SESSIONS_FILE` (`:290`);
  - `state = CLIState()` (`:289`), cuyo `__init__` solo asigna atributos (`cli/ui_manager.py:143-150`);
  - de forma transitiva, `load_dotenv` sobre la ruta absoluta del `.env` (`core/math_engine.py:8-9`), que solo
    lee.
- `get_active_engine()` se llama únicamente desde adentro de funciones (`cli/main.py:233-234`, `:998`, `:1006`,
  etc.).

**Qué ruta de DB resuelve `get_active_engine()`** (`cli/main.py:255-286`):
1. Si hay sesión activa, devuelve `ACTIVE_ENGINE` (`:257-258`).
2. Si no, y si `tools.database.engine_default` no es `None`, devuelve ese engine (`:261-262`).
3. Si no:
   - renombra `.data/journal.db` a `.data/flight_account_001_xauusd.db` si el segundo no existe (`:271-272`);
   - lee `FLIGHT_SESSIONS_FILE = ".data/flight_sessions.json"`, que es **relativo a la carpeta de trabajo**
     (`:274`, `:290`);
   - si falta la sesión "001", escribe una nueva, "Main Flight Account" (`:275-282`);
   - abre `init_db(f"sqlite:///.data/{sessions['001'].db_name}")`, también relativa (`:284-285`).
4. `engine_default` casi nunca está asignado en los tests: `init_db` solo lo fija para la URL exacta del paso 3
   (`tools/database.py:380-381`), no para `:memory:`.

**Qué test lo dispara:** `tests/test_cli_report_command.py:71-75`, `test_report_command_rejects_conflicting_flags`.
- Invoca `cli` sin fijar `engine_default`, y el callback del grupo (`cli/main.py:995-998`) llama a
  `get_active_engine()`.
- Se confirmó ejecutándolo el 2026-09-24 desde una carpeta vacía: creó `.data/flight_sessions.json` y
  `.data/flight_account_001_xauusd.db`. Los otros dos tests del mismo archivo no crearon nada.
- **En el checkout principal**, la sesión "001" es `flight_account_000_us500.db`, así que correr `pytest` desde la
  raíz abre esa DB real y le aplica `init_db`: WAL, `create_all` y el shim.

**Segundo archivo relativo: `CACHE_FILE = ".data/paused_audits.json"`** (`cli/main.py:65`).
- `AuditSession.__init__` y `load_state` lo leen (`:98-113`); `save_state` lo escribe (`:115-140`), y
  `clear_state` lo reescribe cuando el archivo existe (`:142-154`).
- Solo 3 archivos de test lo apuntan a `tmp_path`: `tests/test_tactical_tier_gate.py:42`,
  `tests/test_emotional_gate.py:36` y `tests/test_stop_deviation_journal.py:39`.
- En el checkout principal existe el real (8984 bytes, 2026-09-21).

**Rastros observados (medido):**
- El `.pytest_cache` del checkout principal se escribió el 2026-09-23 entre las 16:55 y las 17:11: ese día la suite
  corrió ahí. Si se corrió desde la raíz, el test de H-C abrió la DB real de US500. [NO VERIFICADO; el efecto
  esperado es aditivo o nulo, porque US500 fue creada por el CLI el 2026-09-16 con el esquema completo.]
- Los `-wal` y `-shm` de US500 tienen fecha **2026-09-25 06:52**, igual que el análisis más reciente de esa cuenta
  (`created_at` 2026-09-25 06:52:37). Es uso del CLI, no de tests.
- Después de la suite del 2026-09-24, el worktree `spec-002-auto-resolucion-velas` tiene
  `.data/flight_sessions.json` y `.data/flight_account_001_xauusd.db`, los dos creados por el test de H-C. R14
  exige que `.data/` no exista antes de la próxima corrida en el worktree donde se corra la suite.

---

## 3. Contratos públicos de la zona

| Contrato | Forma actual | Consumidores |
|---|---|---|
| Fila de `efficiency_audit` | `resolution_type` es el texto largo del enum, con default `"Open"`. `resolution_time` es un datetime naive, el del guardado del audit. `structural_mae`/`mfe` son precios. `structural_resolution`/`failure_reason` son textos largos | §4 |
| Fila de `tactical_audit` | `mae_adverse`/`mfe_favorable` en R, de 0 a 10. `could_hit_tp` es `"yes"`/`"no"`. `session` es un valor derivado | §4 |
| `EfficiencyAudit` / `TacticalAudit` (pydantic) | Ver §2.5 | Wizard, reparación y tests de schema |
| CSV de velas | `{dir}/{TF}.csv`, `time,open,high,low,close`, `time` = apertura en GT naive | `CsvOHLCProvider`, `tools/edge_evaluation.py` (`--account DB_PATH=OHLC_DIR`, `:928-973`), `tools/p2_backtest.py main`, `jupyter/p2_edge_evaluation.ipynb` |
| `P2_SYSTEMATIC_OHLC_DIR` | Un directorio de un solo símbolo | `load_ohlc_provider_from_csv` (`tools/p2_backtest.py:462-479`), `tools/edge_evaluation.py:973` |
| CLI del exportador | Ver §2.11 | El operador, a mano |
| `calibrate_clock_offset`, `first_touch_direction`, `infer_thesis_direction`, `closed_bars`, `open_readonly_session` | Ver §2.8 a §2.10 | `tools/p2_backtest.py`, `tools/edge_evaluation.py`, el cuaderno y los tests `test_p2_*` |

---

## 4. Consumidores de los campos que el feature propondría o llenaría

| Consumidor | Qué lee | Ubicación | Efecto de un backfill |
|---|---|---|---|
| Notion sync (no se toca) | `resolution_type`, `structural_resolution` y `failure_reason` en el payload de Efficiency | `tools/notion_sync.py:42-44`; solo sincroniza análisis `READY_FOR_NOTION`/`FAILED` (`:109`) | En un análisis ya `SYNCED`, un valor llenado por backfill **no llega a Notion** [NO VERIFICADO en ejecución] |
| Reparación | Carga y reescribe `resolution_type` (sin `OPEN`), `structural_mae`/`mfe`, `could_hit_tp` y `mae`/`mfe`. Si `mae_adverse` es falsy, lo pone en `0.0` | `cli/main.py:4939-4985`, `:5533`, `:5583-5600` | Convive con el backfill: si la reparación escribe 0.0, ese campo deja de estar en NULL |
| Vista de detalle ("Review History") | `mae_adverse` y `mfe_favorable` | `cli/main.py:1558`, `:1680`, `:1691-1693` | Muestra el valor llenado |
| Reportes y export a LLM | Columnas de efficiency y `mfe_favorable`/`mae_adverse` | `tools/report_data.py`, `core/llm_report_export.py:48`, `core/analytics_engine.py:214-215`, `core/plotting_engine.py:120-121` | Cambian los números de los reportes |
| `core/edge_analysis.py` (no se toca) | `specific_bias_compliance` vía `get_market_outcome` (CLAUDE.md, "Auditoría") | — | Ninguno: R8 deja ese campo manual |
| `tools/backfill_r_multiple.py` | `mae_adverse`/`mfe_favorable` | `:107`, `:179`, `:205-206` | Recalcula `captured_*` con los valores nuevos si se vuelve a correr |

---

## 5. Cobertura de tests en la zona

| Test | Qué prueba | Qué **no** prueba |
|---|---|---|
| `tests/test_audit_efficiency_schema.py` (6) | Derivados del schema `EfficiencyAudit` | El wizard |
| `tests/test_tactical_tier_gate.py`, `test_emotional_gate.py`, `test_stop_deviation_journal.py` | La rama `"tac"` hasta el guardado, **siempre con `order_filled = "no"`** (por ejemplo, `tests/test_stop_deviation_journal.py:111-116`) | La rama de orden llenada: `exit_time`, `close_p`, `could_hit_tp`, `mae` y `mfe` |
| `tests/test_entry_time_auto_date.py` | `get_unified_created_at` y la hora de Entry Time | El resto del wizard |
| `tests/test_flow_new_analysis.py` (2) | El wizard completo hasta el guardado y el traspaso a Tactical | Valores persistidos de precios, `created_at` y retroactivo (baseline 001 §5) |
| `tests/test_p2_closed_bars.py` (11) | `closed_bars`, `last_closed_close` y el ancla de análisis | 5M, camino hacia adelante desde el ancla |
| `tests/test_p2_clock.py` (6) | Reloj alineado, desalineado +3h, `mark_price` y retroactivos ignorados | `aligned=None` por pocas referencias; una TF 5M |
| `tests/test_p2_ground_truth.py` (5) | Primer toque, vela doble e incompleto | Hora del toque y MAE/MFE (no existen) |
| `tests/test_p2_ohlc_csv_loader.py` (8), `test_p2_pipeline.py` (30) | Carga de CSV, snapshot, anclas y alcance | Fusión de CSV y recarga tras un re-export |
| `tests/test_export_p2_ohlc.py` (15) | Conversiones de reloj, vela en formación y rango hacia atrás | `main()` (necesita MT5) y escritura atómica |
| `tests/test_cli_report_command.py:71-75` | Rechazo de flags incompatibles | — (**es el test que viola el aislamiento**, §2.13) |

- **No hay ningún test que recorra el wizard de Efficiency** (`grep` de "Real Bias B", `preselected_choice="eff"` y
  `Resolution Type` en `tests/`: 0 coincidencias de uso del wizard).
- **Ningún test llega a la rama de orden llenada del Tactical.**
- Para R10.1, la red de seguridad tiene que cubrir los dos huecos: el orden y los valores guardados del
  Efficiency, y los de la rama llenada del Tactical.
- **Tampoco hay ningún test de aislamiento**: nada falla si un test crea archivos fuera de `tmp_path` o de
  `:memory:` (R16).

---

## 6. Hallazgos que la spec o el plan tienen que resolver

Ninguno de estos es una decisión tomada.

| # | Hallazgo | Evidencia | Afecta a |
|---|---|---|---|
| H1 | `resolution_time` es el sello del guardado del audit, no la hora de resolución: 79/80 con segundos y mediana de 36 h después de `created_at`. **Proponerlo (RF-7) exige un prompt nuevo o cambiar su significado**, y cualquiera de las dos cosas choca con INV-1. **Compararlo (RF-6) no mide acierto** | `cli/main.py:3222`; §2.12 | RF-6, RF-7, INV-1 → `[NECESITA ACLARACIÓN]` |
| H2 | `OPEN` está excluido en el wizard y en la reparación, y `"Open"` es el default de la DB para toda fila no auditada. Una propuesta `Open` no puede ofrecerse, y el backfill "solo NULL" nunca llena `resolution_type` | `cli/main.py:3194`, `:3287`, `:5533`; `tools/database.py:133`; `cli/main.py:2922` | RF-5, RF-7, RF-11, R7 → `[NECESITA ACLARACIÓN]`: ¿`"Open"` cuenta como vacío? |
| H3 | `structural_mae`/`mfe` son **precios** tomados "entre la creación del análisis y su resolución". El resolvedor puede darlos directamente: el precio extremo entre el ancla y el toque | `cli/schemas/audit_efficiency.py:53-58`; `tools/database.py:143-150`; `cli/main.py:3200`, `:3204` | RF-4, RF-7 |
| H4 | Los MAE/MFE tácticos son `mae`/`mfe` (0–10, R) en pydantic y `mae_adverse`/`mfe_favorable` en la DB. La etiqueta del prompt no dice la unidad. 26 filas no llenadas valen `0.0`, no NULL | `cli/schemas/audit_tactical.py:261-262`, `:415-425`; `tools/database.py:230`, `:234`, `:495-498`; `cli/main.py:3953-3954`, `:4984-4985` | RF-9, RF-11 |
| H5 | `session` ya se deriva de `entry_time`. **RF-10 no tiene nada que proponer para `session`**, y la aclaración "¿`session` ya se calcula hoy?" queda respondida: sí | `cli/schemas/audit_tactical.py:437-448` | RF-10 |
| H6 | `could_hit_tp` es un select `"yes"`/`"no"` que se pide después de `close_p` y antes de `mae`/`mfe`. La ventana de evaluación sigue abierta | `cli/main.py:3477-3484`, `:3950` | RF-10 → `[NECESITA ACLARACIÓN]` (`{{VENTANA_TP}}`) |
| H7 | No existe `backdated_timestamp` en la DB: en un retroactivo, `created_at` ya es la hora elegida. El ancla es siempre `created_at`, e `is_backdated` los distingue para R11 | `cli/main.py:2917-2925`; `tools/database.py:118` | RF-4, R11 |
| H8 | El prompt ancla en `created_at` (el instante de "Confirm & Save"), pero el cuaderno y `tools/p2_backtest.py` usan `created_at − 15 min` para juzgar decisiones, y además excluyen los retroactivos. Para **medir resultados**, arrancar al guardar puede perder el tramo entre el inicio del análisis y el guardado | `tools/p2_backtest.py:687`, `:876-879`; CLAUDE.md, "Ancla tardía" | RF-4 → `[NECESITA ACLARACIÓN]`: ¿`created_at` o `created_at − 15 min`? |
| H9 | `first_touch_direction` no da la hora del toque ni los extremos, y devuelve `(None, True)` tanto sin toque como con una vela ambigua. Reutilizarla "sin reimplementar" obliga a extenderla o a envolverla. `core/p2_ground_truth.py` no está en "No toques" | `core/p2_ground_truth.py:52-105`, `:96-97`, `:105` | RF-4, E5, plan |
| H10 | El camino hacia adelante es solo 1H y empieza en `time > as_of` (H21 de 001, vigente). R3 exige arrancar en el ancla con la TF más fina. `TIMEFRAME_MINUTES` no tiene 5M | `tools/p2_backtest.py:83-86`, `:97`, `:451-460` | R3, RF-4, RF-9 |
| H11 | **5M:** el exportador no lo genera, y el único `5M.csv` (XAU) termina el 2026-09-22 a las 10:45, antes de la corrección del reloj de ese día. Tiene el desfase de +3h [NO VERIFICADO: no se calibró; `calibrate_clock_offset` no mira 5M]. `{{TF_TACTICO}}` = "5M donde exista" usaría velas contaminadas | `windows_export/export_p2_ohlc.py:82-90`, `:345`; `tools/p2_backtest.py:1044`; §2.12 | RF-9, R5 → `[NECESITA ACLARACIÓN]` |
| H12 | **Cobertura:** US500 no tiene velas. Todo termina el 2026-09-22 cerca de las 18:00, así que los 5 análisis de US500 (del 09-17 al 09-25) no tienen camino hacia adelante. La 15M de BTC empieza el 07-12 y sus análisis el 07-02. **Un banco que termina antes del horizonte no es lo mismo que `Open`** | §2.12 | RF-4, E3 → la spec tiene que distinguir "sin toque dentro del horizonte" de "banco insuficiente" |
| H13 | El reloj se calibra por DB y por símbolo. USTEC tiene 9 referencias y US500 4, así que los dos dan `aligned=None`. R5 y E2 solo cubren `aligned=False` | `tools/p2_backtest.py:1045`, `:1111-1112`; §2.9 | R5, E2 → `[NECESITA ACLARACIÓN]`: ¿qué hacer con `aligned=None`? |
| H14 | El exportador sobrescribe cada TF sin fusionar y sin escritura atómica. La fusión de R4 tiene que envolverlo o reemplazar su escritura | `windows_export/export_p2_ohlc.py:286-287`, `:345-355` | R4, RF-1, E1 |
| H15 | El `.env` apunta a un solo símbolo, y `tools/edge_evaluation.py` recibe pares `DB=DIR`. Un banco de varios símbolos necesita un mapa, y los CSV actuales tienen que seguir siendo legibles (R4) | `tools/p2_backtest.py:462-479`; `tools/edge_evaluation.py:928-973` | R4, RF-1 |
| H16 | Los helpers del wizard no aceptan un `default`. Para R6 hay que agregarlo, y los tests existentes mockean esos helpers | §2.4 | RF-7 a RF-10, plan |
| H17 | El `payload` del wizard no trae `created_at` ni `is_backdated`. Existe `get_unified_created_at`, pero no un equivalente para `is_backdated` | `tools/database.py:539`, `:567-613` | RF-7, R11 |
| H18 | No hay red de seguridad para el wizard de Efficiency ni para la rama llenada del Tactical | §5 | R10.1 |
| H19 | **H-C confirmado**, más `paused_audits.json` como segunda ruta relativa. El test de R16 tiene que fallar si un test crea o abre cualquiera de los dos fuera de `tmp_path` | §2.13 | R16 |
| H20 | US100 no está migrada (le faltan 3 columnas tácticas). El reporte tiene que pedir columnas explícitas. El backfill, pasando por `init_db` sobre la copia (R9), las agregaría | §2.7 | RF-6, RF-11, R9 |
| H21 | Un backfill sobre análisis ya `SYNCED` no llega a Notion, y cambia los números de los reportes | §4 | RF-11 |
| H22 | Hay 1 fila de XAU con `failure_reason = "Reversal"`, que no es un valor válido del enum. Una propuesta o un backfill que lea ese campo con el enum fallaría | §2.12; `cli/schemas/audit_efficiency.py:31` | RF-8, RF-11 |
| H23 | **Corrección a `CIERRE.md` de 001 (`158869b`):** cita `tools/database.py:381-382` para `engine_default`, pero la línea correcta es `:380-381` | `tools/database.py:380-381` | Documentación |

---

## 7. Resumen de `[NO VERIFICADO]`

1. Que `closed_bars("5M")` y `bar_containing("5M", …)` lancen `KeyError` (H10).
2. Que el `5M.csv` de XAU tenga el desfase de +3h (H11). Se puede verificar con `calibrate_clock_offset` sobre 5M
   una vez que 5M exista en `TIMEFRAME_MINUTES`.
3. Que los `0.0` de `mae_adverse` en filas no llenadas vengan de la reparación (H4).
4. Que la corrida de la suite del 2026-09-23 en el checkout principal haya abierto la DB de US500, y con qué efecto
   (§2.13).
5. Que un backfill sobre análisis `SYNCED` no llegue a Notion (H21).
6. El tramo final de persistencia del Efficiency Audit (`update_record_state`), que no se leyó (§2.2).
7. Que un proveedor creado antes de un re-export no vea las velas nuevas, por el caché (§2.8).
8. Que un fallo a mitad del export deje CSV mezclados (§2.11).

---

## 8. Reutilización del baseline de 001 (re-verificado)

| Parte de 001 | Estado el 2026-09-25 |
|---|---|
| §2.1 `flow_new_analysis` | **Vigente, mismas líneas.** `cli/` no cambió desde `fc639e8`. Re-leídas: `:2492`, `:2615-2618`, `:2877-2946`, `:2917-2925` |
| §2.6 `init_db` y H12 (migra al abrir) | **Vigente.** `tools/database.py` no cambió. `engine_default` está en `:380-381`, no en `:381-382` (H23) |
| §2.7 Cuentas | **Actualizado:** US500 tiene 5 análisis, no 3. Las referencias del reloj de US500 son 4, no 2. La sesión "001" sigue siendo US500 |
| §2.8 `tools/p2_backtest.py` | **Líneas desplazadas por `bd5c987`.** `CsvOHLCProvider` está en `:334-460`, `calibrate_clock_offset` en `:1076-1129` y `CLOCK_MIN_ENTRIES` en `:1045`. **H19 (look-ahead) está resuelto** con `closed_bars` (`:386-396`). El `select` de la entidad completa (H11 de 001) no se re-leyó para 002, porque 002 no lo usa |
| §2.8, import de `cli.main` | **Corregido:** el import no dispara `get_active_engine()` (§2.13) |
| §2.10 Exportador | **Vigente, mismas líneas** (`windows_export/` no cambió). H7 a H9 siguen abiertos (H14) |
| §2.11 Cobertura de CSV | **Vigente:** mismas filas y fechas. Se agregó 5M de XAU (§2.12) |
| §2.12 Entorno del worktree | **Actualizado:** la suite crea `.data/` (H-B, §2.13) |
| H6 (reloj con <10 referencias) | **Vigente** (H13) |
| H15 (caché del proveedor) | **Vigente** (§2.8) |
| H20 (no existe la hora de inicio del análisis) | **Vigente** (H8) |
| H21 (tramo inicial no observado) | **Vigente** (H10); R3 lo exige resolver |
