# Tareas — Spec 002: auto-resolución con velas

- **Fecha:** 2026-09-27.
- **Fase:** F5.
- **Base:** `plan.md` (aprobado el 2026-09-27) y `spec.md`.
- **Orden:** R10, con la corrección de H-C primero (R16).
- **Ritmo:** una tarea por turno, con la suite en verde y la salida mostrada (F7).

**Convenciones**
- `WT` = `/home/jorgecg/projects/trading/blast_master/.claude/worktrees/spec-001-closure-tests-b99a6d`, la fotocopia de
  esta sesión, sin `.data/` ni `.env` reales.
- **SUITE** = los tres pasos de R14, siempre en `WT`:
  1. `ls -d $WT/.data` debe fallar (no existe);
  2. `conda run --cwd $WT -n blast_master pytest -q -p no:cacheprovider`;
  3. `find $WT/.data -type f` después de correr.
- "En verde" significa `0 failed`, con el conteo de `passed` informado.
- **Las tareas marcadas 🖐 son manuales.** Necesitan que estés presente o tu aprobación explícita (R9, R17, o escribir en
  `.data/` real).

## Etapa 0 — Punto de partida y aislamiento de tests (R16)

- [x] T1. Correr la suite de referencia en `WT` y limpiar lo que deja H-C. (RF: —)
      Hecho cuando: SUITE da `429 passed, 1 skipped`; `find` muestra solo `.data/flight_sessions.json` y
      `.data/flight_account_001_xauusd.db` (los de H-C, documentados); y ese `.data/` de `WT` se borra, dejando
      `ls -d $WT/.data` en falla.
      **Hecho 2026-09-28:** `429 passed, 1 skipped, 92 warnings in 10.44s`. `find .data -type f` dio exactamente los
      2 archivos documentados de H-C, nada más. Se borró `.data/` después; `ls -d .data` vuelve a fallar.
- [x] T2. Arreglar `tests/test_cli_report_command.py:71-75` para que fije `tools.database.engine_default` con
      `monkeypatch`. (RF-16b)
      Hecho cuando: SUITE en verde (`429 passed, 1 skipped`) y `find $WT/.data -type f` no lista nada.
      **Hecho 2026-09-28:** `test_report_command_rejects_conflicting_flags` (líneas 71-79) era el único de los 3 tests
      del archivo sin `monkeypatch`; como `cli()` (`cli/main.py:996-998`) llama `get_active_engine()` para
      **cualquier** subcomando antes de despachar, ese test creaba `.data/flight_account_001_xauusd.db` y
      `.data/flight_sessions.json` reales aunque el propio comando `report` nunca llegara a leerlos (el chequeo de
      flags en conflicto ocurre después). Se le agregó `monkeypatch` + `init_db("sqlite:///:memory:")` +
      `monkeypatch.setattr(tools.database, "engine_default", ...)`, igual que los otros dos tests. SUITE:
      `429 passed, 1 skipped, 92 warnings in 7.47s`; `find .data -type f` ya no encuentra nada (ni `.data/` existe).
- [x] T3. Crear `tests/conftest.py`: un fixture autouse `monkeypatch.chdir(tmp_path)` y un audit hook sobre `open` y
      `sqlite3.connect` que hace fallar el test si la ruta cae bajo `<repo>/.data/` o `/mnt/c/`. (RF-16)
      Hecho cuando: SUITE en verde y sin `.data/` en `WT` después de correr.
      **Hecho 2026-09-28:** `tests/conftest.py` nuevo, con dos capas: (1) fixture autouse `_isolated_cwd` que hace
      `monkeypatch.chdir(tmp_path)` en cada test; (2) `sys.addaudithook` sobre los eventos `open` y `sqlite3.connect`,
      instalado una sola vez al importar el archivo, que resuelve la ruta a absoluta y hace fallar la operación
      (`ForbiddenTestPathError`) si cae bajo el `.data/` real del repo o bajo `/mnt/c/`; deja pasar `:memory:` y
      cualquier otra ruta. Antes de commitear se probó a mano con un script fuera de la suite (no forma parte de
      T4, que es el meta-test formal): `open()` y `sqlite3.connect()` bajo `.data/` real bloqueados, `/mnt/c/`
      bloqueado, `:memory:` y una ruta de `tempfile` permitidos. SUITE: `429 passed, 1 skipped, 92 warnings in
      6.99s`; `find .data -type f` no encuentra nada, `.data/` no existe.
- [x] T4. Meta-test de aislamiento. Corre `pytest` en un subproceso sobre un archivo temporal con un test que abre
      `<repo>/.data/x.db` y otro que abre `/mnt/c/x`. (RF-16)
      Hecho cuando: el meta-test pasa (el subproceso informa 2 fallos por el guard) y SUITE en verde.
      **Hecho 2026-09-28:** `tests/test_isolation_guard.py` nuevo. Escribe `tests/test_zz_isolation_meta_tmp.py`
      (temporal, con dos tests: uno abre `<repo>/.data/x.db` con ruta absoluta —así se salta la capa 1, el `chdir`
      a `tmp_path`— y el otro `/mnt/c/x`), corre `pytest -q -p no:cacheprovider` sobre ese archivo en un subproceso
      (`cwd=<repo>`, así `tests/conftest.py` se descubre solo) y verifica en el output: `returncode == 1`,
      `"2 failed"`, los dos nombres de test y `ForbiddenTestPathError`. También verifica que `.data/x.db` nunca
      llegó a crearse (el hook aborta `open()` antes del syscall). Borra el archivo temporal y su `__pycache__` en
      un `finally`, con limpieza defensiva al empezar por si una corrida anterior se cortó a mitad. SUITE:
      `430 passed, 1 skipped, 92 warnings in 13.38s`; sin `.data/`, sin el archivo temporal, sin `__pycache__`
      residual.

## Etapa 1 — Red de seguridad de los wizards (R10.1)

- [x] T5. Test de red de seguridad del wizard de Efficiency, contra el código **sin modificar**: el orden de los 7
      prompts (baseline §2.2), `OPEN` excluido, y los valores guardados en `efficiency_audit` (tipo, estructurales,
      precios de MAE/MFE y un `resolution_time` no nulo). (INV-1)
      Hecho cuando: `pytest -q tests/test_wizard_safety_net.py -k efficiency` pasa y SUITE en verde.
      **Hecho 2026-09-28:** `tests/test_wizard_safety_net.py` nuevo (compartido con T6/T7). Dos tests: uno maneja
      `flow_pending_audits(preselected_choice="eff")` con `cli.main.get_enum_choice`/`get_optional_text` e
      `InquirerPy.inquirer.text`/`select` mockeados, verifica los 4 `call_args` de `get_enum_choice` en orden
      (real_bias_b, res_type con `exclude=[ResolutionType.OPEN]`, struct_res, fail_reason), el orden de
      lesson_eff → MAE → MFE, y los 8 valores guardados en la fila ORM de `efficiency_audit` (incluido
      `resolution_time` acotado entre el antes/después de la llamada — la hora del guardado, nunca preguntada,
      baseline §2.2). El otro prueba que un MAE inválido deja MAE **y** MFE en `None`, aunque el MFE sí
      convertía. `pytest -q tests/test_wizard_safety_net.py -k efficiency`: `2 passed in 0.56s`. SUITE:
      `432 passed, 1 skipped, 96 warnings in 9.55s`; sin `.data/`.
- [x] T6. Test de red de seguridad de la rama llenada del Tactical: el orden desde `mid_trade_emotions` hasta
      `visual_lesson_path` (baseline §2.3), más `mae_adverse`, `mfe_favorable`, `could_hit_tp`, `exit_time` y
      `session` guardados. (INV-1)
      Hecho cuando: `pytest -q tests/test_wizard_safety_net.py -k tactical` pasa y SUITE en verde.
      **Hecho 2026-09-28:** agregado a `tests/test_wizard_safety_net.py`. Corre
      `flow_pending_audits(preselected_choice="tac", force_new_tactical=True)` por el camino principal (gates 7/7,
      2 confirmaciones → Tier C, no bloquea) con `order_filled → "yes"`, y confirma: `get_mandatory_text` con
      `Mid Trade Emotions` → `Post Trade Emotions` → `Tactical Lesson Learned` en ese orden; `Exit Type` y
      `Followed Plan` en las posiciones 6ª y 7ª de los 7 `get_enum_choice`; `get_mandatory_datetime` llamado una vez
      con `"Exit Time"`; y en la fila ORM: `mae_adverse`, `mfe_favorable`, `could_hit_tp`, `exit_time`,
      `mid_trade_emotions`, `post_trade_emotions`, `exit_type`, `closing_price`, `followed_plan`,
      `lesson_learned`. `session` sale auto-derivado de `entry_time` (nunca preguntado) — 10:30 GT + 6h = 16:30
      UTC cae en la banda de New York, verificado igual. `pytest -q tests/test_wizard_safety_net.py -k tactical`:
      `1 passed in 0.50s`. SUITE: `433 passed, 1 skipped, 97 warnings in 9.14s`; sin `.data/`.
- [x] T7. Test de red de seguridad de `flow_new_analysis`: los precios guardados, `created_at` igual a la hora
      tipeada en un retroactivo, e `is_backdated`. (INV-7)
      Hecho cuando: `pytest -q tests/test_wizard_safety_net.py -k new_analysis` pasa y SUITE en verde.
      **Hecho 2026-09-28:** agregado a `tests/test_wizard_safety_net.py`, con un helper local
      `_drive_new_analysis_wizard` (mismo patrón que `tests/test_flow_new_analysis.py`, copiado para no acoplar
      archivos de test entre sí). Dos tests: (1) Mark Price, Edge Validation Price y Structural Invalidation
      quedan como `Decimal` cuando se completan, en ese orden de prompt; (2) con `backdated_timestamp`,
      `created_at`/`updated_at` quedan en la hora tipeada tanto en `unified_department` como en
      `efficiency_audit` (baseline §2.1, `cli/main.py:2917-2925`), e `is_backdated=True`.
      `pytest -q tests/test_wizard_safety_net.py -k new_analysis`: `2 passed in 0.94s`. SUITE:
      `435 passed, 1 skipped, 97 warnings in 9.62s`; sin `.data/`. Cierra la Etapa 1 (red de seguridad de los
      wizards, R10.1).

## Etapa 2 — Banco de velas y reloj

