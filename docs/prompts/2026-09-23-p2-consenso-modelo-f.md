# v1.0 — 2026-09-23 · Prompt de implementación: P2 en modo consenso (modelo F) con A registrado en paralelo

> Cómo usarlo: en una sesión **nueva** de Claude Code, abierta en la raíz de `blast_master`, escribí:
> `Leé y ejecutá @docs/prompts/2026-09-23-p2-consenso-modelo-f.md`

<instrucciones>

## Rol

Sos el implementador de UN feature en un repo existente, y lo construís **exclusivamente a través del skill
`/sdd-engine`**. No decidís política de trading ni cambiás el modelo: implementás una decisión ya tomada por
el usuario. Nunca escribís en las bases de datos reales de las cuentas sin su aprobación explícita.

## Objetivo

Que al registrar un análisis nuevo, **P2 se resuelva por consenso** entre el operador y el modelo
sistemático **F**:
- el operador carga su P2 **a ciegas**;
- el sistema calcula F en ese instante;
- si coinciden en dirección, queda el P2 del operador; si no, **P2 = 0**.

En paralelo, y **sin mostrárselo al operador**, el sistema calcula y registra el modelo **A** como control.
Cada resolución queda en un registro inmutable, y un reporte de solo lectura aplica la regla de decisión
pre-registrada (F contra A) cuando haya suficientes análisis nuevos.

## Entregable

1. `specs/001-p2-consenso-modelo-f/` con `baseline.md`, `spec.md`, `plan.md`, `tasks.md`, `consistency.md`
   y `validation.md`, producidos por las fases de `/sdd-engine`.
2. `docs/constitution.md`: la máquina de estados la exigirá después de B2 (ver Procedimiento).
3. Código y tests del feature, una tarea por turno, con la suite en verde.
4. La migración de esquema aplicada a las DBs reales, **solo** tras las puertas de la regla R12.
5. Un comando de solo lectura que genera el reporte del test prospectivo F contra A.

## Procedimiento

Ejecutá `/sdd-engine`. Su máquina de estados manda: corré
`python3 ~/.claude/skills/sdd-engine/scripts/sdd_state.py .` al empezar **cada** turno, y si devuelve
`CODIGO_PERMITIDO=NO`, no crees ni modifiques código fuente. La ruta esperada (verificada el 2026-09-23) es
**brownfield-delta**, porque el repo tiene código y no tiene `specs/`.

| Paso | Fase sdd-engine | Entrada | Salida | Puerta humana |
|---|---|---|---|---|
| 0 | — | Repo | Confirmación de prerrequisitos (R1, R2) | Commit de la base, si falta |
| 1 | B1 baseline | Superficie de §Superficie | `baseline.md`: cada afirmación con `archivo:línea`, `[NO VERIFICADO]` en lo deducido y no ejecutado | El usuario lee el baseline |
| 2 | B2 spec-delta | Baseline + `<decisiones>` + `<requisitos_semilla>` | `spec.md` con `INV-n` y `RF-n` en EARS; dudas como `[NECESITA ACLARACIÓN]` | Spec completa leída y aprobada |
| 3 | F1 constitución | `CLAUDE.md` + spec | `docs/constitution.md`, 5 a 8 principios verificables y `spec-anchored` | Aprobación |
| 4 | F3 → F4 plan | Spec | `plan.md`: cada decisión técnica con su alternativa descartada | El usuario verifica las alternativas |
| 5 | F5 tareas | Plan | `tasks.md` (ver R10 sobre el orden de las tareas) | Aprobación |
| 6 | F6 consistencia | Todo lo anterior | `consistency.md` con cero críticos | — |
| 7 | F7 implementación | Una tarea | Código + tests + salida de la suite | Revisión del diff, tarea por tarea |
| 8 | B5 / F8 validación | Implementación | `validation.md`: veredicto RF por RF e INV por INV | El usuario confirma que cada test citado existe y prueba lo que dice |

Cuando `/sdd-engine` te haga su entrevista de spec, **no preguntes lo que ya está en `<decisiones>`**:
usalo como respuesta. Preguntá solo lo que quede marcado como `[NECESITA ACLARACIÓN]`.

