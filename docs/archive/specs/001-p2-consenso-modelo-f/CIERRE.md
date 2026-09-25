# Cierre de la spec 001: P2 por consenso (modelo F, luego D)

- **Fecha de cierre:** 2026-09-24.
- **Estado:** cerrada **sin implementar**. P2 sigue siendo manual.
- **Fase alcanzada:** B2 de `sdd-engine` (brownfield-delta). `spec.md` está redactada, pero el usuario no la
  aprobó. No hay `plan.md`, `tasks.md`, código ni migración de DBs.
- **Decisión que la cierra:** D1 de `docs/prompts/2026-09-24-auto-resolucion-velas.md` (v1.2): *"Se cierra el
  feature de consenso P2 (`specs/001-p2-consenso-modelo-f`). P2 sigue siendo manual."*
- **Continúa en:** spec 002, `specs/002-auto-resolucion-velas/`, que se trabaja en el worktree
  `.claude/worktrees/spec-002-auto-resolucion-velas`.

Esta carpeta está fuera de `specs/` a propósito. `sdd_state.py` toma como spec activa la primera carpeta
`specs/NNN-*` sin `validation.md` con `verdict: SPEC_CUMPLIDA`. Si 001 siguiera ahí, bloquearía a 002 para
siempre, y marcarla `SPEC_CUMPLIDA` sería falso.

## Contenido de la carpeta

| Archivo | Qué es | sha256 |
|---|---|---|
| `baseline.md` | Baseline B1 de 001, del 2026-09-23, sobre `df71147` | `0ecd1dd478402bec5937e12c0a74df5c51e675e858001b38a4982b3b6c333647` |
| `spec.md` | Spec B2 de 001, del 2026-09-23, sobre `fc639e8` | `e8f0c17106291746fb7aa9aa9ed2f96d10cf6adc816847d49fff646732f1365a` |
| `CIERRE.md` | Este documento | — |

`baseline.md` y `spec.md` se archivaron **sin editar**, byte a byte iguales al original. Por eso sus rutas
internas siguen diciendo `specs/001-p2-consenso-modelo-f/`, y sus citas `archivo:línea` corresponden a su
commit base, no al código actual. Las correcciones posteriores van en la sección "Correcciones" de abajo.

## Por qué cambió el modelo y por qué se cerró

1. **2026-09-23, prompt v1.0** (`docs/prompts/2026-09-23-p2-consenso-modelo-f.md`). Proponía que el operador
   cargara P2 a ciegas y que un modelo sistemático, **F**, lo confirmara: si coincidían en dirección quedaba el
   P2 del operador, y si no, P2 = 0. A se calculaba en paralelo como control pre-registrado, para un test
   prospectivo F contra A.
2. **2026-09-23, adenda 1** (`docs/prompts/2026-09-23-p2-consenso-modelo-f-adenda-1.md`).
   - C1: el proveedor de velas tenía look-ahead. La vela todavía abierta en el ancla entraba con su cierre
     final. Ejemplo: en 1W, un análisis de un miércoles veía el cierre del viernes.
   - C2: las cifras que justificaban el feature estaban infladas por ese look-ahead y por anclar en la primera
     ejecución en vez de en la hora del análisis.
   - D14: se agrega G como segundo modelo sombra.
   - **D15: el modelo activo pasa de F a D.** Con velas cerradas y ancla en la hora del análisis, D tuvo el mejor
     neto, 25/38 = 66% (neto +12, XAUUSD + BTC), contra 60% de F. Además, D se definió **antes** de ver los
     datos y F después: F se había elegido sobre datos con look-ahead, donde empataba con D. La adenda misma
     advierte que la ventaja no es significativa: D contra F da p = 0.45, y D como mejor de 7 variantes da
     permutación p = 0.105.
