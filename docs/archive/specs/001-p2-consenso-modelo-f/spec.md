# Spec 001 — P2 por consenso con el modelo activo D (A, F y G como sombras)

> **El modelo activo es D** (`MODEL_D`), por la adenda D15. El directorio se llama `001-p2-consenso-modelo-f` por historia, y no se renombra porque romperíamos las referencias de las fases ya hechas.

- **Ruta:** brownfield-delta, fase B2.
- **Fecha:** 2026-09-23.
- **Base:** `fc639e8`.
- **Fuentes, en orden de prioridad:**
  1. las respuestas del usuario del 2026-09-23 en esta sesión;
  2. la adenda `docs/prompts/2026-09-23-p2-consenso-modelo-f-adenda-1.md` (C1, C2, D7–D15). La versión vigente es la del disco del checkout principal, posterior al commit `fc639e8`: agrega D14, D15 y la tabla de C2 actualizada;
  3. el prompt `docs/prompts/2026-09-23-p2-consenso-modelo-f.md` (R1–R13, D1–D6);
  4. el baseline `specs/001-p2-consenso-modelo-f/baseline.md` (§ y H citados).
- **Idioma:** la spec está en español. Los mensajes nuevos del wizard van en inglés, el idioma de esa pantalla.

## Contexto y objetivo

Hoy el operador carga P2 (EMAs, ADX, etc.) a mano en cada análisis (`cli/main.py:2545-2547`), y eso le lleva tiempo y es subjetivo. El feature pone al lado un modelo sistemático, **D**, calculado con velas de MT5 en el momento del análisis:
- si el operador y D coinciden en dirección, queda el P2 del operador;
- si no coinciden, P2 = 0 (D1, D4, D15).

A (control, definido antes de ver los datos), F y G se calculan en silencio. Todo se registra en un log inmutable, y un reporte de solo lectura aplica la regla pre-registrada D contra A tras 40 análisis nuevos (D5, D15).

**Qué no promete el feature.** P2 pesa 0.075 por punto frente a un umbral de ±0.26, y ningún modelo invirtió nunca una decisión direccional: 0 de 89. Medida de nuevo sin look-ahead y con el ancla correcta (adenda, C2 y D15), **no hay evidencia retrospectiva significativa de que ningún modelo ni el consenso acierten más que el azar**:
- D: 25/38 = 66%, neto +12. Es el mejor de 7 variantes, y la permutación con corrección por selección da p = 0.105. D contra F: p = 0.45.
- F: 26/43 = 60%.
- P2 del operador: 26/46 = 57%.
- Consenso operador + F: 14/20 = 70%, p = 0.115.
- F en momentos al azar: 54%.
- El edge P0–P4 completo: 36/60 = 60%, p Holm = 0.47.

El feature aporta consistencia, tiempo y **datos prospectivos limpios**. El test de los 40 es la evidencia principal, no una confirmación.

## Actores

- **Operador:** carga el análisis en el wizard y ve el revelado de D.
- **Usuario analista:** es la misma persona. Corre el reporte prospectivo y decide si cambia de modelo (R11).
- **MT5:** terminal de Windows, abierto por el operador, fuente de las velas. Acceso de solo lectura.

## Historias de usuario

- H1: Como operador, quiero cargar P2 sin ver el modelo y después ver qué dice D, para que mi P2 no quede sesgado y el test sea válido.
- H2: Como operador, quiero que un fallo de MT5 nunca me impida guardar el análisis, para no perder trabajo.
- H3: Como analista, quiero un reporte que me diga, con una regla fijada de antemano, si conviene pasar de D a A.
- H4: Como analista, quiero que las velas históricas se acumulen y nunca se borren, para construir un banco de datos con los meses.

## Comportamiento actual (a preservar)

