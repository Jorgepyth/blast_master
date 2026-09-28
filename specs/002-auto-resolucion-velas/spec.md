# Spec 002 — Auto-resolución con velas

- **Ruta:** brownfield-delta, fase B2.
- **Fecha:** 2026-09-25.
- **Base:** `158869b`.
- **Fuentes, en orden de prioridad:**
  1. las decisiones del usuario del 2026-09-25 en esta sesión (sección "Decisiones", N1–N42);
  2. las reglas R14–R17 y los hallazgos H-A a H-C que el usuario dio el 2026-09-24;
  3. el prompt `docs/prompts/2026-09-24-auto-resolucion-velas.md` (v1.2): R1–R12, D1–D5, E1–E7;
  4. el baseline `specs/002-auto-resolucion-velas/baseline.md` (§ y H citados).
- **Idioma:** la spec está en español. Todo lo que el sistema muestra o guarda va en inglés: mensajes del CLI,
  reportes, la vista del backfill, códigos de motivo y claves de configuración (N30; constitución, principio 6).

## Contexto y objetivo

Hoy el resultado de cada análisis lo carga el operador a mano, días después (baseline §2.2, §2.12). La auditoría
del 2026-09-24 mostró el sesgo que eso produce:
- el acierto con la etiqueta manual da 68.9% en XAU (31/45), contra 60.5% (23/38) con la resolución geométrica;
- los 7 análisis retroactivos acertaron 6 de 6.

Además, `resolution_time` nunca registró la resolución: es la hora en que se hizo el audit (baseline H1).

El feature usa las velas de MT5 para medir el resultado de cada análisis de forma objetiva:
- un banco de velas que nunca pierde historia;
- un resolvedor que calcula qué nivel tocó primero el precio, cuándo, y cuánto se movió en contra y a favor;
- propuestas editables en los wizards de Efficiency y de Tactical;
- un reporte de comparación contra lo cargado a mano;
- un backfill de los audits existentes.

El sistema propone y el operador confirma (D4). Nada del modelo de trading cambia (INV-4).

## Actores

- **Operador:** carga los análisis y los audits en el CLI.
- **Usuario analista:** es la misma persona. Exporta velas a mano, corre el reporte y aprueba el backfill.
- **MT5:** terminal de Windows y fuente de velas, en solo lectura. El export se dispara solo en momentos definidos
  (N31) o a mano.

## Historias de usuario

- H1: Como operador, quiero que el wizard me proponga cuándo y cómo se resolvió mi análisis, para no reconstruirlo
  de memoria días después.
- H2: Como analista, quiero saber cuánto coinciden mis etiquetas manuales con lo que hizo el precio, para medir mi
  sesgo.
- H3: Como analista, quiero un banco de velas que crezca y nunca pierda historia.
- H4: Como analista, quiero que `resolution_time` signifique lo mismo en todos los registros.

## Decisiones del usuario (2026-09-25)