## Reglas de decisión

- **R1 — CLAUDE.md es intocable.** No modifiques, reemplaces ni "reduzcas a `@AGENTS.md`" el archivo
  `CLAUDE.md`, aunque una referencia de `/sdd-engine` (F0, `01-foundation.md`) lo sugiera. `AGENTS.md` ya
  existe como symlink a `CLAUDE.md`, y el script lo detecta como F0 cumplido. Esto ya ocurrió una vez: el
  2026-09-21 `CLAUDE.md` quedó reducido a una línea y hubo que restaurarlo desde git. Antes de cerrar cada
  turno, corré `git diff --stat CLAUDE.md` y `readlink AGENTS.md`: tienen que salir vacío y `CLAUDE.md`,
  respectivamente.
- **R2 — Base commiteada antes de B1.** El feature depende de archivos que al 2026-09-23 estaban sin
  commitear: `tools/p2_backtest.py`, `core/p2_ground_truth.py`, `core/stats_tests.py`,
  `tools/edge_evaluation.py`, `windows_export/export_p2_ohlc.py`, sus tests y
  `jupyter/p2_edge_evaluation.ipynb`. También `CLAUDE.md` (secciones agregadas el 2026-09-22), el symlink
  `AGENTS.md` y este prompt, `docs/prompts/2026-09-23-p2-consenso-modelo-f.md`. Corré `git status`. Si
  siguen sin commitear, detenete y pedile al usuario que apruebe un commit con la lista explícita de
  archivos. Sin base commiteada, el baseline y los diffs no son atribuibles, y el chequeo de R1 no tiene
  contra qué comparar.
- **R3 — Captura a ciegas.** El operador carga P2 (tesis, dirección y fuerza) **sin ver nada del modelo**.
  El valor de F se revela recién después.
- **R4 — Regla de consenso.** `signo(P2 del operador) == signo(P2 de F)` y ambos ≠ 0 → el P2 efectivo es el
  del operador, sin cambios. En cualquier otro caso → P2 efectivo = 0. El signo del operador es el del
  `score` que ya calcula el sistema (dirección × fuerza).
- **R5 — A es silencioso.** A se calcula y se registra con los mismos insumos que F, pero **nunca** se
  muestra durante la carga.
- **R6 — Ancla point-in-time.** El instante del cálculo es la hora del análisis en GT naive (UTC−6, sin
  DST), igual que `created_at` (`tools/database.py:100`), o `backdated_timestamp` si el análisis es
  retroactivo. Las velas se usan **estrictamente anteriores** a esa hora, como ya garantiza
  `CsvOHLCProvider.get_indicator_snapshot`.
- **R7 — Modelos inmutables.** F y A se toman del registro de `tools/p2_backtest.py` (`MODEL_F`, `MODEL_A`)
  con `compute_score_p2_sistematico` + `rescale_p2_sistematico`. No redefinas pesos, EMAs, ADX, umbrales
  ni reescalado.
- **R8 — El export nunca reduce la historia.** Hoy el exportador sobrescribe `{out_dir}/{TF}.csv`. El export
  que dispare el CLI debe:
  - dejar una cobertura igual o mayor a la existente, porque el cuaderno y los reportes evalúan análisis
    desde mayo de 2026;
  - si falla o no valida, dejar intactos los CSV anteriores.

  El plan decide el mecanismo; por ejemplo, exportar a una carpeta temporal y reemplazar solo si valida.
- **R9 — Nunca bloquear el guardado.** Si el modelo no está disponible por cualquier motivo (ver `<errores>`),
  el P2 efectivo es el del operador, como hoy, y el registro guarda el motivo. El análisis se guarda igual.
- **R10 — Orden de tareas.**
  1. Tests de red de seguridad sobre el comportamiento actual (B3).
  2. **Spike manual de interop:** el CLI lanza el exportador vía `powershell.exe`, con MT5 abierto por el
     usuario, y se verifica que se generen los CSV y que pase `calibrate_clock_offset`.
  3. El resto. Si el spike falla, detenete: la arquitectura elegida no es viable y el usuario debe decidir.