- [x] T8. `config/auto_resolution.py` con todas las constantes de la spec y los códigos de motivo; resolución de
      rutas desde `.env` (`CANDLE_BANK_DIR`, `MT5_INCOMING_DIR`, `ACCOUNTS_DATA_DIR`, `WINDOWS_PYTHON`,
      `EXPORTER_WIN_PATH`, `AUTO_EXPORT`, `BROKER_DST_RULE`), siempre como rutas absolutas; y `.env.template`
      actualizado. Incluye `P2_LOG_MODELS = {"D": "2026-09-27"}` (N42). (Configuración, RF-20, RF-15b, RF-12e)
      Hecho cuando: un test verifica los valores (48 h, 0.5R, 1R, 20 min, 0.1%, 10 velas, 2160), que cada fecha de
      `P2_LOG_MODELS` es una fecha ISO válida, y que las rutas sean absolutas aunque se corra desde `tmp_path`. SUITE
      en verde.
      **Hecho 2026-09-28:** `config/auto_resolution.py` nuevo, con `ROOT_DIR` anclado a `__file__` (nunca a `cwd`,
      mismo patrón que `tools/backup.py:38-43`, incluido su propio `load_dotenv()`) y `os.path.abspath()` en cada
      ruta, así son absolutas sin importar desde dónde se importe el módulo. `CLOCK_MIN_ENTRIES` (la otra regla de
      "10") ya existe en `tools/p2_backtest.py:1045` — no se duplicó. Motivos con parámetro (`insufficient_history`,
      `unknown_model`, `timeframe_not_in_bank`, `model_recipe_changed`) como funciones, no f-strings sueltos.
      `.env.template` actualizado con las 7 variables nuevas, comentadas, sin valores reales.
      `tests/test_config_auto_resolution.py` nuevo: los valores de negocio, las 2 tablas (cuentas, símbolos),
      `P2_LOG_MODELS` y que su fecha sea ISO válida (`date.fromisoformat`), los 8 códigos de motivo E1-E8 y sus
      plantillas, el orden de `RESOLUTION_TIME_SOURCE_VALUES` contra `plan.md:68-69`, y — reimportando el módulo
      en caliente estando `chdir`'ado a un `tmp_path` sin las variables de entorno — que las rutas siguen siendo
      absolutas y siguen apuntando al repo real, no a `tmp_path`. `pytest -q tests/test_config_auto_resolution.py`:
      `12 passed in 0.03s`. SUITE: `447 passed, 1 skipped, 97 warnings in 7.09s`; sin `.data/`.
      en verde.
- [x] T9. En `tools/p2_backtest.py`, agregar `5M` y `1M` a `TIMEFRAME_MINUTES`. (RF-15, RF-4f)
      Hecho cuando: `closed_bars` y `bar_containing` funcionan con un fixture de 5M y otro de 1M
      (test nuevo), y `tests/test_p2_*.py` sigue en verde. SUITE en verde.
      **Hecho 2026-09-28:** cambio de una línea (`"5M": 5, "1M": 1`). `TIMEFRAME_MINUTES` solo se lee por
      `[timeframe]` en `closed_bars`/`bar_containing` (verificado con grep, ningún sitio lo itera completo), así
      que es puramente aditivo — INV-4/INV-5 intactos. `windows_export/export_p2_ohlc.py` tiene su propio dict
      separado, sin tocar. `tests/test_p2_finer_timeframes.py` nuevo (7 tests, mismo patrón de fixture que
      `tests/test_p2_closed_bars.py`): `closed_bars` y `bar_containing` con 5M y 1M, incluida la vela en
      formación excluida, una vela que cierra justo en el ancla, y los bordes antes/después del rango.
      `pytest -q tests/test_p2_*.py`: `118 passed in 1.58s`. SUITE: `454 passed, 1 skipped, 97 warnings in
      14.23s`; sin `.data/`.
- [x] T10. Extraer `evaluate_clock_entries(entries, provider, tf, offsets)` de `calibrate_clock_offset`, sin
      cambiar su resultado, y agregar `count_entries_in_range()`. (RF-2, RF-2b, RF-4f)
      Hecho cuando: `tests/test_p2_clock.py` sigue en verde sin cambios; un test nuevo prueba que las dos rutas dan el
      mismo resultado y cuenta referencias dentro del rango. SUITE en verde.
      **Hecho 2026-09-28:** `evaluate_clock_entries()` es exactamente el bucle interno de `calibrate_clock_offset`
      (el `for off in offsets: ...` que arma `rates`), movido tal cual a una función aparte;
      `calibrate_clock_offset` ahora la llama en lugar de tener el bucle inline. `count_entries_in_range()` es
      nueva, para la regla de "≥10 referencias dentro del rango exportado" (N29, plan.md §3.8 paso 4) que usará
      `tools/candle_bank.py` — distinta de `CLOCK_MIN_ENTRIES`, que exige 10 en toda la cuenta sin acotar a un
      rango. Los 6 tests existentes de `tests/test_p2_clock.py` no se tocaron. 2 tests nuevos: uno arma las mismas
      `entries` que arma `calibrate_clock_offset` internamente y compara `evaluate_clock_entries(...)` contra
      `c.rate_by_offset` (iguales, con CSV corrido +3h para que `best_offset` no sea 0 por casualidad); otro
      prueba `count_entries_in_range` con bordes inclusivos, un rango vacío y uno que cubre todo.
      `pytest -q tests/test_p2_clock.py`: `8 passed in 1.10s`. `pytest -q tests/test_p2_*.py`: `120 passed in
      1.38s`. SUITE: `456 passed, 1 skipped, 97 warnings in 14.32s`; sin `.data/`.
- [x] T11. `tools/candle_bank.py`, parte 1: lectura y escritura atómica de un `{TF}.csv` (temporal + `os.replace`) y
      la fusión por `time`, conservando la vela existente. (RF-1, RF-1b)
      Hecho cuando: los tests prueban que ninguna vela previa desaparece ni cambia, que la cobertura hacia atrás no
      se reduce, y que un duplicado conserva la vela del banco. SUITE en verde.
      **Hecho 2026-09-28:** `tools/candle_bank.py` nuevo (parte 1 de 6; T12-T16 agregan candado, verificación por
      superposición y por referencias, filtro de estación y `status.json` sobre estas mismas funciones):
      `read_candle_csv`/`write_candle_csv_atomic` (temporal en el mismo directorio + `os.replace`, atómico en
      POSIX), `merge_candle_frames` (unión por `time`, `keep="first"` con el banco primero en el `concat` para
      que gane sobre el export) y `merge_timeframe_into_bank` (compone las tres, sin candado ni verificación —
      eso es de otras tareas). Reusa `REQUIRED_CSV_COLUMNS` de `tools/p2_backtest.py` para no divergir del
      formato que ya lee `CsvOHLCProvider` (INV-6). `tests/test_candle_bank.py` nuevo, 15 tests: ninguna vela
      previa desaparece ni cambia, la cobertura hacia atrás nunca se reduce, un duplicado conserva la vela del
      banco (no la entrante), el resultado queda ordenado, el primer merge sobre un banco vacío trae todo, y una
      falla simulada a mitad de la escritura (`to_csv` mockeado para tirar `RuntimeError`) deja el archivo real
      byte a byte igual y sin temporales sueltos. `pytest -q tests/test_candle_bank.py`: `15 passed in 1.18s`.
      SUITE: `471 passed, 1 skipped, 97 warnings in 10.05s`; sin `.data/`.
- [x] T12. `candle_bank`, parte 2: candado por símbolo (PID y vencimiento de 2 h). (RF-1d)
      Hecho cuando: los tests prueban que con el candado tomado se cancela y el banco queda byte a byte igual, y que
      un candado vencido se reemplaza. SUITE en verde.
      **Hecho 2026-09-28:** `acquire_bank_lock(bank_dir, stale_hours=2.0)` en `tools/candle_bank.py`, mismo
      formato de candado (PID + hora) y misma lógica de vencimiento que `tools/backup.py:122-174`
      (`acquire_backup_lock`/`_is_pid_alive`) — con una diferencia deliberada: es un context manager que levanta
      `CandleBankLockedError` en vez de `sys.exit(2)`, porque acá el candado protege una fusión dentro de una
      llamada de librería, no un script de proceso completo; quien llame (`candle_sync.py`, T20; los subcomandos
      `candles`, T21) atrapa la excepción para el mensaje "Candle export skipped: ..." sin matar el proceso. Se
      libera solo al salir del `with`, incluso si el cuerpo lanza. Un candado corrupto/ilegible también se trata
      como huérfano, igual que `backup.py`.
      `tests/test_candle_bank.py` +7 tests, mismo patrón que `tests/test_backup.py` (PID del propio proceso de
      test para "vivo", un subproceso real ya terminado para "muerto", un timestamp de 3h para "vencido", un
      candado corrupto): candado tomado y vivo cancela; PID muerto se recupera; TTL vencido se recupera; JSON
      corrupto se recupera; se libera en salida normal y en excepción; y un test combinado que toma el candado a
      mano, intenta `merge_timeframe_into_bank` bajo `acquire_bank_lock`, y confirma que el `.csv` del banco
      queda byte a byte igual (RF-1d). `pytest -q tests/test_candle_bank.py`: `22 passed in 0.66s`. SUITE:
      `478 passed, 1 skipped, 97 warnings in 8.00s`; sin `.data/`.
