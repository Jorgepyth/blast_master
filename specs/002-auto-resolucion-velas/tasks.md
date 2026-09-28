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
- [ ] T10. Extraer `evaluate_clock_entries(entries, provider, tf, offsets)` de `calibrate_clock_offset`, sin
      cambiar su resultado, y agregar `count_entries_in_range()`. (RF-2, RF-2b, RF-4f)
      Hecho cuando: `tests/test_p2_clock.py` sigue en verde sin cambios; un test nuevo prueba que las dos rutas dan el
      mismo resultado y cuenta referencias dentro del rango. SUITE en verde.
- [ ] T11. `tools/candle_bank.py`, parte 1: lectura y escritura atómica de un `{TF}.csv` (temporal + `os.replace`) y
      la fusión por `time`, conservando la vela existente. (RF-1, RF-1b)
      Hecho cuando: los tests prueban que ninguna vela previa desaparece ni cambia, que la cobertura hacia atrás no
      se reduce, y que un duplicado conserva la vela del banco. SUITE en verde.
- [ ] T12. `candle_bank`, parte 2: candado por símbolo (PID y vencimiento de 2 h). (RF-1d)
      Hecho cuando: los tests prueban que con el candado tomado se cancela y el banco queda byte a byte igual, y que
      un candado vencido se reemplaza. SUITE en verde.
- [ ] T13. `candle_bank`, parte 3: verificación por superposición (1H, 30M, 15M, 5M y 1M; `OVERLAP_MIN_BARS`;
      tolerancia 1e-9). (RF-2d, N32)
      Hecho cuando: los tests prueban que la superposición igual verifica, que una vela distinta da
      `clock_misaligned` sin fusionar, y que menos de 10 velas superpuestas no verifican. SUITE en verde.
- [ ] T14. `candle_bank`, parte 4: verificación por referencias. Junta fills y Mark Price no retroactivos de las
      cuentas de `REAL_ACCOUNTS` que mapean al símbolo, con `mode=ro`, exige 10 dentro del rango y
      `evaluate_clock_entries` alineado. (RF-2, RF-2b, N29)
      Hecho cuando: los tests, con DBs de fixture en `tmp_path`, cubren 12 referencias adentro (verifica), 9 adentro
      (`clock_unverified`) y un reloj corrido +3h (`clock_misaligned`). SUITE en verde.
- [ ] T15. `candle_bank`, parte 5: filtro por estación de horario (RF-15c), `status.json`, y el banco sin cambios
      ante cualquier fallo (RF-1c). (RF-1c, RF-15c)
      Hecho cuando: los tests prueban que, con la regla sin verificar, en 1H y en las TF menores solo entran las velas
      de la estación del export, mientras que 4H, 12H, 1D y 1W entran completas (N39);
      que `status.json` refleja el resultado, y que ante una excepción a mitad de la fusión el banco queda byte a
      byte igual. SUITE en verde.
- [ ] T16. `candle_bank`, parte 6: `import_legacy()` desde `MT5Exports/{SYMBOL}/`, sin el `5M.csv` de XAU. (RF-2c)
      Hecho cuando: el test, con un directorio de fixture que imita `MT5Exports`, prueba que se importa lo
      verificado, que el 5M queda excluido e informado, y que el origen no se modifica. SUITE en verde.
- [ ] T17. Exportador, parte 1: agregar `5M` y `1M` al mapa de TF, a la duración y al rango hacia atrás, más el
      argumento `--timeframes`. (RF-15)
      Hecho cuando: los tests, con el stand-in de `MetaTrader5`, cubren el mapa y `compute_backward_start` para
      5M/1M; `tests/test_export_p2_ohlc.py` sigue en verde. SUITE en verde.
- [ ] T18. Exportador, parte 2: conversión vela por vela según `--dst-rule {us,eu,none}`, descartando la hora del
      cambio. (RF-15b, N34)
      Hecho cuando: los tests prueban que una vela de enero y otra de julio exportadas en septiembre con la regla
      `us` quedan con el offset correcto, que `none` da lo mismo que hoy, y que se informan las descartadas. SUITE en
      verde.
