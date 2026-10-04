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

- [x] T16c. *(Tarea agregada el 2026-09-30 por la decisión N44 del usuario; corrección de T16 y T16b.)* El import
      legacy filtra por estación en todas las TF, no solo en 1H y menores. Nueva `find_relabeled_duplicates()` en
      `tools/candle_bank.py` y nueva herramienta `tools/dedup_candle_bank.py`: por defecto solo informa; con `--apply`
      respalda, limpia y verifica (plan.md §3.8, "Limpieza única"). (RF-1, RF-2c, RF-15c, N39, N44)
      Hecho cuando: un test reproduce el caso real (CSV legacy con velas de invierno a +3 h y export nuevo a +2 h: ya
      no quedan pares en 1D); los tests de la herramienta prueban que el modo por defecto no escribe nada, que `--apply`
      respalda los bytes originales, quita solo la copia de 1 h antes de cada par, no toca 1H ni un par con precios
      distintos, y que una segunda corrida no encuentra nada. SUITE en verde.
      **Hecho 2026-09-30:**
      - `import_legacy` pasa `season_timeframes=ALL_BANK_TIMEFRAMES` al filtro de estación.
      - `find_relabeled_duplicates(df, tf)` devuelve pares `(copia a quitar, copia que queda)`. Solo acepta 4H, 12H,
        1D y 1W: en 1H y menores, dos velas reales pueden estar a 1 h. Frena si encuentra tres copias seguidas.
      - `tools/dedup_candle_bank.py`: sin `--apply` solo informa. Con `--apply` toma el candado de cada símbolo,
        respalda en `.data/archives/candle_bank_pre_dedup_<fecha>/`, reescribe de forma atómica y verifica; si la
        verificación falla, restaura el original desde el respaldo.
      - El test de T16b que exigía la vela de enero en 1D se invirtió, porque N44 cambia esa regla a propósito.
      - Tests: +11 en `tests/test_candle_bank.py` (incluido el caso real: CSV legacy a +3 h y export nuevo a +2 h ya
        no dejan pares) y 7 en `tests/test_dedup_candle_bank.py`.
      - Mutación: sin el filtro en 4H o más, sin comparar precios, quitando la copia correcta, sin verificación, sin
        restaurar o sin candado: en cada caso fallan entre 1 y 4 tests.
      - Auditoría de solo lectura del banco real antes de limpiar: en los 3.824 pares, la copia de 1 h antes es
        siempre la que no abre en el borde del servidor. Aparte hay 38 velas fuera del borde sin gemela, que **no**
        son duplicados y no se tocan: 35 de XAU 1W de 1998 a 2006 (semanas en que EE.UU. cambiaba de hora en otras
        fechas antes de 2007, y el exportador usa el calendario actual) y 3 de BTC 4H en la hora del cambio de horario
        (`DST_TRANSITION_HOUR`, sin verificar, ver `spike.md`).
      SUITE: `712 passed, 3 skipped, 97 warnings in 22.47s`; sin `.data/`.
- [x] T22b. *(Tarea agregada el 2026-09-30 por la decisión N43 del usuario; no altera la numeración.)* Herencia del
      reloj verificado entre símbolos del mismo servidor: el exportador imprime `SERVER:` y `BASE_UTC_OFFSET:`,
      `candle_sync` los pasa a la fusión, `status.json` guarda `verified_export`, y `merge_incoming_run` prueba la
      herencia cuando faltan referencias (plan.md §3.8, paso 4b). (RF-2, RF-2e, N43)
      Hecho cuando: los tests prueban una herencia exitosa y cada caso en que no se hereda: sin donante, donante que a
      su vez heredó, otro servidor, otro desfase base, otra regla, menos de 5 referencias propias, una que no calza y
      superposición desalineada. También, que el import legacy nunca hereda ni sirve de donante, y que un export que no
      se fusiona conserva el `verified_export` anterior. SUITE en verde.
      **Hecho 2026-09-30:** el exportador imprime `SERVER:` (de `mt5.account_info()`, de solo lectura) y
      `BASE_UTC_OFFSET:`. `ExporterRun` los lee y `candle_sync` los pasa a `merge_incoming_run`, que prueba la herencia
      con `verify_by_inheritance()` y `find_clock_donor()` (plan §3.8, paso 4b). `status.json` guarda
      `verified_export`, y el resumen dice "clock inherited from XAUUSD". `INHERIT_MIN_OWN_REFERENCES = 5` está en la
      configuración. Tests: +18 en `tests/test_candle_bank.py`, +3 en `tests/test_candle_sync.py` y +2 en
      `tests/test_export_p2_ohlc.py`. Mutación: aceptar un donante que heredó, otro servidor u otro desfase, quitar el
      mínimo de 5 o el "calzan todas", o perder `verified_export` en un export que no se fusiona: en cada caso falla al
      menos un test.
      **Encontrado de paso (commit aparte, anterior a este):** al correr `test_config_auto_resolution.py` antes que
      `test_cli_candles.py` (fuera del orden alfabético), 6 tests del CLI fallaron y apareció un `.data/candle_bank/BTCUSD`
      vacío en el worktree. Causa: los tests que reimportan la configuración restauraban `sys.modules` pero no el
      atributo del paquete `config`, así que el CLI recibía un módulo sin los monkeypatch y apuntaba al banco por
      defecto. El guard de `conftest.py` frenó la escritura de archivos, pero no la creación de la carpeta, porque solo
      auditaba `open` y `sqlite3.connect`. No se tocó nada real: sin cambios de hoy en el banco del principal ni en
      `_incoming`, y la carpeta vacía se borró. Arreglo: un helper que restaura las dos cosas, más un test de
      regresión que falla sin él; y el guard ahora también audita `os.mkdir`, `os.rename`/`os.replace`, `os.remove` y
      `os.rmdir`, con 3 casos nuevos en su meta-test.
      SUITE: `676 passed, 3 skipped, 97 warnings in 21.21s`; sin `.data/`, también corriendo esos archivos en el orden
      que falló.

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
- [x] T24. `core/candle_resolution.py`, parte 1: cobertura por TF y códigos `pending_candles` y `no_history`. (RF-4c,
      RF-4g)
      Hecho cuando: los tests prueban un ancla anterior al banco (`no_history`) y un banco que termina antes del
      toque (`pending_candles`). SUITE en verde.
      **Hecho 2026-09-30:** `core/candle_resolution.py` nuevo y puro (sin DB ni disco). Tiene:
      - `LADDER` (1M → 1H) y `LADDER_MINUTES`; un test exige que coincida con `TIMEFRAME_MINUTES` del backtest,
        repetida para que `core/` no dependa de `tools/`;
      - `bank_coverage()` → `BankCoverage`, con el tramo de cada TF, `start`, `end` y `covering(t)`, que T25 usará
        para elegir la TF más fina;
      - `anchor_missing_reason()`: `no_history` si el ancla es anterior a la vela más vieja en todas las TF, y
        `pending_candles` si el banco todavía no llega al ancla o no tiene velas de la escalera;
      - `untouched_missing_reason()`: sin toque, `pending_candles` hasta que el camino llegue al fin del horizonte.
        El cálculo del horizonte queda para T26.
      Los dos casos que el plan no cubría (banco que no llega al ancla, banco sin velas de la escalera) quedaron
      escritos en plan.md §3.1. Tests: 18 en `tests/test_candle_resolution.py`, incluido el del banco que termina
      antes del toque. Mutación: 6 bordes, y cada uno rompe 1 o 2 tests.
      SUITE: `694 passed, 3 skipped, 97 warnings in 26.75s`; sin `.data/`.
- [x] T25. `candle_resolution`, parte 2: camino multi-TF con escalera y empalme en el borde de la TF gruesa, sin
      huecos, empezando en la primera vela de 1M en el ancla o después. (RF-4, N17, N18)
      Hecho cuando: los tests prueban la secuencia 1M → 5M → 1H en un fixture, que no hay huecos ni velas repetidas,
      y que una vela de 1M que contiene el ancla queda fuera. SUITE en verde.
      **Hecho 2026-10-02:** `forward_path(anchor, bars_by_timeframe)` en `core/candle_resolution.py`. Es un generador
      de `PathBar(timeframe, time, end, high, low)`, para que T26 corte en el primer toque sin armar el horizonte
      entero: 130 mil velas de 1M se recorren en 0,6 s. `PathBar` sirve directo para `first_touch_detail`.
      - Arranca en la primera vela de 1M que abre en el ancla o después (N17).
      - Sube a una TF más fina en el borde de la vela gruesa (N18).
      - Baja a una TF más gruesa solo en el borde de su vela; si no, corta (paso 5).
      - Cruza los tramos de mercado cerrado.
      - Las dos aclaraciones a los pasos 3 y 5, y el límite conocido (un hueco de datos se cruza como un mercado
        cerrado), quedaron escritas en plan.md §3.1.
      Tests: 11 más en `tests/test_candle_resolution.py`, incluida la secuencia 1M → 5M → 1H sin huecos ni velas
      repetidas, y la vela de 1M que contiene el ancla. Mutación: sin el corte al bajar, con el empate a favor de la
      gruesa, solo con la TF base, excluyendo la vela que abre en `t`, o con la base en la TF más gruesa: en cada caso
      falla al menos un test. La del empate no la detectaba nadie, así que se agregó un test para ese caso.
      SUITE: `794 passed, 3 skipped, 97 warnings`, sin `.data/`. Tardó 146 s en frío y 60 s en la segunda corrida (antes,
      unos 22 s). Ningún test lento es de T25: el más lento es el de P2 banco v2, de otra sesión, con 4,4 s.
- [x] T26. `candle_resolution`, parte 3: primer toque con refinamiento, `ambiguous`, hora de apertura y TF usada,
      horizonte y `open`. (RF-4, RF-4b, RF-5, RF-5b, N19)
      Hecho cuando: los tests cubren Confirmed, Invalidated, una vela doble en 15M resuelta por 1M, una doble en la
      TF más fina (`ambiguous`), y ningún toque en `MAX_HORIZON` (`open`). SUITE en verde.
      **Hecho 2026-10-02:** en `core/candle_resolution.py`:
      - `resolve_first_touch(anchor, bank, thesis, evp, si, max_horizon)` recorre `forward_path` vela por vela con la
        regla de `first_touch_detail` y devuelve `FirstTouch(outcome, touch_time, timeframe, direction, level,
        path_end, horizon_end)`. La hora del toque es la apertura de esa vela y la TF da la precisión (N19).
      - `horizon_end()` da el cierre de la vela 1H número `MAX_HORIZON` desde el ancla, igual que el backtest.
      - Los resultados son códigos neutros `confirmed`, `invalidated` y `open`; los valores del wizard les tocan a
        T30 y T40.
      - La tesis es obligatoria, porque `no_levels` lo decide T27 antes de llamar.
      - No hizo falta código de refinamiento: el camino de T25 ya va en la TF más fina de cada tramo. Una vela doble
        es `ambiguous` y el caso "doble en 15M resuelta por 1M" se da solo. Queda escrito en plan.md §3.2, junto con
        cómo se cuenta el horizonte.
      Tests: 13 más en `tests/test_candle_resolution.py`. Cubren Confirmed, Invalidated, la vela doble de 15M que
      resuelve el 1M (y sin ese 1M es `ambiguous`), la doble en 1M, `open` con un toque justo después del horizonte,
      el toque en la última vela del horizonte, el mercado que cierra justo antes del fin del horizonte (es `open`, no
      `pending`), `pending_candles`, `no_history` y el conteo del horizonte. Mutación: 9 cambios y todos rompen al
      menos un test; 2 los detectan los tests de borde agregados.
      SUITE: `807 passed, 3 skipped, 97 warnings in 56.96s`; sin `.data/`.