- **INV-1:** EL SISTEMA seguirá pidiendo la tesis, la dirección y la fuerza de P2 en un análisis nuevo con los mismos prompts, opciones y orden que hoy (P2 después de P0 y antes de P3) [baseline §2.1; `cli/main.py:2545-2547`].
- **INV-2:** EL SISTEMA calculará `calc_edge` con `core/math_engine.calculate_edge_score` y sus pesos actuales, y `market_bias` con `determine_market_bias` y su umbral de ±0.26, sin modificar ninguna de las dos [§2.3].
- **INV-3:** EL SISTEMA guardará el análisis aunque el modelo no esté disponible por cualquier motivo [R9; §2.1 "Guardado"].
- **INV-4:** EL SISTEMA no alterará la captura, el cálculo ni la persistencia de P0, P1, P3 y P4 [§2.1].
- **INV-5:** EL SISTEMA no modificará ninguna fila existente de las DBs reales. Esto incluye `unified_department`, `analysis_layer`, `efficiency_audit` y `tactical_audit` de los análisis previos al feature. Los cambios de esquema son solo aditivos [R12].
- **INV-6:** EL SISTEMA mantendrá la suite en verde. Referencia: `418 passed, 1 skipped` [baseline, encabezado].
  - Todo test existente que cambie de comportamiento por un RF de esta spec se ajusta en la tarea de ese RF, citándolo. Por ejemplo, `tests/test_flow_new_analysis.py` hoy deja vacíos los niveles, y RF-18 los vuelve obligatorios.
  - Ningún otro test se modifica.
- **INV-7:** EL SISTEMA no modificará:
  - `core/math_engine.py`, `determine_market_bias` ni su umbral;
  - los gates Tier D/F, emocional y Stop Deviation;
  - `tools/notion_sync.py` ni `core/edge_analysis.py` [prompt, §Restricciones].
- **INV-8:** EL SISTEMA mantendrá la definición y los resultados de los modelos A a F del registro de `tools/p2_backtest.py`: pesos, TFs, cadena de EMAs, DI, `compute_score_p2_sistematico` y `rescale_p2_sistematico` [R7; §2.8].
  - Hay dos excepciones:
    - la corrección de velas cerradas de la adenda C1, que llega desde la rama base;
    - el agregado de `MODEL_G` (RF-19), que es aditivo. El único test existente que cambia por eso es `tests/test_p2_models.py:186` ("cubre los seis"), y se ajusta en la tarea de RF-19.
- **INV-9:** DONDE un análisis no tenga filas en el registro de consenso, lo que incluye todos los análisis previos al feature, EL SISTEMA se comportará como hoy en la reparación (`cli/main.py:4826`, `:5143`), la vista de detalle (`:1755-1766`) y los reportes [§2.4, §4].
- **INV-10:** EL SISTEMA mantendrá funcionando, sobre DBs migradas y no migradas, los lectores de solo lectura existentes: `assemble_p2_systematic_rows`, `tools/edge_evaluation.py` y `tools/report_data.py` [H11].
- **INV-11:** EL SISTEMA mantendrá legibles para `CsvOHLCProvider`, el cuaderno y los reportes los CSV de velas actuales (`{P2_SYSTEMATIC_OHLC_DIR}/{TF}.csv`), o los migrará con su propio test [D10].

## Comportamiento nuevo o corregido

Numeración: RF-1 a RF-11 vienen de los requisitos semilla del prompt, RF-12 a RF-17 de la adenda y RF-18 en adelante de esta sesión. Donde el texto cambió respecto de la fuente, se indica.

### Ancla y horas

- **RF-12:** CUANDO el operador entre a `flow_new_analysis`, EL SISTEMA registrará una sola vez la hora de inicio en GT naive (UTC−6, sin DST). Esa hora no cambia con los reinicios internos del wizard (`RestartFlowException`) [D13; H20].
- **RF-7a:** DONDE el análisis sea un análisis nuevo o un clon en modo `[1] Use Current System Time`, EL SISTEMA usará como ancla la hora de inicio de RF-12 [D13, D8].
- **RF-7b:** DONDE el análisis sea retroactivo ("Add Backdated Analysis") o un clon en modo `[2] Enter Custom/Backdated Time`, EL SISTEMA usará como ancla la hora que eligió el operador, interpretada como GT naive [RF-7 del prompt; D8; el usuario confirmó el 2026-09-23 la misma lógica para el retroactivo y para el clon [2]].
- **RF-13:** EL SISTEMA registrará, por cada resolución de consenso, cuatro horas:
  - el ancla y su origen (`inicio`, `retroactivo` o `clon_modo_2`);
  - la hora de confirmación de P2;
  - la hora del cálculo del modelo;
  - por cada modelo y cada TF, la hora de apertura de la última vela cerrada usada.

  Ninguna de estas horas modifica `created_at` [adenda RF-13].