| # | Decisión | Afecta a |
|---|---|---|
| N1 | Se crea `efficiency_audit.audit_registration_time` con la hora en que se guarda el audit, lo que hoy se guarda en `resolution_time`. **`resolution_time` pasa a significar la hora en que el precio tocó el primer nivel, según las velas** | RF-14, RF-7, RF-11 |
| N2 | En los audits existentes, la hora vieja se copia a `audit_registration_time`, y `resolution_time` pasa a la hora de las velas, o a vacío si no hay velas. **Es una excepción autorizada a R7/R9**, con las 3 puertas de R9 | RF-11 |
| N3 | Se crea `unified_department.analysis_start_time`. Se registra la primera vez que el operador carga la dirección y la fuerza de P0 en un análisis nuevo, porque un análisis que se abre y se cancela antes de P0 no cuenta | RF-13 |
| N4 | En los análisis existentes, que no tienen `analysis_start_time`, el ancla es `created_at − 20 min` | RF-4 |
| N5 | En los retroactivos y en los clones modo [2], el inicio es la hora que tipeó el operador | RF-13, RF-4 |
| N6 | En los clones modo [1], el inicio es el momento en que el operador elige "[1] Use Current System Time" | RF-13 |
| N7 | El exportador agrega 5M y 1M. El resolvedor usa la temporalidad más fina disponible y validada por el reloj | RF-15, RF-4 |
| N8 | El MAE/MFE táctico y `could_hit_tp` usan la TF más fina validada: 1M, si no 5M, si no 15M. Cuentan las mechas (máximo y mínimo de cada vela). *Reemplaza la versión anterior, "15M ahora, 5M después" (F3 [1][9]).* | RF-9, RF-10 |
| N9 | Un `resolution_type = "Open"` en un audit nunca hecho (`real_bias_b` NULL) cuenta como vacío | RF-5, RF-7, RF-11 |
| N10 | Sin reloj verificado (`aligned` distinto de `True`) no se resuelve ni se propone nada para ese símbolo | RF-2, RF-4 |
| N11 | **Overlap:** A queda "Overlap Invalidation" si se inicia otro análisis B de la misma cuenta después del inicio de A y antes de que el precio toque un nivel de A, cualquiera sea la dirección de B | RF-5 |
| N12 | `could_hit_tp` se evalúa desde la entrada hasta que el precio toca el SL, con tope `MAX_HORIZON` | RF-10 |
| N13 | "Confirmed pero inmediatamente revertido": el precio toca el objetivo y vuelve **hasta el Mark Price** dentro de 24 h | RF-8 |
| N14 | "Expansión significativa" si el precio corre ≥ 0.5R más allá del objetivo en las 24 h siguientes al toque; si no, "mínima" | RF-8 |
| N15 | "Liquidity Sweep": el precio toca la invalidación, la supera en 1R o menos y toca el objetivo dentro de 48 h | RF-8 |
| N16 | Tolerancia del Mark Price: 0.1% del precio | RF-3 |
| N17 | El camino hacia adelante arranca en la primera vela de 1M que abre en el ancla o después. Si esa fecha no tiene 1M, usa la siguiente más fina: 5M, 15M, 30M, 1H (F3 [1][1]) | RF-4 |
| N18 | En cada instante se usa la TF más fina validada que cubre ese instante. El cambio de TF se hace solo en el borde de la vela más gruesa, sin huecos ni superposición (F3 [1][2]) | RF-4 |
| N19 | La hora del toque es la hora de apertura de la vela que tocó, en la TF más fina usada. El reporte muestra la precisión (±1 min con 1M) (F3 [1][3]) | RF-4, RF-14 |
| N20 | La expansión de N14 se mide desde el toque del objetivo hasta lo que ocurra primero: 24 h o el toque de la invalidación (F3 [1][4]) | RF-8 |
| N21 | El exceso de N15 se mide entre el primer cruce de la invalidación y el toque del objetivo (F3 [1][5]) | RF-8b |
| N22 | Un análisis retroactivo cuenta como B para el Overlap, con su hora tipeada como inicio (F3 [1][6]) | RF-5 |
| N23 | `specific_bias_compliance` sigue manual. El reporte lo muestra como columna informativa al lado del resultado de las velas, sin porcentaje de coincidencia, para ver si la decisión se está tomando bien (F3 [1][7]) | RF-6 |
| N24 | Las cuentas reales son una lista fija en configuración (hoy 000, 001, 002 y 003). Una cuenta nueva, por ejemplo de otro activo, se agrega a la lista. Las Flight Sessions de prueba nunca cuentan (F3 [1][8]) | RF-2b, RF-6, RF-11 |
| N25 | `Resolution Time` puede quedar vacío: se afloja la validación del schema. El origen del valor, o el motivo por el que está vacío, se guarda en la columna nueva `resolution_time_source` (F3 [2][4]) | RF-7g, RF-14b |
| N26 | En un retroactivo gana la hora tipeada: RF-13b prevalece sobre RF-13 (F3 [2][3]) | RF-13, RF-13b |
| N27 | El backfill tiene control de versiones: una tabla `backfill_history` en cada DB, una vista estilo `git log --decorate --oneline --graph`, y la posibilidad de aceptar conflictos uno por uno. Los audits nunca hechos se llenan en sus campos objetivos (F3 [2][1], [2][2]) | RF-11b, RF-11c, RF-11e, RF-18, RF-19 |
| N28 | Si faltan velas: `pending_candles` cuando el banco todavía no llega, que es temporal y se resuelve con el próximo export; `no_history` cuando el tramo es anterior al inicio del banco. Las velas entran al banco solo con cada export, no con cada análisis (F3 [2][6]) | RF-4c, RF-4g, RF-9d |
| N29 | El reloj cuenta como verificado solo si al menos 10 precios de referencia caen dentro de las fechas exportadas (F3 [3][1]) | RF-2 |
| N31 | **Export automático, que reemplaza la restricción del prompt v1.2** ("el export se dispara a mano"). Se dispara al guardar un unified analysis (en segundo plano), al abrir un Efficiency o Tactical Audit (espera acotada), antes del reporte y del backfill, y al abrir el CLI (en segundo plano). Nunca bloquea nada. El comando manual sigue existiendo. Depende de un spike que verifique WSL → `powershell.exe` → Python de Windows con `MetaTrader5` → MT5; si el spike falla, el export sigue siendo manual (decisión del usuario, 2026-09-27) | RF-20 a RF-20f |
| N32 | Un export que repite velas ya verificadas del banco se verifica por superposición: esas velas tienen que coincidir exactamente. La regla de las 10 referencias (N29) queda para los exports sin superposición, como el primero de un símbolo (2026-09-27) | RF-2, RF-2d |
| N33 | Se registran `mark_price_time` (cuando se tipea el Mark Price; en retroactivos, la hora tipeada) y `saved_at` (la hora real de "Confirm & Save", también en retroactivos). El Mark Price se chequea con la escalera 1M → 5M → 15M en su minuto exacto, y en los análisis viejos con el período de `created_at − 20 min` a `created_at` (F3 [3][8]) | RF-3, RF-3b, RF-13d, RF-13e |
| N34 | Horario de verano: el exportador convierte cada vela con el desfase del servidor vigente en la fecha de esa vela, según el calendario de horario de verano del broker, en lugar de un solo desfase por corrida (F3 [3][9], opción A) | RF-15b, RF-15c |
| N35 | **Criterios de acierto.** Win rate principal **S1**: el primer toque de todos los análisis, sin límite de tiempo (hasta `MAX_HORIZON`). Win rate secundario **S4**: ventana de 48 h desde el ancla para todos, informando cuántos quedan fuera. Se descartan S2 (sacar los Overlap, maquilla el resultado), S3 (48 h solo para los Overlap, inconsistente) y S3b (48 h desde B, más complejo y sin cambios). Evidencia en `analisis-overlap-2d.md` (F3 [2][5]) | RF-5, RF-6, RF-21 |
| N36 | **Overlap es solo una etiqueta informativa** ("hubo un análisis nuevo antes de la resolución", regla N11/N22). **Nunca saca un análisis de las cifras de acierto.** Para cada Overlap, el reporte muestra qué nivel tocó primero el precio y cuál es el B (F3 [2][5], [2][6]) | RF-5, RF-6 |
| N37 | `resolution_time` es **siempre** la hora del primer toque, también en un Overlap. Reemplaza lo confirmado antes ("en un Overlap, la resolución es el inicio de B"). El momento del Overlap (ID de B y su inicio) va al reporte (F3 [2][5]) | RF-5, RF-14b |
| N38 | **P2 del modelo D registrado de forma automática y prospectiva.** RF-12 deja de ser opcional. En cada análisis nuevo (no retroactivo ni clon [2]), apenas el banco cubra su ancla, se calcula el P2 del modelo D con velas cerradas y se agrega **una** línea a `.data/p2_model_log.jsonl`. No se muestra en el wizard, no cambia ningún campo ni decisión (D3), y el comando `p2-model` sirve para verlo a pedido. Sirve para juntar datos limpios y decidir después, con S1, si una spec 003 integra un P2 sistemático (decisión del usuario, 2026-09-27) | RF-12 a RF-12d |
| N39 | El filtro por estación de horario (RF-15c) se aplica solo a 1H y a las TF menores, que son las que definen la hora del toque. 4H, 12H, 1D y 1W se importan completas, porque el desfase de 1 h es despreciable para EMA y ADX (`windows_export/export_p2_ohlc.py:31-39`) y el modelo D necesita 800 velas cerradas por TF | RF-15c, RF-12 |
| N40 | El catch-up del P2 del modelo D (RF-12b) registra **solo análisis nuevos**: los que tienen `analysis_start_time` y no son retroactivos ni clones [2]. Los 118 históricos no entran al registro prospectivo (F6 K2, opción a) | RF-12b |
| N41 | La migración aditiva de las DBs reales (columnas nuevas y `backfill_history`) pasa por las 3 puertas de R9 **antes** de correr cualquier comando del CLI nuevo en el checkout principal, porque `init_db` migra al abrir cada DB (F6 K1) | NFR-1 |
| N42 | **Los modelos registrados salen de la configuración, no del código.** Amplía N38. `P2_LOG_MODELS` dice qué modelos del registro de `tools/p2_backtest.py` se registran y desde qué fecha cada uno. Hoy es solo D. Un modelo nuevo (por ejemplo H) se agrega a la lista con la fecha del día en que entra, y convive con D: los dos se registran sobre los mismos análisis, en líneas separadas. Cada modelo se registra **solo** en los análisis con `analysis_start_time` igual o posterior a su fecha de alta, para que un modelo diseñado mirando los datos no se evalúe sobre esos mismos datos (sesgo de selección, como F frente a D en la spec 001). La receta de un modelo de la lista no se modifica: un cambio es un modelo nuevo, con otro nombre. Cada línea guarda la receta completa y su huella, así que dos recetas distintas nunca se mezclan (decisión del usuario, 2026-09-28) | RF-12 a RF-12e |
| N30 | Todo lo que el sistema muestra o guarda va en inglés, incluidos los códigos de motivo y las claves de configuración. Las specs, la documentación, los cuadernos y la conversación van en español (F3 [4][1]) | Todos |