- [x] T27. `candle_resolution`, parte 4: precio de partida, dirección de la tesis, `no_levels`, R y MAE/MFE
      estructurales en precio. (RF-4, RF-4d)
      Hecho cuando: los tests cubren long y short, niveles del mismo lado (`no_levels`), y valores de MAE/MFE
      verificados a mano en el fixture. SUITE en verde.
      **Hecho 2026-10-02:** en `core/candle_resolution.py`:
      - `resolve_analysis(anchor, candles, evp, si, max_horizon)` devuelve `AnalysisResolution(outcome, start_price,
        thesis_direction, r, first_touch, structural_mae, structural_mfe)`. El orden es: niveles vacíos →
        `no_levels`; ancla fuera del banco → su motivo; precio de partida; tesis (mismo lado → `no_levels`); primer
        toque; MAE/MFE solo si hubo toque.
      - `start_price()` toma la vela cerrada más reciente en el ancla (más fina en un empate).
      - `Candle` es el tipo nuevo con apertura y cierre.
      - Las aclaraciones quedaron en plan.md §3.4.
      Tests: 15 más en `tests/test_candle_resolution.py`:
      - long y short confirmados, y un long invalidado, con MAE/MFE calculados a mano. Ejemplo: partida 100, mínimo
        97.5, toque en 110.5; en el short, MAE 102.5 y MFE 89.5;
      - `no_levels` en 5 variantes;
      - el precio de partida en un empate, con 1M que todavía no empezó, después de un mercado cerrado y sin ninguna
        vela cerrada.
      Mutación: 8 cambios, todos detectados. "R medido contra el objetivo" solo lo detecta un test con niveles
      asimétricos (objetivo 120, invalidación 90), porque con 110/90 los dos dan 10.
      SUITE: `822 passed, 3 skipped, 97 warnings in 28.09s`; sin `.data/`.