3. **2026-09-24, cierre.** Cuatro razones:
   - **No hay evidencia de que funcione.** Así lo dice la propia `spec.md` ("Qué no promete el feature"): medido
     sin look-ahead, ningún modelo ni el consenso superan al azar de forma significativa. El consenso operador
     + F dio 14/20 con p = 0.115, y el edge P0–P4 completo 36/60 con p Holm = 0.47. Además, P2 pesa 0.075 por
     punto frente a un umbral de ±0.26: ningún modelo invirtió nunca una decisión direccional (0 de 89).
   - **La auditoría del 2026-09-24.** No existe como documento aparte en el repo (verificado con `grep`); sus
     números están en el `<contexto>` del prompt v1.2. El acierto medido con `specific_bias_compliance` (etiqueta
     manual puesta después del hecho) da 68.9% en XAU (31/45). Medido con la resolución geométrica desde el
     ancla, da 60.5% (23/38). Los 7 análisis retroactivos, cargados con el resultado ya conocido, acertaron 6
     de 6.
   - **D2 del prompt v1.2:** el valor de las velas está en **medir resultados** de forma objetiva, no en
     sistematizar más parámetros. Eso es la spec 002.
   - **D3 del prompt v1.2:** si 002 implementa el modelo D (RF-12, opcional), solo se imprime como
     retroalimentación después de "Confirm & Save", y no cambia ningún campo ni ninguna decisión.

## Cómo se migró (procedencia de los archivos)

- 001 se trabajó en el worktree `.claude/worktrees/consenso-modelo-f-471f7d`, en la rama
  `claude/consenso-modelo-f-471f7d`. Esa rama apuntaba a `fc639e8` y no tenía commits propios. `baseline.md` y
  `spec.md` **nunca se commitearon**.
- El 2026-09-24, ni ese worktree ni esa rama existían ya en el repo de WSL. Lo muestran `git worktree list` y
  `git branch -a`, y el directorio no estaba en disco. No quedó registrado cómo se borraron.
- **La única copia que quedaba** estaba en
  `/mnt/c/Users/jcifu/OneDrive/Escritorio/Docs GPI/blast_master_copy/.claude/worktrees/consenso-modelo-f-471f7d/specs/001-p2-consenso-modelo-f/`.
  `blast_master_copy` es una copia completa del repo en Windows, `.git` incluido, con carpetas fechadas el
  2026-09-24 a las 07:01.
- En esa copia, la rama existe y apunta a `fc639e8`, sin commits propios. Los únicos archivos distintos de
  `fc639e8` eran estos dos. `AGENTS.md` también aparecía modificado, pero solo porque la copia a Windows
  convirtió el symlink en un archivo vacío.
- El 2026-09-24 se copiaron de ahí a esta carpeta, con autorización del usuario y verificando el sha256. La
  copia de Windows no se modificó: su `.git` conserva la rama y una entrada de worktree "prunable".
- **Base de la spec 002**, commiteada en `feature/tactical-tier-gate-df`:
  - `bd5c987`: `closed_bars()` y ancla de análisis en `tools/p2_backtest.py`, `tests/test_p2_closed_bars.py`,
    `tools/edge_evaluation.py`, `jupyter/p2_edge_evaluation.ipynb` v2, adenda 1 con D14/D15, prompt v1.2 y las
    líneas de `CLAUDE.md` sobre el look-ahead y el ancla tardía.
  - `db971a2`: `.gitignore` ignora `tools/migration_data/`.
  - El worktree de 002 se creó desde `db971a2`. Su suite da **429 passed, 1 skipped**, que es la referencia de
    418 del baseline de 001 más los 11 tests de `tests/test_p2_closed_bars.py`.

## Prompts que quedan obsoletos

- `docs/prompts/2026-09-23-p2-consenso-modelo-f.md` (v1.0).
- `docs/prompts/2026-09-23-p2-consenso-modelo-f-adenda-1.md`.

Se conservan **sin editar** como registro de por qué existió 001 (R12, paso 0, del prompt v1.2). No deben
usarse para implementar nada.

## Correcciones al baseline archivado

Son hallazgos posteriores al 2026-09-23. No se editó `baseline.md`, así que se registran acá.

