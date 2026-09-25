# v1.2 — 2026-09-24 · Prompt de implementación: banco de velas, resolución automática y pre-llenado de audits

> Cómo usarlo: copiá este archivo a `docs/prompts/` en el repo de WSL y, en una sesión **nueva** de Claude Code
> abierta en la raíz de `blast_master`, escribí:
> `Leé y ejecutá @docs/prompts/2026-09-24-auto-resolucion-velas.md`
> Changelog: v1.0 — versión inicial, derivada de la auditoría de P2 del 2026-09-24 (v1.2).
> v1.1 — agrega el Paso 0 (cierre y archivo de la spec 001, retiro de su worktree) y la reutilización de su baseline.
> v1.2 — los prompts del 2026-09-23 se conservan sin editar; este prompt entra en el commit de R2; `CIERRE.md` los marca como obsoletos.

<instrucciones>

## Rol

Sos el implementador de UN feature en un repo existente, construido **exclusivamente a través del skill
`/sdd-engine`** (ruta brownfield-delta). Implementás decisiones ya tomadas por el usuario; no cambiás el modelo
de trading (`calc_edge`, pesos, umbral ±0.26, gates). Nunca escribís en las bases de datos reales sin su
aprobación explícita.

## Objetivo

Usar las velas de MT5 para que blast_master **mida los resultados de cada análisis de forma automática y
objetiva**, en lugar de depender de etiquetas manuales puestas después del hecho:

1. Un **banco de velas acumulativo** que nunca pierde historia.
2. **Calibración del reloj** de las velas y **validación del Mark Price** en cada export y al guardar un análisis.
3. Un **resolvedor** que, para cada análisis, calcula qué nivel tocó primero el precio, cuándo, y el MAE/MFE
   estructural.
4. **Pre-llenado** de los campos del Efficiency Audit y del Tactical Audit que se pueden derivar de velas: el
   sistema **propone**, el operador **confirma o corrige**.
5. Opcional, al final: un comando que imprime el P2 del modelo D como retroalimentación (solo lectura).

## Entregable

1. `specs/002-auto-resolucion-velas/` con `baseline.md`, `spec.md`, `plan.md`, `tasks.md`, `consistency.md`,
   `validation.md`.
2. Código y tests, una tarea por turno, con la suite en verde.
3. Un reporte de solo lectura que compare la resolución automática contra las etiquetas manuales existentes.
4. Backfill de campos **vacíos** en las DBs reales, solo tras las puertas de R9.

## Procedimiento

Ejecutá `/sdd-engine`. Corré `python3 ~/.claude/skills/sdd-engine/scripts/sdd_state.py .` al empezar cada turno;
si devuelve `CODIGO_PERMITIDO=NO`, no toques código fuente.

| Paso | Fase | Salida | Puerta humana |
|---|---|---|---|
| 0a | — | Cierre de la spec 001 según R12 | Aprobación del usuario antes de mover o borrar nada |
| 0b | — | Prerrequisitos R1 y R2 verificados | Commit de la base si falta |
| 1 | B1 baseline | `baseline.md` con `archivo:línea` en cada afirmación. Reutilizá el baseline archivado de 001 (superficie de `flow_new_analysis`, `init_db`, H19–H21) **re-verificando cada línea citada**, no copiándola | Lectura del usuario |
| 2 | B2 spec | `spec.md` con INV-n y RF-n en EARS; dudas como `[NECESITA ACLARACIÓN]` | Aprobación |
| 3 | F3→F4 plan | Cada decisión técnica con su alternativa descartada | Verificación de alternativas |
| 4 | F5 tareas | `tasks.md` en el orden de R10 | Aprobación |
| 5 | F6 | `consistency.md` con cero críticos | — |
| 6 | F7 | Código + tests + salida de la suite, tarea por tarea | Revisión del diff |
| 7 | F8 | `validation.md`, RF por RF | Confirmación de que cada test citado existe |

No preguntes lo que ya está en `<decisiones>`. Preguntá solo lo marcado `[NECESITA ACLARACIÓN]`.

## Reglas de decisión