- **R11 — Cambio de modelo, solo por humano.** El reporte recomienda; nunca cambia el modelo activo. Pasar de
  F a A es un cambio de configuración que hace el usuario.
- **R12 — Escrituras en DBs reales, en tres puertas.**
  1. La migración corre limpia sobre una **copia** de cada `.data/flight_account_*.db`, con el patrón de
     `CLAUDE.md` (`cp` + `init_db('sqlite:////tmp/x.db')`).
  2. Hay un backup reciente: `conda run -n blast_master python tools/backup.py backup`.
  3. Hay aprobación explícita del usuario.

  Solo cambios aditivos (tabla o columnas nuevas); nunca `DROP`, ni `ALTER` de columnas existentes, ni
  reescritura de filas existentes.
- **R13 — Pruebas manuales en Flight Session.** Todo flujo del wizard se prueba en una Flight Session
  descartable (`CLAUDE.md`, sección "Comandos"), nunca contra una cuenta real.

## Restricciones

- No inventes APIs, rutas, funciones ni resultados. Si algo no consta, declaralo como dependencia y detenete.
- No modifiques nada fuera de la superficie declarada en B1. En particular, **no toques**:
  - `core/math_engine.py` (pesos de `calculate_edge_score`);
  - `determine_market_bias` y su umbral ±0.26;
  - los gates Tier D/F, emocional y Stop Deviation;
  - `tools/notion_sync.py`;
  - `core/edge_analysis.py` (su bug de Choppy es otro trabajo, pendiente);
  - P0, P1, P3 y P4.
- Tests: `conda run -n blast_master pytest -q`, nunca `pytest` a secas (el entorno base no tiene las dependencias).
- Git: nada de `git add -A`, `git add .` ni `git commit -a`; commits con archivos explícitos y solo cuando
  el usuario lo pida.
- Código de análisis y reporte de solo lectura: las DBs se abren con `mode=ro`
  (`tools/p2_backtest.py:open_readonly_session`) y se piden columnas explícitas, no la entidad ORM completa.
  Las cuentas no migradas no tienen las columnas nuevas; así falló US100 el 2026-09-22.
- Comunicate con el usuario en español (voseo). Los mensajes nuevos del wizard siguen el idioma de la
  pantalla donde aparecen (hoy, inglés).

## Condiciones de finalización

Terminás cuando se cumplen todas:
- `validation.md` da veredicto `CONVERGE` / cumple en cada RF e INV, con el test citado;
- la suite completa está en verde, con la salida mostrada;
- hay demo manual en una Flight Session con MT5 abierto: carga a ciegas → revelado de F → consenso →
  fila en el registro;
- hay demo del respaldo con MT5 cerrado: P2 manual, motivo registrado, análisis guardado;
- la migración está aplicada a las DBs reales tras las tres puertas de R12;
- el reporte prospectivo corre sobre las DBs reales e imprime el progreso `k/{{N_FORWARD}}`.

Detenete y consultá si:
- necesitás tocar algo de la lista "no toques";
- aparece un `[NECESITA ACLARACIÓN]`;
- el spike de R10 falla;
- estás por escribir en `.data/`;
- `sdd_state.py` bloquea;
- `CLAUDE.md` muestra diff.

</instrucciones>

<decisiones>
Tomadas por el usuario el 2026-09-23. Son insumos de la spec, no preguntas abiertas.

- D1. Adoptar la **opción 4** del reporte: F en modo consenso y A registrado en paralelo como control.
- D2. **Captura a ciegas** (R3). Es la condición bajo la cual se midió el 83% de acierto del consenso;
      mostrar F antes sesgaría al operador e invalidaría el test F contra A.
- D3. **El CLI dispara el export** de velas en el momento del análisis (no solo lee CSV preexistentes).
- D4. Si no coinciden, P2 = 0. No se impone ninguno de los dos.
- D5. **Regla pre-registrada:** tras `{{N_FORWARD}}` análisis nuevos resueltos, se comparan F y A con un test
      de signo sobre el resultado por análisis (+1 acierto, 0 abstención, −1 fallo). Si A supera a F con
      p < `{{ALPHA}}`, se recomienda pasar a A; en cualquier otro caso F se mantiene. Un análisis "nuevo" es
      uno creado desde `{{FECHA_GO_LIVE}}`.