### Velas: banco, export y validación

- **RF-14:** EL SISTEMA calculará D, A, F y G usando en cada TF solo las velas cerradas en el ancla: `time + duración(TF) <= ancla`, donde una vela que cierra exactamente en el ancla cuenta como cerrada [adenda C1 y RF-14; H19].
  - Esto aplica en producción y en el reporte.
  - Un test tiene que fallar si la vela en formación influye en el resultado.
  - **Dependencia:** el fix del proveedor llega desde la rama base (adenda C1). No se reimplementa en esta spec.
- **RF-1 (reformulado):** CUANDO el operador confirme P2 y el banco no tenga, para el símbolo del análisis, las velas cerradas hasta el ancla en todas las TF de los modelos, EL SISTEMA lanzará el exportador de MT5 para ese símbolo [RF-1 del prompt; D3; D8].
  - El símbolo sale de `MT5_SYMBOL_MAP[unified_department.asset]`.
  - En un análisis a la hora actual el banco nunca llega hasta el ancla, así que siempre exporta.
- **RF-22:** SI el export no termina en `TIMEOUT_EXPORT_S` (180 s por defecto; el spike mide la duración real), ENTONCES EL SISTEMA lo cancelará y aplicará E1 con el motivo `export_fallido:timeout`.
- **RF-2a (reformulado):** CUANDO el export termine con éxito, EL SISTEMA validará las velas nuevas antes de incorporarlas al banco: `calibrate_clock_offset` tiene que devolver `aligned=True` sobre ellas [RF-2 del prompt; E2].
- **RF-15:** CUANDO un export valide, EL SISTEMA fusionará sus velas con el banco del símbolo, sin borrar ni alterar ninguna vela existente y sin duplicar por `time` [D10; adenda RF-15].
  - Un test tiene que fallar si una vela previa desaparece, cambia, o si la cobertura hacia atrás se reduce.
- **RF-15b:** SI un export trae una vela con un `time` que ya existe en el banco, ENTONCES EL SISTEMA conservará la vela existente. Motivo: el exportador usa un solo offset de servidor por corrida, y los cambios de horario del broker desplazan las etiquetas de 4H, 12H, 1D y 1W (§2.10; `export_p2_ohlc.py:31-39`).
- **RF-15c:** SI un export falla, no valida o se cancela, ENTONCES EL SISTEMA dejará el banco byte a byte igual que antes del export [R8; D10; H7].
- **RF-15d:** EL SISTEMA guardará en el banco las 7 TF que exporta hoy el script (1W, 1D, 12H, 4H, 1H, 30M y 15M) para cada símbolo del mapa [D10].
- **RF-2b (reformulado):** CUANDO las velas del banco cubran el ancla, EL SISTEMA calculará D, A, F y G en el ancla con `compute_score_p2_sistematico` + `rescale_p2_sistematico`, usando un proveedor nuevo que lea el banco actualizado [RF-2 del prompt; R7; H15].
  - Cada modelo exige al menos `MIN_BARS_PER_TF = 800` velas cerradas anteriores al ancla en cada una de sus TF.

### Consenso y revelado

- **RF-4a:** EL SISTEMA fijará el P2 efectivo según R4:
  - si `signo(score del operador) == signo(P2 reescalado del modelo activo, D)` y ninguno de los dos es 0, el P2 efectivo es el score del operador, sin cambios;
  - en cualquier otro caso, el P2 efectivo es 0.

  El score del operador es `peso(dirección) × peso(fuerza)`, como en `cli/schemas/efficiency.py:59-84` [R4; D4; D15].