Definición usada en N13–N15:
- **R** = |precio de partida − `structural_invalidation`|;
- el **precio de partida** es el cierre de la última vela cerrada en el ancla (15M, o la menor TF disponible).

La regla de 0.5R en 24 h coincide con 35 de 42 etiquetas pasadas, y la de Liquidity Sweep con 3 de 4. Es un
análisis exploratorio en solo lectura, sobre velas 15M/1H de XAU y BTC.

## Comportamiento actual (a preservar)

- **INV-1:** EL SISTEMA seguirá pidiendo, en el wizard de Efficiency, los mismos campos en el mismo orden que hoy
  (baseline §2.2). La única excepción es el prompt nuevo "Resolution Time" (RF-7c), que va después de "Structural
  MFE". En el wizard de Tactical pedirá los mismos campos en el mismo orden (baseline §2.3).
- **INV-2:** EL SISTEMA guardará un análisis o un audit aunque no haya velas, el reloj no calibre o el resolvedor
  falle (E1–E5).
- **INV-3:** EL SISTEMA no modificará ningún valor existente en las DBs reales. Hay una sola excepción, autorizada
  (N2): el movimiento de `resolution_time` a `audit_registration_time` y la escritura del `resolution_time`
  de velas (RF-11c).
- **INV-4:** EL SISTEMA no cambiará `calc_edge`, bias, probabilidades, tier ni gates. Tampoco tocará los archivos
  de "No toques" del prompt: `core/math_engine.py`, `determine_market_bias` y su umbral, los gates Tier D/F,
  emocional y Stop Deviation, `tools/notion_sync.py`, `core/edge_analysis.py`, P0–P4 y los modelos A–G de
  `tools/p2_backtest.py`.
- **INV-5:** EL SISTEMA mantendrá la suite en verde. Referencia: 429 passed, 1 skipped (baseline, encabezado).
  - Un test existente que cambie de comportamiento por un RF de esta spec se ajusta en la tarea de ese RF,
    citándolo.
  - Ningún otro test se modifica.
- **INV-6:** EL SISTEMA mantendrá legibles los CSV de velas actuales (`{dir}/{TF}.csv`) para `CsvOHLCProvider`,
  el cuaderno y `tools/edge_evaluation.py`, o los migrará con su propio test (R4; baseline §3).
- **INV-7:** EL SISTEMA seguirá pidiendo en `flow_new_analysis` los mismos campos en el mismo orden. Registrar
  `analysis_start_time` (RF-13) no agrega ningún prompt.
- **INV-8:** EL SISTEMA dejará manuales `bias_a`, `real_bias_b`, `specific_bias_compliance`,
  `false_regime_rate`, las emociones, los gates G1–G7, las confirmaciones C1–C8, `lesson_learned`,
  `followed_plan`, `market_state`, `htf_trend_context` y `ltf_trend_context` (R8).

## Comportamiento nuevo o corregido

### Aislamiento de los tests (primera tarea, R16)

- **RF-16:** EL SISTEMA hará fallar la suite si algún test crea, abre o escribe un archivo bajo un `.data/`
  relativo a la carpeta de trabajo, o cualquier archivo fuera de `tmp_path` o de `:memory:`. Esto cubre
  `.data/flight_sessions.json`, `.data/*.db` y `.data/paused_audits.json` (baseline §2.13, H19).
- **RF-16b:** EL SISTEMA corregirá `tests/test_cli_report_command.py:71-75` y cualquier otro test que RF-16 detecte,
  para que corran sin tocar `.data/`. No se cambia el comportamiento de `get_active_engine()` en producción.

### Horas del análisis y del audit

- **RF-13:** CUANDO el operador confirme por primera vez la fuerza de P0 en un análisis nuevo, EL SISTEMA
  registrará esa hora (GT naive) como `unified_department.analysis_start_time` (N3).
  - La hora no cambia con los reinicios internos del wizard (`RestartFlowException`), ni si el operador vuelve a
    editar P0.
  - Si el análisis se descarta, no se guarda nada.
- **RF-13b:** DONDE el análisis sea retroactivo o un clon en modo [2], EL SISTEMA guardará en
  `analysis_start_time` la hora que tipeó el operador (N5). Esta regla prevalece sobre RF-13 (N26).
- **RF-13c:** DONDE el análisis sea un clon en modo [1], EL SISTEMA guardará en `analysis_start_time` la hora en que
  el operador eligió "[1] Use Current System Time" (N6).
- **RF-13d:** CUANDO el operador ingrese el Mark Price, EL SISTEMA registrará esa hora como
  `unified_department.mark_price_time`. En los retroactivos y los clones en modo [2] registrará la hora tipeada (N33).
- **RF-13e:** CUANDO el operador confirme "Confirm & Save" de un análisis, EL SISTEMA registrará la hora real del
  reloj como `unified_department.saved_at`, también en los retroactivos, donde `created_at` guarda la hora tipeada
  (N33).
- **RF-14:** CUANDO el operador guarde un Efficiency Audit, EL SISTEMA guardará en
  `efficiency_audit.audit_registration_time` la hora del guardado. Ese valor es el que hoy se guarda en
  `resolution_time` (`cli/main.py:3222`) (N1).

### Banco de velas y reloj

- **RF-15:** EL SISTEMA exportará 1W, 1D, 12H, 4H, 1H, 30M, 15M, 5M y 1M por símbolo (N7).
  - Si MT5 no entrega una temporalidad (por ejemplo, poca historia de 1M), el export informa cuántas velas trajo
    y sigue con las demás.