- **R1 — CLAUDE.md es intocable** (misma regla que el prompt 2026-09-23). Al cerrar cada turno:
  `git diff --stat CLAUDE.md` vacío y `readlink AGENTS.md` = `CLAUDE.md`.
- **R2 — Base commiteada antes de B1.** Deben estar commiteados: `closed_bars()` y el ancla por análisis en
  `tools/p2_backtest.py`, `tests/test_p2_closed_bars.py`, `jupyter/p2_edge_evaluation.ipynb` (v2), la adenda
  1 con D14/D15 (versión del disco) y **este prompt**. Si no lo están, detenete y pedí un commit con lista
  explícita de archivos.
- **R3 — Solo velas cerradas.** Todo cálculo en un instante `t` usa velas con `time + duración(TF) <= t`
  (`CsvOHLCProvider.closed_bars`). Todo camino hacia adelante empieza **en el ancla**, no en la vela 1H
  siguiente (corrige H21 del baseline 001: usá la temporalidad más fina disponible para el primer tramo).
- **R4 — El banco nunca pierde velas.** Cada export se fusiona con lo existente, sin duplicar por `time`; si el
  export falla o no valida, el banco queda intacto. Los CSV actuales deben seguir siendo legibles por el
  cuaderno y los reportes, o migrarse con su propio test.
- **R5 — El reloj manda.** Si `calibrate_clock_offset` da `aligned=False` para un símbolo, no se resuelve ni se
  pre-llena nada de ese símbolo; se informa el motivo.
- **R6 — Proponer, nunca imponer.** En el wizard, los valores derivados de velas aparecen como **valor por
  defecto editable**, marcados como automáticos. El operador puede aceptarlos o corregirlos. Nunca se guarda un
  valor automático sin que el operador pase por el campo.
- **R7 — Nunca sobrescribir datos manuales.** El backfill solo llena campos `NULL`. Un valor manual existente
  que difiera del automático se **reporta**, no se toca.
- **R8 — Los campos de juicio no se automatizan.** `bias_a`, `real_bias_b`, `specific_bias_compliance`,
  `false_regime_rate`, emociones, gates G1–G7, confirmaciones C1–C8, `lesson_learned`, `followed_plan`,
  `market_state`, `htf_trend_context`, `ltf_trend_context`: se quedan manuales. La resolución geométrica es una
  medida **distinta**, no un reemplazo de `specific_bias_compliance`.
- **R9 — Escrituras en DBs reales, en tres puertas:** (1) la operación corre limpia sobre una **copia** de cada
  `.data/flight_account_*.db` (`cp` + `init_db('sqlite:////tmp/x.db')`); (2) backup reciente con
  `conda run -n blast_master python tools/backup.py backup`; (3) aprobación explícita del usuario. Solo cambios
  aditivos; nunca `DROP`, ni `ALTER` de columnas existentes, ni reescritura de filas con valor.
- **R10 — Orden de tareas.**
  1. Tests de red de seguridad sobre el comportamiento actual de los wizards de Efficiency y Tactical Audit.
  2. Banco acumulativo + calibración (sin tocar DBs).
  3. Resolvedor + reporte de comparación, **solo lectura**.
  4. Pre-llenado en el wizard de Efficiency Audit.
  5. Pre-llenado en el Tactical Audit.
  6. Backfill (R7, R9).
  7. Opcional: comando de P2 del modelo D (RF-12).