- **RF-3:** CUANDO el modelo activo (D) esté calculado, EL SISTEMA mostrará al operador, en una sola pantalla y antes del panel "Edge Bias & Probabilities Preview" (`cli/main.py:2587-2605`), tres valores:
  - su P2, como dirección, fuerza y score;
  - el P2 de D, reescalado a −2..2 y con su dirección, identificado por nombre;
  - el P2 efectivo resultante [RF-3 del prompt; R3].
- **RF-5c:** EL SISTEMA no mostrará al operador A, F ni G en ningún punto del wizard [R5; D14; D15].
- **RF-4b:** EL SISTEMA usará el P2 efectivo como `x2` en todo cálculo de `calc_edge`, `market_bias` y probabilidades del análisis [RF-4 del prompt]:
  - la vista previa (`cli/main.py:2575-2583`);
  - la revisión del Step 3 (`:2701-2724`);
  - el guardado (`:2883-2898`);
  - la reparación de un análisis con filas en el registro (`recalculate_unified_metrics`, `:5143`).
- **RF-4c:** DONDE un análisis tenga filas en el registro, EL SISTEMA mostrará en la vista de detalle una descomposición del edge (`cli/main.py:1755-1766`) que reproduzca exactamente el `calc_edge` guardado. En esa descomposición aparece el P2 efectivo, junto al P2 del operador [H3].
- **RF-23:** EL SISTEMA guardará en la fila P2 de `analysis_layer` la dirección, la fuerza y el score **que cargó el operador**, como hoy (`cli/schemas/efficiency.py:76-79`). El P2 efectivo se guarda en el registro de consenso (RF-5) [decisión N1 del usuario, 2026-09-23].
  - "P2" en el historial y en los cuadernos sigue significando la opinión del operador.
  - Por eso `tools/report_data.py`, `assemble_p2_systematic_rows` (`p2_discrecional`) y los cuadernos no cambian (INV-10).
- **RF-4d:** DONDE un análisis tenga filas en el registro y su P2 efectivo difiera del score del operador, EL SISTEMA mostrará en la vista de detalle la línea P2 como `<dirección> <fuerza> → effective <valor>`, identificando al modelo que decidió.
- **RF-6c:** CUANDO el operador cambie la dirección o la fuerza de P2 en la reparación de un análisis con filas en el registro, EL SISTEMA reaplicará R4 con el valor registrado del modelo activo, agregará una fila `edicion_p2` y recalculará `calc_edge` con el P2 efectivo resultante [RF-4b; requisito derivado de N1].

### Registro de consenso

- **RF-5:** EL SISTEMA agregará, por cada resolución, una fila al registro de consenso de la DB de la cuenta. La fila contiene [RF-5 del prompt; adenda RF-13]:
  - el id del análisis, la cuenta (nombre del archivo de DB), el `asset` y el símbolo MT5;
  - las horas de RF-13;
  - la dirección, la fuerza y el score del operador;
  - por cada modelo (D, A, F y G): score crudo, score reescalado e identidad (nombre, pesos por TF, cadena de EMAs y DI), además de cuál es el modelo activo;
  - el resultado del consenso (`acuerdo`, `desacuerdo` o `sin_modelo`) y el P2 efectivo;
  - el motivo, si el modelo no estuvo disponible;
  - el tipo de fila: `inicial`, `edicion_p2` o `cambio_activo`.
- **RF-5b:** EL SISTEMA nunca modificará ni borrará una fila del registro. Un test tiene que fallar ante cualquier `UPDATE` o `DELETE` sobre esa tabla.
- **RF-5d:** EL SISTEMA persistirá las filas del registro en la misma transacción que el análisis. SI el análisis se descarta o falla su guardado, ENTONCES no quedará ninguna fila de ese análisis.

### Edición, clon y cambio de activo