- **RF-15b:** EL SISTEMA convertirá la hora de cada vela exportada con el desfase del servidor vigente en la fecha de
  esa vela, según el calendario `BROKER_DST_RULE`, en lugar de un solo desfase para toda la corrida
  (`windows_export/export_p2_ohlc.py:31-39`) (N34).
- **RF-15c:** SI `BROKER_DST_RULE` no está verificado, ENTONCES EL SISTEMA, en 1H y en las TF menores, solo
  fusionará las velas de la misma estación de horario que el momento del export, e informará `clock_unverified`
  para las demás. 4H, 12H, 1D y 1W se fusionan completas (N34, N39).
- **RF-1:** CUANDO el usuario ejecute el export de un símbolo, EL SISTEMA fusionará las velas nuevas con el banco de
  ese símbolo, sin borrar ni alterar ninguna vela existente y sin duplicar por `time` (R4).
  - Un test tiene que fallar si una vela previa desaparece o cambia, o si la cobertura hacia atrás se reduce.
- **RF-1b:** SI un export trae una vela con un `time` que ya existe en el banco, ENTONCES EL SISTEMA conservará la
  vela existente.
- **RF-1c:** SI un export falla o no valida, ENTONCES EL SISTEMA dejará el banco byte a byte igual e informará
  `export_failed` o `clock_misaligned` (E1, E2).
- **RF-1d:** SI ya hay un export en curso del mismo símbolo, ENTONCES EL SISTEMA cancelará el segundo con un mensaje
  y dejará el banco intacto (candado por símbolo; F3 [3][6]).
- **RF-2:** CUANDO termine un export, EL SISTEMA correrá `calibrate_clock_offset` sobre las velas nuevas y reportará
  el resultado del símbolo.
  - Solo fusiona si el reloj queda verificado por uno de dos caminos:
    - **por superposición** (RF-2d), si el export repite velas que ya están en el banco (N32);
    - si no hay superposición, con `aligned=True` **y** al menos 10 precios de referencia dentro de las fechas
      exportadas (N29; F3 [3][1]).
  - Si no se verifica por ninguno de los dos, informa `clock_unverified` y no fusiona.
  - Con `aligned=False` informa `clock_misaligned`.
  - Con `aligned=None` informa `clock_unverified` y la cantidad de precios de referencia que faltan para llegar
    a 10 (N10).
- **RF-2d:** CUANDO un export repita velas que ya están en el banco, EL SISTEMA comparará esas velas en 1H y en las TF
  más finas. Si al menos `OVERLAP_MIN_BARS` velas por TF coinciden exactamente (mismo `time`, `open`, `high`, `low` y
  `close`), el reloj queda verificado. Si alguna no coincide, informa `clock_misaligned` y no fusiona (N32).
  - El exportador aplica un solo desfase del servidor a toda la corrida (`windows_export/export_p2_ohlc.py:31-39`).
    Por eso, las velas de la estación de horario opuesta a la del export quedan corridas 1 h, y una superposición
    que cruzara un cambio de horario no coincidiría. Lo resuelve RF-15b (N34).
- **RF-2b:** EL SISTEMA contará los precios de referencia del reloj por símbolo MT5, sumando las cuentas de
  `REAL_ACCOUNTS` (N24) cuyo `asset` mapea a ese símbolo, leídas en `mode=ro`.
- **RF-2c:** CUANDO se cree el banco por primera vez, EL SISTEMA importará los CSV actuales de cada símbolo solo si
  `calibrate_clock_offset` da `aligned=True` sobre ellos.
  - **El `5M.csv` actual de XAU no se importa:** es anterior a la corrección del reloj (baseline H11). Se informa
    como excluido y no se borra.

### Resolvedor (solo lectura)

- **RF-4:** EL SISTEMA calculará, para cada análisis con `edge_validation_price` y `structural_invalidation`:
  - **ancla:** `analysis_start_time` si existe; si no, `created_at − 20 min` (N4). En los retroactivos,
    `created_at` ya es la hora tipeada (baseline H7).
  - **precio de partida:** el cierre de la última vela ya cerrada en el ancla (`time + duración(TF) <= ancla`), en
    la TF más fina validada (R3);
  - **dirección de la tesis:** `infer_thesis_direction(precio de partida, EVP, SI)`;
  - **primer nivel tocado y hora del toque:** recorriendo desde la primera vela de 1M que abre en el ancla o
    después, con la TF más fina validada en cada instante: 1M, 5M, 15M, 30M, 1H (R3, N7, N17, N18). La hora del
    toque es la apertura de la vela que tocó (N19);
  - **MAE y MFE estructurales, en precio:** el precio más adverso y el más favorable a la tesis entre el ancla y la
    resolución;
  - **R** y las medidas de N13–N15.
- **RF-4b:** SI una misma vela toca los dos niveles aun en la TF más fina disponible, ENTONCES EL SISTEMA marcará el
  análisis como `ambiguous` y no propondrá tipo ni hora (E5).
- **RF-4c:** SI el banco todavía no llega hasta el primer toque o hasta el fin del horizonte, ENTONCES EL SISTEMA
  marcará `pending_candles` y no propondrá nada. Se resuelve con el próximo export. Esto no es `Open` (E3; baseline
  H12; N28).
- **RF-4g:** SI el ancla es anterior a la vela más vieja del banco, ENTONCES EL SISTEMA marcará `no_history` y no
  propondrá nada (E3b; N28).
- **RF-4h:** SI el `asset` del análisis no está en `MT5_SYMBOL_MAP`, ENTONCES EL SISTEMA marcará `no_mt5_symbol` y no
  lo resolverá (E8; F3 [3][5]).
- **RF-4d:** SI al análisis le falta EVP o SI, o los dos niveles caen del mismo lado del precio de partida,
  ENTONCES EL SISTEMA marcará `no_levels` (E4).
- **RF-4e:** MIENTRAS el reloj del símbolo no esté verificado (`aligned` distinto de `True`, RF-2), EL SISTEMA no
  resolverá los análisis de ese símbolo y marcará `clock_unverified` o `clock_misaligned` (R5, N10).
- **RF-4f:** EL SISTEMA reutilizará `CsvOHLCProvider`, `closed_bars`, `calibrate_clock_offset`,
  `infer_thesis_direction`, `first_touch_direction` y `open_readonly_session`, extendiéndolos solo de forma aditiva.
  Ninguna firma pública existente cambia su resultado para sus llamadores actuales (baseline H9, H10).

### Tipo de resolución