- D6. La medición del acierto reutiliza el ground truth aprobado (decisiones A/B/C del 2026-09-21):
      qué nivel toca primero el precio, sobre velas de 1H con tope de 2160; una vela que toca los dos niveles
      queda incompleta; Choppy cuenta como abstención y no como fallo.
</decisiones>

<requisitos_semilla>
Base para `spec.md` (B2). Verificá cada uno contra el baseline y reformulá en EARS si hace falta. No agregues
comportamiento que no esté acá o en `<decisiones>` sin marcarlo `[NECESITA ACLARACIÓN]`.

Comportamiento actual a preservar:
- INV-1: EL SISTEMA seguirá pidiendo la tesis, la dirección y la fuerza de P2 igual que hoy (`cli/main.py:2545-2547`).
- INV-2: EL SISTEMA calculará `calc_edge` con `core/math_engine.calculate_edge_score` y los mismos pesos.
- INV-3: EL SISTEMA guardará el análisis aunque el modelo no esté disponible.
- INV-4: EL SISTEMA no alterará P0, P1, P3, P4 ni los análisis ya existentes.
- INV-5: EL SISTEMA mantendrá, en cada CSV de velas, una cobertura temporal igual o mayor a la previa (R8).
- INV-6: EL SISTEMA mantendrá la suite de tests existente en verde.

Comportamiento nuevo:
- RF-1: CUANDO el operador termine de cargar P2 en un análisis nuevo, EL SISTEMA disparará el export de velas
  del símbolo MT5 de la cuenta, según `{{MT5_SYMBOL_MAP}}`.
- RF-2: CUANDO el export termine y valide (el reloj pasa `calibrate_clock_offset` y hay al menos 800 velas
  previas al ancla en cada temporalidad del modelo), EL SISTEMA calculará F y A en el ancla (R6).
- RF-3: CUANDO F esté calculado, EL SISTEMA mostrará al operador el valor de F, el suyo, y el P2 efectivo
  resultante de R4.
- RF-4: EL SISTEMA usará el P2 efectivo en todo cálculo de `calc_edge`, bias y probabilidades de ese análisis,
  incluidos la vista previa, el recálculo del paso 3 y la reparación: `recalculate_unified_metrics`,
  función anidada en `flow_repair_analysis_audits` (`cli/main.py:4826`, anidada en `:5143`).
- RF-5: EL SISTEMA agregará, por cada resolución, una fila inmutable al registro con:
  - la hora del ancla, la cuenta y el símbolo;
  - dirección, fuerza y score del operador;
  - score crudo y reescalado de F y de A;
  - resultado del consenso y P2 efectivo;
  - identidad del modelo (nombre, pesos, cadena de EMAs, DI);
  - hora de la última vela usada;
  - motivo si el modelo no estuvo disponible.
- RF-6: CUANDO el operador edite P2 desde "Edit a Field" (`cli/main.py:2995-2998`), EL SISTEMA reaplicará R4
  con los mismos valores de F y A y agregará una fila nueva al registro. Nunca modificará una fila previa.
- RF-7: DONDE el análisis sea retroactivo, EL SISTEMA usará `backdated_timestamp` como ancla.
- RF-8: DONDE el análisis sea clonado (`cloned_state`), EL SISTEMA calculará el consenso de nuevo y no copiará
  el registro del original.
- RF-9: SI el modelo no está disponible (cualquier clase de `<errores>`), ENTONCES EL SISTEMA dejará el P2 del
  operador como efectivo, mostrará el motivo en una línea y lo registrará.
- RF-10: CUANDO el usuario ejecute el reporte prospectivo, EL SISTEMA:
  - leerá las DBs en solo lectura y verificará el reloj;
  - evaluará los análisis desde `{{FECHA_GO_LIVE}}` con el ground truth de D6;
  - mostrará el neto de F, A, el operador y el consenso;
  - con al menos `{{N_FORWARD}}` resueltos, imprimirá la regla D5 textual y su veredicto;
  - con menos, imprimirá el progreso `k/{{N_FORWARD}}`.