- **§2.12, entorno del worktree (H-B).** El baseline dice que el worktree no tenía `.data/`. Era cierto cuando se
  midió con `ls -a`, pero después la suite de tests (2026-09-23, 15:25) creó ahí dos archivos:
  - `.data/flight_account_001_xauusd.db`, con las 7 tablas y 0 filas (contado el 2026-09-24 en solo lectura,
    `mode=ro&immutable=1`, sobre la copia de Windows);
  - `.data/flight_sessions.json`, con una sola sesión "001", "Main Flight Account".

  Es una DB vacía de prueba. No se archivó.
- **§2.8 "Import de `cli.main`" y §2.12 (H-C, confirmado el 2026-09-24).**
  - El import de `tools/p2_backtest.py:73` **no abre ninguna DB**: `get_active_engine()` solo se llama desde
    adentro de funciones. El comentario de `tools/p2_backtest.py:70-72`, que dice que el import la dispara, es
    inexacto.
  - Lo que crea `.data/` es el test `tests/test_cli_report_command.py:71-75`
    (`test_report_command_rejects_conflicting_flags`). Invoca el CLI sin fijar
    `tools.database.engine_default`, y entonces pasa esto:
    1. el callback del grupo (`cli/main.py:995-998`) llama a `get_active_engine()` (`:255-286`);
    2. `engine_default` sigue en `None`, porque `init_db()` solo lo asigna para la URL exacta
       `sqlite:///.data/flight_account_001_xauusd.db` (`tools/database.py:381-382`), no para `:memory:`;
    3. entonces usa rutas relativas a la carpeta donde se corre `pytest`: escribe `.data/flight_sessions.json`
       si no tiene la sesión "001", y llama a `init_db` sobre la DB de esa sesión.
  - Se confirmó corriendo solo ese test desde una carpeta vacía: creó `.data/`. Los otros dos tests del mismo
    archivo no crearon nada.
  - **Consecuencia:** en el checkout principal, la sesión "001" es `flight_account_000_us500.db`, así que
    correr `pytest` desde la raíz abre esa DB real y le aplica `init_db` (WAL, `create_all` y `ALTER TABLE`).
  - La corrección es la primera tarea de 002 (R16).
  - Los riesgos 1 y 2 de H5 (import circular y doble import si el CLI importa `tools.p2_backtest`) no se
    evaluaron y siguen `[NO VERIFICADO]`.
- **H19, look-ahead: resuelto** en `bd5c987` con `CsvOHLCProvider.closed_bars()` y
  `tests/test_p2_closed_bars.py`.
- **H21, tramo inicial no observado:** 002 lo retoma con R3 del prompt v1.2. Todo camino hacia adelante empieza
  en el ancla, con la temporalidad más fina disponible para el primer tramo.
- **Citas de línea desplazadas:** `bd5c987` modificó `tools/p2_backtest.py`, así que las citas `:línea` de ese
  archivo en §2.8 del baseline (tomadas sobre `df71147`) ya no coinciden.

## Qué reutiliza 002

Según el paso 1 del prompt v1.2, 002 reutiliza estas partes del baseline, **re-verificando cada línea citada**
contra el código actual y sin copiarlas:

- §2.1: superficie de `flow_new_analysis`, con el orden de captura, el guardado, `created_at` y los retroactivos.
- §2.6 y H12: `init_db` corre `create_all` y el shim de `ALTER TABLE` cada vez que el CLI abre una DB.
- §2.7 y H10: hay 4 cuentas reales, y la sesión "001" es US500.
- §2.8: `calibrate_clock_offset` (H6: necesita ≥10 precios de referencia), el caché del proveedor (H15) y el
  `select` de la entidad ORM completa (H11).
- §2.10 y H7–H9: el exportador sobrescribe TF por TF, los anclas son obligatorios y falla con el mercado cerrado.
  Es la base del banco acumulativo (R4 de 002).
- §2.11: cobertura de los CSV. USTEC tiene 737 velas 1W.
- H19–H21, como se detalla arriba.

**No se reutiliza** el diseño del consenso: revelado, registro de consenso, modelo G, reporte prospectivo y
regla de los 40 (RF-1 a RF-23 de `spec.md`).