- **RF-5:** EL SISTEMA derivará el `resolution_type` propuesto con este orden de prioridad:
  1. **Overlap Invalidation:** existe otro análisis B de la misma cuenta cuya ancla es posterior a la de A y
     anterior al primer toque de A, o anterior al fin del horizonte si A no tocó nada (N11). B puede ser
     retroactivo; en ese caso su ancla es la hora tipeada (N22).
     - La etiqueta es **informativa** (N36). `resolution_time` sigue siendo la hora del primer toque de A (N37), y
       el primer toque cuenta en las cifras de acierto igual que en cualquier otro análisis (N35).
     - Si el Overlap ya se sabe dentro del tramo con velas pero el primer toque de A todavía no llegó, se propone la
       etiqueta, y `resolution_time` queda vacío con `pending_candles` (F3 [2][6]).
  2. **Confirmed:** el primer nivel tocado es `edge_validation_price`.
  3. **Invalidated:** el primer nivel tocado es `structural_invalidation`.
  4. **Open:** ningún nivel tocado dentro de `MAX_HORIZON`, con el banco cubriendo todo el horizonte.
- **RF-5b:** SI el resultado es `Open`, ENTONCES EL SISTEMA no propondrá `resolution_type`, porque el wizard no
  permite elegir `Open` (baseline H2). Mostrará en una línea "Still open according to candles".

### Pre-llenado del Efficiency Audit

- **RF-7:** CUANDO el operador abra el Efficiency Audit de un análisis resuelto por RF-4/RF-5, EL SISTEMA mostrará
  como valor por defecto editable, marcado `(auto)`:
  - **a)** `Resolution Type`;
  - **b)** `Structural MAE` y `Structural MFE` (precios);
  - **c)** `Resolution Time`, en un prompt nuevo después de `Structural MFE` (N1, INV-1);
  - **d)** `Structural Resolution` y `Failure Reason`, según RF-8.
- **RF-7e:** EL SISTEMA guardará un valor propuesto solo si el operador pasó por el campo: lo acepta con Enter o lo
  corrige (R6). Nunca lo guarda sin pedírselo.
- **RF-7f:** SI no hay resultado del resolvedor para ese análisis, ENTONCES EL SISTEMA pedirá los campos como hoy,
  sin default, y mostrará el motivo en una línea (`pending_candles`, `no_history`, `ambiguous`, `clock_unverified`, etc.).
- **RF-7g:** EL SISTEMA aceptará `Resolution Time` vacío. El schema `EfficiencyAudit` pasa a
  `resolution_time: Optional[datetime]` (N25).
- **RF-14b:** CUANDO se guarde o se llene `resolution_time`, EL SISTEMA guardará en la columna nueva
  `efficiency_audit.resolution_time_source` de dónde salió el valor o por qué está vacío (N25):
  - `candles`: el valor salió de las velas y el operador lo aceptó;
  - `corrected`: el operador cambió el valor propuesto;
  - `pending_candles`, `no_history`, `clock_unverified`, `clock_misaligned`, `ambiguous`, `no_levels`,
    `no_mt5_symbol` u `open`: el campo está vacío, por ese motivo.

### Reglas objetivas de Structural Resolution y Failure Reason

- **RF-8:** DONDE el tipo propuesto sea Confirmed, EL SISTEMA propondrá `Structural Resolution` con esta
  prioridad:
  1. **"Confirmed pero inmediatamente revertido":** el precio vuelve al Mark Price dentro de las 24 h
     siguientes al toque del objetivo (N13).
  2. **"Confirmed + expansión significativa":** el precio corre ≥ 0.5R más allá del objetivo, medido desde el toque
     hasta lo que ocurra primero: 24 h o el toque de la invalidación (N14, N20).
  3. **"Confirmed pero mínima":** en cualquier otro caso.

  En los tres casos propone `Failure Reason` = "N/A".
- **RF-8b:** DONDE el tipo propuesto sea Invalidated, EL SISTEMA propondrá `Structural Resolution` = "N/A". Además,
  propondrá `Failure Reason` = "Liquidity Sweep" si el precio superó la invalidación en ≤ 1R y tocó el objetivo
  dentro de las 48 h siguientes (N15). El exceso se mide entre el primer cruce de la invalidación y el toque del
  objetivo (N21). En cualquier otro caso no propone `Failure Reason`: Reversal, Regime Decay y
  Range Expansion siguen manuales.
- **RF-8c:** DONDE el tipo propuesto sea Overlap Invalidation, EL SISTEMA propondrá `Structural Resolution` = "N/A"
  y `Failure Reason` = "Overlap -- nuevo bias antes de resolucion".
- **RF-8d:** SI el análisis no tiene Mark Price, ENTONCES EL SISTEMA usará el precio de partida de RF-4 para la regla
  de RF-8.

### Pre-llenado del Tactical Audit

- **RF-9:** CUANDO el operador llegue a los prompts de `MAE` y `MFE` de una orden llenada, con `entry_time`,
  `exit_time`, `entry_price` y `stop_loss` cargados, EL SISTEMA propondrá los dos valores en R:
  - `R = |entry_price − stop_loss|`;
  - calculados con la TF más fina validada (1M, si no 5M, si no 15M), sobre las velas que se solapan con
    `[entry_time, exit_time]`, contando las mechas (N8);
  - redondeados a 2 decimales y limitados a 10, el máximo del campo (baseline §2.5).
- **RF-9b:** SI `exit_time` es igual o anterior a `entry_time`, ENTONCES EL SISTEMA no propondrá MAE ni MFE y mostrará
  `no_interval`. Ese patrón corresponde a un trade que en realidad no se ejecutó (F3 [3][2]).
- **RF-9c:** SI `entry_price` es igual a `stop_loss`, ENTONCES EL SISTEMA no propondrá MAE, MFE ni `Could hit TP?` y
  mostrará `zero_r` (F3 [3][3]).
- **RF-9d:** SI el banco no cubre el período del trade (de la entrada a la salida para MAE/MFE; de la entrada al SL
  para `Could hit TP?`), ENTONCES EL SISTEMA no propondrá esos campos y mostrará `pending_candles` o `no_history`
  (N28; F3 [3][4]).
- **RF-10:** CUANDO el operador llegue al prompt `Could hit TP?`, EL SISTEMA propondrá "yes" si el precio tocó
  `take_profit` entre la entrada y el primer toque del `stop_loss` (tope `MAX_HORIZON`), y "no" si no lo tocó
  (N12). Recorre con la TF más fina validada (N8).
- **RF-10b:** SI el TP y el SL se tocan en la misma vela de la TF más fina disponible, ENTONCES EL SISTEMA no
  propondrá `Could hit TP?`.
- **RF-10d:** SI el `take_profit` está del lado contrario de la tesis (en un Long, por debajo de la entrada; en un
  Short, por encima), ENTONCES EL SISTEMA no propondrá `Could hit TP?` y mostrará `invalid_tp` (F3 [3][7]).