- **R12 — Cierre de la spec 001 (antes de B1).** `sdd_state.py` toma como spec activa la primera carpeta
  `specs/NNN-*` sin `validation.md` con `verdict: SPEC_CUMPLIDA`. Si 001 queda en `specs/`, bloquea a 002 para
  siempre, y marcarla `SPEC_CUMPLIDA` sería falso. Procedimiento, cada paso con aprobación del usuario:
  0. **No borres ni edites** `docs/prompts/2026-09-23-p2-consenso-modelo-f.md` ni su adenda: son el registro de
     por qué existió la spec 001. Su estado de obsoletos se declara en `CIERRE.md`, no en los archivos.
  1. En el worktree `.claude/worktrees/consenso-modelo-f-471f7d` solo existen, sin commitear, `specs/001-p2-consenso-modelo-f/baseline.md`
     y `spec.md`; la rama `claude/consenso-modelo-f-471f7d` no tiene commits propios (apunta a `fc639e8`).
     Verificalo con `git -C <worktree> status` y `git log fc639e8..claude/consenso-modelo-f-471f7d`.
  2. Copiá esos dos archivos al checkout principal en `docs/archive/specs/001-p2-consenso-modelo-f/` (fuera de
     `specs/`, para que `sdd_state.py` no la lea) y agregá `CIERRE.md` con: fecha, fase alcanzada (B2, spec sin
     aprobar, sin plan ni código), motivo (decisión D1 de este prompt y auditoría del 2026-09-24) y qué se
     reutiliza en 002, y la lista de prompts que quedan obsoletos (el prompt del 2026-09-23 y su adenda 1).
  3. Commit con esos archivos explícitos.
  4. Recién entonces: `git worktree remove .claude/worktrees/consenso-modelo-f-471f7d` y
     `git branch -d claude/consenso-modelo-f-471f7d`. Su `.data/flight_account_001_xauusd.db` es una DB vacía
     creada por el CLI en el worktree (0 filas): se descarta, pero confirmalo contando filas antes.
  5. Trabajá 002 en un worktree nuevo creado desde la base commiteada de R2.
- **R11 — Retroactivos.** Los análisis con `is_backdated = 1` se resuelven igual (el pre-llenado les sirve),
  pero todo reporte de acierto los **excluye** por defecto y dice cuántos excluyó. Motivo: la auditoría del
  2026-09-24 mostró que se cargaron después de conocerse el resultado.

## Restricciones

- No inventes APIs, rutas, columnas ni resultados. Si algo no consta, declaralo como dependencia y detenete.
- **No toques:** `core/math_engine.py`, `determine_market_bias` y su umbral, los gates Tier D/F, emocional y
  Stop Deviation, `tools/notion_sync.py`, `core/edge_analysis.py`, P0–P4, los modelos A–G de
  `tools/p2_backtest.py`.
- Reutilizá lo que ya existe y tiene tests: `CsvOHLCProvider`, `closed_bars`, `calibrate_clock_offset`,
  `core/p2_ground_truth.py` (`first_touch_direction`), `open_readonly_session`,
  `windows_export/export_p2_ohlc.py`. No los reimplementes.
- Código de análisis y reportes: DBs con `mode=ro` y columnas explícitas (las cuentas no migradas no tienen
  todas las columnas).
- Tests: `conda run -n blast_master pytest -q`. Sin procesos externos, sin MT5, sin `/mnt/c` ni `.data/` en los
  tests: SQLite `:memory:`/`tmp_path` y velas de fixture.
- El export se dispara **a mano** en esta versión (comando o PowerShell). La integración automática con MT5
  desde el CLI queda fuera de alcance: la interoperabilidad WSL → `powershell.exe` → MT5 no está verificada.
- Git: nada de `git add -A`, `git add .` ni `git commit -a`; commits con archivos explícitos y solo cuando el
  usuario lo pida.
- Comunicate en español. Los mensajes nuevos del wizard van en inglés, el idioma de esas pantallas.

## Condiciones de finalización

- `validation.md` con veredicto por RF e INV, citando el test.
- Suite completa en verde, con la salida mostrada.
- Reporte de comparación automático-vs-manual corriendo sobre las DBs reales en solo lectura.
- Demo en una Flight Session descartable: Efficiency Audit con valores propuestos, aceptados y uno corregido.
- Backfill aplicado tras las tres puertas de R9, con el conteo de campos llenados y de diferencias reportadas.

Detenete y consultá si: necesitás tocar algo de "No toques"; aparece un `[NECESITA ACLARACIÓN]`; estás por
escribir en `.data/`; `sdd_state.py` bloquea; `CLAUDE.md` muestra diff; el reloj de un símbolo no calibra.

</instrucciones>

<decisiones>
Tomadas por el usuario el 2026-09-24.

- D1. Se cierra el feature de consenso P2 (`specs/001-p2-consenso-modelo-f`). P2 sigue siendo manual.
- D2. El valor de las velas está en **medir resultados**, no en sistematizar más parámetros.
- D3. El modelo D, si se implementa (RF-12), se imprime **solo como retroalimentación**, después de
  "Confirm & Save", y no cambia ningún campo ni ninguna decisión.