- [x] T13. `candle_bank`, parte 3: verificación por superposición (1H, 30M, 15M, 5M y 1M; `OVERLAP_MIN_BARS`;
      tolerancia 1e-9). (RF-2d, N32)
      Hecho cuando: los tests prueban que la superposición igual verifica, que una vela distinta da
      `clock_misaligned` sin fusionar, y que menos de 10 velas superpuestas no verifican. SUITE en verde.
      **Hecho 2026-09-28:** `verify_overlap(bank_dir, incoming_dir, ...)` en `tools/candle_bank.py`, de solo
      lectura (RF-2d es solo el veredicto; fusionar según el resultado es tarea de T15). Recorre
      `OVERLAP_TIMEFRAMES = ("1H", "30M", "15M", "5M", "1M")` en ese orden de prioridad — 4H/12H/1D/1W quedan
      afuera porque no necesitan verificación de reloj (N39). Por cada TF, `_bars_overlap_match` compara las
      velas con `time` en común, con tolerancia relativa `OVERLAP_PRICE_TOLERANCE = 1e-9` (plan T20) contra el
      valor del banco. Una discrepancia en CUALQUIER TF corta ahí (`misaligned=True`, sin seguir mirando las
      demás) e invalida cualquier `verified_timeframe` ya encontrado en una TF anterior — probado con un caso
      donde 1H verifica y 30M (chequeada después) desalinea. Sin discrepancias, `verified=True` con la primera TF
      que llegó a `OVERLAP_MIN_BARS` velas superpuestas coincidentes; con 0 superposición en todas las TF (primer
      export de un símbolo) o con superposición insuficiente, ni verifica ni desalinea — le toca a T14
      (referencias).
      `tests/test_candle_bank.py` +9 tests: verifica con superposición suficiente; una vela distinta desalinea
      (sin `verified_timeframe`); menos de `OVERLAP_MIN_BARS` no decide nada; cero superposición en todas las TF
      no decide nada; una diferencia de `1e-10` (dentro de tolerancia) sigue verificando; una de `1e-6` (fuera de
      tolerancia) desalinea; un desalineo en una TF posterior (30M) invalida un `verified` anterior (1H); y que
      `verify_overlap` nunca escribe el banco (byte a byte igual antes/después, aunque el resultado sea
      `clock_misaligned`). `pytest -q tests/test_candle_bank.py`: `31 passed in 1.35s`. SUITE:
      `487 passed, 1 skipped, 97 warnings in 8.79s`; sin `.data/`.
- [x] T14. `candle_bank`, parte 4: verificación por referencias. Junta fills y Mark Price no retroactivos de las
      cuentas de `REAL_ACCOUNTS` que mapean al símbolo, con `mode=ro`, exige 10 dentro del rango y
      `evaluate_clock_entries` alineado. (RF-2, RF-2b, N29)
      Hecho cuando: los tests, con DBs de fixture en `tmp_path`, cubren 12 referencias adentro (verifica), 9 adentro
      (`clock_unverified`) y un reloj corrido +3h (`clock_misaligned`). SUITE en verde.
      **Hecho 2026-09-28:** `gather_reference_entries()` (RF-2b) junta fills y Mark Price no retroactivos de las
      cuentas de `REAL_ACCOUNTS` cuyo `asset` mapea al símbolo MT5 pedido (vía `MT5_SYMBOL_MAP`), leídas con
      `tools.p2_backtest.open_readonly_session` (`mode=ro`, nunca migra la DB real) — una cuenta cuyo archivo no
      existe se saltea. `verify_by_references()` (RF-2, N29) cuenta cuántas caen en `[export_start, export_end]`
      con `count_entries_in_range` (T10); si son menos que `min_references` (default `CLOCK_MIN_ENTRIES` de
      `tools/p2_backtest.py`, mismo "10" que la regla de N29 — no se duplicó como constante nueva), corta ahí con
      `aligned=None`, sin evaluar el reloj. Si alcanzan, corre `evaluate_clock_entries()` (T10) sobre el CSV
      entrante y aplica la MISMA regla de `aligned` que `calibrate_clock_offset` (offset 0 óptimo, o dentro de
      `CLOCK_MISALIGNMENT_MARGIN` del mejor).
      `tests/test_candle_bank.py` +7 tests: `gather_reference_entries` filtra por `asset` y agrega varias
      cuentas, incluye Mark Price y excluye retroactivos, y saltea un archivo faltante sin error; y los 3
      escenarios del "hecho cuando" — 12 referencias alineadas verifica (`best_offset=0`), 9 referencias no
      alcanza (`aligned=None`, sin evaluar), y un CSV corrido +3h dentro del rango desalinea (`aligned=False`,
      `best_offset=3`) — más un caso sin ninguna TF de `CLOCK_TIMEFRAME_PREFERENCE` en el export entrante.
      `pytest -q tests/test_candle_bank.py`: `38 passed in 4.79s`. SUITE: `494 passed, 1 skipped, 97 warnings in
      18.64s`; sin `.data/`.
- [x] T15. `candle_bank`, parte 5: filtro por estación de horario (RF-15c), `status.json`, y el banco sin cambios
      ante cualquier fallo (RF-1c). (RF-1c, RF-15c)
      Hecho cuando: los tests prueban que, con la regla sin verificar, en 1H y en las TF menores solo entran las velas
      de la estación del export, mientras que 4H, 12H, 1D y 1W entran completas (N39);
      que `status.json` refleja el resultado, y que ante una excepción a mitad de la fusión el banco queda byte a
      byte igual. SUITE en verde.
      **Hecho 2026-09-28:** tres piezas, todavía sin la orquestación completa "candado → verificar → filtrar →
      fusionar → status.json" para un símbolo (eso lo arma T16 en adelante, componiendo T11-T15):
      1. `filter_by_export_season()`: en `OVERLAP_TIMEFRAMES` (1H y más finas) y con `BROKER_DST_RULE` distinto de
         `"none"`, deja solo las velas cuya fecha cae en la misma estación de horario de verano que
         `export_moment` (`_dst_active_on`, con las fechas de cambio de EE.UU. o UE, calculadas con
         `_nth_weekday_of_month`/`_last_weekday_of_month`); 4H/12H/1D/1W y `dst_rule="none"` no filtran nada. No
         hay ninguna bandera de "verificado" en el sistema todavía, así que el filtro está activo siempre.
      2. `status.json`: `build_status_payload()` arma el dict con la forma exacta del ejemplo de plan.md §2.3;
         `read_bank_status()`/`write_bank_status()` (atómico, mismo patrón que `write_candle_csv_atomic`).
      3. `merge_timeframe_into_bank_checked()`: como la fusión de T11, pero con `_assert_bank_not_regressed()`
         antes de escribir — confirma que ninguna vela existente se pierde ni cambia de valor (garantizado por
         construcción en `merge_candle_frames`, pero es una red de seguridad extra); si algo no cuadra, levanta
         `RuntimeError` **antes** de tocar el archivo.
      `tests/test_candle_bank.py` +14 tests: fechas de cambio de DST sin ambigüedad para `us` y `eu`; el filtro
      conserva la misma estación y descarta la opuesta en las 5 TF finas; deja completas las 4 TF lentas; con
      `dst_rule="none"` no filtra nada; `build_status_payload` calza con el ejemplo del plan; ida y vuelta de
      `status.json` sin temporales sueltos; un resultado `clock_misaligned` se refleja correctamente; la fusión
      chequeada agrega velas igual que la de T11; una fusión rota a propósito (mockeada para perder velas del
      banco) levanta `RuntimeError` sin escribir nada; y una falla real a mitad de la escritura (mismo truco de
      T11 con `to_csv` mockeado) deja el banco byte a byte igual, sin temporales.
      `pytest -q tests/test_candle_bank.py`: `52 passed in 1.33s`. SUITE: `508 passed, 1 skipped, 97 warnings in
      8.47s`; sin `.data/`.
- [x] T16. `candle_bank`, parte 6: `import_legacy()` desde `MT5Exports/{SYMBOL}/`, sin el `5M.csv` de XAU. (RF-2c)
      Hecho cuando: el test, con un directorio de fixture que imita `MT5Exports`, prueba que se importa lo
      verificado, que el 5M queda excluido e informado, y que el origen no se modifica. SUITE en verde.
      **Hecho 2026-09-28:** `import_legacy()` es la única función del módulo que arma una orquestación completa
      hasta ahora (de solo lectura sobre el origen): junta las TF presentes en `legacy_dir`, reusa
      `verify_by_references()` (T14) sobre TODO el rango de fechas del origen — así "verificado" significa lo
      mismo que en un export nuevo, `min_entries` referencias en rango y reloj alineado — y solo si verifica,
      importa cada TF con `merge_timeframe_into_bank_checked()` (T15). Si no verifica, no escribe nada y **todas**
      las TF presentes quedan en `excluded` con el motivo (`clock_unverified`/`clock_misaligned`); ni el
      directorio del banco se crea. `LEGACY_IMPORT_EXCLUSIONS = {("XAUUSD", "5M")}` (baseline H11) excluye ese par
      puntual aunque XAU verifique — un TF `"5M"` de cualquier otro símbolo no se toca, la exclusión es por
      `(símbolo, TF)`, no solo por nombre de TF.
      `tests/test_candle_bank.py` +8 tests: importa todas las TF presentes cuando verifica; excluye XAU/5M con
      motivo aunque el resto verifique; el mismo nombre `"5M"` en BTC no se excluye; con pocas referencias no
      importa nada y ni crea el directorio del banco; un reloj corrido excluye todo con `clock_misaligned`; un
      origen vacío da `aligned=None`; y el origen queda byte a byte igual después de importar.
      `pytest -q tests/test_candle_bank.py`: `60 passed in 1.80s`. SUITE: `516 passed, 1 skipped, 97 warnings in
      9.03s`; sin `.data/`.
- [x] T17. Exportador, parte 1: agregar `5M` y `1M` al mapa de TF, a la duración y al rango hacia atrás, más el
      argumento `--timeframes`. (RF-15)
      Hecho cuando: los tests, con el stand-in de `MetaTrader5`, cubren el mapa y `compute_backward_start` para
      5M/1M; `tests/test_export_p2_ohlc.py` sigue en verde. SUITE en verde.
      **Hecho 2026-09-28:** `windows_export/export_p2_ohlc.py`: `5M`/`1M` en `TIMEFRAME_MAP`
      (`mt5.TIMEFRAME_M5`/`TIMEFRAME_M1`, mismo criterio de "verificado contra la doc oficial" que las demás),
      en `TIMEFRAME_MINUTES` y en el `span` de `compute_backward_start`. `ALL_EXPORT_TIMEFRAMES` nueva, las 9 en
      orden, reemplaza la tupla de 7 hardcodeada en el loop de `main()`. `--timeframes` nuevo, con el parsing
      extraído a `parse_timeframes_arg()` (función pura, mismo criterio que `compute_backward_start` —
      testeable sin MT5): sin valor, las 9 de siempre; con una lista separada por coma, la recorta y valida
      contra `TIMEFRAME_MAP`, levantando `ValueError` con las desconocidas antes de intentar `mt5.initialize()`.
      El stand-in de `MetaTrader5` en el test ganó `TIMEFRAME_M30/M15/M5/M1` explícitos (antes solo hacía falta
      para que `TIMEFRAME_MAP` no reventara, por el auto-vivify de `MagicMock`; ahora quedan documentados como
      las demás).
      `tests/test_export_p2_ohlc.py` +10 tests: el mapa y las duraciones tienen 5M/1M; `ALL_EXPORT_TIMEFRAMES`
      son exactamente las 9, en orden, y coincide con las claves de `TIMEFRAME_MAP`; `compute_backward_start`
      para 5M y 1M se sumó a la parametrización existente (mismo test que ya cubría 1H-1W); y
      `parse_timeframes_arg`: default las 9, parsea una lista, recorta espacios y descarta vacíos, acepta 5M/1M,
      y rechaza una TF desconocida listando las válidas en el mensaje.
      `pytest -q tests/test_export_p2_ohlc.py`: `29 passed in 0.31s`. SUITE: `526 passed, 1 skipped, 97 warnings
      in 8.98s`; sin `.data/`.