- [x] T28. `candle_resolution`, parte 5: reglas de revertido, expansión, mínima y sweep, más los N/A de Invalidated y
      Overlap. (RF-8, RF-8b, RF-8c, RF-8d)
      Hecho cuando: los tests cubren los bordes (vuelta al Mark Price a las 23:59 y a las 24:01 h; 0.49R y 0.50R;
      exceso de 1.0R y 1.01R; objetivo a las 47 y a las 49 h) y el caso sin Mark Price. SUITE en verde.
      **Hecho 2026-10-02:** `propose_structural(resolution, candles, evp, si, mark_price, overlap)` en
      `core/candle_resolution.py` devuelve `StructuralProposal(structural_resolution, failure_reason,
      missing_reason)`:
      - Overlap da `n/a` + `overlap`.
      - Confirmed da `reverted`, `expansion` o `minimal`, por prioridad, con `n/a`.
      - Invalidated da `n/a` + `liquidity_sweep` o nada.
      - Sin toque, no se propone nada.
      - Si el banco no cubre la ventana que decide, el resultado es `pending_candles`.
      - Las aclaraciones (ventanas medio abiertas, qué velas cuentan en cada regla, duraciones separadas) quedaron en
        plan.md §3.5.
      Tests: 24 más en `tests/test_candle_resolution.py`:
      - los cuatro pares de bordes del "Hecho cuando", el caso sin Mark Price, y la prioridad de revertido sobre
        expansión;
      - el cierre de la expansión en el toque de la invalidación, y la vela del toque que no prueba una vuelta;
      - la vela del objetivo dentro del exceso del sweep, y `pending_candles` en Confirmed y en Invalidated;
      - Overlap, sin toque, y el mismo resultado en un short espejado.
      Mutación: 13 cambios, todos detectados. Dos tests se agregaron porque al principio no se detectaban: la vela
      del objetivo en el exceso, y la expansión sin tope propio. La segunda era un defecto real: el recorrido se
      cortaba en la ventana del revertido, así que una ventana de expansión más larga nunca se habría mirado entera.
      Ahora se recorre la más larga, con un test que cambia `EXPANSION_H`.
      **Verificación del banco pedida por el usuario el mismo día** (otro chat decía que había velas repetidas en 1H y
      4H). En solo lectura:
      - el banco real no tiene ninguna repetida en ninguna TF de XAUUSD ni de BTCUSD: ni la misma hora dos veces, ni
        la misma vela corrida 1 h, ni el mismo OHLC a cualquier distancia;
      - los CSV viejos de `MT5Exports\` y las 8 corridas de `_incoming\` tampoco tienen repetidas dentro de cada
        archivo;
      - al cruzar el CSV viejo con el banco, en 4H aparecen exactamente 528 velas de XAU y 737 de BTC con la hora
        corrida 1 h: son las de invierno del CSV viejo, las mismas que N44 quitó del banco;
      - en 1H, 0.
      SUITE: `846 passed, 3 skipped, 97 warnings in 25.97s`; sin `.data/`.
- [x] T28b. *(Agregada el 2026-10-03, N45.)* Structural Resolution: gana lo que pasa primero, la vuelta al Mark
      Price o la expansión; en la misma vela, "revertido". (RF-8)
      Hecho cuando: los tests cubren expansión antes de la vuelta, vuelta antes de la expansión, las dos en la misma
      vela, y siguen en verde los de T28. SUITE en verde.
      Evidencia (2026-10-03):
      - **Regla:** `_confirmed_proposal` recorre vela por vela y decide con el primer evento. Una expansión ya ocurrida
        es definitiva, así que no queda `pending_candles` aunque falten velas para cerrar las 24 h. El test viejo
        `test_reverted_wins_over_expansion` pasó a `test_whichever_happens_first_wins`: 3 casos, cada uno también
        en espejo short. Hay un test nuevo para una expansión con menos de 24 h de velas.
      - **Mutación:** 7 de 7 muertos.
      - **Datos reales, en solo lectura:** en XAU, los Confirmed sin Overlap coinciden con lo manual en 26 de 38
        (antes 19), y Structural Resolution difiere en 23 de 74 (antes 30). En BTC, 3 de 7, sin cambios.
      SUITE: `958 passed, 3 skipped, 97 warnings in 48.39s`; sin `.data/`.
- [x] T29. `core/outcome_metrics.py`: etiqueta Overlap (N11, N22), S1, S4, exclusión de retroactivos con conteo, y
      corte direccional. (RF-5, RF-17, RF-21)
      Hecho cuando: los tests prueban que Overlap nunca excluye, S1 y S4 con "fuera", los retroactivos contados, y que
      los 21 candidatos de `analisis-overlap-2d.md` se reproducen sobre un fixture equivalente. SUITE en verde.
      **Hecho 2026-10-02:** `core/outcome_metrics.py` nuevo y puro, con:
      - `analysis_anchor()` (N4, N5);
      - `AnalysisOutcome`, que es lo que las métricas necesitan de cada análisis. `overlap_limit` lo arma T30: el
        primer toque, la vela ambigua o el fin del banco;
      - `s1()` y `s4()`, que devuelven `WinRate(wins, n, outside, excluded_backdated)`;
      - `overlap_labels()`, que devuelve `OverlapLabel(analysis_id, b_id, b_anchor, hours_a_to_b)`.
      Reglas: copiadas del script de referencia `analisis/overlap_2d.py`.
      - B es el primer análisis de la misma cuenta, retroactivos incluidos, que cumple `ancla(A) < ancla(B) <
        límite(A)`.
      - S4 cuenta los toques a 48 h o menos.
      - Los ambiguos, abiertos y pendientes no cuentan.
      - `excluded_backdated` cuenta los retroactivos **con toque**: los que habrían entrado en la cifra.
      Tests: 15 en `tests/test_outcome_metrics.py`. Incluyen los 21 candidatos reconstruidos desde la tabla, cada uno
      con su B y sus horas, y los conteos de la tabla en 48 h: 6 VALIDATION, 6 INVALIDATION y 9 sin toque. Mutación:
      9 cambios, todos detectados.
      SUITE: `861 passed, 3 skipped, 97 warnings in 31.14s`; sin `.data/`.
- [x] T29b. *(Agregada el 2026-10-03, N50 y N51.)* `core/outcome_metrics.py`: S4 estricto como cifra principal y los 7
      retroactivos recuperados (`RECOVERED_BACKDATED`) dentro de las cifras. El reporte, `docs/criterios-de-acierto.md`
      y su ejemplo pasan a mostrar S4 estricto primero, con S1 y S4 al lado. (RF-5, RF-17, RF-21)
      Hecho cuando: los tests cubren cada caso de N51 (gana en 48 h, invalidación en 48 h, toque después de 48 h, sin
      toque con 48 h cubiertas, pending antes de 48 h, ambiguo) y que un retroactivo recuperado cuenta y uno nuevo no.
      SUITE en verde.
      Evidencia (2026-10-03):
      - **`s4_strict()`** en `core/outcome_metrics.py`. `WinRate` ganó `late` (las pérdidas tardías) y
        `AnalysisOutcome` ganó `path_end`, que dice si las velas ya cubren 48 h sin toque. También
        `counted_outcomes(strict=True)`.
      - **`RECOVERED_BACKDATED`** en la configuración: los 7 ids completos. `AccountResolver.outcomes()` los marca como
        no retroactivos y les deja el ancla tipeada.
      - **Reporte:** S4 estricto primero, con S1 y S4 al lado y una columna "Late, counted as loss". El resumen en
        consola usa S4 estricto. `docs/criterios-de-acierto.md` y su ejemplo, actualizados.
      - **Mutación:** 17 de 17 muertos; 3 sobrevivieron al principio y se agregaron 2 tests.
      - **Datos reales, en solo lectura:** XAU S4 estricto todos 43/75, direccional 28/45 (4 tardíos); BTC 9/22 y
        8/16. Los recuperados ya no quedan fuera.
      SUITE: `1005 passed, 3 skipped, 97 warnings in 45.73s`; sin `.data/`.
- [x] T30. `tools/auto_resolution.py`: lectura con columnas explícitas (`mode=ro` o engine), mapa
      `asset → símbolo` (`no_mt5_symbol`), estado del reloj (`clock_unverified`/`clock_misaligned`) y armado de la
      propuesta. (RF-4e, RF-4h, RF-6b)
      Hecho cuando: los tests, con una DB de fixture sin columnas nuevas (como US100) y un banco en `tmp_path`,
      prueban la propuesta completa y cada motivo. SUITE en verde.
      **Hecho 2026-10-02:** `tools/auto_resolution.py` tiene estas piezas:
      - `read_analysis_rows()` lee en `mode=ro` (`open_readonly_session`) y con columnas explícitas. Una columna que
        no existe llega como `None`.
      - `bank_clock_reason()` devuelve `None` si el reloj está verificado. Si no, devuelve `clock_unverified` o
        `clock_misaligned`; un banco sin `status.json` da `clock_unverified`.
      - `load_bank_candles()` carga la escalera como `Candle`.
      - `AccountResolver(db, bank_root, account).propose(id)` / `.propose_all()` devuelven `AutoProposal` con:
        - el motivo (`no_mt5_symbol`, reloj, `no_levels`, `no_history`, `pending_candles`, `ambiguous`, `open`);
        - el resultado del resolvedor, la propuesta de Structural y la etiqueta Overlap;
        - los valores **ya traducidos al wizard**: "Confirmed (A equal to B)", "Overlap Invalidation (New Bias
          before resolution)", etc.;
        - la hora del toque y el MAE/MFE.
      - Con Overlap: tipo "Overlap Invalidation", Structural N/A, Failure "Overlap", y la hora del primer toque igual
        (N37). Si todavía no tocó, además da `pending_candles`.
      Tests: 12 en `tests/test_auto_resolution.py`. La DB se lee sin cambiar un byte, y se prueba el ancla con
      `analysis_start_time` cuando la columna existe. Mutación: 8 cambios. Se detectan 7; el octavo no cambia el
      comportamiento, porque sin toque la hora ya es `None`.
      **Prueba de humo en solo lectura con los datos reales:**

      | Cuenta | Análisis | Tiempo | Resultado |
      |---|---|---|---|
      | XAU | 81 | 9.3 s | 38 Confirmed, 23 Invalidated, 14 Overlap, 6 `no_levels` |
      | BTC | 24 | 3.4 s | 7, 7, 8 Overlap, 2 `no_levels` |
      | US100 | 8 | — | los 8 `clock_unverified` |

      Las DBs quedaron byte a byte iguales. Los 14 Overlap de XAU son los mismos 14 de `analisis-overlap-2d.md`; en
      BTC hay uno más que el 2026-09-27 (análisis posteriores).
      SUITE: `873 passed, 3 skipped, 97 warnings in 31.94s`; sin `.data/`.
- [x] T30b. *(Agregada el 2026-10-03, N46.)* Excepciones de toque por análisis (`TOUCH_EXCEPTIONS`): el toque pasa a
      la vela de máximo acercamiento al nivel confirmado por el operador, y el reporte lo marca. Primera: `4b17b903`.
      (RF-4, RF-6)
      Hecho cuando: los tests prueban la excepción con el otro nivel tocado después, sin toque, con el mismo nivel ya
      tocado (no cambia nada) y la marca en el reporte. SUITE en verde.
      Evidencia (2026-10-03):
      - **Configuración:** `TOUCH_EXCEPTIONS` en `config/auto_resolution.py` (id completo → nivel y nota).
      - **Resolvedor:** `resolve_analysis(touched_level=...)` y `_excepted_touch()`. La vela candidata es anterior al
        toque real; la del toque real queda fuera porque dentro de ella no se sabe el orden (la mutación lo mostró y se
        agregó el test). Hay empate, gana la primera. Si el banco ya toca ese nivel, no cambia nada. `FirstTouch`
        ganó `exception` y `closest_price`.
      - **Servicio y reporte:** `AccountResolver(touch_exceptions=...)` usa por defecto los de la configuración, y el
        reporte tiene una sección "Touch exceptions".
      - **Tests:** 6 en `tests/test_candle_resolution.py`, 3 en `tests/test_auto_resolution.py` y 1 en
        `tests/test_resolution_report.py`. Mutación: 12 de 12 muertos (3 sobrevivieron al principio y se agregaron
        2 tests).
      - **Datos reales, en solo lectura:** `4b17b903` pasa a Confirmed a las 08:06 (4369.62 contra 4370). XAU S1
        todos 38/68 → 39/68; con retroactivos, 45/75 → 46/75. Los direccionales no cambian, porque es Choppy.
      SUITE: `968 passed, 3 skipped, 97 warnings in 57.50s`; sin `.data/`.
- [x] T30c. *(Agregada el 2026-10-03, N52.)* El Overlap deja de decidir las propuestas: el tipo sale del primer toque
      y la Structural Resolution y el Failure Reason, de RF-8 y RF-8b. La etiqueta sigue en `AutoProposal.overlap` y en
      el reporte. (RF-5, RF-8c)
      Hecho cuando: los tests prueban que un análisis con Overlap propone Confirmed o Invalidated según su toque, con la
      Structural Resolution y el Failure Reason de cualquier otro análisis, y conserva la etiqueta; que un Overlap sin
      toque no propone tipo; que el reporte sigue listando los Overlap; y que el backfill ya no propone "Overlap
      Invalidation". SUITE en verde.
      Evidencia (2026-10-03):
      - **`tools/auto_resolution.py`:** `propose()` toma el tipo de `RESOLUTION_TYPE_BY_OUTCOME` siempre, y llama a
        `propose_structural` sin la etiqueta. La etiqueta se sigue calculando y queda en `AutoProposal.overlap`, que
        usan el reporte y los criterios de acierto.
      - **`core/candle_resolution.py`:** `propose_structural` ya no tiene el parámetro `overlap` ni el código
        `FAILURE_OVERLAP`.
      - **El wizard** no cambia de código: muestra lo que propone el servicio. "Overlap Invalidation" y el Failure
        Reason "Overlap" se siguen pudiendo elegir a mano.
      - **Tests:**
        - `tests/test_auto_resolution.py`: un Overlap propone lo mismo que sin el B (Confirmed, mínima, N/A, la hora y
          el MAE/MFE) y conserva la etiqueta; un Overlap sin toque no propone tipo.
        - `tests/test_candle_resolution.py`: `propose_structural` ya no conoce el Overlap.
        - `tests/test_auto_backfill.py`: un Overlap con el mismo toque que el audit manual no es conflicto.
        - `tests/test_resolution_report.py` y `tests/test_cli_resolution_report.py`: la fixture a1 pasa a coincidir
          con la regla nueva, y a3 muestra lo que proponen las velas.
      - **Mutación:** 3 de 3 muertos (volver a proponer Overlap Invalidation, perder la etiqueta, volver a N/A).
      - **El plan real, recalculado sobre copias:**
        - **XAU:** los conflictos bajan de 180 a 167. Los de tipo bajan de 11 a 8: 5 son desacuerdos reales sobre qué
          se tocó primero (3 Confirmed → Invalidated, 2 al revés), y 3 son análisis que el operador marcó como
          "Overlap Invalidation" con la regla anterior. Los de Failure Reason bajan de 11 a 3.
        - **BTC:** los conflictos bajan de 56 a 50, y los de tipo de 7 a 6 (3 reales y 3 marcados como Overlap).
      SUITE: `1199 passed, 3 skipped, 405 warnings in 54.94s`; sin `.data/`.
- [x] T31. Chequeo del Mark Price: escalera 1M → 5M → 15M con ±0.1% y versión por período. (RF-3, RF-3b)
      Hecho cuando: los tests prueban que coincide en 1M, que coincide recién en 15M, y que queda fuera (advertencia),
      más la versión por período para filas sin `mark_price_time`. SUITE en verde.
      **Hecho 2026-10-02:** en `core/candle_resolution.py`, `check_mark_price(mark_price, mark_price_time, candles)` y
      `check_mark_price_in_period(mark_price, start, end, candles)` devuelven `MarkPriceCheck(fits, timeframe,
      distance, reason)`. Las aclaraciones quedaron en plan.md §3.7: tolerancia en la última TF con vela, hora justo en
      el cierre, vela de `created_at` fuera, y el motivo `no_candle`.
      Tests: 13 más en `tests/test_candle_resolution.py`:
      - coincide en 1M; coincide recién en 5M y en 15M; dentro y fuera del 0.1% por arriba y por abajo;
      - sin 1M y sin 15M a esa hora;
      - una hora justo en el cierre de una vela;
      - las tres razones para no chequear;
      - la versión por período, con 1M, con 15M, y con las dos a la vez.
      Mutación: 6 cambios, todos detectados. Dos se agregaron porque al principio no se detectaban: la hora justo en el
      cierre, y el período con 1M y 15M a la vez.
      SUITE: `885 passed, 3 skipped, 97 warnings in 26.29s`; sin `.data/`.
- [x] T32. `tools/resolution_report.py`, datos: por cuenta, motivos, coincidencia manual contra automática,
      compliance informativo, demora del audit, lista de diferencias, S1/S4 para todos y direccionales con el manual
      al lado, Overlap con B, Mark Price fuera de sus velas, y demora de los retroactivos. (RF-6, RF-3b, RF-17, RF-21)
      Hecho cuando: los tests sobre DBs y velas de fixture verifican cada bloque. SUITE en verde.
      **Hecho 2026-10-02:** `tools/resolution_report.py` tiene:
      - `read_manual_audits()`: `mode=ro`, columnas explícitas, y "Open" sin `real_bias_b` cuenta como vacío (N9);
      - `build_report(accounts_data_dir, bank_root)`, que devuelve un `AccountReport` por cuenta existente con:
        - total, resueltos, % y motivos faltantes;
        - la comparación por campo y la lista de los que difieren;
        - el compliance al lado;
        - la demora del audit (`audit_registration_time`, o hoy `resolution_time`, menos el toque);
        - S1 y S4, para todos y para los direccionales, con el win rate manual sobre los mismos análisis;
        - los Overlap con su B, los Mark Price fuera de sus velas y la demora de los retroactivos con `saved_at`.
      Agregados de apoyo:
      - `counted_outcomes()` en `core/outcome_metrics.py`;
      - `AnalysisRow` lee también `mark_price_time` y `saved_at`, vacías hasta T35;
      - `AccountResolver.bank_for()`;
      - los helpers `parse_datetime` y `parse_float` pasan a ser públicos.
      Decisión de T32: MAE y MFE coinciden si la diferencia es de 0.1% o menos, la misma tolerancia de N16.
      Tests: 11 en `tests/test_resolution_report.py`, uno por bloque, incluida la cuenta sin archivo y la de reloj sin
      verificar. Mutación: 7 cambios, todos detectados. Tres se agregaron al fixture porque al principio no se
      detectaban: un MAE dentro del 0.1%, un `Valid` fuera de la cifra, y `saved_at` en uno no retroactivo.
      **Prueba de humo en solo lectura con los datos reales** (4 cuentas en 15 s, DBs intactas):
      - XAU: 75 de 81 resueltos (6 `no_levels`). S1 direccional 23/39 = 59% (manual 24/39); S4 direccional 23/36
        (3 fuera).
      - BTC: 22 de 24 resueltos. S1 direccional 9/16.
      - US500 y US100: todos `clock_unverified`.
      - **Punto a revisar con el usuario:** en XAU difieren 69 de 75, casi todo por MAE/MFE (41 y 48 de 73) y por
        Structural Resolution (30 de 74). Los MAE/MFE manuales parecen medir otra ventana que "del ancla al primer
        toque" (a veces terminan antes y a veces mucho después). El tipo difiere en 12 de 74: 11 son Overlap que la
        regla N11 marca y el manual no (14 contra 3); solo 1 es un desacuerdo real.
      SUITE: `896 passed, 3 skipped, 97 warnings in 26.94s`; sin `.data/`.
- [x] T33. Salida del reporte en Markdown (inglés) y subcomando `resolution-report`. (RF-6)
      Hecho cuando: el test con `CliRunner` genera el `.md` en `tmp_path`, con los encabezados esperados y código 0, y
      no escribe en ninguna DB (lo cuida el audit hook y un test de `mtime`). SUITE en verde.
      Evidencia (2026-10-02): `render_markdown` en `tools/resolution_report.py`; el subcomando y el resumen en consola,
      en `cli/main.py` (O2). Carpeta por defecto `.data/reports/`, documentada en plan.md §4 (O3). Sigue el contrato
      de §4: `--output-dir`, `--account` repetible y código 1 sin escribir el reporte si una cuenta no existe, falta
      una DB o falta el banco. `tests/test_cli_resolution_report.py`: 12 tests. Mutación: 25 mutantes, todos muertos
      (2 sobrevivieron al principio y se agregó un test). Humo con datos reales, en solo lectura y con el `.md` en el
      scratchpad: 423 líneas, DBs con el mismo SHA-256 antes y después.
      - **Punto a revisar con el usuario:** la demora del audit sale negativa en 7 análisis (4 de 74 en XAU, 3 de 22
        en BTC): el `resolution_time` manual es anterior al primer toque de las velas. 6 de los 7 son Overlap (el audit
        se cerró por un análisis nuevo antes de que el precio tocara un nivel). El otro, `70ea32f5`, está marcado
        Confirmed a las 12:33 del 27-may y las velas tocan la validación recién a las 20:13.
      SUITE: `908 passed, 3 skipped, 97 warnings in 31.68s`; sin `.data/`.
- [x] T34. `docs/criterios-de-acierto.md`: apuntar la implementación a `core/outcome_metrics.py` y agregar un ejemplo
      de uso desde un cuaderno. (RF-21)
      Hecho cuando: `grep -n "core/outcome_metrics.py" docs/criterios-de-acierto.md` da resultado y el ejemplo corre
      en un test de humo sobre el fixture. SUITE en verde.
      Evidencia (2026-10-02): el `grep` da la línea 6. Sección nueva "Uso desde un cuaderno", con las rutas de
      `config/auto_resolution.py`, así que sirve desde cualquier carpeta de trabajo. Para no copiar lógica del reporte
      en el ejemplo, el paso de propuestas a `AnalysisOutcome` pasó de `build_account_report` a
      `AccountResolver.outcomes()`; el reporte con datos reales sale igual línea por línea. `tests/test_criterios_doc_example.py`
      (3 tests) saca el bloque de código del documento y lo corre sobre el fixture. Mutación de `outcomes()`: 6 de 6
      muertos.
      SUITE: `911 passed, 3 skipped, 97 warnings in 29.01s`; sin `.data/`.

## Etapa 4 — Esquema y horas del análisis

- [x] T33b. *(Agregada el 2026-10-03.)* `python cli/main.py ...` falla con `No module named 'core'`: correr el
      archivo pone `cli/` en `sys.path`, no la raíz del repo. Solo funcionaba `python -m cli.main` (la función
      `trading` del usuario). Los comandos documentados en el plan §4 y en `CLAUDE.md` usan la primera forma.
      `cli/main.py` agrega la raíz del repo a `sys.path` antes de sus imports. (—)
      Hecho cuando: un test corre `python cli/main.py --help` en un subproceso, desde una carpeta temporal, con código
      0. SUITE en verde.
      Evidencia (2026-10-03): `cli/main.py` agrega la raíz del repo a `sys.path` antes de importar `core`. El test
      `tests/test_cli_entrypoint.py` corre el archivo sin PYTHONPATH, desde una carpeta temporal. Antes del arreglo
      fallaba con el mismo error que vio el usuario; después pasa, y el `--help` del grupo no crea `.data/`.
      SUITE: `1006 passed, 3 skipped, 97 warnings in 55.71s`; sin `.data/`.
- [x] T35. `tools/database.py`: columnas nuevas, ORM de `backfill_history` y shim de `init_db`. (RF-13d, RF-13e,
      RF-14, RF-14b, RF-18, NFR-1)
      Hecho cuando: los tests prueban que `init_db` sobre una DB de archivo en `tmp_path`, creada con el esquema
      viejo, agrega las 5 columnas y la tabla, y que correrlo dos veces no falla. SUITE en verde.
      Evidencia (2026-10-02):
      - **Columnas:** `analysis_start_time`, `mark_price_time` y `saved_at` en `UnifiedDepartment`;
        `audit_registration_time` y `resolution_time_source` en `EfficiencyAudit`. El shim las agrega vacías.
      - **Tabla:** `BackfillHistory`, según §2.2, con dos triggers que abortan UPDATE y DELETE (aclaración en el plan
        §2.2).
      - **Lector en solo lectura:** `assemble_p2_systematic_rows` pide columnas explícitas. Con la entidad completa,
        sobre una DB sin migrar, fallaba con "no such column".
      - **Fixtures:** los de T30 y T32 crean por defecto el esquema de antes de T35 (`drop_spec002_schema`, O1), y hay
        un test con el esquema nuevo.
      - **Tests:** `tests/test_database_spec002_schema.py` (10). Mutación: 7 de 7 muertos.
      - **Ensayo sobre copias** de las 4 DBs reales, hechas con la API de backup de SQLite en solo lectura y guardadas
        en el scratchpad: `init_db` dos veces. En las 4 aparecen las 5 columnas, la tabla y los 2 triggers;
        `integrity_check` da ok, `foreign_key_check` vacío y los conteos de filas son iguales. Las reales tienen el
        mismo SHA-256 antes y después.
      - **Esto todavía no es T58:** las DBs reales no se tocaron.
      SUITE: `922 passed, 3 skipped, 97 warnings in 33.70s`; sin `.data/`.
- [x] T36. `cli/schemas/audit_efficiency.py`: `resolution_time` opcional, `audit_registration_time` y
      `resolution_time_source`. (RF-7g, RF-14, RF-14b)
      Hecho cuando: los tests del schema (existentes y nuevos) pasan y el guardado persiste los campos nuevos vía
      `update_record_state`. SUITE en verde.
      Evidencia (2026-10-02): `RESOLUTION_TIME_SOURCES` usa los códigos de `config/auto_resolution.py` y
      `OUTCOME_OPEN`, así que no hay copias. Un validador rechaza valores desconocidos y otro rechaza `candles` o
      `corrected` sin hora (aclaración en el plan §2.1). `update_record_state` no cambió: ya filtra por las columnas
      del ORM, y las nuevas pasan solas. `tests/test_audit_efficiency_spec002.py` (17); los 6 tests viejos del schema
      siguen en verde. Mutación: 5 de 5 muertos. El wizard todavía guarda `resolution_time = now()` y nada en las
      columnas nuevas; eso lo cambia T43.
      SUITE: `939 passed, 3 skipped, 97 warnings in 33.07s`; sin `.data/`.
- [x] T37. `flow_new_analysis`: `analysis_start_time` al confirmar P0 por primera vez, sin cambiar con
      `RestartFlowException`; hora tipeada en retroactivo y clon [2] (RF-13b gana); hora de elección en clon [1].
      (RF-13, RF-13b, RF-13c)
      Hecho cuando: los tests cubren los 4 casos y la red de seguridad de T7 sigue en verde. SUITE en verde.
      Evidencia (2026-10-02):
      - **Reloj:** `_now_gt()`, en GT naive como `created_at`.
      - **Clon:** la elección del modo de hora pasó a `ask_clone_timestamps()`, que devuelve `(hora tipeada, None)` o
        `(None, hora de la elección)`. Así se puede probar fuera de la función anidada `show_unified_detail`.
      - **`flow_new_analysis(started_at=...)`:** el valor inicial es `backdated_timestamp or started_at`. Si no hay
        ninguno, se fija la primera vez que vuelve el prompt de la fuerza de P0, y nunca se sobrescribe.
      - **Tests:** `tests/test_analysis_times.py`, 6 tests: los 4 casos, la vuelta atrás que vuelve a pedir P0 y el
        descarte. El reloj falso devuelve una hora atada a la cantidad de prompts respondidos, así que el test
        distingue "al confirmar P0" de "al abrir el wizard". Mutación: 7 de 7 muertos. La red de seguridad de T7 sigue
        en verde.
      - **Sin test automático:** la línea que conecta `ask_clone_timestamps()` con `show_unified_detail`, por estar
        anidada. Se revisa en la demo T61.
      SUITE: `945 passed, 3 skipped, 97 warnings in 34.14s`; sin `.data/`.
- [x] T38. `flow_new_analysis`: `mark_price_time` (la hora tipeada en retroactivos) y `saved_at` real. (RF-13d,
      RF-13e)
      Hecho cuando: los tests verifican las dos horas en un análisis nuevo y en uno retroactivo. SUITE en verde.
      Evidencia (2026-10-02):
      - **`mark_price_time`:** el prompt del Mark Price anota la hora apenas se responde. Al guardar, se usa la hora
        tipeada del análisis si es retroactivo o un clon [2], y si no, la de ese momento. Si se vuelve atrás y el Mark
        Price se tipea de nuevo, cuenta la última vez. Sin Mark Price (vacío o inválido) queda vacía. El Mark Price no
        está en el menú "Edit a Field", así que solo cambia volviendo atrás.
      - **`saved_at`:** la hora real del "Confirm & Save", también en un retroactivo.
      - **Tests:** 4 nuevos en `tests/test_analysis_times.py` (10 en total). Mutación: 6 de 6 muertos.
      SUITE: `949 passed, 3 skipped, 97 warnings in 42.23s`; sin `.data/`.
- [x] T39. Aviso de una línea del Mark Price después de guardar, sin bloquear nunca. (RF-3)
      Hecho cuando: los tests prueban el aviso con el banco cubriendo, que no hay aviso sin banco, y que el guardado
      ocurre en los dos casos. SUITE en verde.
      Evidencia (2026-10-02):
      - **`check_saved_mark_price()`** (`tools/auto_resolution.py`): lee solo la hora de velas anterior a
        `mark_price_time` en 1M, 5M y 15M y usa `check_mark_price` (T31). Devuelve `None` si no hay símbolo MT5, si
        el reloj no está verificado o si no hay vela en esa hora. Con el banco real tarda 0.14 s.
      - **`warn_if_mark_price_off()`** (`cli/main.py`) muestra la línea "Warning: Mark Price ... The analysis was
        saved." solo con `fits=False`. Atrapa cualquier error del chequeo.
      - **Defecto encontrado por mutación:** al principio la llamada iba dentro del `try` de la transacción, así que
        un error después del commit mostraba "Transaction rolled back" (falso, ya estaba guardado) y se salteaba
        "¿Deseas alimentar un Tactical Audit ahora?". Ahora va después del bloque, y los tests comprueban que el
        wizard llega hasta el final sin ese mensaje.
      - **Tests:** 6 nuevos en `tests/test_analysis_times.py` (16 en total): el Mark Price dentro y fuera de la
        vela, sin banco, con el reloj sin verificar, con el chequeo roto, y la función sola. Mutación: 7 de 7
        muertos.
      SUITE: `955 passed, 3 skipped, 97 warnings in 38.50s`; sin `.data/`.

## Etapa 5 — Propuestas en el Efficiency Audit

- [x] T40. Parámetro `default` en `get_enum_choice` y `get_mandatory_float`, y helper nuevo
      `get_optional_datetime`. (RF-7, RF-7e)
      Hecho cuando: los tests prueban que el default llega a `inquirer`, que sin default el comportamiento es el de
      hoy, y que los tests existentes siguen en verde. SUITE en verde.
      Evidencia (2026-10-03):
      - **Con un valor propuesto:** el mensaje lo marca `(auto: ...)` y llega a InquirerPy como `default`.
        `get_enum_choice` acepta el miembro o su texto e ignora un default que no está entre las opciones.
        `get_mandatory_float` lo escribe sin notación científica.
      - **`get_optional_datetime`:** `(auto: 2026-08-21 09:15, ±1 min)`, acepta el campo vacío y devuelve `None` en
        ese caso.
      - **Sin default:** InquirerPy recibe exactamente lo mismo que antes; lo prueban los tests.
      - **Tests:** `tests/test_prompt_defaults.py`, 13 tests. Mutación: 9 de 9 muertos; uno sobrevivió al principio y
        se agregó un test con un `Enum` común.
      SUITE: `1018 passed, 3 skipped, 97 warnings in 47.16s` (más el test nuevo); sin `.data/`.
- [x] T41. Efficiency, parte 1: pedir la propuesta al abrir el wizard y mostrar el motivo en una línea cuando no la
      hay. (RF-7f, RF-5b)
      Hecho cuando: los tests prueban "Still open according to candles", `pending_candles` y `clock_unverified`, y que
      los prompts siguen sin default. SUITE en verde.
      Evidencia (2026-10-03):
      - **Servicio:** `propose_for_trade()` en `tools/auto_resolution.py` lee la DB de la cuenta activa en solo lectura
        y el banco.
      - **Wizard:** `efficiency_proposal()` la pide una sola vez antes de los prompts y nunca bloquea: un error da
        `None`. `proposal_status_line()` arma la línea en inglés: "Still open according to candles", "Candles: no
        proposal (<motivo>)" o "Candles: no proposal (unavailable)".
      - **Tests:** `tests/test_efficiency_proposals.py`, 5 tests. Mutación: 4 de 4 muertos. La red de seguridad de T5
        sigue en verde.
      SUITE: `1024 passed, 3 skipped, 105 warnings in 38.20s`; sin `.data/`.
- [x] T42. Efficiency, parte 2: defaults `(auto)` en Resolution Type, Structural Resolution, Failure Reason y
      Structural MAE/MFE. (RF-7, RF-7e, RF-8 a RF-8d)
      Hecho cuando: los tests prueban que cada default llega a su prompt, que aceptar guarda el valor, que corregir
      guarda el valor del operador, y que la red de seguridad de T5 sigue en verde en el orden. SUITE en verde.
      Evidencia (2026-10-03):
      - **Defaults:** cada valor propuesto llega como default `(auto)` a Resolution Type, Structural Resolution,
        Failure Reason y los dos precios. `auto_default()` no agrega la clave si no hay valor, así que sin propuesta el
        prompt queda idéntico al de antes (INV-1).
      - **Precios:** MAE y MFE siguen siendo opcionales; con propuesta, el campo viene escrito y dice `(auto: …)`.
        Vaciarlo deja el campo vacío.
      - **Solo del operador:** Real Bias B y la lección nunca llevan default.
      - **Tests:** 4 nuevos en `tests/test_efficiency_proposals.py`: el default llega a cada prompt, aceptar guarda lo
        propuesto, corregir guarda lo del operador, y lo que las velas no proponen no lleva default. Mutación: 8 de 8
        muertos. La red de seguridad de T5 sigue en verde, con el mismo orden.
      SUITE: `1028 passed, 3 skipped, 113 warnings in 38.30s`; sin `.data/`.
- [x] T43. Efficiency, parte 3: prompt "Resolution Time" opcional al final, `audit_registration_time`, y
      `resolution_time_source` (`candles`, `corrected` o el motivo). (RF-7c, RF-7g, RF-14, RF-14b)
      Hecho cuando: los tests cubren aceptar, corregir, dejar vacío y el caso sin propuesta, con el valor persistido
      de las tres columnas. SUITE en verde.
      Evidencia (2026-10-03):
      - **Prompt:** "Resolution Time" va al final, después de Structural MFE, con `get_optional_datetime`: la hora
        propuesta como `(auto: …, ±1 min)`, con la precisión según la TF del toque.
      - **Al guardar:** `resolution_time` es lo que quedó en el prompt (antes era `now()`), `audit_registration_time`
        es la hora real (GT) y `resolution_time_source` sigue `resolution_time_source()`. La aclaración de T43 está en
        el plan §2.1.
      - **Al retomar una pausa:** la hora llega como texto y se vuelve a leer como fecha (`resolution_time_entry`).
      - **Red de seguridad de T5:** se actualizó a propósito, como anunciaba su propio comentario: 3 prompts de texto,
        y la hora del guardado ahora va en `audit_registration_time`.
      - **Tests:** 9 nuevos en `tests/test_efficiency_proposals.py`. Mutación: 7 de 8 muertos. El que sobrevive es
        equivalente: una propuesta con hora de toque nunca trae motivo.
      - **Pendiente fuera de esta tarea:** el flujo de reparación (`flow_repair_analysis_audits`, opción
        `ea_res_time`) todavía edita `resolution_time` sin tocar `resolution_time_source`. Lo cubre T44 o se anota
        aparte.
      SUITE: `1039 passed, 3 skipped, 127 warnings in 39.29s`; sin `.data/`.
- [x] T44. Efficiency, parte 4: "Resolution Time" en el menú "Edit a Field" y en el panel de revisión con la marca
      `(auto)`. (RF-7, INV-1)
      Hecho cuando: los tests prueban que la edición funciona y que el panel muestra la marca. SUITE en verde.
      Evidencia (2026-10-03):
      - **Panel de revisión:** `(auto)` en cada valor que sigue siendo el que propusieron las velas (`auto_mark`), y una
        línea nueva "Resolution Time". Sin hora, muestra el motivo, por ejemplo `N/A (pending_candles)`. Al corregir
        un valor, la marca desaparece.
      - **"Edit a Field":** suma "Resolution Time". Las ediciones de los campos propuestos vuelven a ofrecer el valor
        `(auto)`.
      - **Flujo de reparación:** editar `ea_res_time` ahora marca `resolution_time_source = corrected` (era el
        pendiente de T43).
      - **Tests:** 6 nuevos en `tests/test_efficiency_proposals.py`. Mutación: 9 de 9 muertos; 2 sobrevivieron al
        principio y se agregó el test de valores corregidos.
      SUITE: `1045 passed, 3 skipped, 139 warnings in 35.16s`; sin `.data/`.

## Etapa 6 — Propuestas en el Tactical Audit

- [x] T45. MAE/MFE propuestos, con la TF más fina validada, R, tope de 10 y los motivos `no_interval`, `zero_r` y
      `pending_candles`. (RF-9, RF-9b, RF-9c, RF-9d)
      Hecho cuando: los tests cubren la propuesta aceptada y corregida y cada motivo, y la red de seguridad de T6 sigue
      en verde. SUITE en verde.
- [x] T46. `Could hit TP?` propuesto, más `invalid_tp` y la vela doble. `session` no se toca. (RF-10, RF-10b, RF-10c,
      RF-10d)
      Hecho cuando: los tests cubren "yes", "no", la vela doble sin propuesta e `invalid_tp`, y `session` sigue
      calculándose igual. SUITE en verde.
      Evidencia de T45 y T46 (2026-10-03), en un solo commit porque comparten el núcleo, el servicio y el wizard:
      - **Núcleo** (`core/candle_resolution.py`):
        - `trade_excursion()`: la primera de 1M, 5M y 15M que cubre todo el trade; las velas que se solapan con
          [entrada, salida], también las de la entrada y la salida; en R, de 0 a 10 y a 2 decimales.
        - `could_hit_tp()`: el camino de T25 solo en 1M, 5M y 15M (N8), hasta el SL o `MAX_HORIZON`.
        - Motivos: `no_interval`, `zero_r`, `invalid_tp`, `ambiguous`, `pending_candles` y `no_history`.
      - **Servicio:** `propose_tactical()` en `tools/auto_resolution.py`, con la dirección del schema (Long si la
        entrada está sobre el SL), el símbolo MT5 y el reloj verificado.
      - **Wizard:**
        - `Could hit TP?`, MAE y MFE llevan la propuesta como default `(auto)`, también en "Edit a Field".
        - Sin propuesta, una línea con el motivo y los prompts de siempre.
        - La propuesta se calcula una vez por cada combinación de datos de la orden.
        - Un error del servicio nunca bloquea.
        - `session` sigue saliendo de `entry_time`.
      - **Tests:** `tests/test_trade_proposals.py` (17) y `tests/test_tactical_proposals.py` (7). La red de seguridad
        de T6 sigue en verde. Mutación: 15 de 15 en el núcleo y 11 de 11 en el servicio y el wizard; 2 sobrevivieron
        al principio (el camino solo en 1M/5M/15M y el reloj sin verificar) y se agregaron sus tests.
      - **Datos reales, en solo lectura:**
        - XAU: de 35 órdenes llenadas, hay MAE/MFE propuesto en 31; las 4 sin propuesta son `no_interval`, con la
          salida igual a la entrada. Coinciden con lo manual, a ±0.1R, el MAE en 6 y el MFE en 12. Las diferencias
          tienen mediana casi 0 (sin sesgo, pero dispersas). `Could hit TP?` coincide en 32 de 35.
        - BTC: 4 órdenes.
        - **Punto a revisar con el usuario.**
      SUITE: `1069 passed, 3 skipped, 144 warnings in 35.88s`; sin `.data/`.

## Etapa 7 — Backfill

- [x] T47. `tools/auto_backfill.py`: plan de cambios (`fill`, `unchanged`, `conflict`), "Open" con `real_bias_b` NULL
      como vacío, audits nunca hechos incluidos, y la regla `legacy_move` solo con `audit_registration_time` vacío.
      (RF-11, RF-11b, RF-11c)
      Hecho cuando: los tests sobre DBs de fixture verifican cada tipo de cambio. SUITE en verde.
      Evidencia (2026-10-03):
      - **Funciones:** `plan_account()` y `build_plan()` en `tools/auto_backfill.py`. Cada cambio es un
        `PlannedChange` (`fill`, `unchanged`, `conflict` o `legacy_move`) con su valor viejo, el nuevo y el origen.
        Tolerancias y reglas finas en el plan §3.11.
      - **Reuso:** el táctico usa `tactical_from_candles()` (sale de `propose_tactical`, T46) con las velas que ya
        cargó el resolvedor.
      - **Tests:** `tests/test_auto_backfill.py` (9). Mutación: 12 de 13 muertos; el que sobrevive es equivalente,
        porque con el reloj sin verificar `bank_for()` no carga velas.
      - **Datos reales, en solo lectura (12.5 s, DBs intactas):**
        - XAU: 168 `fill`, 180 `conflict`, 267 `unchanged` y 80 `legacy_move`. Los conflictos son sobre todo MAE/MFE
          estructurales (41 y 48) y tácticos (25 y 19), Structural Resolution (22) y el tipo (11).
        - BTC: 62 `fill`, 56 `conflict` y 24 `legacy_move`.
        - US500: 10 `fill`, solo el motivo `clock_unverified`.
        - US100: 13 `fill` y 5 `legacy_move`.
      SUITE: `1078 passed, 3 skipped, 144 warnings in 38.85s`; sin `.data/`.
- [x] T48. `cli/backfill_view.py`: vista estilo `git log --decorate --oneline --graph`. (RF-19)
      Hecho cuando: los tests verifican el texto: una línea por corrida con las decoraciones, `!` en los conflictos y
      `—` en los vacíos. SUITE en verde.
      Evidencia (2026-10-03):
      - **`render_runs()`** devuelve las líneas con su estilo, para testearlas sin terminal; `print_runs()` las
        imprime con Rich.
      - **Formato:** la corrida más nueva primero; decoraciones con la cuenta, `HEAD` y `dry-run`; un resumen por tipo.
        En cada cambio, `+` para llenar (verde), `>` para el traslado de la hora vieja (verde), `!` para un conflicto
        (amarillo) y `=` si no cambia (gris), y `—` en los vacíos. Se puede ocultar lo que no cambia.
      - **Tests:** `tests/test_backfill_view.py` (5). Mutación: 10 de 10 muertos.
      - **Muestra con datos reales**, en solo lectura, para `887dbdc1`: 2 `fill`, 5 `unchanged` y 1 `legacy_move`.
      SUITE: `1083 passed, 3 skipped, 144 warnings in 44.85s`; sin `.data/`.
- [x] T49. Aplicación transaccional, inserciones en `backfill_history` e idempotencia. (RF-11b, RF-11c, RF-18)
      Hecho cuando: los tests prueban que la segunda corrida no cambia nada, que el historial tiene una fila por
      cambio, y que el código nunca hace `UPDATE` ni `DELETE` sobre `backfill_history`. SUITE en verde.
      Evidencia (2026-10-03):
      - **`apply_plan()`** en `tools/auto_backfill.py`: una transacción por cuenta. Por cambio, un `UPDATE` que tiene
        que tocar exactamente una fila y un `INSERT` en `backfill_history`. Escribe los `fill`, los `legacy_move` y
        los conflictos aceptados, que quedan como `accepted_conflict`.
      - **Seguridad:** solo escribe los campos de `WRITABLE_FIELDS`, así que nunca los de INV-8. Si no hay nada que
        aplicar, ni abre la DB.
      - **Defecto encontrado por el test de idempotencia:** en un audit nunca hecho, la segunda corrida tomaba la
        hora del toque por la hora vieja del guardado y la movía. El `legacy_move` ahora exige también
        `resolution_time_source` vacío (aclaración en el plan §3.11). Las 80 filas viejas de XAU siguen entrando
        igual.
      - **Tests:** `tests/test_backfill_apply.py` (10) y uno más en `tests/test_auto_backfill.py`. Mutación: 9 de 9
        muertos; 2 sobrevivieron al principio y se agregaron sus tests.
      SUITE: `1093 passed, 3 skipped, 144 warnings in 45.61s`; sin `.data/`.
- [x] T49b. *(Agregada el 2026-10-03: bug encontrado al medir el backfill sobre copias de las DBs reales.)* Una
      `resolution_time` vacía con un motivo temporal (`pending_candles`, `clock_unverified`, `clock_misaligned`) se
      vuelve a intentar en la corrida siguiente. Hoy queda trabada: el backfill solo llena filas sin motivo. Afecta a
      los audits viejos de US100 y US500, cuyo reloj todavía no está verificado, y a los audits guardados con
      `pending_candles` desde T43. (RF-11, RF-11c, RF-14b)
      Hecho cuando: los tests prueban que una fila con motivo temporal se llena cuando las velas ya tienen el toque
      (con `resolution_time_source = candles`), que cambia de motivo si cambió, que no se repite si sigue igual, y que
      los motivos definitivos (`no_levels`, `no_history`, `ambiguous`, `open`, `no_mt5_symbol`) no se tocan. SUITE en
      verde.
      Evidencia (2026-10-03):
      - **`tools/auto_backfill.py`:** `RETRY_TIME_SOURCES` (`pending_candles`, `clock_unverified`, `clock_misaligned`).
        Una `resolution_time` vacía con uno de esos motivos se vuelve a intentar en cada corrida:
        - si las velas ya tienen el toque, se llenan la hora y el origen (`candles`), con el motivo anterior como valor
          viejo en el historial;
        - si cambió el motivo, se actualiza solo el origen;
        - si sigue igual, no hay cambio.
        Un motivo definitivo, o `corrected` (el operador la vació), no se toca. La regla del `legacy_move` no cambia.
      - **Tests** (`tests/test_auto_backfill.py`, +11):
        - los tres motivos temporales se llenan cuando hay toque;
        - un motivo que cambia se actualiza y uno que sigue igual no se repite;
        - los seis motivos definitivos no se tocan;
        - de punta a punta, el caso de US100: la primera corrida, con el reloj sin verificar, mueve la hora del
          guardado; la segunda, ya verificado, llena la hora del toque; la tercera no cambia nada.
      - **Mutación:** 5 de 5 muertos.
      - **También cubre los audits del wizard:** desde T43, un audit guardado antes de que llegaran las velas queda
        con `pending_candles`, que antes tampoco se volvía a intentar.
      SUITE: `1210 passed, 3 skipped, 405 warnings in 54.26s`; sin `.data/`.
- [x] T50. Puertas: ensayo sobre una copia temporal con `init_db`, backup de las últimas 24 h, confirmación
      escribiendo `APPLY`, y sus códigos de salida. (RF-11d, R9)
      Hecho cuando: los tests prueban los códigos 3, 4 y 5 y que en esos casos no se escribe nada. SUITE en verde.
      Evidencia (2026-10-03):
      - **`run_apply()`** en `tools/auto_backfill.py`, con `rehearse()`, `recent_backup()` e `integrity_check()`.
      - **Ensayo:** copia con la API de backup de SQLite en una carpeta temporal que siempre se borra, `init_db`,
        aplicación del plan, `integrity_check` y un segundo plan que ya no tiene nada para llenar. Ensaya todas las
        cuentas antes de escribir la primera (RF-11d).
      - **Mensajes:** cada paso informa con una línea en inglés. Orden de las puertas aclarado en el plan §3.11.
      - **Tests:** `tests/test_backfill_gates.py` (13), que prueban 0, 3 (incluida una copia que falla
        `integrity_check`), 4 (sin backups, uno de 24 h y 1 s, sin la DB y archivo vacío) y 5 (vacío, minúsculas,
        con espacio y otra palabra). En todos los casos sin escritura comprueban los bytes de las DBs.
      - **Mutación:** 8 de 8 muertos; uno sobrevivió al principio y se separó `integrity_check` para testearlo.
      SUITE: `1106 passed, 3 skipped, 144 warnings in 47.96s`; sin `.data/`.
- [x] T51. Subcomando `backfill [--apply]` con aceptación de conflictos uno por uno. (RF-11, RF-11e, RF-19)
      Hecho cuando: el test con `CliRunner` sobre fixtures en `tmp_path` cubre el dry-run sin escrituras y un conflicto
      aceptado que se registra como `accepted_conflict`. SUITE en verde.
      Evidencia (2026-10-03):
      - **`backfill [--apply] [--account ID ...] [--hide-unchanged]`** en `cli/main.py`. Por cuenta muestra la vista
        previa (`dry-run`, `HEAD`) y debajo el historial de `backfill_history` (`read_history`). La vista sabe mostrar
        `accepted_conflict`.
      - **Con `--apply`:** pregunta cada conflicto con `y`, `n` o `q` (`q` deja todos los que faltan), informa cuántos
        se aceptaron y pasa por `run_apply`: ensayo, backup de 24 h y `APPLY`.
      - **Códigos de salida:** 0, 1 (cuenta desconocida), 3, 4 y 5.
      - **Tests:** `tests/test_cli_backfill.py` (7), con el dry-run sin escrituras (bytes iguales), un conflicto
        aceptado registrado como `accepted_conflict` y los otros sin tocar, `q`, la cancelación (5), la falta de backup
        (4), el historial en la vista previa siguiente, en el orden aplicado, y la cuenta desconocida (1).
      - **Mutación:** 9 de 9 muertos; uno sobrevivió al principio (el orden del historial) y se agregó la verificación.
      SUITE: `1114 passed, 3 skipped, 144 warnings in 45.63s`; sin `.data/`.

- [x] T51b. *(Agregada el 2026-10-03, N53.)* `backfill --apply --accept CAMPO` (se puede repetir): acepta todos los
      conflictos de ese campo sin preguntar, y pregunta los demás uno por uno. Sin `--apply`, la vista previa dice
      cuántos aceptaría. Un campo que no puede tener conflictos sale con 1. (RF-11e)
      Hecho cuando: los tests con `CliRunner` prueban que los conflictos del campo se aceptan sin preguntarse y quedan
      como `accepted_conflict`, que los demás se siguen preguntando, que `q` no frena a los del campo, la vista previa
      sin escrituras y el campo desconocido. SUITE en verde.
      Evidencia (2026-10-03):
      - **`tools/auto_backfill.py`:** `CONFLICT_FIELDS` (los 9 campos que se comparan con un valor existente) y
        `conflicts_of_fields(plans, fields)`, que junta los conflictos de esos campos con las claves que recibe
        `apply_plan`. Solo toma conflictos: un campo sin cambio no se acepta.
      - **`cli/main.py`:** `backfill --accept CAMPO` (se puede repetir).
        - Con `--apply`, esos conflictos se aceptan antes de las preguntas y no se preguntan; los demás siguen uno por
          uno, y `q` frena solo las preguntas. El resumen dice `N conflicts accepted (M with --accept)`.
        - Sin `--apply`, la vista previa agrega `With --accept, N conflicts would be accepted: structural_mae 41, ...`.
        - Un campo que no puede tener conflictos sale con 1 y la lista de los que sí, antes de exportar o calcular
          nada.
      - **En las DBs reales** (plan recalculado sobre copias con N52): `--accept structural_mae --accept structural_mfe
        --accept mae_adverse --accept mfe_favorable` acepta 133 conflictos en XAU y 31 en BTC. Quedan para preguntar 34
        y 19.
      - **Tests:** `tests/test_cli_backfill.py` (+5): aceptar dos campos sin preguntarlos (queda una sola pregunta),
        `q` no frena a los del campo, la vista previa sin escrituras, el campo desconocido (1, sin escribir), y
        `conflicts_of_fields` solo con conflictos.
      - **Mutación:** 7 de 7 muertos.
      - **De paso:** el test de T56 que compara la salida del wizard con y sin velas fallaba a veces, porque el panel de
        revisión muestra `Created:` con la hora real al segundo. Arreglado en el test (`ac554ba`), y probado con un
        segundo de diferencia entre las dos corridas.
      SUITE: `1215 passed, 3 skipped, 405 warnings in 71.66s`; sin `.data/`.

## Etapa 8 — Export automático (solo si el spike de T22 funcionó)

- [x] T52. Disparo en segundo plano al guardar un unified analysis, con `AUTO_EXPORT`. (RF-20, RF-20f)
      Hecho cuando: los tests, con `Popen` mockeado, prueban que se lanza una vez, que el guardado no espera, y que no
      se lanza con `AUTO_EXPORT` desactivado. SUITE en verde.
      Evidencia (2026-10-03):
      - **`tools/auto_export.py`** (nuevo): `start_export(symbols)` lanza `python -m tools.candle_sync --symbol S ...
        --log <ACCOUNTS_DATA_DIR>/candle_export.log` con `start_new_session` (sigue vivo aunque se cierre el CLI), sin
        stdin ni stdout, y con stderr al mismo log. Le pasa al proceso las rutas del CLI y el `PYTHONPATH` del repo.
        Con `AUTO_EXPORT` apagado no lanza nada. Un símbolo con un export en curso no se vuelve a lanzar (RF-20f), y
        los procesos que terminaron se cosechan en el lanzamiento siguiente. Si no se puede lanzar, vuelve
        `sync_not_launched: <motivo>`, sin levantar.
      - **`tools/candle_bank.py`:** `bank_lock_held(bank_dir)`, de solo lectura. Sale de la misma regla que
        `acquire_bank_lock` (`_live_lock`): un candado de un proceso muerto, vencido o ilegible no cuenta.
      - **`tools/candle_sync.py`:** `--symbol` se puede repetir. Los símbolos se exportan uno detrás del otro, para no
        abrirle a MT5 varias sesiones a la vez, con una línea por símbolo y el peor código de salida. `--log` agrega
        cada línea con la hora. Un pipe cerrado o un log que no se puede escribir no tumban el export.
      - **`cli/main.py`:** `auto_export_in_background()`, llamada después de guardar el análisis, junto al aviso del
        Mark Price. Muestra `Candle export started in the background: XAUUSD` (o `already running`, o `skipped`).
        Con `AUTO_EXPORT` apagado no busca ni el símbolo, y un error inesperado se muestra en una línea y el wizard
        sigue.
      - **Tests:** `tests/test_auto_export.py` (17). Prueban:
        - que guardar lanza un proceso, una sola vez, y que nadie lo espera;
        - que con `AUTO_EXPORT` apagado no se lanza nada, ni con un export en curso;
        - que un fallo al lanzar y un error inesperado no frenan el guardado;
        - los candados muertos o ilegibles, la cosecha, el log ausente y el entorno del proceso;
        - `candle_sync` con tres símbolos (orden, líneas, log y código 6);
        - un proceso real de `candle_sync`, sin exportador configurado y con todo en `tmp_path`, que termina en
          `exporter_not_configured` y lo deja en el log y en `status.json`.
      - **Mutación:** 23 de 23 muertos. Dos sobrevivieron al principio (el chequeo de `AUTO_EXPORT` dentro de
        `start_export` y el `except` del CLI) y se sumó un test para cada uno.
      - **Lo que paró el aislamiento:** la primera versión de los tests reemplazaba `subprocess.Popen` para todo el
        CLI, pero `start_export` había tomado el original al importarse, así que lanzó procesos reales de
        `candle_sync`. Todos quedaron en `tmp_path`, sin exportador configurado. Ahora los tests reemplazan
        `auto_export.Popen`.
      SUITE: `1131 passed, 3 skipped, 144 warnings in 72.52s`; sin `.data/`.
- [x] T53. Disparo al abrir un audit, con espera acotada y progreso. (RF-20b, RF-20e)
      Hecho cuando: los tests prueban que la espera termina en `AUTO_EXPORT_WAIT_S` y continúa, y la línea "Candle
      export skipped". SUITE en verde.
      Evidencia (2026-10-03):
      - **`tools/auto_export.py`:**
        - `start_export(symbols, wait=True)` lee la salida del proceso con un hilo, una línea por símbolo.
        - `wait_for_export(launch, wait_s, progress)` espera hasta `wait_s` y devuelve qué mostrar:
          - la línea de cada símbolo que terminó;
          - para un export que ya estaba en curso (candado tomado), espera a que suelte el candado y muestra cómo
            terminó según su `status.json`, con `(started earlier)`;
          - si algo no terminó: `Candle export skipped: not finished in 30s (XAUUSD); continuing with the candles in
            the bank, the export keeps running in the background`.
        - Ctrl+C corta la espera, no el export (el proceso está en otra sesión y no recibe la señal). Un proceso que
          termina sin dar su línea se informa con el código y la ruta del log.
      - **`cli/main.py`:**
        - `auto_export_and_wait()` muestra el progreso con `console.status`: `Exporting candles (XAUUSD): 12s of 30s,
          Ctrl+C to stop waiting`.
        - Se llama al abrir el Efficiency Audit, antes de pedir la propuesta, y al abrir el Tactical, antes de su
          bucle.
        - Con `AUTO_EXPORT` apagado no hace nada, y un error inesperado se muestra en una línea y el audit sigue.
      - **A tener en cuenta:** en "¿alimentar un Tactical Audit ahora?", justo después de guardar el análisis, el
        export que lanzó el guardado (T52) sigue en curso. El Tactical espera ese mismo export, unos 16 s con MT5
        abierto según el spike, en vez de lanzar otro. Ctrl+C saltea la espera.
      - **Tests:** `tests/test_auto_export_wait.py` (16), con reloj falso:
        - termina a tiempo y deja de esperar;
        - la espera termina justo en 30 s (60 vueltas de 0.5 s) sin matar el proceso;
        - los símbolos se informan a medida que terminan;
        - un export previo se espera por su candado (terminado bien o con error, y sin terminar);
        - el lector sigue vivo después de que el proceso terminó;
        - Ctrl+C, un proceso sin salida, nada lanzado y un lanzamiento fallido;
        - un `candle_sync` real esperado con el reloj real;
        - en los wizards: el orden exportar → esperar → propuesta en Efficiency y en Tactical, apagado no espera, la
          espera real de 0.6 s con un export que nunca termina deja guardar el audit, y un error inesperado no lo
          frena.
      - **Mutación:** 19 de 19 muertos, entre ellos `>=` contra `>` en el límite de la espera. La mutación que
        ignora Ctrl+C dejó escapar el `KeyboardInterrupt` y cortó la corrida de pytest.
      SUITE: `1147 passed, 3 skipped, 153 warnings in 76.99s`; sin `.data/`.
- [x] T54. Disparos antes del reporte y del backfill, y al abrir el CLI (`start()`). (RF-20c, RF-20d)
      Hecho cuando: los tests con los disparos mockeados prueban que ocurren y que no bloquean. SUITE en verde.
      Evidencia (2026-10-03):
      - **`tools/auto_export.account_symbols(accounts, data_dir)`:** los símbolos MT5 de las cuentas, en su orden. Son
        los `asset` distintos de cada `unified_department`, leídos en `mode=ro` pidiendo solo esa columna. Un asset
        sin símbolo MT5 y una DB que no existe se saltean.
      - **`cli/main.py`:**
        - `resolution-report` y `backfill` (también con `--apply`) exportan antes, con la espera acotada de T53,
          solo los símbolos de las cuentas elegidas: con `--account 003`, solo USTEC. Una cuenta desconocida sale con 1
          antes de exportar nada.
        - `start()` lanza de fondo los símbolos de las cuentas reales, antes de abrir el menú, y no espera.
        - Con `AUTO_EXPORT` apagado ninguno de los tres lee las DBs.
      - **Tests:** `tests/test_auto_export_triggers.py` (12), con los disparos reemplazados:
        - `account_symbols` con una DB mínima, sin las columnas nuevas, y sin escribirla;
        - el reporte (las dos cuentas o una), el backfill con y sin `--apply`: el orden exportar → esperar → trabajo,
          y que el comando termina con 0 aunque el export no termine;
        - `start()`: un solo lanzamiento de fondo, sin espera, y un error que no impide abrir el menú;
        - apagado: nada se lanza ni se lee.
      - **Mutación:** 10 de 10 muertos, entre ellos exportar después del plan del backfill y que `start()` espere.
      SUITE: `1159 passed, 3 skipped, 153 warnings in 51.08s`; sin `.data/`.

## Etapa 9 — Registro prospectivo del P2 sistemático (N38, N42)

- [x] T55. `tools/p2_model_feedback.py`: para cada modelo de `P2_LOG_MODELS` (N42), calcular su P2 con velas
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
      Evidencia (2026-10-03):
      - **`tools/p2_model_feedback.py`** (nuevo):
        - `log_account(db_path, account, ...)` registra los análisis nuevos de una cuenta. Se puede limitar a unos
          `trade_ids` (el guardado, T56) o a un símbolo (el catch-up, T56).
        - `model_p2(model, symbol, anchor, bank_root)` calcula el P2 de un modelo con `CsvOHLCProvider` sobre el banco
          del símbolo: velas cerradas en el ancla, y la receta de `MODELS_BY_NAME` leída en el momento, sin copiarla.
        - `check_models()` hace los chequeos de RF-12e.
        - `model_recipe()` y `model_hash()` dan la receta y su huella; la de D es `20315fe7bfe2`.
        - `read_log()` y `latest_lines()` leen el registro y se quedan con la última línea de cada par.
      - **Reglas:** las aclaraciones de T55 en `plan.md` §2.5 (ancla, cobertura, qué se reintenta, `by_tf` y el candado
        del archivo). `P2_MODEL_LOG_NAME` en `config/auto_resolution.py`.
      - **Tests:** `tests/test_p2_model_feedback.py` (19), con un banco sintético en tendencia limpia, donde todo modelo
        da 1.0 y reescalado 2:
        - la línea completa (P2 del operador, receta, huella, detalle por TF);
        - que se usan solo velas cerradas: el precio de 30M es el cierre de la vela 09:30, no el de la de las 10:00;
        - sin segunda línea para el mismo par;
        - retroactivos, clones [2], históricos y anteriores a la fecha de alta no se registran, y la DB queda byte a
          byte igual;
        - `pending_candles` y un reloj sin verificar se reemplazan con `supersedes`, y `insufficient_history` no;
        - los bordes: una vela que cierra justo en el ancla la cubre, y 800 velas alcanzan pero 799 no;
        - D más `H_TEST` (agregado al registro solo en el test): una línea por modelo, y `H_TEST` solo desde su fecha;
        - `unknown_model`, `timeframe_not_in_bank` y `model_recipe_changed`, con D que se sigue registrando;
        - el filtro por símbolo, una línea cortada, una DB sin la columna nueva, y que sin análisis nuevos no se crea
          el archivo.
      - **Mutación:** 22 de 22 muertos. Las dos de los bordes sobrevivían hasta que se agregó el test de 800 contra
        799 velas.
      - **Con datos reales, en solo lectura:** las 4 DBs todavía no tienen ningún análisis nuevo (`analysis_start_time`
        se llena desde la integración de hoy), así que el registro arranca vacío. `model_p2` con D sobre el banco real
        de XAU, en el ancla de los 3 últimos análisis de XAU, da `ok` en 0.1 s, con 1480 velas de 1W y P2 crudos de
        0.0, 0.16 y −0.37 (reescalados 0, 0 y −1).
      SUITE: `1178 passed, 3 skipped, 311 warnings in 63.22s`; sin `.data/`.
- [x] T56. Hooks: registrar al guardar un análisis (si hay velas), catch-up en `candle_sync` después de cada fusión,
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
      Evidencia (2026-10-03):
      - **`tools/p2_model_feedback.py`:**
        - `log_on_save(db_path, trade_id)`, solo para cuentas de `REAL_ACCOUNTS` (`account_of`);
        - `catch_up(symbol, ...)`, para todas las cuentas reales y solo los análisis de ese símbolo;
        - `find_analysis()` y `model_views()` para `p2-model`;
        - `log_account(write_pending=False)`: los disparos no escriben líneas sin velas.
        - `log_account` ahora calcula primero y agrega después, con el candado; ver las aclaraciones de T56 en
          `plan.md` §2.5.
      - **`tools/candle_sync.py`:** después de cada fusión, con el candado del símbolo tomado, corre el catch-up. La
        línea del export suma `P2 model log: N new lines` (cuántas, nunca qué dice el modelo), los avisos de RF-12e o
        el error. Nada de esto cambia el resultado del export.
      - **`cli/main.py`:**
        - `log_p2_models_on_save()` después de guardar, junto al export de T52. Solo muestra los avisos de RF-12e.
        - El comando `p2-model --trade-id ID [--model NAME]` muestra una línea por modelo, por ejemplo
          `D (logged 2026-10-03 13:00): P2 +1.00 -> +2 | 1W +1 | 1D +1 | ...`. Lo que falta se calcula a pedido como
          `not logged`. Un análisis o modelo desconocido, o un prefijo ambiguo, sale con 1.
      - **Tests:** `tests/test_p2_model_hooks.py` (20):
        - guardar con velas registra, sin velas no, y la salida del wizard es idéntica en los dos casos (mismos ids);
        - una Flight Session no se registra;
        - el aviso `unknown_model` con D registrado, y un registro que falla no frena el guardado;
        - el catch-up registra una vez por modelo, solo el símbolo fusionado, nunca históricos, retroactivos ni clones
          [2] ni anteriores al alta, y sin escribir pendientes hasta que el banco cubre;
        - sumar `H_TEST` deja las líneas de D byte a byte iguales;
        - un guardado y un catch-up a la vez escriben la línea una sola vez;
        - el catch-up corre después de una fusión real de `candle_sync` (con un exportador falso) y no sin fusión, y si
          falla, la fusión se mantiene;
        - `p2-model`: la línea registrada, los modelos que faltan y `--model A` sin escribir el registro (bytes
          iguales), un histórico "never logged", y los códigos 1;
        - un id exacto gana sobre otro más largo que empieza igual.
      - **Mutación:** 21 de 21 muertos.
      - **Lo que encontró el test de guardar sin velas:** `log_account` creaba el archivo vacío aunque no tuviera nada
        que escribir, porque lo abría para leer con el candado. Por eso ahora calcula primero y agrega después.
      SUITE: `1198 passed, 3 skipped, 405 warnings in 63.26s`; sin `.data/`.

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
      **3.ª integración 2026-09-30 (aprobada por el usuario: "puedes hacer los commits"):** otra sesión había avanzado
      la rama principal a `89dbf90` (P2 banco v2), así que primero se mezcló esa rama en la de la sesión (`1613e95`,
      sin conflictos: los dos lados tocaban el exportador y sus tests en partes distintas). La SUITE sobre la mezcla
      dio `783 passed, 3 skipped`, sin `.data/`. Después, avance rápido de la rama principal a este commit, con las
      mismas verificaciones que en las integraciones anteriores.
      **4.ª integración 2026-10-03 (aprobada por el usuario: "add the integration"):** avance rápido de `9ffc717` a
      `da29692`, 11 commits (registro de N43, T25 a T34). Se cortó en T34 a propósito: desde T35, abrir el CLI migra
      las DBs reales, así que T35 en adelante se integra recién con T58. Mismas verificaciones: ningún archivo del
      usuario entre los cambiados, `git status` idéntico y los 30 archivos del usuario byte a byte iguales. La SUITE
      sobre `da29692` es la de T34 (`911 passed`).
      **5.ª integración 2026-10-03 (con el "go" de T58):** avance rápido de `da29692` a `e94c580`, 13 commits: T35 a
      T39, N45 a N51, T28b, T30b, T62a, T58a y el registro de T58. Recién después de migrar las 4 DBs reales (T58).
      La SUITE de la rama justo antes dio `997 passed, 3 skipped`, sin `.data/`. Ningún archivo del usuario entre los
      cambiados, y los 30 quedaron byte a byte iguales. Un `tools/backup.py backup --dry-run` desde el checkout
      principal detecta el banco (20.8 MB) y B2; el USB no está montado.
      **6.ª integración 2026-10-03 (aprobada por el usuario: "add the commits"):** avance rápido de `21f9cb5` a
      `1fbdf58`, 7 commits: T29b, T33b, CLAUDE.md, T40, T41, T42 y T43. La SUITE de la rama dio `1039 passed, 3
      skipped`, sin `.data/`. Ningún archivo del usuario entre los cambiados, y los 30 quedaron byte a byte iguales.
      **7.ª integración 2026-10-03 (el usuario corrió `backfill` en el checkout principal y le dio "No such command"):**
      avance rápido de `de7f47c` a `a7c38e9`, 7 commits: T44, T45 y T46, T47, T48, T49, T50 y T51. La SUITE de la rama
      dio `1114 passed, 3 skipped`, sin `.data/`. Ningún archivo del usuario entre los cambiados, y los 30 quedaron
      byte a byte iguales.
      **8.ª integración 2026-10-03 (aprobada por el usuario: "sí, integra T52-T56"):** avance rápido de `89bcd61` a
      `45e39af`, 5 commits: T52 a T56. La SUITE de la rama dio `1198 passed, 3 skipped`, sin `.data/`. Ningún archivo
      del usuario entre los cambiados, y los 30 quedaron byte a byte iguales. El usuario ya había puesto
      `AUTO_EXPORT=true` en el `.env` del principal (verificado leyendo solo esa clave), así que desde esta integración
      abrir el CLI lanza los exports de fondo.
      **9.ª integración 2026-10-03 (aprobada por el usuario: "sí, integra N52, T30c y T49b"):** avance rápido de
      `1054a3e` a `6a2937a`, 3 commits: N52, T30c y T49b. La SUITE de la rama dio `1210 passed, 3 skipped`, sin
      `.data/`. Ningún archivo del usuario entre los cambiados, y los 30 quedaron byte a byte iguales. Desde acá el
      wizard ya no propone "Overlap Invalidation", y el backfill vuelve a intentar las horas vacías por motivos
      temporales.
      **10.ª integración 2026-10-04 (aprobada por el usuario: "sí, integra N53 y T51b"):** avance rápido de `5c79775`
      a `7536a87`, 4 commits: N53, el arreglo del test inestable de T56, T51b y la nota de T62. La SUITE de la rama dio
      `1215 passed, 3 skipped`, sin `.data/`. Ningún archivo del usuario entre los cambiados, y los 30 quedaron byte a
      byte iguales.
- [x] T58a. *(Agregada el 2026-10-03, N48.)* El banco de velas en el backup 3-2-1: artefacto `candle_bank.tar.gz` en
      local, USB y B2, y su restore en `<destino>/candle_bank/`. (NFR-1)
      Hecho cuando: los tests prueban el archivo (solo los CSV y `status.json` del banco, rutas relativas), la subida
      con su clave, el restore que nunca escribe en `.data/` ni fuera del destino, y el listado de B2 por fecha.
      SUITE en verde.
      Evidencia (2026-10-03):
      - **Respaldo:** `archive_candle_bank()` y `backup_candle_bank()` en `tools/backup.py`. Clave en B2
        `candle_bank/<fecha>.tar.gz`; en el USB, `<fecha>/candle_bank.tar.gz`. `main_backup` lo suma como un artefacto
        más, y si la carpeta del banco no existe se saltea con un aviso.
      - **Restauración:** `restore_candle_bank_archive()` valida todas las entradas antes de escribir, y es todo o
        nada: rechaza rutas que salen del destino, enlaces y archivos que no son del banco.
      - **Arreglos de paso:** las tres restauraciones comparten `_check_restored_file()`; el nombre local en B2
        conserva `.tar.gz`, y el listado de B2 agrupa por fecha.
      - **Tests:** `tests/test_backup_candle_bank.py` (15); los 26 de `tests/test_backup.py` siguen en verde.
        Mutación: 11 de 11 muertos; uno sobrevivió al principio y se sumó al fixture un temporal `1M.csv.tmp`.
      - **Lo que paró el guard de aislamiento:** el primer test de `main_backup` llegó a pedir el candado en la ruta
        por defecto. `acquire_backup_lock` fija su ruta al importar el módulo, así que el test ahora reemplaza las
        funciones. No se creó nada: el worktree no tiene `.data/`.
      - **Humo con el banco real:** se comprimió y se restauró en el scratchpad, en solo lectura. 22 archivos, 5.7 MB,
        3.9 s; el restaurado es idéntico byte a byte y el banco no cambió. No se corrió ningún backup real, porque
        el cron usa el checkout principal: esto entra con la integración.
      SUITE: `997 passed, 3 skipped, 97 warnings in 36.93s`; sin `.data/`.
- [x] T58. 🖐 **Migración de esquema de las 4 DBs reales con las 3 puertas**, antes de correr cualquier comando del
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
      **Hecho 2026-10-03, con el "go" del usuario ("continue with the go for T58"):**
      1. **Copias:** se copiaron las 4 DBs con la API de backup de SQLite, en solo lectura, y se corrió `init_db` del
         código de la rama tres veces sobre cada copia. Las 5 columnas, `backfill_history` y sus 2 triggers;
         `integrity_check` ok; `foreign_key_check` vacío; conteos iguales. Desde la segunda corrida no cambia nada.
      2. **Backup:** `tools/backup.py backup`, el del cron, a las 09:04, run `20261003_090434`. Copia local y B2 con
         `b2_verified=True` en los 5 artefactos, y `verify` ok en las 4 copias locales. **El USB falló**: `/mnt/d` no
         está montado. Tampoco lo estaba a las 08:00 del 1 y del 3 de octubre; solo el 2 dio 5/5. Con dos copias
         verificadas, una inmutable en B2, y una migración que solo agrega, se siguió.
      3. **Aprobación:** el "go" del usuario.
      4. **Migración real:** un comando de Python aparte, sin abrir el CLI y sin procesos del CLI corriendo. En las 4
         DBs quedaron las 5 columnas, `backfill_history` vacía y los 2 triggers; `integrity_check` ok y
         `foreign_key_check` vacío. Antes y después, los conteos son iguales: XAU 81 unified, 82 tactical, 81
         efficiency y 405 layers; BTC 24/24/24/120; US500 10/2/10/50; US100 8/6/8/40. En US100 el shim también agregó
         las columnas viejas que le faltaban, porque el CLI no la abría desde antes de `stop_slippage_r`.
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
- [x] T59b. 🖐 *(Agregada el 2026-09-30, N43.)* Alimentar US500 y USTEC por herencia. Con MT5 abierto, exportar
      XAUUSD (para que su `status.json` guarde el servidor y el desfase base) y después US500 y USTEC. Escribe en
      `.data/candle_bank/`, aprobado por el usuario junto con T59. (RF-2e, N43)
      Hecho cuando: `candles status` muestra US500 y USTEC `verified` por `inherited:...`, o el motivo exacto por el
      que no heredaron.
      **Hecho 2026-09-30 (con MT5 abierto; código del worktree, banco y DBs reales; exportador del worktree):**
      - XAUUSD se fusionó, verificado por superposición, en 11 s. Su `status.json` ahora guarda `server:
        ICMarketsSC-Demo`, `base_utc_offset: 2.0` y `dst_rule: us`. Trajo solo velas nuevas (1D +1, 4H +7), así que
        no volvió ningún duplicado.
      - **US500 y USTEC no heredaron:** a cada uno le falla 1 testigo propio y la regla exige que calcen todos.
        - US500: 1 de 7. El Mark Price del 2026-09-28 07:00, 7713.07, queda 0.03 puntos por debajo de su vela de 15M
          (7713.1–7719.6).
        - USTEC: 1 de 9. El Mark Price del 2026-09-03 18:42, 29490.6, queda 7.7 puntos por encima (29469.8–29482.9).
        - Los otros 6 y 8 calzan exacto, y los dos que fallan calzan en la vela siguiente, así que no es un reloj
          corrido. Con la tolerancia que la spec ya usa para el Mark Price (0.1%, N16) calzarían los dos.
      - **Decisión del usuario (2026-10-02): N43 no se ajusta; se espera a que US500 y USTEC tengan 10 referencias**
        (USTEC necesita 1 más; US500, 3 más). Con 10, la regla normal (N29) tolera un testigo que no calza.
- [x] T59c. 🖐 *(Agregada el 2026-09-30, N44.)* Limpiar el banco real con `tools/dedup_candle_bank.py`: primero
      sin `--apply`, y después con `--apply`. Escribe en `.data/candle_bank/`, aprobado por el usuario el 2026-09-30.
      (RF-1, N44)
      Hecho cuando: se quitaron exactamente los 3.824 pares medidos (XAU 1W 509, 1D 407, 12H 362, 4H 528; BTC 1W 272,
      1D 505, 12H 504, 4H 737), la copia de respaldo existe, una segunda corrida sin `--apply` da 0, y 1H y las TF
      menores quedaron byte a byte iguales.
      **Hecho 2026-09-30, aprobado por el usuario:**
      - Sin `--apply`, informó exactamente los 3.824 pares medidos, en 8 archivos. Con `--apply` los quitó.
      - Respaldo: `.data/archives/candle_bank_pre_dedup_20260930_130245/`, con los 8 originales (hash verificado).
      - Verificación externa a la herramienta: de los 22 archivos del banco cambiaron solo esos 8; los otros 14
        (1H y menores, `status.json`) quedaron byte a byte iguales. Una segunda corrida no encuentra nada.
      - Filas antes → después: XAU 1W 1993 → 1484, 1D 1635 → 1228, 12H 1691 → 1329, 4H 2258 → 1730; BTC 1W 1081 → 809,
        1D 2200 → 1695, 12H 2295 → 1791, 4H 2911 → 2174. Los de XAU coinciden con lo que trajo el primer export nuevo
        (1W +1484, 1D +1228, 12H +1329, 4H +1730).
- [x] T60. Correr `resolution-report` sobre las DBs reales, en solo lectura. (RF-6, RF-17, RF-21)
      Hecho cuando: el `.md` generado se muestra, y el S1 direccional de XAU se compara con el 61% de
      `analisis-overlap-2d.md`, explicando cualquier diferencia (1M/5M, ancla).
      Evidencia (2026-10-04), detalle en `validation.md` § T60:
      - **Cómo se corrió:** el comando real, desde el scratchpad, con `AUTO_EXPORT=false` y las rutas de `.data/` real.
        Las 4 DBs y el banco quedaron sin cambios (fecha y tamaño de cada archivo, antes y después). 21 s, código 0. El
        `.md` se le mostró al usuario y quedó fuera del repo.
      - **Resultado:** XAU resuelve 75 de 81 (6 `no_levels`), con S4 estricto direccional 28/45 = 62% y S1 direccional
        29/45 = 64%. BTC resuelve 22 de 24, con S4 estricto direccional 8/16. US500 y US100 dan 0, por
        `clock_unverified`.
      - **El 61% (23/38) contra el 64% (29/45):** se repitió el script del 2026-09-27 y se comparó análisis por
        análisis.
        - Los 38 de entonces dan el mismo primer toque: ni las velas de 1M/5M ni el ancla cambiaron nada.
        - Se suman 6 retroactivos recuperados (N50), que ganan los 6, y `792518cd`, que tocó la invalidación el
          2026-09-27, después de la fecha de los CSV de entonces.
- [ ] T61. 🖐 Demo en una Flight Session descartable: un Efficiency Audit con propuestas aceptadas y una corregida,
      más un Tactical con MAE/MFE y `Could hit TP?` propuestos. (RF-7, RF-9, RF-10)
      Hecho cuando: queda documentado en `validation.md` con la salida, y la Flight Session se borra al terminar.
- [x] T62a. *(Agregada el 2026-10-03, N47.)* Retención de `_incoming`: al final de cada export se conservan las 3
      corridas más recientes del símbolo y se borran las demás, solo si son carpetas de corrida con CSV de
      temporalidades. (RF-1, RF-20)
      Hecho cuando: los tests prueban que se borran solo las corridas viejas y que quedan intactos los nombres que no
      son `run_id`, los enlaces, las carpetas con otro contenido, los otros símbolos y la corrida recién exportada.
      SUITE en verde.
      Evidencia (2026-10-03):
      - **`prune_incoming_runs()`** (`tools/candle_sync.py`) con `INCOMING_RUNS_KEEP = 3`, que no acepta menos de 1.
        Ordena por hora y después por el sufijo `-N` como número.
      - **Qué borra:** archivo por archivo y después la carpeta vacía, nunca con `rmtree`.
      - **Qué no toca:** enlaces (la carpeta del símbolo, la corrida o un CSV), nombres que no son `run_id`, corridas
        con otra cosa adentro (se informan), otros símbolos y la corrida recién exportada.
      - **Cuándo corre:** `sync_symbol` la llama con el candado tomado, después de `status.json`. Un error de la
        limpieza nunca cambia el resultado del export. La línea del resultado dice qué se borró.
      - **Tests:** `tests/test_incoming_retention.py`, 14 tests. Mutación: 13 de 13 muertos; uno sobrevivió al
        principio (el orden de `-2` contra `-10`) y se corrigió el test.
      - **Con las corridas reales de hoy** (XAU 3, USTEC 2, US500 2, BTC 1) no se borraría nada.
      SUITE: `982 passed, 3 skipped, 97 warnings in 58.90s`; sin `.data/`.
- [ ] T62. 🖐 Demo del export automático con MT5 abierto y con MT5 cerrado. Solo si T22 funcionó. (RF-20 a RF-20e)
      Hecho cuando: las dos salidas quedan en `validation.md`.
      *(Resuelta el 2026-10-03: la retención de corridas que estaba pendiente acá la decidió el usuario en N47 y está
      implementada en T62a. `AUTO_EXPORT=true` está en el `.env` del principal desde la 8.ª integración.)*
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
| RF-1, RF-1b | T11, T21, T16c, T59c |
| RF-1c | T15, T20 |
| RF-1d | T12, T20 |
| RF-2, RF-2b | T10, T14 |
| RF-2c | T16, T21, T59, T16c |
| RF-2d | T13 |
| RF-2e | T22b, T59b |
| RF-3 | T31, T39 |
| RF-3b | T31, T32 |
| RF-4 | T23, T25, T26, T27 |
| RF-4b | T23, T26 |
| RF-4c, RF-4g | T24 |
| RF-4d | T27 |
| RF-4e, RF-4h | T30 |
| RF-4f | T9, T10, T23 |
| RF-5, RF-5b | T26, T29, T41, T30c |
| RF-6 | T32, T33, T60 |
| RF-6b | T30 |
| RF-7, RF-7e | T40, T42, T44, T61 |
| RF-7f | T41 |
| RF-7g | T36, T43 |
| RF-8, RF-8b, RF-8c, RF-8d | T28, T42, T30c |
| RF-9, RF-9b, RF-9c, RF-9d | T45 |
| RF-10, RF-10b, RF-10c, RF-10d | T46 |
| RF-11, RF-11b, RF-11c | T47, T49, T49b, T51, T63 |
| RF-11d | T50 |
| RF-11e | T51, T51b, T63 |
| RF-12, RF-12c | T55, T56 |
| RF-12b, RF-12d | T56 |
| RF-12e | T8, T55 |
| RF-13, RF-13b, RF-13c | T37 |
| RF-13d, RF-13e | T35, T38 |
| RF-14, RF-14b | T35, T36, T43 |
| RF-15 | T9, T17, T19 |
| RF-15b | T18, T22 |
| RF-15c | T15, T16c |
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