- D4. El sistema propone y el operador confirma (R6). Nada se guarda en silencio.
- D5. Los retroactivos se excluyen de las estadísticas de acierto (R11).
</decisiones>

<requisitos_semilla>
Verificá cada uno contra el baseline y reformulá en EARS. Lo que no esté acá, márcalo `[NECESITA ACLARACIÓN]`.

Comportamiento actual a preservar:
- INV-1: Los wizards de Efficiency y Tactical Audit siguen pidiendo los mismos campos en el mismo orden.
- INV-2: Un análisis o audit se guarda aunque no haya velas, el reloj no calibre o el resolvedor falle.
- INV-3: Ningún valor manual existente se modifica (R7).
- INV-4: `calc_edge`, bias, probabilidades, tier y gates no cambian.
- INV-5: La suite existente sigue en verde.

Banco de velas y reloj:
- RF-1: CUANDO el usuario ejecute el export de un símbolo, EL SISTEMA fusionará las velas nuevas con el banco sin
  borrar ninguna existente y sin duplicar por `time` (R4).
- RF-2: CUANDO termine un export, EL SISTEMA correrá `calibrate_clock_offset` y reportará el resultado por
  símbolo; SI no calibra, el banco no se actualiza con ese export (R5).
- RF-3: CUANDO el operador guarde un análisis nuevo con Mark Price, SI el banco cubre ese instante, EL SISTEMA
  verificará que el Mark Price caiga dentro del rango de la vela de 15M correspondiente (con la tolerancia de
  `{{TOL_MARK_PRICE}}`) y, si no cae, mostrará una advertencia de una línea. Nunca bloquea el guardado.

Resolvedor (solo lectura):
- RF-4: EL SISTEMA calculará para cada análisis con `edge_validation_price` y `structural_invalidation`:
  - qué nivel tocó primero el precio desde el ancla (`created_at`, o `backdated_timestamp` si es retroactivo);
  - la hora del toque, con la temporalidad más fina disponible;
  - el MAE y MFE estructural (precio más adverso y más favorable a la tesis entre el ancla y el toque);
  - el estado `Open` si no tocó ninguno dentro de `{{MAX_HORIZONTE}}`;
  - "ambiguo" si una misma vela toca los dos niveles incluso en la temporalidad más fina.
- RF-5: EL SISTEMA derivará `resolution_type` propuesto: validación primero → `Confirmed`; invalidación
  primero → `Invalidated`; ninguno → `Open`; SI existe un análisis posterior del mismo activo creado antes del
  toque → `Overlap Invalidation` [NECESITA ACLARACIÓN: confirmar esta regla de overlap].
- RF-6: CUANDO el usuario ejecute el reporte de comparación, EL SISTEMA mostrará, por cuenta, la coincidencia
  entre la resolución automática y los campos manuales (`resolution_type`, `resolution_time`,
  `structural_mae/mfe`, `specific_bias_compliance`), con n y % faltante, excluyendo retroactivos (R11), y
  listará los análisis que difieren.

Pre-llenado del Efficiency Audit:
- RF-7: CUANDO el operador abra el Efficiency Audit de un análisis, SI el resolvedor tiene resultado, EL SISTEMA
  propondrá `resolution_time`, `resolution_type`, `structural_mae` y `structural_mfe` como valores por defecto
  editables, marcados como automáticos (R6).
- RF-8: EL SISTEMA propondrá `structural_resolution` y `failure_reason` solo donde haya regla objetiva
  [NECESITA ACLARACIÓN: umbrales de "mínima" y "expansión significativa"; tolerancia de "Liquidity Sweep"];
  el resto de sus valores queda manual.

Pre-llenado del Tactical Audit:
- RF-9: CUANDO el operador cargue un trade con `entry_time`, `exit_time`, `entry_price` y `stop_loss`, EL SISTEMA
  propondrá `mae_adverse` y `mfe_favorable` en R, calculados sobre velas de `{{TF_TACTICO}}` entre la entrada y la
  salida.