- [x] T18. Exportador, parte 2: conversión vela por vela según `--dst-rule {us,eu,none}`, descartando la hora del
      cambio. (RF-15b, N34)
      Hecho cuando: los tests prueban que una vela de enero y otra de julio exportadas en septiembre con la regla
      `us` quedan con el offset correcto, que `none` da lo mismo que hoy, y que se informan las descartadas. SUITE en
      verde.
      **Hecho 2026-09-28:** `windows_export/export_p2_ohlc.py`. Funciones puras nuevas: `dst_transition_dates()`
      (`us`: 2do domingo de marzo → 1er domingo de noviembre; `eu`: último domingo de marzo → último de octubre),
      `dst_masks()` (`is_dst` y `ambiguous` por vela), `base_offset_from_current()` (el offset detectado/pasado es
      el de "ahora"; el base de invierno sale de restarle 1h si "ahora" está en verano; falla si "ahora" cae en la
      hora del cambio) y `server_time_to_utc_dst()` (offset vigente en la fecha de CADA vela). `export_timeframe()`
      gana `dst_rule` (default `none`), descarta las velas ambiguas, avisa por stderr cuántas fueron y las expone en
      `ExportResult.discarded_dst`. `--dst-rule {us,eu,none}` nuevo, default `none` (= comportamiento de siempre);
      `main()` deduce el offset base y lo imprime. El rango pedido a MT5 sigue usando el offset de ahora (`date_to`
      es "ahora" exacto y `date_from` tiene semanas de colchón, así que 1h no importa).
      **Supuestos [NO VERIFICADO] para el spike (T22), no inventados en silencio:** `DST_TRANSITION_HOUR = 2` (hora
      del día del cambio en que cae el salto; la vela de `[02:00, 03:00)` se descarta) y que la regla real del broker
      sea la de EE.UU./UE — ambos quedan como constantes nombradas y documentados en la docstring del módulo.
      El exportador es standalone (no importa del repo), así que las fechas de cambio están duplicadas de
      `tools/candle_bank.py`; un test compara las dos implementaciones día por día durante 2026-2027 para las dos
      reglas, así no divergen sin que se note.
      `tests/test_export_p2_ohlc.py` +13 tests: fechas de cambio 2026 (verificadas con código: `us` 8-mar/1-nov,
      `eu` 29-mar/25-oct); coincidencia con `candle_bank` (2×730 días); `none` todo False; la hora del cambio es
      ambigua en las dos transiciones (01:59 no, 02:00-02:59 sí, 03:00 no); `base_offset_from_current`; una vela de
      enero y otra de julio exportadas en septiembre con `us` (servidor +3 → base +2) quedan a +2 y +3 respectivamente;
      `none` da idéntico a `server_time_to_utc` con un solo offset; y `export_timeframe` de punta a punta con
      `mt5.copy_rates_range` reemplazado: con `us` escribe 2 velas, descarta 1 y lo informa; con `none` escribe las 3,
      con enero y julio a la misma hora GT (el desfase de 1h de siempre). Comprobación de mutación: cambiar
      `DST_TRANSITION_HOUR` rompe 3 tests (revertido).
      `pytest -q tests/test_export_p2_ohlc.py`: `42 passed in 1.68s`. SUITE: `539 passed, 1 skipped, 97 warnings in
      14.44s`; sin `.data/`.
- [x] T19. Exportador, parte 3: un directorio único por corrida y escritura atómica por archivo. (RF-15, plan T2)
      Hecho cuando: los tests prueban que dos corridas no se pisan y que un fallo a mitad no deja CSV a medias. SUITE en
      verde.
      **Hecho 2026-09-28:** `windows_export/export_p2_ohlc.py`. `atomic_write_csv()` (temporal en el mismo
      directorio + `os.replace`; si falla, borra el temporal y deja el destino como estaba) reemplaza el `to_csv`
      directo de `export_timeframe` — vale en los dos modos. `make_run_dir(base, symbol)` crea
      `{base}/{SIMBOLO}/{run_id}/` con `run_id = YYYYmmddTHHMMSS` (UTC) y `mkdir` sin `exist_ok` (atómico: dos
      corridas nunca comparten carpeta); si el segundo ya existe prueba `-1`, `-2`…, que además mantienen el orden
      alfabético == cronológico. `main()` gana `--per-run-dir` e imprime `RUN_ID: …` y `RUN_DIR: …` (lo que va a
      parsear `tools/candle_sync.py`, T20). El directorio se crea recién con MT5 inicializado y el reloj resuelto,
      así que un fallo temprano no deja carpetas vacías.
      **Decisión de diseño:** `--per-run-dir` es opt-in. Sin el flag, el script escribe directo en `--out-dir`, como
      siempre, para no redirigir en silencio las corridas manuales que hoy alimentan el cuaderno
      (`MT5Exports/{SYMBOL}/`, INV-6). T20 tiene que pasar `--per-run-dir` con `--out-dir` = `MT5_INCOMING_DIR`.
      Una corrida que falla deja su directorio con las TF que sí terminaron (completas, nunca a medias), para poder
      auditarla; quien orquesta se guía por el código de salida (RF-20e).
      **No incluido, porque ninguna tarea lo pide:** la retención de "las últimas 5 corridas por símbolo" de
      plan.md §2.4. Lo natural es hacerlo en T20 (`candle_sync.py`), que es quien sabe cuándo una corrida ya se
      fusionó.
      `tests/test_export_p2_ohlc.py` +11 tests, con el stand-in de `MetaTrader5` y `main()` de punta a punta:
      `atomic_write_csv` escribe sin dejar temporales, crea directorios, y ante un fallo a mitad de escritura deja
      el archivo existente byte a byte igual (o nada, si era nuevo) y sin temporal; `make_run_dir` arma la ruta, da
      3 directorios distintos para 3 corridas del mismo segundo y no mezcla símbolos; `main` con `--per-run-dir`
      escribe bajo `{SIMBOLO}/{run_id}/` e imprime la ruta, sin el flag escribe directo como siempre; dos corridas
      seguidas no se pisan (la primera queda byte a byte igual); y una corrida que falla en la 2ª TF deja el 1H
      completo, sin 30M ni temporales, y la corrida anterior intacta. Comprobaciones de mutación: `mkdir(exist_ok=True)`
      rompe 3 tests y no limpiar el temporal rompe 2 (revertidas).
      `pytest -q tests/test_export_p2_ohlc.py`: `53 passed in 0.87s`. SUITE: `550 passed, 1 skipped, 97 warnings in
      10.58s`; sin `.data/`.
- [x] T20. `tools/candle_sync.py`: arma el comando del exportador desde la configuración, lo corre con
      `EXPORT_TIMEOUT_S` y llama a la fusión. (RF-20e, RF-20f, RF-1c)
      Hecho cuando: los tests, con un exportador falso en `tmp_path`, cubren éxito con fusión, timeout con
      `export_failed`, "MT5 no disponible" (código 1) con `export_failed`, y candado tomado. SUITE en verde.
      **Hecho 2026-09-28:** dos módulos.
      1. `tools/candle_bank.py` (parte 7): `merge_incoming_run()` es el orquestador que T15 dejó pendiente
         (plan.md §3.8, pasos 2 a 7): `verify_overlap` → si no decide, `verify_by_references` sobre el rango del
         export → para cada TF presente, `filter_by_export_season` + `merge_timeframe_into_bank_checked`. Nunca
         levanta; todo vuelve como `SyncResult` (`merged`, `clock_misaligned`, `clock_unverified`, `export_failed`
         o `locked`). **No toma el candado**: lo toma quien llama. `write_sync_status()` escribe `status.json`:
         `clock` es `"verified"` solo si una fusión lo verificó, y un export que falla o no verifica **no** le
         quita esa confianza a un banco que ya la tenía (sus velas no cambiaron); `last_export` y `last_error`
         sí reflejan siempre el último intento.
      2. `tools/candle_sync.py` (nuevo, proceso aparte, plan T8): `sync_symbol()` toma el candado del símbolo
         **antes de lanzar el exportador** (RF-20f: un segundo export ni arranca), arma el comando
         (`build_export_command`: `powershell.exe -Command "& '<python>' '<exportador>' --symbol … --per-run-dir …;
         exit $LASTEXITCODE"`, con la ruta de llegada convertida por `wsl_to_windows_path` y las anclas
         `--min/--max-anchor` derivadas del `created_at` de las DBs en `mode=ro`), lo corre con
         `run_exporter()` y fusiona la corrida que el exportador informa por `RUN_ID`. Todo fallo → `export_failed`
         con motivo (`timeout`, `exporter_exit_N: <stderr>`, `exporter_not_configured`, `exporter_not_launched`,
         `no_run_dir`, `incoming_dir_not_on_windows_drive`), banco intacto, sin excepción (INV-2). `main()` con
         `--symbol` y `--wait-seconds` (default `EXPORT_TIMEOUT_S`): códigos 0 = fusionó, 2 = reloj sin verificar,
         6 = `export_failed` o candado tomado; imprime `Candle export skipped: <motivo>` (RF-20e).
      **Decisiones que conviene revisar:** (a) el timeout usa `Popen` + `start_new_session` y `killpg` en vez de
      `subprocess.run(timeout=)`, que solo mata al hijo directo; (b) candado tomado devuelve código 6 (el contrato de
      plan.md §4 solo define 0/2/6) y NO toca `status.json`, para no pisar el estado del otro export.
      **[NO VERIFICADO], lo cubre el spike T22:** el armado exacto del comando de PowerShell (comillas,
      `exit $LASTEXITCODE`), la conversión de rutas, y que matar `powershell.exe` desde WSL mate también al Python
      de Windows (si no, solo termina de escribir su carpeta de corrida, que nadie fusiona).
      **Sigue sin hacerse (ninguna tarea lo pide):** la retención de "las últimas 5 corridas por símbolo" de
      plan.md §2.4. Implica **borrar** carpetas bajo `/mnt/c`, así que prefiero que se decida y se pruebe como
      tarea propia antes que agregarla acá.
      `tests/test_candle_bank.py` +13 tests del orquestador (verifica por superposición y fusiona todas las TF;
      una vela distinta → `clock_misaligned` sin fusionar ni la TF sana; primer export por referencias; 9
      referencias → `clock_unverified` sin escribir nada; +3h → `clock_misaligned`; el filtro de estación solo en
      las TF rápidas; carpeta vacía; falla a mitad de la fusión conserva lo ya fusionado y deja el resto
      intacto) y de `status.json`. `tests/test_candle_sync.py` nuevo, 24 tests con un exportador falso (script en
      `tmp_path`): éxito con fusión y `status.json`; timeout con `export_failed`, banco byte a byte igual y el
      proceso realmente muerto; MT5 caído (código 1); un export fallido no revoca un banco ya verificado; sin
      RUN_ID; comando no lanzable; sin configuración; reloj sin verificar; candado tomado (el exportador ni se
      lanza y `status.json` no se toca); el comando de PowerShell armado desde la configuración y la DB (con
      `run_exporter` reemplazado, nunca se lanza `powershell.exe`); y `main` con los códigos 0/2/6 y
      `--wait-seconds`. Mutaciones: sin `killpg` rompe el test de timeout, e ignorar el código de salida rompe 3.
      SUITE: `587 passed, 1 skipped, 97 warnings in 15.45s`; sin `.data/`.