- **RF-10c:** EL SISTEMA no agregará nada para `session`: ya se calcula sola desde `entry_time` (baseline H5).

### Validación del Mark Price

- **RF-3:** CUANDO el operador guarde un análisis con Mark Price, SI el banco cubre `mark_price_time`, EL SISTEMA
  buscará la vela que contiene `mark_price_time` empezando en 1M y subiendo a 5M y 15M hasta que el Mark Price caiga
  en su rango (N33).
  - Si no cae ni en la vela de 15M con una tolerancia de 0.1% del precio (N16), muestra una advertencia de una
    línea.
  - Nunca bloquea el guardado.
- **RF-3b:** EL SISTEMA revisará en el reporte (RF-6) los análisis sin `mark_price_time`. Para cada uno comprobará si
  el Mark Price cae dentro del rango de precios entre `created_at − 20 min` y `created_at` (1M, si no 5M, si no
  15M), con 0.1% de tolerancia (N33).

### Reporte de comparación (solo lectura)

- **RF-6:** CUANDO el usuario ejecute el reporte de comparación, EL SISTEMA mostrará, por cada cuenta de
  `REAL_ACCOUNTS` (N24):
  - n resueltos, % faltante y motivo (`pending_candles`, `no_history`, `no_levels`, `ambiguous`, `clock_unverified`, `no_mt5_symbol`);
  - la coincidencia entre lo automático y lo manual en `resolution_type`, `structural_mae`/`structural_mfe`,
    `structural_resolution` y `failure_reason`;
  - `specific_bias_compliance` como columna informativa al lado del resultado de las velas, sin porcentaje de
    coincidencia (N23);
  - la demora del audit, `audit_registration_time − resolution_time` (hoy `resolution_time − toque`);
  - la lista de los análisis que difieren;
  - el **win rate S1** (principal) y el **win rate S4** (secundario, con cuántos quedan fuera), para todos los análisis y
    para los direccionales (Bullish/Bearish), junto al win rate manual sobre los mismos análisis (N35, RF-21);
  - para cada Overlap: qué nivel tocó primero, a qué hora, y el ID y el inicio de B (N36, N37);
  - los Mark Price que no coinciden con sus velas (RF-3, RF-3b);
  - en los retroactivos con `saved_at`, cuánto después del hecho se cargaron (`saved_at − created_at`) (N33, R11).
- **RF-6b:** EL SISTEMA abrirá cada DB en `mode=ro`, pedirá columnas explícitas y tratará una columna ausente como
  vacía, sin fallar (baseline H20).
- **RF-21:** EL SISTEMA definirá las métricas S1 y S4 y la regla de Overlap en un solo módulo reutilizable,
  documentado, que usarán el reporte (RF-6) y cualquier análisis de los cuadernos (`jupyter/*.ipynb`). La
  definición en palabras vive en `docs/criterios-de-acierto.md`, y `CLAUDE.md` apunta a ese documento para que
  todo análisis futuro use los mismos criterios (N35, N36; pedido explícito del usuario, 2026-09-27).
- **RF-17:** EL SISTEMA excluirá los análisis retroactivos (`is_backdated = 1`) de toda cifra de acierto, e informará
  cuántos excluyó (R11).

### Backfill

- **RF-11:** CUANDO el usuario ejecute el backfill, EL SISTEMA correrá en modo dry-run por defecto sobre las cuentas
  de `REAL_ACCOUNTS` (N24) y listará, por cuenta y campo, los valores que llenaría y las diferencias con los valores manuales.
- **RF-11b:** DONDE se pase `--apply`, y solo después de las 3 puertas de R9, EL SISTEMA llenará los campos vacíos
  con el resultado de RF-4 a RF-10. "Vacío" significa NULL; además, `resolution_type = "Open"` con `real_bias_b`
  NULL cuenta como vacío (N9).
  - Incluye los audits nunca hechos: se llenan los campos objetivos, y los manuales (`real_bias_b` y los demás de
    INV-8) quedan vacíos. El análisis conserva su estado del ciclo de vida, así que el wizard los sigue pidiendo
    (N27; F3 [2][2]).
  - Nunca sobrescribe por su cuenta un valor manual existente: lo muestra como conflicto (R7), salvo que el
    usuario lo acepte uno por uno (RF-11e).
- **RF-11c:** DONDE se pase `--apply`, EL SISTEMA hará la excepción N2 para cada fila de `efficiency_audit` con
  `resolution_time` no NULL y `audit_registration_time` vacío, es decir, las que todavía guardan la hora vieja del
  audit (F3 [2][1]):
  - copiará `resolution_time` a `audit_registration_time`, si este último está vacío;
  - después pondrá en `resolution_time` la hora de resolución de RF-4/RF-5, o NULL si no hay resultado.

  Antes de escribir, el backup de R9 debe contener el valor original. En las filas que ya tienen
  `audit_registration_time`, un `resolution_time` distinto del de las velas es un conflicto: se muestra y no se
  aplica, salvo aceptación (RF-11e). Por eso correr el backfill dos veces no pisa lo ya confirmado.
- **RF-11d:** SI la operación sobre la copia de alguna DB falla, ENTONCES EL SISTEMA no tocará ninguna DB real (E6).
- **RF-11e:** CUANDO el usuario revise los conflictos en la pantalla del backfill, EL SISTEMA le permitirá aceptarlos
  uno por uno. Un conflicto aceptado se aplica y se registra en el historial (RF-18) con su valor anterior. Los que
  no acepta no se tocan (N27; F3 [2][1]).
- **RF-18:** EL SISTEMA registrará cada cambio que aplique el backfill, incluidos los conflictos aceptados, en una
  tabla nueva `backfill_history` de la DB de cada cuenta. Cada fila guarda el id de la corrida, la fecha, la tabla,
  el id del registro, el campo, el valor anterior, el valor nuevo y el origen. La tabla solo acepta inserciones:
  nunca se modifica ni se borra una fila (N27).
- **RF-19:** EL SISTEMA mostrará la vista previa del backfill y su historial con el formato de
  `git log --decorate --oneline --graph` (N27):
  - una línea por corrida, con decoraciones: la cuenta, `HEAD` en la más reciente y `dry-run` en las vistas
    previas;
  - debajo, una línea por cambio: id del registro, campo, valor anterior → valor nuevo, y origen;
  - en verde lo que se llena, en amarillo y con `!` los conflictos, y en gris lo que no cambia;
  - un campo vacío se muestra como `—`.

### Export automático (N31)