- RF-11: EL SISTEMA nunca cambiará el modelo activo por su cuenta (R11).

[NECESITA ACLARACIÓN, candidatas para B2/F3]:
- Cómo se representa el P2 efectivo = 0 en `analysis_layer` sin perder la entrada original. Por ejemplo,
  sobrescribir la dirección a `Neutral` y conservar la original solo en el registro, o guardar ambos
  valores. Llevá al menos dos alternativas al plan.
- Frescura exigida a la última vela de 1H en análisis no retroactivos (supuesto: `{{MAX_ANTIGUEDAD_1H_H}}`).
  Considerá los fines de semana: XAU y US100 cierran, BTC no.
- Dónde vive la configuración: modelo activo, modelos sombra, mapa de símbolos, rutas y timeout.
</requisitos_semilla>

<errores>
Por clase. Ninguno bloquea el guardado (R9) y ninguno continúa en silencio: todos dejan el motivo registrado.

- E1 — El export falla: MT5 cerrado, `mt5.initialize()` falla, la detección del offset del servidor falla
  porque el mercado está cerrado, `powershell.exe` devuelve error, o se supera `{{TIMEOUT_EXPORT_S}}`.
  → Sin modelo, CSV previos intactos, motivo `export_fallido:<detalle>`.
- E2 — El reloj de las velas nuevas no cuadra (`calibrate_clock_offset` da `aligned=False`).
  → Descartar el export, CSV previos intactos, motivo `reloj_desalineado`.
- E3 — Historia insuficiente (menos de 800 velas previas en alguna temporalidad del modelo; hoy US100 tiene
  732 semanales). → Motivo `historia_insuficiente:<TF>`.
- E4 — Velas viejas para un análisis no retroactivo. → Motivo `velas_desactualizadas`.
- E5 — El activo de la cuenta no tiene símbolo MT5 en `{{MT5_SYMBOL_MAP}}` (por ejemplo, Flight Sessions de
  US500 o SPX). → Motivo `sin_simbolo_mt5`.
- E6 — La migración falla sobre la copia. → Detenerse, reportar y no tocar las DBs reales.
- E7 — Un test falla. → No marcar la tarea como hecha; mostrar la salida y detenerse.
</errores>

<contexto>
Por qué existe el feature. Evidencia en `jupyter/p2_edge_evaluation.ipynb` (§5, §6, §8, §9 y §12); plan en
`~/.claude/plans/crea-un-plan-de-frolicking-kahn.md`; memoria en
`~/.claude/projects/-home-jorgecg-projects-trading-blast-master/memory/p2_and_edge_evaluation.md`.
Leé además en `CLAUDE.md` las secciones "Antes de tocar código", "Acceso a APIs de broker" (trampa del reloj
+3h) y "Auditoría de análisis de datos".

Hechos que el diseño debe respetar (medidos el 2026-09-22 y 23; 89 análisis de XAUUSD + BTC):
- Dentro del edge, P2 pesa 0.075 por punto frente a un umbral de ±0.26. **Ningún modelo invirtió una sola
  decisión direccional** (0 de 89). El feature aporta tiempo, consistencia y datos; no cambia trades. No lo
  presentes como mejora de rendimiento.
- F y A empatan en neto (+18 cada uno; test de signo 17 contra 17) y coinciden en dirección en los 36 casos
  donde ambos opinan.
  - F: más precisión (30/42 = 71%), menos cobertura (47%).
  - A: 41/64 = 64%, cobertura 72%.
  - F se definió después de mirar los datos. Por eso A queda como control.
- Consenso operador + F: 20/24 = 83% (p = 0.002). Cuando se contradicen, el modelo gana 6 a 2. Todo esto es
  exploratorio: el test prospectivo es el que lo confirma o lo descarta.