- [x] T20b. *(Tarea agregada el 2026-09-28 a pedido del usuario, después de T20; no altera la numeración de las
      demás.)* Endurecer `tools/candle_sync.py` con lo que se pudo verificar contra Windows real antes del spike.
      (RF-20e, RF-1c)
      Hecho cuando: al vencer el timeout se mata también el árbol de procesos de Windows, no solo `powershell.exe`;
      el exportador se lanza desde una unidad de Windows; un mensaje de Windows con acentos no rompe la
      sincronización; y existen pruebas opt-in contra Windows real. SUITE en verde.
      **Hecho 2026-09-28:** se probó a mano en este WSL (`powershell.exe` existe) con comandos inofensivos (`cmd.exe`,
      `ping` a localhost, sin MT5 ni archivos): (1) `exit $LASTEXITCODE` **sí** conserva el código de salida (llega el
      7; sin él PowerShell lo aplasta a 1); (2) las comillas simples con `''` entregan bien argumentos con espacios y
      apóstrofes; (3) **matar `powershell.exe` desde WSL NO mata al programa de Windows que lanzó** (el `PING.EXE`
      siguió vivo) — es decir, el riesgo que T20 dejó como [NO VERIFICADO] era real; (4) `cmd.exe` rechaza el
      directorio actual de WSL (ruta UNC `\\wsl.localhost\...`); (5) los mensajes de Windows salen en cp850, no UTF-8.
      Correcciones en `tools/candle_sync.py`: `powershell_argv()` (el wrapper único, usado por `build_export_command`)
      imprime primero `WINPID: <n>` (`$PID` de PowerShell; verificado que llega de inmediato, sin buffer), y al vencer el
      timeout `run_exporter()` lo lee mientras el proceso sigue vivo (hilos lectores) y llama a
      `kill_windows_tree()` = `taskkill /F /T /PID <n>` antes del `killpg` de WSL; sin línea `WINPID` (exportador que no
      corre bajo PowerShell) no hay nada de Windows que matar. El error de timeout dice si el árbol se mató o por qué
      no (`Windows process tree N killed` / `could NOT kill Windows process N (…)`). El comando se lanza con
      `cwd="/mnt/c"`. La salida se decodifica con `errors="replace"`: con la decodificación estricta que tenía T20, un
      mensaje de Windows con acentos hacía fallar toda la sincronización con `UnicodeDecodeError`.
      `tests/test_candle_sync.py` +11 tests con un `taskkill` falso (nunca se toca Windows): el wrapper y la línea
      WINPID, el parseo con salida CRLF, el árbol de Windows se mata al vencer el timeout con el PID informado, se
      informa si no se pudo, no se llama sin WINPID ni en una corrida exitosa, bytes que no son UTF-8 dan un
      `export_failed` limpio, `cwd` se respeta, y `kill_windows_tree` (éxito, fallo, binario ausente).
      `tests/test_candle_sync_windows_interop.py` nuevo, **opt-in** (`WINDOWS_INTEROP_TESTS=1`; por defecto se saltea,
      así que la suite pasa de 1 a 3 skipped): repite contra Windows real el código de salida, las comillas y que el
      `PING.EXE` muera al vencer el timeout. Pasan. Mutaciones: sin llamar a `taskkill` rompe 2 tests, y con la
      decodificación estricta el test de bytes reproduce el `UnicodeDecodeError` real.
      **Sigue [NO VERIFICADO] para el spike (T22):** el exportador real (MT5 abierto, el Python de Windows con
      `MetaTrader5`, la detección del offset, la hora del cambio de horario).
      SUITE: `598 passed, 3 skipped, 97 warnings in 16.76s`; sin `.data/`.
- [x] T21. Subcomandos `candles status`, `candles import-legacy` y `candles export --symbol S [--wait N]` en
      `cli/main.py`. (RF-2c, RF-1)
      Hecho cuando: los tests con `CliRunner` y configuración en `tmp_path` prueban la salida y los códigos 0, 2 y 6.
      SUITE en verde.
      **Hecho 2026-09-28:** primera tarea que toca código de producción de `cli/main.py`, y solo **agrega** (111
      líneas nuevas, 0 modificadas ni borradas: los wizards, INV-1/INV-7, no se tocan). Capas finas de presentación
      (constitución, principio 3; todo en inglés, N30); la lógica está en `tools/`.
      - `candles status`: tabla Rich con, por símbolo MT5 (los 4 de `MT5_SYMBOL_MAP`), reloj, quién lo verificó, último
        export (`resultado (run_id)`), TF presentes (en el orden de `ALL_BANK_TIMEFRAMES`) y último error. Un símbolo
        sin `status.json` dice `never exported`. Siempre código 0.
      - `candles import-legacy`: para cada símbolo llama a `import_legacy_with_status()` (candado + `import_legacy` +
        `status.json`), e informa una línea por símbolo (importado y excluido con su motivo / no importado con
        `clock_unverified` o `clock_misaligned` / sin CSV legacy / saltado por candado). Códigos: 0; 2 si algún símbolo no
        verificó; **6 si algún símbolo estaba con candado tomado** (gana sobre el 2: ese símbolo ni se procesó, hay
        que repetirlo).
      - `candles export --symbol S [--wait N]`: normaliza `S` sin distinguir mayúsculas contra los símbolos conocidos
        (uno desconocido → `Candle export skipped: no_mt5_symbol`, código 6, sin lanzar nada), llama a
        `candle_sync.sync_symbol(S, timeout_s=N)` y usa `format_result_line`/`exit_code_for` de T20: 0 fusionó, 2 reloj
        sin verificar, 6 `export_failed` o candado tomado. `--wait` es el tope de espera al exportador (default
        `EXPORT_TIMEOUT_S`).
      Soporte agregado: `LEGACY_EXPORTS_DIR` en `config/auto_resolution.py` y `.env.template` (por defecto, la carpeta
      que contiene a `MT5_INCOMING_DIR`, o sea `.../MT5Exports`; solo la lee `import-legacy`), y en
      `tools/candle_bank.py` `collect_bank_status()` (solo lectura; un `status.json` ilegible se informa, no rompe) e
      `import_legacy_with_status()` (sin CSV legacy no toca nada, ni crea el directorio; con el candado tomado levanta
      `CandleBankLockedError`; cuando el reloj no verifica igual deja `status.json` con el motivo, para que `status`
      lo muestre, pero ningún CSV entra al banco).
      **Detalle a tener en cuenta:** click devuelve **2** para un error de uso (p.ej. falta `--symbol`), el mismo
      número que el contrato del plan usa para "reloj sin verificar". Es una colisión del contrato, no de este
      código; solo afecta a quien invoque el comando mal.
      `tests/test_cli_candles.py` nuevo, 15 tests con `CliRunner`, `config.auto_resolution` apuntando a `tmp_path` y el
      engine de cuenta fijado en memoria (como los tests de `report`): el grupo lista sus 3 subcomandos; `status` con
      banco vacío (4 × `never exported`) y con un símbolo con estado y CSV; `import-legacy` verificado (importa 15M,
      excluye XAU 5M, los otros 3 símbolos sin CSV, y el origen queda byte a byte igual), sin referencias (2), con
      reloj corrido +3h (2) y con candado tomado (6); `export` fusionado (0, con `--wait` y sin distinguir
      mayúsculas), reloj sin verificar (2), export fallido (6, línea `Candle export skipped: …`), candado (6), símbolo
      desconocido (6); y un extremo a extremo por el `sync_symbol` real con un exportador falso que fusiona y luego
      se ve en `status`. +7 tests en `tests/test_candle_bank.py` y +1 en `tests/test_config_auto_resolution.py`.
      Mutación: hacer que `export` ignore el código de salida rompe 3 tests.
      SUITE: `622 passed, 3 skipped, 97 warnings in 18.68s`; sin `.data/`.