- **RF-6:** CUANDO el operador cambie la dirección o la fuerza de P2 desde "Edit a Field" (`cli/main.py:2995-2998`), EL SISTEMA reaplicará R4 con los valores del modelo activo ya calculados, sin un nuevo export, y agregará una fila `edicion_p2`. Nunca modificará la fila previa [RF-6 del prompt].
- **RF-6b:** CUANDO el operador cambie el `asset` desde "Edit a Field" (`cli/main.py:3029-3044`) después de resuelto el consenso, EL SISTEMA resolverá de nuevo el modelo para el símbolo nuevo, en el mismo ancla, y agregará una fila `cambio_activo` [H16; requisito derivado, a confirmar al leer la spec].
- **RF-16:** DONDE el análisis sea un clon, EL SISTEMA pedirá de nuevo, a ciegas, la tesis, la dirección y la fuerza de P2 en lugar de copiarlas del original. El resto de `macro_state` se sigue copiando como hoy (`cli/main.py:2306-2320`) [D8; RF-8 del prompt; H2].
- **RF-8:** DONDE el análisis sea un clon, EL SISTEMA resolverá el consenso de nuevo en su propio ancla y no copiará filas del registro del original.

### Disponibilidad del modelo y errores

- **RF-17:** MIENTRAS el símbolo MT5 del análisis tenga menos de `MIN_PRECIOS_REFERENCIA` (10) precios de referencia, EL SISTEMA no correrá los modelos ni lanzará el export, dejará el P2 del operador y registrará `reloj_sin_calibrar` (E9) [D9; adenda RF-17].
  - **La cuenta es por símbolo**, decidido por el usuario el 2026-09-23 (N4). Se suman los precios de todas las DBs de cuentas reales de `flight_sessions.json` cuyo `asset` mapea a ese símbolo, leídas en `mode=ro`.
  - Una Flight Session nueva de ese símbolo hereda la cuenta. Hoy: XAUUSD 105, BTCUSD 19, USTEC 9, US500 2.
  - Cuentan como precios de referencia:
    - las órdenes llenadas con `entry_time` y `entry_price > 0`;
    - los análisis no retroactivos con `mark_price > 0` (`tools/p2_backtest.py:1010-1027`).
- **RF-9:** SI el modelo no está disponible por cualquiera de las clases E1 a E9, ENTONCES EL SISTEMA:
  - dejará el P2 del operador como efectivo;
  - mostrará el motivo en una línea;
  - registrará la fila con el motivo;
  - seguirá con el wizard sin bloquear el guardado [RF-9 del prompt; R9].
- **RF-9b:** SI D está disponible pero A, F o G no lo están, ENTONCES EL SISTEMA resolverá el consenso con D y registrará como nulo el modelo sombra faltante, con su motivo.
- **RF-18:** CUANDO `flow_new_analysis` pida Edge Validation Price y Structural Invalidation (`cli/main.py:2617-2618`), EL SISTEMA no aceptará un valor vacío ni no numérico. Aplica a análisis nuevos, clones y retroactivos. Mark Price sigue siendo opcional [decisión del usuario del 2026-09-23; adenda D12].
- **RF-18b:** EL SISTEMA mantendrá opcionales esos dos campos en la reparación de análisis existentes (`cli/main.py:5315-5327`) [INV-5].

### Modelos sombra y configuración

- **RF-19:** EL SISTEMA agregará `MODEL_G` al registro de `tools/p2_backtest.py`, junto a `MODEL_A` a `MODEL_F`, sin tocar los existentes. Un test fija sus pesos [adenda D14].
  - Mecánica de E: cadena EMA (20, 100, 200), ADX14 y filtro DI.
  - Pesos: 1W 0.10, 1D 0.10, 12H 0.10, 4H 0.10, 1H 0.20, 30M 0.20, 15M 0.20.
  - Su descripción dice que se definió el 2026-09-23, después de ver los datos.