Todo este grupo depende del spike de interoperabilidad (ver "Dependencias"). Mientras `AUTO_EXPORT` esté
desactivado, nada de este grupo corre y el export sigue siendo manual.

- **RF-20:** CUANDO el operador guarde un unified analysis, EL SISTEMA lanzará el export del símbolo en segundo plano,
  sin demorar el guardado ni el resto del wizard.
- **RF-20b:** CUANDO el operador abra un Efficiency o Tactical Audit, EL SISTEMA lanzará el export del símbolo y
  esperará hasta `AUTO_EXPORT_WAIT_S` segundos, mostrando el progreso. Si no termina, seguirá con las velas que ya
  tiene el banco.
- **RF-20c:** CUANDO el usuario ejecute el reporte de comparación o el backfill, EL SISTEMA exportará antes los símbolos
  de `REAL_ACCOUNTS`, con la misma espera acotada.
- **RF-20d:** CUANDO se abra el CLI, EL SISTEMA lanzará en segundo plano el export de los símbolos de `REAL_ACCOUNTS`.
- **RF-20e:** SI MT5 no está disponible (cerrado, sin sesión, mercado cerrado o exportador ausente), o el export supera
  `EXPORT_TIMEOUT_S`, ENTONCES EL SISTEMA mostrará una línea "Candle export skipped: <reason>" (`export_failed`),
  dejará el banco intacto y continuará sin bloquear (INV-2).
- **RF-20f:** SI ya hay un export del mismo símbolo en curso, ENTONCES EL SISTEMA no lanzará otro (RF-1d). Esto incluye
  los exports automáticos.

### Registro prospectivo del P2 sistemático (N38, N42)

- **RF-12:** CUANDO el operador guarde un unified analysis nuevo (no retroactivo ni clon en modo [2]) y el banco
  cubra su ancla, EL SISTEMA calculará el P2 de cada modelo de `P2_LOG_MODELS` cuya fecha de alta sea igual o
  anterior al `analysis_start_time` del análisis. Usará la receta del registro de modelos de `tools/p2_backtest.py`
  sin modificarla (hoy, `MODEL_D`) y solo velas cerradas en el ancla. Agregará a `.data/p2_model_log.jsonl` **una
  línea por modelo** con el `trade_id`, la cuenta, el ancla, el P2 del operador (dirección, fuerza y score), el modelo
  (nombre, receta completa y huella), el P2 del modelo (crudo y reescalado) y el detalle por TF (EMAs, ADX, DI y
  peso). No lo mostrará en el wizard y no cambiará ningún campo ni decisión (D3).
- **RF-12b:** CUANDO el banco de un símbolo se actualice (RF-1), EL SISTEMA registrará, para cada modelo de
  `P2_LOG_MODELS`, los análisis **nuevos** de `REAL_ACCOUNTS` (con `analysis_start_time`, no retroactivos ni clones
  en modo [2]) que cumplan tres condiciones: su `analysis_start_time` es igual o posterior a la fecha de alta de ese
  modelo (N42), todavía no tienen línea de ese modelo y su ancla ya está cubierta (catch-up). Hay como máximo una línea
  por análisis y modelo. Los análisis históricos, que no tienen `analysis_start_time`, nunca se registran (N40).
- **RF-12c:** SI un modelo no se puede calcular, ENTONCES EL SISTEMA registrará su línea con el motivo, por ejemplo
  `insufficient_history:1W` o `pending_candles`. Las líneas `pending_candles` se reintentan en el siguiente catch-up.
- **RF-12d:** CUANDO el usuario ejecute `p2-model --trade-id ID [--model NAME]`, EL SISTEMA mostrará las líneas
  registradas de ese análisis, una por modelo. Si falta la de un modelo, o si `--model` pide uno del registro que no
  está en la lista, la calculará a pedido y la mostrará marcada como `not logged`, **sin escribirla**. Solo el
  guardado (RF-12) y el catch-up (RF-12b) escriben el registro.
- **RF-12e:** SI `P2_LOG_MODELS` nombra un modelo que no existe en el registro, o un modelo que usa una TF que el
  banco no guarda (RF-15), ENTONCES EL SISTEMA no registrará ese modelo, mostrará un aviso de una línea
  (`unknown_model:<NAME>` o `timeframe_not_in_bank:<TF>`) y seguirá registrando los demás. SI la receta actual de un
  modelo de la lista no coincide con la huella de sus líneas ya registradas, ENTONCES EL SISTEMA dejará de registrarlo
  y avisará `model_recipe_changed:<NAME>` (N42). Ninguno de estos casos bloquea el guardado (INV-2).

## Clases de error (motivos visibles)

Ninguna bloquea el guardado. Todas dejan el motivo visible.

| Clase | Condición | Motivo |
|---|---|---|
| E1 | El export falla o MT5 está cerrado | `export_failed` (banco intacto) |
| E2 | El reloj da `aligned=False` | `clock_misaligned` (no se fusiona ni se resuelve) |
| E2b | Menos de 10 precios de referencia dentro de las fechas exportadas | `clock_unverified` (no se fusiona ni se resuelve) |
| E3 | El banco todavía no llega al tramo necesario | `pending_candles` (temporal) |
| E3b | El tramo es anterior a la vela más vieja del banco | `no_history` |
| E4 | Faltan niveles, o los dos caen del mismo lado | `no_levels` |
| E5 | Una vela toca los dos niveles aun en la TF más fina | `ambiguous` |
| E6 | Falla la operación sobre la copia de la DB | Se detiene sin tocar las reales |
| E7 | Falla un test | La tarea no se marca como hecha |
| E8 | El `asset` no está en `MT5_SYMBOL_MAP` | `no_mt5_symbol` |
| E9 | El trade no permite calcular: salida no posterior a la entrada, entrada igual al SL, o TP del lado contrario | `no_interval`, `zero_r`, `invalid_tp` |

## Casos límite

- **US500** no tiene velas ni reloj verificado (4 referencias), así que queda `clock_unverified` o `pending_candles`
  hasta que haya export y calibración. Su símbolo MT5, `US500`, no está verificado: se confirma en el primer export.
- **US100/USTEC** tiene 9 referencias, así que queda `clock_unverified` hasta tener 10.
- **Frecuencia de export:** el banco crece solo con cada export manual, no con cada análisis. Si se exporta seguido,
  `pending_candles` casi no aparece.
- **Análisis recientes** cuyo horizonte todavía no terminó y que no tocaron nada: `pending_candles`, no `Open`.
- **BTC de julio**, anterior al 07-12: sin 15M, el tramo se resuelve con 30M o 1H (RF-4).
- **1 fila de XAU con `failure_reason = "Reversal"`**, un valor fuera del enum: el backfill y el reporte la leen
  como texto, sin validarla contra el enum (baseline H22).