- [x] T22. 🖐 **Spike de interoperabilidad**, con MT5 abierto en Windows:
      - encontrar el Python de Windows que tiene `MetaTrader5`;
      - correr `candles export --symbol XAUUSD` desde `WT`, con el banco en un directorio temporal y
        `ACCOUNTS_DATA_DIR` apuntando a `.data/` real en solo lectura;
      - medir la duración;
      - determinar `BROKER_DST_RULE`;
      - documentar todo en `specs/002-auto-resolucion-velas/spike.md`. (RF-20, RF-15b, N31, N34)

      Hecho cuando: `spike.md` tiene el comando, la duración, el resultado (fusionado o motivo) y la regla. Vos
      cargás `WINDOWS_PYTHON`, `EXPORTER_WIN_PATH` y `BROKER_DST_RULE` en el `.env` del checkout principal. Si
      falla, `AUTO_EXPORT` queda desactivado y la Etapa 8 se salta.

      **Avance 2026-09-29 (parte automática hecha; ver `spike.md`):** con MT5 abierto se encontró el Python de Windows
      (3.12.10, con `MetaTrader5 5.0.6180`), se corrió `candles export --symbol XAUUSD` con el banco y las DBs en una
      carpeta temporal, y **fusionó en 16 s** (reloj verificado por referencias, 9 TF) después del arreglo T22a. Sobre
      `BROKER_DST_RULE`: los datos son consistentes con `us` pero no lo prueban (`spike.md`). **Sigue abierta (tuya):**
      cargar `WINDOWS_PYTHON`, `EXPORTER_WIN_PATH` y `BROKER_DST_RULE` en el `.env` del checkout principal; por eso T22
      queda sin marcar. (Tras T57 ya se puede: `EXPORTER_WIN_PATH` existe en el checkout principal. Valores en
      `spike.md`.)
      **Cerrada 2026-09-29:** el usuario cargó `WINDOWS_PYTHON`, `EXPORTER_WIN_PATH`, `BROKER_DST_RULE=us` y
      `AUTO_EXPORT=false` en el `.env` del principal. Se verificó leyendo solo esas 4 claves con `dotenv_values`: los
      valores coinciden con `spike.md` y las barras invertidas llegan intactas. `BROKER_DST_RULE=us` sigue sin probar:
      a pedido del usuario, quedó un recordatorio con fecha en `CLAUDE.md` (sección "Reloj de las velas exportadas").
      Con el `.env` cargado, `test_windows_paths_have_no_default` fallaba si la suite corría en el principal (la
      config lee ese `.env`); ahora reimporta la config sin las variables y sin leer ningún `.env`.
- [x] T22a. *(Tarea agregada el 2026-09-29, a partir de lo que mostró el spike; no altera la numeración de las demás.)*
      Endurecer el exportador y `candle_sync` con los defectos que el spike encontró contra MT5 real. (RF-15, RF-20e)
      Hecho cuando: un export con un rango que MT5 rechazaría de un solo pedido se completa partiéndolo en tramos; una
      TF que falla no tira abajo a las demás y se informa; los mensajes de Windows llegan con tildes; y la corrida real
      contra MT5 funciona. SUITE en verde.
      **Hecho 2026-09-29:** (1) `windows_export/export_p2_ohlc.py`: `split_range()` + `fetch_rates()` piden el rango en
      tramos de a lo sumo `MAX_BARS_PER_CALL = 30 000` velas (límite **medido**: MT5 falla con `Invalid params` a partir
      de ~60-70 mil; el 1M del rango real son ~140 mil, así que sin esto el export fallaba siempre) y unen los tramos sin
      repetir velas. (2) Una TF que MT5 rechaza o no tiene ya no aborta el export (RF-15, que T17 había dejado para
      después): se informa por stderr y por una línea `SKIPPED_TF: <tf>`, y el código de salida es 0 si al menos una TF se
      exportó y 1 si ninguna. (3) `tools/candle_sync.py`: fija `PYTHONIOENCODING=utf-8` en el wrapper de PowerShell (el
      error salía con `�`), lee las `SKIPPED_TF` y, si la fusión igual ocurrió, lo deja en `SyncResult.error` (`exporter
      skipped timeframes: 1M`), en `status.json` (`last_error`) y en la línea de resumen, para que un export parcial no
      pase inadvertido.
      Tests: `tests/test_export_p2_ohlc.py` +12 (tramos contiguos, sin solapes y bajo el tope; un MT5 emulado con el
      límite real rechaza el pedido único y acepta el partido, con todas las velas exactamente una vez; una TF que falla
      se saltea y la corrida A queda intacta; si fallan todas, código 1) — el test T19 de "falla a mitad de corrida" se
      reescribió porque ese comportamiento cambió a propósito; `tests/test_candle_sync.py` +3 (lectura de `SKIPPED_TF`,
      export parcial que igual fusiona y avisa, export completo sin nota) y 2 ajustados al nuevo prefijo. Mutación:
      sin partir el rango se rompen 10 tests.
      Verificado con MT5 real: la corrida que antes fallaba en 1M ahora fusiona las 9 TF, y el Python de Windows entrega
      `Válidas` con su tilde.
      SUITE: `633 passed, 3 skipped, 97 warnings in 20.14s`; sin `.data/`.