- **RF-19b:** EL SISTEMA calculará y registrará en silencio G como modelo sombra. G nunca decide el consenso. SI falta historia de 30M o de 15M, ENTONCES G se abstiene con el motivo de E3, y los demás modelos se calculan igual [D14].
- **RF-19c:** EL SISTEMA calculará y registrará en silencio F como modelo sombra [D15].
- **RF-19d:** EL SISTEMA no agregará 15M al modelo activo [D11].
- **RF-21:** EL SISTEMA leerá de su configuración, sin tener que editar código para cambiarlos, estos valores [R11; `<configuracion>`]:
  - el modelo activo (`D`) y los modelos sombra (`A`, `F`, `G`) [D15];
  - `MT5_SYMBOL_MAP` (`XAUUSDT.P→XAUUSD`, `BTCUSDT.P→BTCUSD`, `US100→USTEC`, `US500→US500`);
  - la ruta del banco, `EXPORTER_WIN_PATH` y `TIMEOUT_EXPORT_S`;
  - `MAX_ANTIGUEDAD_1H_H`, `MIN_PRECIOS_REFERENCIA`, `N_FORWARD`, `ALPHA` y `FECHA_GO_LIVE`.
- **RF-11:** EL SISTEMA nunca cambiará por su cuenta el modelo activo. El reporte solo recomienda [R11].

### Reporte prospectivo

- **RF-10a:** CUANDO el usuario ejecute el comando del reporte prospectivo, EL SISTEMA abrirá cada DB de cuenta en solo lectura (`mode=ro`), pidiendo columnas explícitas. Una cuenta sin la tabla del registro cuenta como 0 análisis, en lugar de fallar [prompt §Restricciones; H11].
- **RF-10b:** EL SISTEMA verificará el reloj de cada banco con `calibrate_clock_offset`. SI da `aligned=False`, ENTONCES informará el motivo y excluirá del veredicto los análisis de esa cuenta.
- **RF-10c:** EL SISTEMA evaluará solo los análisis que cumplan tres condiciones:
  - ancla ≥ `FECHA_GO_LIVE`;
  - D y A calculados en la fila `inicial` del registro;
  - ground truth resuelto;
  - origen del ancla `inicio`, es decir, un análisis nuevo o un clon en modo [1].
- **RF-10c-bis:** EL SISTEMA excluirá del conteo y del veredicto los análisis retroactivos y los clones en modo [2], porque al cargarlos el operador ya conoce lo que hizo el precio. Esos análisis igual se registran, y el reporte muestra cuántos se excluyeron por este motivo [decisión N3 del usuario, 2026-09-23].
- **RF-10d:** EL SISTEMA calificará cada análisis con el ground truth aprobado [D6; adenda D12; `core/p2_ground_truth.py`; §2.9]:
  - precio de partida: el cierre de la última vela 15M cerrada en el ancla;
  - niveles: `edge_validation_price` y `structural_invalidation`;
  - path de velas 1H con `time > ancla` y tope de 2160;
  - una vela que toca los dos niveles deja el resultado incompleto;
  - Choppy o un P2 igual a 0 cuenta como abstención (0), no como fallo.
- **RF-10e:** EL SISTEMA usará la fila `inicial` del registro para dos brazos: el del operador (su captura a ciegas) y el del consenso (el P2 efectivo de esa fila). Las ediciones posteriores al revelado no son a ciegas.
- **RF-10f:** EL SISTEMA mostrará para D, A, F, G, el operador y el consenso los aciertos sobre apuestas y el neto (aciertos − fallos). F y G se rotulan "exploratorio, sin regla de decisión" [D14; D15].
- **RF-10g:** MIENTRAS haya menos de `N_FORWARD` (40) análisis evaluables, EL SISTEMA imprimirá el progreso como `k/40`.
- **RF-10h:** CUANDO haya al menos `N_FORWARD` análisis evaluables, EL SISTEMA imprimirá la regla D5 textual y su veredicto sobre los primeros `N_FORWARD` en orden de ancla:
  - `val = +1` si acierta, `−1` si falla y `0` si se abstiene;
  - `d = val(D) − val(A)`, `up = #(d > 0)`, `dn = #(d < 0)`;
  - `p = binomial_test_two_sided(min(up, dn), up + dn)`, como en el cuaderno §8.6 (celda 51);
  - veredicto "recomendar pasar a A" si `dn > up` y `p < ALPHA`, con `ALPHA` = 0.05; "D se mantiene" en cualquier otro caso [D5; D15].