- [ ] T19. Exportador, parte 3: un directorio único por corrida y escritura atómica por archivo. (RF-15, plan T2)
      Hecho cuando: los tests prueban que dos corridas no se pisan y que un fallo a mitad no deja CSV a medias. SUITE en
      verde.
- [ ] T20. `tools/candle_sync.py`: arma el comando del exportador desde la configuración, lo corre con
      `EXPORT_TIMEOUT_S` y llama a la fusión. (RF-20e, RF-20f, RF-1c)
      Hecho cuando: los tests, con un exportador falso en `tmp_path`, cubren éxito con fusión, timeout con
      `export_failed`, "MT5 no disponible" (código 1) con `export_failed`, y candado tomado. SUITE en verde.
- [ ] T21. Subcomandos `candles status`, `candles import-legacy` y `candles export --symbol S [--wait N]` en
      `cli/main.py`. (RF-2c, RF-1)
      Hecho cuando: los tests con `CliRunner` y configuración en `tmp_path` prueban la salida y los códigos 0, 2 y 6.
      SUITE en verde.
- [ ] T22. 🖐 **Spike de interoperabilidad**, con MT5 abierto en Windows:
      - encontrar el Python de Windows que tiene `MetaTrader5`;
      - correr `candles export --symbol XAUUSD` desde `WT`, con el banco en un directorio temporal y
        `ACCOUNTS_DATA_DIR` apuntando a `.data/` real en solo lectura;
      - medir la duración;
      - determinar `BROKER_DST_RULE`;
      - documentar todo en `specs/002-auto-resolucion-velas/spike.md`. (RF-20, RF-15b, N31, N34)

      Hecho cuando: `spike.md` tiene el comando, la duración, el resultado (fusionado o motivo) y la regla. Vos
      cargás `WINDOWS_PYTHON`, `EXPORTER_WIN_PATH` y `BROKER_DST_RULE` en el `.env` del checkout principal. Si
      falla, `AUTO_EXPORT` queda desactivado y la Etapa 8 se salta.

## Etapa 3 — Resolvedor, métricas y reporte (solo lectura)

- [ ] T23. `first_touch_detail()` en `core/p2_ground_truth.py`. (RF-4, RF-4b, RF-4f)
      Hecho cuando: los tests nuevos cubren el índice, el nivel y el caso ambiguo, y `tests/test_p2_ground_truth.py`
      sigue en verde sin cambios. SUITE en verde.
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

- [ ] T57. 🖐 Integrar la rama a `feature/tactical-tier-gate-df`, solo con tu aprobación y la lista explícita de
      commits. (—)
      Hecho cuando: `git log` de la rama principal contiene los commits de la spec, y R1 se cumple.
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
- [ ] T59. 🖐 Crear el banco real con `candles import-legacy` en el checkout principal. Escribe en `.data/`, así que
      requiere tu aprobación. (RF-2c)
      Hecho cuando: `candles status` muestra XAUUSD y BTCUSD verificados, USTEC en `clock_unverified`, y el 5M de XAU
      excluido.
- [ ] T60. Correr `resolution-report` sobre las DBs reales, en solo lectura. (RF-6, RF-17, RF-21)
      Hecho cuando: el `.md` generado se muestra, y el S1 direccional de XAU se compara con el 61% de
      `analisis-overlap-2d.md`, explicando cualquier diferencia (1M/5M, ancla).
- [ ] T61. 🖐 Demo en una Flight Session descartable: un Efficiency Audit con propuestas aceptadas y una corregida,
      más un Tactical con MAE/MFE y `Could hit TP?` propuestos. (RF-7, RF-9, RF-10)
      Hecho cuando: queda documentado en `validation.md` con la salida, y la Flight Session se borra al terminar.
- [ ] T62. 🖐 Demo del export automático con MT5 abierto y con MT5 cerrado. Solo si T22 funcionó. (RF-20 a RF-20e)
      Hecho cuando: las dos salidas quedan en `validation.md`.
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