- **El operador vuelve atrás o edita un campo propuesto:** vale el valor que eligió.
- **Horas:** todo en GT naive (UTC−6, sin DST).

## Configuración

| Clave | Valor |
|---|---|
| `MAX_HORIZON` | 2160 velas de 1H después del ancla, igual que el backtest (~90 días en un mercado 24/7) |
| `ANCHOR_FALLBACK_MIN` | 20 (N4) |
| `MARK_PRICE_TOLERANCE` | 0.1% (N16) |
| `REVERT_H` / `EXPANSION_H` / `EXPANSION_R` | 24 h / 24 h / 0.5 (N13, N14) |
| `SWEEP_R` / `SWEEP_H` | 1.0 / 48 h (N15) |
| `MT5_SYMBOL_MAP` | `XAUUSDT.P→XAUUSD`, `BTCUSDT.P→BTCUSD`, `US100→USTEC`, `US500→US500` |
| `AUTO_EXPORT` | Activado solo si el spike funciona (N31) |
| `AUTO_EXPORT_WAIT_S` / `EXPORT_TIMEOUT_S` | Se fijan con la duración que mida el spike. Valores iniciales: 30 s / 180 s |
| `OVERLAP_MIN_BARS` | 10 velas coincidentes por TF (N32) |
| `BROKER_DST_RULE` | Calendario de horario de verano del servidor del broker. Lo determina el spike; el candidato son las fechas de EE.UU. `[NO VERIFICADO]` (N34) |
| `REAL_ACCOUNTS` | 000 `flight_account_001_xauusd.db`, 001 `flight_account_000_us500.db`, 002 `flight_account_002_btcusdtp.db`, 003 `flight_account_003_us100.db` (N24) |
| `P2_LOG_MODELS` | `{"D": "2026-09-27"}`: nombre del modelo en el registro de `tools/p2_backtest.py` → fecha de alta. Para sumar un modelo nuevo se agrega una entrada con la fecha del día; nunca se cambia la fecha ni la receta de uno existente (N42) |

## Dependencias

- **Spike de interoperabilidad (N31):** verificar que desde WSL se puede lanzar
  `windows_export/export_p2_ohlc.py` con un Python de Windows que tenga `MetaTrader5`, vía `powershell.exe`, y medir
  cuánto tarda.
  - Medido el 2026-09-27: `powershell.exe` existe en WSL y MT5 está instalado en `C:\Program Files\MetaTrader 5`.
  - `[NO VERIFICADO]`: qué Python de Windows tiene `MetaTrader5`; no está en `AppData\Local\Programs\Python`.
  - Si el spike falla, `AUTO_EXPORT` queda desactivado y nada de RF-20 corre.
  - El spike también determina `BROKER_DST_RULE`. Se vuelve a confirmar con `calibrate_clock_offset` cuando haya
    fills en las dos estaciones (N34).

## Requisitos no funcionales

- **NFR-1 (DB):** los cambios de esquema son solo aditivos: `analysis_start_time`, `audit_registration_time`,
  `resolution_time_source`, `mark_price_time`, `saved_at`, la tabla `backfill_history` y lo que pida el plan. No hay `DROP` ni `ALTER` de columnas
  existentes. Las únicas reescrituras de valores son la de N2 y los conflictos aceptados uno por uno (RF-11e),
  siempre con las 3 puertas y registradas en `backfill_history`.
  - La **migración de esquema** de las DBs reales también pasa por las 3 puertas: `init_db` sobre copias, backup y
    aprobación. Tiene que ocurrir **antes** de correr cualquier comando del CLI nuevo en el checkout principal,
    porque `init_db` migra al abrir cada DB (`cli/main.py:285`; N41).
- **NFR-2 (tests):** sin MT5, sin `/mnt/c` y sin `.data/`. Se usa `:memory:`/`tmp_path` y velas de fixture. Los
  tests se corren en un worktree sin `.data/` ni `.env` reales, con `.data/` verificado antes y listado después
  (R14).
- **NFR-3 (manual):** las demos se hacen en una Flight Session descartable.

## Fuera de alcance

- Instalar o configurar MT5 o el Python de Windows. El spike solo verifica lo que ya existe.
- Sincronizar a Notion los valores del backfill en análisis ya `SYNCED` (baseline H21).
- Umbrales automáticos para Reversal, Regime Decay y Range Expansion.
- Cambiar `session` (ya es automática).
- Arreglar el bug de Choppy de `core/edge_analysis.py`.
- Integrar un P2 sistemático en `calc_edge`, en el wizard o en cualquier decisión. Si los datos de
  `p2_model_log.jsonl` lo justifican, será una spec 003 (N38).
- Diseñar o agregar modelos nuevos (como un modelo H). Esta spec solo deja lista `P2_LOG_MODELS` para sumarlos después
  sin tocar el código del registro prospectivo (N42). Un modelo con indicadores que hoy no se calculan (por ejemplo
  EMA 50 o RSI) necesita además extender `TFIndicatorSnapshot` (`tools/p2_backtest.py:237`).

## Criterios de finalización

- `validation.md` con veredicto por RF e INV, citando el test.
- La suite completa en verde, con la salida mostrada.
- El reporte de comparación corriendo sobre las DBs reales en solo lectura.
- Una demo en una Flight Session: Efficiency Audit con valores propuestos, varios aceptados y uno corregido.
- El backfill aplicado tras las 3 puertas, con el conteo de campos llenados y de diferencias reportadas.
- El spike de export automático documentado con su resultado y su duración medida. Si funciona, dos demos: una
  con MT5 abierto (velas nuevas fusionadas y verificadas por superposición) y otra con MT5 cerrado ("Candle export
  skipped", sin bloquear).

## Puntos a confirmar al leer esta spec

Son reglas derivadas de las decisiones anteriores, no suposiciones nuevas. Si alguna no es lo que querés, se
cambia antes de aprobar:

1. ~~**RF-5.1:** en un Overlap, la "hora de resolución" de A es el inicio de B.~~ Reemplazado por N37:
   `resolution_time` es siempre la hora del primer toque.
2. **RF-8d:** sin Mark Price, la regla de revertido usa el precio de partida de las velas.
3. **RF-9:** si el MAE/MFE supera 10R, se propone 10, el máximo del campo.
4. **`MAX_HORIZON` = 2160 velas de 1H:** son ~90 días en BTC, que opera 24/7, pero ~4 meses en XAU, que cierra
   los fines de semana.
5. **RF-7g:** `Resolution Time` se puede dejar vacío.