- RF-10: EL SISTEMA propondrá `could_hit_tp` (si el precio alcanzó el TP entre la entrada y `{{VENTANA_TP}}`) y
  `session` a partir de `entry_time` [NECESITA ACLARACIÓN: horarios de sesión en hora GT y si `session` ya se
  calcula hoy].

Backfill:
- RF-11: CUANDO el usuario ejecute el backfill, EL SISTEMA correrá en modo dry-run por defecto, listará los campos
  `NULL` que llenaría y las diferencias con valores manuales, y solo escribirá con `--apply` tras las puertas de
  R9. Nunca sobrescribe un valor existente (R7).

Opcional:
- RF-12: CUANDO el usuario ejecute `p2-model` después de guardar un análisis, EL SISTEMA imprimirá el P2 del
  modelo D (`MODEL_D`, sin modificarlo) con el detalle por temporalidad (EMAs, ADX, DI, peso), usando solo
  velas cerradas en el ancla, y lo registrará en un archivo aparte (no en la DB).
</requisitos_semilla>

<errores>
Ninguno bloquea el guardado; todos dejan el motivo visible.
- E1 — El export falla o MT5 está cerrado → banco intacto, motivo `export_fallido`.
- E2 — El reloj no calibra → no se resuelve ese símbolo, motivo `reloj_desalineado`.
- E3 — El banco no cubre el ancla o el horizonte → resultado `sin_velas`, sin propuesta.
- E4 — Faltan niveles en el análisis → no se resuelve, motivo `sin_niveles`.
- E5 — Una vela toca ambos niveles también en la temporalidad más fina → `ambiguo`, sin propuesta de tipo.
- E6 — La operación sobre la copia de la DB falla → detenerse y no tocar las reales.
- E7 — Un test falla → no marcar la tarea como hecha; mostrar la salida y detenerse.
</errores>

<contexto>
Por qué existe: la auditoría del 2026-09-24 mostró que el acierto medido con `specific_bias_compliance`
(etiqueta manual puesta después) da 68.9% direccional en XAU (31/45), contra 60.5% (23/38) con la resolución
geométrica desde el ancla, y que
los 7 análisis retroactivos, cargados con el resultado ya conocido, acertaron 6 de 6. Una medición automática y
objetiva evita ese sesgo.

Hechos a respetar (re-verificá; el repo cambia):
- Velas: `MT5Exports/{XAUUSD,BTCUSD,USTEC}/{1W,1D,12H,4H,1H,30M,15M}.csv` (XAUUSD tiene además `5M.csv`),
  columnas `time,open,high,low,close`, `time` = hora de **apertura** en GT naive.
- `edge_validation_price` y `structural_invalidation` están en `unified_department`; faltan en 6 de 113 análisis.
- `efficiency_audit` (XAU, 81 filas): `structural_mae/mfe` presentes en 74, `resolution_time` en 80.
- `tactical_audit` tiene `mae_adverse` y `mfe_favorable` (en R, 0–10 según el schema pydantic), no `mae`/`mfe`.
- `efficiency_audit.specific_bias_compliance` se deriva de `bias_a == real_bias_b`
  (`cli/schemas/audit_efficiency.py`).
- US100 tiene solo 9 precios de referencia (el reloj necesita 10) y el símbolo MT5 es `USTEC`.
</contexto>

<configuracion>
| Variable | Significado | Valor por defecto |
|---|---|---|
| `{{TOL_MARK_PRICE}}` | Tolerancia para validar el Mark Price contra su vela de 15M | 0.1% del precio (supuesto) |
| `{{MAX_HORIZONTE}}` | Horizonte máximo del resolvedor | 2160 velas de 1H (~90 días), igual que el backtest |
| `{{TF_TACTICO}}` | Temporalidad para MAE/MFE en R | 5M donde exista, si no 15M |
| `{{VENTANA_TP}}` | Hasta cuándo se evalúa `could_hit_tp` | Hasta que toque el SL o `{{MAX_HORIZONTE}}` [NECESITA ACLARACIÓN] |
| `{{MT5_SYMBOL_MAP}}` | Activo de la cuenta → símbolo MT5 | `XAUUSDT.P→XAUUSD`, `BTCUSDT.P→BTCUSD`, `US100→USTEC`, `US500→US500` |
</configuracion>