- [x] T16b. *(Tarea agregada el 2026-09-29: corrección de T16 a partir de la primera corrida real.)* `import_legacy`
      aplica el filtro de estación de horario. (RF-2c, RF-15c, plan decisión T17)
      Hecho cuando: el import inicial solo trae, en 1H y más finas, las velas de la misma estación que el momento del
      import, y un test reproduce el caso real. SUITE en verde.
      **Qué pasó:** en T16 omití el filtro razonando que RF-2c no lo mencionaba, pero la decisión T17 del plan sí lo
      pedía ("el import inicial solo trae velas de la estación de horario actual mientras `BROKER_DST_RULE` no esté
      verificado"). En T59, `candles export --symbol XAUUSD` contra el banco recién importado dio `clock_misaligned`:
      18 velas de 1H del 5-6 de marzo (invierno, antes del cambio de EE.UU.) venían con 1 h de error del exportador
      viejo, y el chequeo de superposición las detectó. BTCUSD no se afectó (su 1H empieza el 19 de abril).
      **Arreglo:** `import_legacy()` recibe `export_moment` y `dst_rule` (obligatorios, por keyword: la omisión fue el
      error) y pasa cada TF por `filter_by_export_season`; `import_legacy_with_status()` toma la regla de
      `BROKER_DST_RULE` y "ahora" (GT) si no se le pasan. El banco real de XAUUSD (con las 18 velas malas, creado ese
      mismo día por T59) se rehízo desde cero: el banco no modifica velas, así que no había otra forma limpia.
      Tests +4 en `tests/test_candle_bank.py`: solo las TF rápidas pierden las velas de la estación opuesta; `none`
      importa todo; la regla sale de la configuración; y **el caso real** (legacy con velas de invierno mal etiquetadas
      + export nuevo bien etiquetado: con el filtro la superposición verifica). Los tests con fechas de junio ahora
      fijan `export_moment` y `dst_rule` para no depender del día en que se corran. Mutación: sin el filtro se rompen 3.

## Etapa 3 — Resolvedor, métricas y reporte (solo lectura)

- [x] T23. `first_touch_detail()` en `core/p2_ground_truth.py`. (RF-4, RF-4b, RF-4f)
      Hecho cuando: los tests nuevos cubren el índice, el nivel y el caso ambiguo, y `tests/test_p2_ground_truth.py`
      sigue en verde sin cambios. SUITE en verde.
      **Hecho 2026-09-29:** `first_touch_detail()` devuelve `FirstTouchDetail(direction, incomplete, bar_index, level,
      ambiguous)`, en el orden del plan (§3.2, paso 5). `level` es `LEVEL_VALIDATION` o `LEVEL_INVALIDATION`. Una vela
      que toca los dos niveles devuelve su índice con `ambiguous=True`, para que T26 la refine con una TF más fina. El
      campo se llama `bar_index` y no `index` porque `index` taparía el método de la tupla. Solo agregados: 76 líneas
      nuevas, ninguna borrada; `first_touch_direction` y `tests/test_p2_ground_truth.py` sin cambios. El recorrido
      quedó repetido a propósito (el plan pide "solo agregados"): un test con 3000 caminos al azar exige que los dos
      primeros campos coincidan siempre con `first_touch_direction`. Tests: +15 en `tests/test_p2_first_touch_detail.py`.
      Mutación: sin la rama ambigua, con el índice corrido en 1, con los niveles cruzados, con `>` en vez de `>=` o
      con la vela ambigua sin índice, fallan entre 3 y 11 tests.
      SUITE: `652 passed, 3 skipped, 97 warnings in 16.97s`; sin `.data/`.
- [ ] T24. `core/candle_resolution.py`, parte 1: cobertura por TF y códigos `pending_candles` y `no_history`. (RF-4c,
      RF-4g)
      Hecho cuando: los tests prueban un ancla anterior al banco (`no_history`) y un banco que termina antes del
      toque (`pending_candles`). SUITE en verde.
- [ ] T25. `candle_resolution`, parte 2: camino multi-TF con escalera y empalme en el borde de la TF gruesa, sin
      huecos, empezando en la primera vela de 1M en el ancla o después. (RF-4, N17, N18)
      Hecho cuando: los tests prueban la secuencia 1M → 5M → 1H en un fixture, que no hay huecos ni velas repetidas,
      y que una vela de 1M que contiene el ancla queda fuera. SUITE en verde.
- [ ] T26. `candle_resolution`, parte 3: primer toque con refinamiento, `ambiguous`, hora de apertura y TF usada,
      horizonte y `open`. (RF-4, RF-4b, RF-5, RF-5b, N19)
      Hecho cuando: los tests cubren Confirmed, Invalidated, una vela doble en 15M resuelta por 1M, una doble en la
      TF más fina (`ambiguous`), y ningún toque en `MAX_HORIZON` (`open`). SUITE en verde.
- [ ] T27. `candle_resolution`, parte 4: precio de partida, dirección de la tesis, `no_levels`, R y MAE/MFE
      estructurales en precio. (RF-4, RF-4d)
      Hecho cuando: los tests cubren long y short, niveles del mismo lado (`no_levels`), y valores de MAE/MFE
      verificados a mano en el fixture. SUITE en verde.
- [ ] T28. `candle_resolution`, parte 5: reglas de revertido, expansión, mínima y sweep, más los N/A de Invalidated y
      Overlap. (RF-8, RF-8b, RF-8c, RF-8d)
      Hecho cuando: los tests cubren los bordes (vuelta al Mark Price a las 23:59 y a las 24:01 h; 0.49R y 0.50R;
      exceso de 1.0R y 1.01R; objetivo a las 47 y a las 49 h) y el caso sin Mark Price. SUITE en verde.
- [ ] T29. `core/outcome_metrics.py`: etiqueta Overlap (N11, N22), S1, S4, exclusión de retroactivos con conteo, y
      corte direccional. (RF-5, RF-17, RF-21)
      Hecho cuando: los tests prueban que Overlap nunca excluye, S1 y S4 con "fuera", los retroactivos contados, y que
      los 21 candidatos de `analisis-overlap-2d.md` se reproducen sobre un fixture equivalente. SUITE en verde.
- [ ] T30. `tools/auto_resolution.py`: lectura con columnas explícitas (`mode=ro` o engine), mapa
      `asset → símbolo` (`no_mt5_symbol`), estado del reloj (`clock_unverified`/`clock_misaligned`) y armado de la
      propuesta. (RF-4e, RF-4h, RF-6b)
      Hecho cuando: los tests, con una DB de fixture sin columnas nuevas (como US100) y un banco en `tmp_path`,
      prueban la propuesta completa y cada motivo. SUITE en verde.
- [ ] T31. Chequeo del Mark Price: escalera 1M → 5M → 15M con ±0.1% y versión por período. (RF-3, RF-3b)
      Hecho cuando: los tests prueban que coincide en 1M, que coincide recién en 15M, y que queda fuera (advertencia),
      más la versión por período para filas sin `mark_price_time`. SUITE en verde.
- [ ] T32. `tools/resolution_report.py`, datos: por cuenta, motivos, coincidencia manual contra automática,
      compliance informativo, demora del audit, lista de diferencias, S1/S4 para todos y direccionales con el manual
      al lado, Overlap con B, Mark Price fuera de sus velas, y demora de los retroactivos. (RF-6, RF-3b, RF-17, RF-21)
      Hecho cuando: los tests sobre DBs y velas de fixture verifican cada bloque. SUITE en verde.
- [ ] T33. Salida del reporte en Markdown (inglés) y subcomando `resolution-report`. (RF-6)
      Hecho cuando: el test con `CliRunner` genera el `.md` en `tmp_path`, con los encabezados esperados y código 0, y
      no escribe en ninguna DB (lo cuida el audit hook y un test de `mtime`). SUITE en verde.
- [ ] T34. `docs/criterios-de-acierto.md`: apuntar la implementación a `core/outcome_metrics.py` y agregar un ejemplo
      de uso desde un cuaderno. (RF-21)
      Hecho cuando: `grep -n "core/outcome_metrics.py" docs/criterios-de-acierto.md` da resultado y el ejemplo corre
      en un test de humo sobre el fixture. SUITE en verde.

## Etapa 4 — Esquema y horas del análisis

- [ ] T35. `tools/database.py`: columnas nuevas, ORM de `backfill_history` y shim de `init_db`. (RF-13d, RF-13e,
      RF-14, RF-14b, RF-18, NFR-1)
      Hecho cuando: los tests prueban que `init_db` sobre una DB de archivo en `tmp_path`, creada con el esquema
      viejo, agrega las 5 columnas y la tabla, y que correrlo dos veces no falla. SUITE en verde.
- [ ] T36. `cli/schemas/audit_efficiency.py`: `resolution_time` opcional, `audit_registration_time` y
      `resolution_time_source`. (RF-7g, RF-14, RF-14b)
      Hecho cuando: los tests del schema (existentes y nuevos) pasan y el guardado persiste los campos nuevos vía
      `update_record_state`. SUITE en verde.
- [ ] T37. `flow_new_analysis`: `analysis_start_time` al confirmar P0 por primera vez, sin cambiar con
      `RestartFlowException`; hora tipeada en retroactivo y clon [2] (RF-13b gana); hora de elección en clon [1].
      (RF-13, RF-13b, RF-13c)
      Hecho cuando: los tests cubren los 4 casos y la red de seguridad de T7 sigue en verde. SUITE en verde.
- [ ] T38. `flow_new_analysis`: `mark_price_time` (la hora tipeada en retroactivos) y `saved_at` real. (RF-13d,
      RF-13e)
      Hecho cuando: los tests verifican las dos horas en un análisis nuevo y en uno retroactivo. SUITE en verde.
- [ ] T39. Aviso de una línea del Mark Price después de guardar, sin bloquear nunca. (RF-3)
      Hecho cuando: los tests prueban el aviso con el banco cubriendo, que no hay aviso sin banco, y que el guardado
      ocurre en los dos casos. SUITE en verde.

## Etapa 5 — Propuestas en el Efficiency Audit

- [ ] T40. Parámetro `default` en `get_enum_choice` y `get_mandatory_float`, y helper nuevo
      `get_optional_datetime`. (RF-7, RF-7e)
      Hecho cuando: los tests prueban que el default llega a `inquirer`, que sin default el comportamiento es el de
      hoy, y que los tests existentes siguen en verde. SUITE en verde.
- [ ] T41. Efficiency, parte 1: pedir la propuesta al abrir el wizard y mostrar el motivo en una línea cuando no la
      hay. (RF-7f, RF-5b)
      Hecho cuando: los tests prueban "Still open according to candles", `pending_candles` y `clock_unverified`, y que
      los prompts siguen sin default. SUITE en verde.
- [ ] T42. Efficiency, parte 2: defaults `(auto)` en Resolution Type, Structural Resolution, Failure Reason y
      Structural MAE/MFE. (RF-7, RF-7e, RF-8 a RF-8d)
      Hecho cuando: los tests prueban que cada default llega a su prompt, que aceptar guarda el valor, que corregir
      guarda el valor del operador, y que la red de seguridad de T5 sigue en verde en el orden. SUITE en verde.
- [ ] T43. Efficiency, parte 3: prompt "Resolution Time" opcional al final, `audit_registration_time`, y
      `resolution_time_source` (`candles`, `corrected` o el motivo). (RF-7c, RF-7g, RF-14, RF-14b)
      Hecho cuando: los tests cubren aceptar, corregir, dejar vacío y el caso sin propuesta, con el valor persistido
      de las tres columnas. SUITE en verde.
- [ ] T44. Efficiency, parte 4: "Resolution Time" en el menú "Edit a Field" y en el panel de revisión con la marca
      `(auto)`. (RF-7, INV-1)
      Hecho cuando: los tests prueban que la edición funciona y que el panel muestra la marca. SUITE en verde.

## Etapa 6 — Propuestas en el Tactical Audit

- [ ] T45. MAE/MFE propuestos, con la TF más fina validada, R, tope de 10 y los motivos `no_interval`, `zero_r` y
      `pending_candles`. (RF-9, RF-9b, RF-9c, RF-9d)
      Hecho cuando: los tests cubren la propuesta aceptada y corregida y cada motivo, y la red de seguridad de T6 sigue
      en verde. SUITE en verde.
- [ ] T46. `Could hit TP?` propuesto, más `invalid_tp` y la vela doble. `session` no se toca. (RF-10, RF-10b, RF-10c,
      RF-10d)
      Hecho cuando: los tests cubren "yes", "no", la vela doble sin propuesta e `invalid_tp`, y `session` sigue
      calculándose igual. SUITE en verde.

## Etapa 7 — Backfill

- [ ] T47. `tools/auto_backfill.py`: plan de cambios (`fill`, `unchanged`, `conflict`), "Open" con `real_bias_b` NULL
      como vacío, audits nunca hechos incluidos, y la regla `legacy_move` solo con `audit_registration_time` vacío.
      (RF-11, RF-11b, RF-11c)
      Hecho cuando: los tests sobre DBs de fixture verifican cada tipo de cambio. SUITE en verde.
- [ ] T48. `cli/backfill_view.py`: vista estilo `git log --decorate --oneline --graph`. (RF-19)
      Hecho cuando: los tests verifican el texto: una línea por corrida con las decoraciones, `!` en los conflictos y
      `—` en los vacíos. SUITE en verde.
- [ ] T49. Aplicación transaccional, inserciones en `backfill_history` e idempotencia. (RF-11b, RF-11c, RF-18)
      Hecho cuando: los tests prueban que la segunda corrida no cambia nada, que el historial tiene una fila por
      cambio, y que el código nunca hace `UPDATE` ni `DELETE` sobre `backfill_history`. SUITE en verde.
- [ ] T50. Puertas: ensayo sobre una copia temporal con `init_db`, backup de las últimas 24 h, confirmación
      escribiendo `APPLY`, y sus códigos de salida. (RF-11d, R9)
      Hecho cuando: los tests prueban los códigos 3, 4 y 5 y que en esos casos no se escribe nada. SUITE en verde.
- [ ] T51. Subcomando `backfill [--apply]` con aceptación de conflictos uno por uno. (RF-11, RF-11e, RF-19)
      Hecho cuando: el test con `CliRunner` sobre fixtures en `tmp_path` cubre el dry-run sin escrituras y un conflicto
      aceptado que se registra como `accepted_conflict`. SUITE en verde.

## Etapa 8 — Export automático (solo si el spike de T22 funcionó)

- [ ] T52. Disparo en segundo plano al guardar un unified analysis, con `AUTO_EXPORT`. (RF-20, RF-20f)
      Hecho cuando: los tests, con `Popen` mockeado, prueban que se lanza una vez, que el guardado no espera, y que no
      se lanza con `AUTO_EXPORT` desactivado. SUITE en verde.
- [ ] T53. Disparo al abrir un audit, con espera acotada y progreso. (RF-20b, RF-20e)
      Hecho cuando: los tests prueban que la espera termina en `AUTO_EXPORT_WAIT_S` y continúa, y la línea "Candle
      export skipped". SUITE en verde.
- [ ] T54. Disparos antes del reporte y del backfill, y al abrir el CLI (`start()`). (RF-20c, RF-20d)
      Hecho cuando: los tests con los disparos mockeados prueban que ocurren y que no bloquean. SUITE en verde.

## Etapa 9 — Registro prospectivo del P2 sistemático (N38, N42)

- [ ] T55. `tools/p2_model_feedback.py`: para cada modelo de `P2_LOG_MODELS` (N42), calcular su P2 con velas
      cerradas en el ancla (receta de `MODELS_BY_NAME`, sin modificarla) y registrar una línea por análisis y modelo en
      `p2_model_log.jsonl`, con `model_spec`, `model_hash`, motivos (`insufficient_history:<TF>`, `pending_candles`) y
      `supersedes` para reemplazar una línea pendiente. Incluye los chequeos de RF-12e. (RF-12, RF-12c, RF-12e)
      Hecho cuando: los tests con fixture verifican:
      - la línea JSON (P2 del operador, del modelo, receta, huella y detalle por TF), y que la huella de D es
        `20315fe7bfe2`;
      - que no se escribe una segunda línea para el mismo análisis y modelo salvo `supersedes`;
      - que los retroactivos y los clones [2] no se registran, y que ninguna DB se modifica;
      - **con dos modelos configurados** (D y un `H_TEST` con otras TF y pesos, agregado al registro solo dentro del
        test): cada análisis recibe una línea por modelo, y `H_TEST` no se registra en análisis con
        `analysis_start_time` anterior a su fecha de alta;
      - que un nombre inexistente, una TF que el banco no guarda y una receta cambiada se saltean con su aviso
        (`unknown_model`, `timeframe_not_in_bank`, `model_recipe_changed`) sin frenar el registro de D.

      SUITE en verde.
- [ ] T56. Hooks: registrar al guardar un análisis (si hay velas), catch-up en `candle_sync` después de cada fusión,
      y el comando `p2-model --trade-id ID [--model NAME]`. (RF-12, RF-12b, RF-12d)
      Hecho cuando: los tests prueban:
      - que guardar con velas registra y sin velas no;
      - que el catch-up registra los pendientes una sola vez por modelo y **nunca** registra análisis sin
        `analysis_start_time`, retroactivos ni clones [2] (N40), ni análisis anteriores a la fecha de alta de cada
        modelo (N42);
      - que sumar `H_TEST` a `P2_LOG_MODELS` no cambia ni duplica las líneas de D ya registradas;
      - que `p2-model` muestra una línea por modelo, y que calcula a pedido las que faltan (también con `--model`)
        marcándolas `not logged`, sin escribir el registro;
      - que el wizard no muestra nada del modelo.

      SUITE en verde.

## Etapa 10 — Datos reales, demos y validación

- [x] T57. 🖐 Integrar la rama a `feature/tactical-tier-gate-df`, solo con tu aprobación y la lista explícita de
      commits. (—)
      Hecho cuando: `git log` de la rama principal contiene los commits de la spec, y R1 se cumple.
      **Hecho 2026-09-29 (primera integración, con aprobación del usuario):** avance rápido (fast-forward, sin mezclar ni
      reescribir) de `feature/tactical-tier-gate-df` de `158869b` a `ac395b1`: 27 commits (`6162742` … `ac395b1`), 32
      archivos. Se verificó antes que ninguno chocara con los cambios sin commitear o sin trackear del usuario en el
      principal (ninguno) y después que quedaron idénticos; `git diff CLAUDE.md` vacío. **Cada commit posterior
      (T16b, …) requiere otra integración**, que se pide aparte.
      **2.ª integración 2026-09-29 (aprobada por el usuario):** `ac395b1` → `3c557a6`, 4 commits (T16b, registro de
      T59, cierre de T22 y el recordatorio de `BROKER_DST_RULE` en `CLAUDE.md`, pedido por el usuario), 6 archivos.
      Mismas verificaciones: ningún choque, `git status` idéntico, los 30 archivos del usuario byte a byte iguales.
- [ ] T58. 🖐 **Migración de esquema de las 4 DBs reales con las 3 puertas**, antes de correr cualquier comando del
      CLI nuevo en el checkout principal (N41, NFR-1, R9, constitución principio 5):
      1. copiar las 4 `.data/flight_account_*.db` a un directorio temporal, correr `init_db` sobre cada copia, y
         verificar con `PRAGMA table_info` y `PRAGMA integrity_check`;
      2. correr `conda run -n blast_master python tools/backup.py backup` y verificar la salida;
      3. tu aprobación explícita;
      4. correr `init_db` sobre las 4 reales **desde un comando de Python aislado**, sin abrir el CLI.
      (NFR-1, INV-3)
      Hecho cuando: las 4 copias y después las 4 reales tienen las 5 columnas nuevas y `backfill_history`,
      `integrity_check` da `ok`, el conteo de filas de cada tabla es igual antes y después, y el backup del día
      figura en `tools/backup.py list`.
- [x] T59. 🖐 Crear el banco real con `candles import-legacy` en el checkout principal. Escribe en `.data/`, así que
      requiere tu aprobación. (RF-2c)
      Hecho cuando: `candles status` muestra XAUUSD y BTCUSD verificados, USTEC en `clock_unverified`, y el 5M de XAU
      excluido.
      **Hecho 2026-09-29 (con aprobación del usuario; solo se escribió `.data/candle_bank/`, 21 MB):** XAUUSD y
      BTCUSD `verified` con las 9 TF; USTEC (9 de 10 referencias) y US500 (6 de 10) quedan `clock_unverified` y sin
      velas. Se usó el código de la rama de la sesión (el banco real se escribió con `CANDLE_BANK_DIR`,
      `ACCOUNTS_DATA_DIR` y `LEGACY_EXPORTS_DIR` apuntando al `.data/` real). **Encontró un defecto mío (T16):**
      `import_legacy` no aplicaba el filtro de estación; el 1H de XAU quedó con 18 velas de invierno y el export
      siguiente dio `clock_misaligned`. Se arregló en T16b (`14aa72e`) y el banco de XAU (creado minutos antes por esta
      misma tarea) se rehízo desde cero. Cobertura: XAU 1M 139 326 velas desde 2026-05-10, 5M desde 2026-05-05; BTC 1M
      137 793 desde 2026-06-24 (el símbolo de BTC solo tiene ~3 meses de 1M en el servidor). El 5M de XAU del
      *import legacy* está excluido (`LEGACY_IMPORT_EXCLUSIONS`); el 5M que hay en el banco vino del export nuevo.
- [ ] T60. Correr `resolution-report` sobre las DBs reales, en solo lectura. (RF-6, RF-17, RF-21)
      Hecho cuando: el `.md` generado se muestra, y el S1 direccional de XAU se compara con el 61% de
      `analisis-overlap-2d.md`, explicando cualquier diferencia (1M/5M, ancla).
- [ ] T61. 🖐 Demo en una Flight Session descartable: un Efficiency Audit con propuestas aceptadas y una corregida,
      más un Tactical con MAE/MFE y `Could hit TP?` propuestos. (RF-7, RF-9, RF-10)
      Hecho cuando: queda documentado en `validation.md` con la salida, y la Flight Session se borra al terminar.
- [ ] T62. 🖐 Demo del export automático con MT5 abierto y con MT5 cerrado. Solo si T22 funcionó. (RF-20 a RF-20e)
      Hecho cuando: las dos salidas quedan en `validation.md`.
      **Decisión pendiente antes de activar `AUTO_EXPORT` (postergada por el usuario el 2026-09-29): retención de
      corridas.** Cada export deja una carpeta de 2 a 11 MB en `C:\Users\jcifu\MT5Exports\_incoming\<SÍMBOLO>\`
      (5 corridas y 36 MB hoy) y nada las borra. A mano no importa, pero con el export automático varias corridas por
      día crecerían sin límite. El plan (§2.4) propone conservar las últimas 5 por símbolo, para poder auditarlas; no
      está implementado ni tiene tarea. Las 2 corridas del spike ya se borraron, con aprobación del usuario.
- [ ] T63. 🖐 Backfill real:
      1. dry-run y revisión de la vista git-graph;
      2. las 3 puertas (copia, backup y tu aprobación);
      3. `--apply`;
      4. conteo de campos llenados, conflictos aceptados y diferencias. (RF-11 a RF-11e, RF-18, RF-19)

      Hecho cuando: los conteos quedan en `validation.md` y `backfill_history` tiene una fila por cambio.
- [ ] T64. Validación final (F8): recorrido RF por RF e INV por INV, citando cada test, con la suite completa. (Todos)
      Hecho cuando: `validation.md` tiene un veredicto por RF e INV y el frontmatter `verdict:`, y SUITE en verde con la
      salida mostrada.

## Trazabilidad RF → Tarea

| RF / INV | Tareas |
|---|---|
| INV-1 | T5, T6, T42, T44, T45 |
| INV-2 | T20, T30, T39, T41, T53 |
| INV-3 | T47, T49, T58, T63 |
| INV-4 | T9, T10, T23 (solo agregados); T64 |
| INV-5 | T1 a T4 y SUITE en cada tarea |
| INV-6 | T11, T16 (origen intacto) |
| INV-7 | T7, T37 |
| INV-8 | T42, T64 (ningún campo de juicio propuesto) |
| RF-1, RF-1b | T11, T21 |
| RF-1c | T15, T20 |
| RF-1d | T12, T20 |
| RF-2, RF-2b | T10, T14 |
| RF-2c | T16, T21, T59 |
| RF-2d | T13 |
| RF-3 | T31, T39 |
| RF-3b | T31, T32 |
| RF-4 | T23, T25, T26, T27 |
| RF-4b | T23, T26 |
| RF-4c, RF-4g | T24 |
| RF-4d | T27 |
| RF-4e, RF-4h | T30 |
| RF-4f | T9, T10, T23 |
| RF-5, RF-5b | T26, T29, T41 |
| RF-6 | T32, T33, T60 |
| RF-6b | T30 |
| RF-7, RF-7e | T40, T42, T44, T61 |
| RF-7f | T41 |
| RF-7g | T36, T43 |
| RF-8, RF-8b, RF-8c, RF-8d | T28, T42 |
| RF-9, RF-9b, RF-9c, RF-9d | T45 |
| RF-10, RF-10b, RF-10c, RF-10d | T46 |
| RF-11, RF-11b, RF-11c | T47, T49, T51, T63 |
| RF-11d | T50 |
| RF-11e | T51, T63 |
| RF-12, RF-12c | T55, T56 |
| RF-12b, RF-12d | T56 |
| RF-12e | T8, T55 |
| RF-13, RF-13b, RF-13c | T37 |
| RF-13d, RF-13e | T35, T38 |
| RF-14, RF-14b | T35, T36, T43 |
| RF-15 | T9, T17, T19 |
| RF-15b | T18, T22 |
| RF-15c | T15 |
| RF-16 | T3, T4 |
| RF-16b | T2 |
| RF-17 | T29, T32 |
| RF-18 | T35, T49 |
| RF-19 | T48, T51 |
| RF-20, RF-20f | T20, T52 |
| RF-20b, RF-20e | T20, T53 |
| RF-20c, RF-20d | T54 |
| RF-21 | T29, T32, T34 |

Los 70 RF y los 8 INV tienen al menos una tarea.