- **RF-10j:** CUANDO haya al menos `N_FORWARD` análisis evaluables, EL SISTEMA informará además, sobre esos mismos análisis, si D y el consenso aciertan más que el azar [adenda C2; decisión N5 del usuario, 2026-09-23]:
  - binomial de dos colas contra 0.5 sobre las apuestas de cada uno (`binomial_test_two_sided`), con los dos p ajustados por Holm (`holm_adjust`, `core/stats_tests.py:72`);
  - "supera al azar" si el p ajustado es menor que `ALPHA` y los aciertos superan el 50%; en cualquier otro caso, "no supera al azar todavía".
  - Solo informa: no cambia el veredicto de RF-10h ni el modelo activo (RF-11).
- **RF-10i:** SI `FECHA_GO_LIVE` no está configurada, ENTONCES EL SISTEMA terminará el reporte con código de salida ≠ 0 y un mensaje que lo indique.

## Clases de error (motivos registrados)

Ninguna bloquea el guardado (RF-9) y ninguna es silenciosa.

| Clase | Condición | Motivo registrado | Efecto sobre el banco |
|---|---|---|---|
| E1 | El export falla: MT5 cerrado, `mt5.initialize()` falla, falla la detección del offset (mercado cerrado), `powershell.exe` devuelve error, o se supera el timeout | `export_fallido:<detalle>` | Intacto (RF-15c) |
| E2 | `calibrate_clock_offset` da `aligned=False` sobre las velas nuevas | `reloj_desalineado` | Intacto |
| E3 | Menos de 800 velas cerradas antes del ancla en alguna TF de D (1W, 1D, 12H, 4H, 1H y 30M). Para una sombra solo afecta a esa sombra (RF-9b, RF-19b) | `historia_insuficiente:<TF>` | Se fusiona igual, si validó |
| E4 | Análisis no retroactivo cuya última vela 1H cerrada cerró más de `MAX_ANTIGUEDAD_1H_H` (2 h) antes del ancla. Con el mercado cerrado (fin de semana de XAU, US100 o US500) esto aplica: el modelo no corre | `velas_desactualizadas` | Se fusiona igual, si validó |
| E5 | El `asset` no está en `MT5_SYMBOL_MAP` (por ejemplo, un activo custom de una Flight Session) | `sin_simbolo_mt5` | No se exporta |
| E6 | La migración falla sobre la copia | Se detiene la implementación, sin motivo de runtime | No se tocan las DBs reales |
| E7 | Falla un test | Se detiene la tarea, sin motivo de runtime | — |
| E8 | Ancla retroactiva o de clon [2] fuera del banco, y MT5 no puede entregar esas velas | `sin_velas_para_ancla`, con un mensaje claro al operador | Intacto |
| E9 | Cuenta sin calibrar (RF-17) | `reloj_sin_calibrar` | No se exporta |

## Casos límite

- SI el score del operador es 0 (Neutral, o cualquier dirección con fuerza Weak), ENTONCES el P2 efectivo es 0 y EL SISTEMA calcula y registra todos los modelos igual (RF-4a).
- SI el P2 reescalado de D es 0, ENTONCES el P2 efectivo es 0 (RF-4a).
- SI el operador vuelve atrás hasta P2 después del revelado (`GoBackException` → `RestartFlowException`, `cli/main.py:240-253`), ENTONCES la nueva carga de P2 se registra como `edicion_p2`, no como captura a ciegas (RF-10e).
- SI el P2 efectivo deja `calc_edge` exactamente en 0.0, ENTONCES `tactical_classification` pasa a NA por la regla existente de `cli/schemas/tactical.py:59-60`. EL SISTEMA muestra ese cambio en la revisión del Step 3, y la regla no se modifica (H4).
- SI dos exports del mismo símbolo coinciden en el tiempo, por ejemplo con dos CLIs abiertos, ENTONCES el banco no queda corrupto ni pierde velas (RF-15, RF-15c).
- SI el análisis es de US100, ENTONCES hoy aplica E3 con el motivo `historia_insuficiente:1W`, porque USTEC tiene 737 velas 1W (§2.11). Para US500 lo confirma el spike (D7).
- Horas: todo se maneja en GT naive (UTC−6, sin DST). La WSL corre en `CST −0600` (H14).