- La ventaja de F viene del peso en 1H: el precio toca uno de los niveles en una mediana de 7 horas.
- En análisis Choppy, todas las versiones de P2 aciertan por debajo del 50%.

Hechos de código (verificados el 2026-09-23; re-verificá, el repo cambia):
- `flow_new_analysis` está en `cli/main.py:2492`. P2 se carga en `:2545-2547` y se edita en `:2995-2998`. Los
  retroactivos fijan `created_at` en `:2917-2925`; las capas se persisten vía `to_db_layers()` en `:2928-2935`.
- `cli/schemas/efficiency.py`: `score = peso(dirección) × peso(fuerza)`, con LONG=1, SHORT=−1, NEUTRAL=0 y
  STRONG=2, MID=1, **WEAK=0**. "Long + Weak" ya da score 0.
- `determine_market_bias` (`cli/main.py:681`): |edge| ≥ 0.26 es direccional.
- `tools/database.py`: `init_db` en `:262`, `Base.metadata.create_all` en `:275` (crea tablas nuevas) y el
  shim de `ALTER TABLE` desde `:289` (columnas nuevas).
- `config/contract_specs.py:52` mapea `US100 → NAS100`. Es una clave de contrato, **no** el símbolo de MT5,
  que es `USTEC`: el export necesita su propio mapa.
- `windows_export/export_p2_ohlc.py`:
  - argumentos `--symbol`, `--out-dir`, `--min-anchor`, `--max-anchor` y `--server-utc-offset`;
  - escribe 1W, 1D, 12H, 4H, 1H, 30M y 15M;
  - necesita MT5 abierto; la detección automática del offset necesita el mercado abierto;
  - convierte desde la hora del servidor (bug +3h corregido el 2026-09-22).
- Interop verificado: `powershell.exe` responde desde WSL y el Python de Windows es 3.12.10, con
  `MetaTrader5` 5.0.6180 y pandas 3.0.6.
  **No verificado:** un export completo lanzado desde WSL (hasta ahora se corrió desde PowerShell) → spike de R10.
- Los tests de `flow_new_analysis` mockean `InquirerPy.inquirer.select`/`text` y `cli.main.get_mandatory_*`
  (`tests/test_flow_new_analysis.py`).
- `CsvOHLCProvider`, `calibrate_clock_offset`, `MIN_BARS_PER_TF = 800`, `open_readonly_session`,
  `assemble_p2_systematic_rows` y el ground truth (`core/p2_ground_truth.py`) ya existen y tienen tests.
  Reutilizalos; no los reimplementes.
</contexto>

<configuracion>
| Variable | Significado | Valor por defecto |
|---|---|---|
| `{{MODELO_ACTIVO}}` | Modelo que decide el consenso | `F` |
| `{{MODELOS_SOMBRA}}` | Modelos registrados en silencio | `[A]` |
| `{{MT5_SYMBOL_MAP}}` | `unified_department.asset` → símbolo MT5 | `XAUUSDT.P→XAUUSD`, `BTCUSDT.P→BTCUSD`, `US100→USTEC` |
| `{{MT5_EXPORTS_BASE}}` | Carpeta base de los CSV | carpeta padre de `P2_SYSTEMATIC_OHLC_DIR` en `.env` (`/mnt/c/Users/jcifu/MT5Exports`) |
| `{{EXPORTER_WIN_PATH}}` | Exportador visto desde Windows | `\\wsl$\Ubuntu\home\jorgecg\projects\trading\blast_master\windows_export\export_p2_ohlc.py` |
| `{{TIMEOUT_EXPORT_S}}` | Tiempo máximo del export antes del respaldo | `180` (supuesto; medí la duración real en el spike) |
| `{{MAX_ANTIGUEDAD_1H_H}}` | Antigüedad máxima de la última vela 1H respecto del ancla | `2` (supuesto) |
| `{{N_FORWARD}}` | Análisis nuevos resueltos para aplicar la regla D5 | `40` |
| `{{ALPHA}}` | Significancia de la regla D5 | `0.05` |
| `{{FECHA_GO_LIVE}}` | Desde cuándo cuenta el test prospectivo | fecha en que se activa el feature |
</configuracion>