## Requisitos no funcionales

- **NFR-1 (plataforma):** el exportador corre con el Python de Windows vía `powershell.exe` desde WSL. `MetaTrader5` no entra a `requirements.txt` (CLAUDE.md, "Acceso a APIs de broker").
- **NFR-2 (tiempo):** el export tiene un timeout configurable. Si el revelado de RF-3 llega antes de que termine el export, el operador espera con un indicador de progreso hasta el timeout. El plan decide si el export corre en segundo plano mientras el operador carga P3, P1 y P4.
- **NFR-3 (DB):**
  - La migración de las DBs reales es solo aditiva: una tabla o columnas nuevas, nunca `DROP`, ni `ALTER` de columnas existentes, ni reescritura de filas existentes [R12].
  - Como `init_db` migra al abrir cada DB (H12), las tres puertas de R12 (migración limpia sobre una copia, backup reciente y aprobación explícita) se cumplen **antes** de abrir el CLI con el código nuevo contra una cuenta real.
- **NFR-4 (tests):** la suite no lanza procesos externos ni toca MT5, `/mnt/c` ni `.data/`. Los tests usan SQLite `:memory:` o `tmp_path`, y velas de fixture.
- **NFR-5 (pruebas manuales):** todo flujo del wizard se prueba en una Flight Session descartable, nunca en una cuenta real [R13].

## Fuera de alcance

- El fix de look-ahead de `CsvOHLCProvider` y su test: van en la rama base, en otra sesión (adenda C1). Esta spec depende de él.
- Cambiar D, A o F, o meter 15M en el modelo activo (R7, adenda D11). La única alta al registro es G (RF-19).
- El bug de Choppy en `core/edge_analysis.py` y la regeneración del cuaderno y los reportes.
- Sincronizar el registro con Notion.
- Rellenar hacia atrás el consenso de los análisis existentes.
- Volver obligatorios los niveles al reparar análisis viejos (RF-18b).
- El cambio automático de modelo (RF-11).

## Criterios de finalización

- `validation.md` con veredicto `CONVERGE` en cada RF e INV, citando el test correspondiente.
- La suite completa en verde, con la salida mostrada.
- Una demo en una Flight Session con MT5 abierto que muestre: carga a ciegas → revelado de D → consenso → fila en el registro. Es posible porque la Flight Session hereda la calibración por símbolo (RF-17).
- Una demo con MT5 cerrado que muestre: P2 manual, motivo registrado y análisis guardado.
- La migración aplicada a las DBs reales, tras las tres puertas de R12.
- El reporte prospectivo corriendo sobre las DBs reales e imprimiendo `k/40`.

## Dudas abiertas

Ninguna pendiente. Resueltas por el usuario el 2026-09-23:
- **N1:** la fila P2 de `analysis_layer` guarda la opinión del operador, y el P2 efectivo va al registro y se muestra al lado (RF-23, RF-4d, RF-6c).
- **N2:** el modelo sombra con TFs cortas es G, con los pesos fijos de la adenda D14 (RF-19). Reemplaza la propuesta F+15M.
- **N3:** los retroactivos y los clones en modo [2] se registran, pero no cuentan para el test (RF-10c-bis).
- **N4:** la calibración del reloj se cuenta por símbolo (RF-17).
- **N5:** la regla de los 40 informa además si D y el consenso superan al azar (RF-10j).

## Consecuencias conocidas (no son defectos)

- En los análisis donde el P2 efectivo difiere del del operador, recalcular `calc_edge` desde las capas de `analysis_layer` no da el valor guardado, porque las capas guardan la opinión del operador (RF-23). Quien re-pondere el edge desde las capas, como el estudio de pesos de `tools/edge_evaluation.py` (`layer_scores`), usa la opinión del operador. Eso es correcto para medirlo a él, pero no reproduce el edge persistido.
