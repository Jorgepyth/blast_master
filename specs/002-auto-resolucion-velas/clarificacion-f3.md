# F3 — Clarificación de la spec 002 (2026-09-25)

Rol QA: **se detecta, no se resuelve.** Son 24 hallazgos en cuatro bloques. Formato:
`[bloque][n] RF — problema — pregunta que lo cerraría`.

Los resueltos se incorporan a `spec.md`. Los descartados se anotan en "Fuera de alcance" con su motivo.

## 1. Ambigüedades

- [1][1] **RF-4** — No dice si la vela que contiene el ancla entra en el camino, y esa vela tiene precios de antes
  del ancla. — ¿Se incluye esa vela, o el camino arranca en la primera vela que abre en el ancla o después?
- [1][2] **RF-4** — "La TF más fina disponible en cada tramo" no define cómo se pasa de una TF a otra. Por ejemplo,
  1M quizá solo exista para los últimos meses. — ¿El camino usa, en cada instante, la TF más fina cuyo banco cubre
  ese instante, empalmando en el borde de la vela más gruesa?
- [1][3] **RF-4, RF-8** — La "hora del toque" no dice si es la apertura o el cierre de la vela que tocó. — ¿Es la
  hora de apertura de esa vela, con una precisión igual a su duración?
- [1][4] **RF-8** — La expansión de 0.5R en 24 h no dice si se deja de medir cuando el precio toca la invalidación
  antes de las 24 h. El análisis exploratorio cortó ahí. — ¿Se mide hasta las 24 h o hasta ese toque, lo que
  ocurra primero?
- [1][5] **RF-8b** — "Superó la invalidación en ≤ 1R" no dice en qué ventana se mide ese exceso. — ¿Es el exceso
  máximo entre el toque de la invalidación y el toque del objetivo?
- [1][6] **RF-5** — No dice si un análisis retroactivo B genera Overlap sobre A. Un retroactivo se carga después,
  pero con una hora tipeada anterior. — ¿Un retroactivo cuenta como B?
- [1][7] **RF-6** — No dice cómo se compara `specific_bias_compliance` (Valid/Invalid, sobre los bias
  estructurales) con la resolución geométrica. — ¿Valid se compara con Confirmed e Invalid con Invalidated?
- [1][8] **RF-2b, RF-6, RF-11** — Se habla de "cuentas reales de `flight_sessions.json`", pero ese archivo también
  lista las Flight Sessions de prueba (`FlightSessionManager.create_session`). — ¿Las cuentas reales son una lista
  fija en configuración (hoy 000, 001, 002 y 003)?
- [1][9] **RF-9, RF-10** — Las "velas que se solapan con `[entry_time, exit_time]`" incluyen movimiento de antes de
  la entrada y de después de la salida, hasta una vela de 15M en cada punta. — ¿Se aceptan esas puntas, o se usan
  solo las velas que caen enteras dentro del intervalo?

## 2. Contradicciones

- [2][1] **RF-11c contra INV-3/R7** — Si el backfill se corre dos veces, la segunda sobrescribiría el
  `resolution_time` que el operador ya confirmó o corrigió en el wizard. — ¿RF-11c aplica solo a las filas con
  `audit_registration_time` vacío, es decir, a las que todavía tienen la hora vieja del audit?
- [2][2] **RF-11b + N9 contra RF-7e/D4** — En los audits que nunca se hicieron (9 filas: 5 de US500, 3 de US100 y
  1 de XAU), el backfill escribiría el tipo y el MAE/MFE sin que el operador pase por el campo. Además,
  `real_bias_b` seguiría vacío, así que el audit quedaría a medias. — ¿El backfill salta los audits nunca hechos
  y los deja para el wizard?
- [2][3] **RF-13 contra RF-13b** — Un retroactivo también carga P0 en el wizard, así que las dos reglas aplican a
  la vez. — ¿Gana la hora tipeada (RF-13b)?
- [2][4] **RF-7g contra el schema** — `EfficiencyAudit.resolution_time` es obligatorio en pydantic
  (`cli/schemas/audit_efficiency.py:43`), y el baseline marcaba ese schema como "probablemente no se toca". — ¿Se
  acepta volver opcional ese campo del schema?
- [2][5] **RF-5/N11 contra el historial** — Con la regla de Overlap tal como está, 14 de 74 análisis de XAU (19%) y
  7 de 17 de BTC (41%) pasarían a Overlap. Vos marcaste a mano 3 y 2. La mediana entre el ancla y el primer toque
  es 6 h en XAU y 21 h en BTC (medido: velas 15M/1H, ancla `created_at − 20 min`). — ¿Es el resultado que
  querés?
- [2][6] **RF-5.1 contra RF-4c** — Supongamos que B empieza dentro del tramo que el banco cubre, antes de cualquier
  toque, pero el banco no cubre el resto del horizonte de A. RF-4c dice `sin_velas` y RF-5.1 dice Overlap. —
  ¿Gana Overlap?

## 3. Casos límite no cubiertos

- [3][1] **RF-2** — `calibrate_clock_offset` devuelve `aligned=True` si ningún precio de referencia cae dentro de
  las velas del export: todas las tasas quedan en 0 y gana el desplazamiento 0 (`tools/p2_backtest.py:1114-1128`).
  [NO VERIFICADO en ejecución, deducido del código.] Un export corto y reciente se fusionaría sin verificar nada. —
  ¿Se exige que al menos 10 referencias caigan dentro del rango exportado?
- [3][2] **RF-9** — `entry_time == exit_time` (las 13 filas con el patrón placeholder, según CLAUDE.md), o
  `exit_time < entry_time`. — ¿Se deja de proponer MAE/MFE en esos casos?
- [3][3] **RF-9, RF-10** — `entry_price == stop_loss` da R = 0, o sea, una división por cero. — ¿Se deja de
  proponer en ese caso?
- [3][4] **RF-9, RF-10** — No dicen qué pasa si el banco no cubre el trade, de la entrada a la salida o de la
  entrada al SL. — ¿Se muestra `sin_velas` y no se propone, como en RF-7f?
- [3][5] **RF-4** — Un `asset` que no está en `MT5_SYMBOL_MAP` (por ejemplo, un activo custom de una Flight
  Session) no tiene motivo en la tabla de errores. — ¿Se agrega el motivo `sin_simbolo_mt5`?
- [3][6] **RF-1** — Dos exports del mismo símbolo al mismo tiempo, por ejemplo desde dos terminales. — ¿El banco
  se protege con un candado de archivo?
- [3][7] **RF-10** — Un TP del lado equivocado; por ejemplo, en un long, un TP por debajo de la entrada. — ¿Se deja
  de proponer?
- [3][8] **RF-3** — Como el export es manual, al guardar un análisis el banco casi nunca va a cubrir ese instante,
  así que el aviso casi nunca va a aparecer en ese momento. — ¿Alcanza con que el reporte (RF-6) liste los Mark
  Price fuera de su vela?

## 4. Conflictos con la constitución

- [4][1] **Principio 6 contra RF-6/RF-11** — No se dice en qué idioma salen el reporte de comparación y el listado
  del backfill. El principio 6 pide los mensajes del CLI en inglés, pero los reportes existentes del repo están en
  español (por ejemplo, `tools/p2_backtest.py:render_report`). — ¿Salen en español (son documentos para vos) o en
  inglés (son salida del CLI)?

---

## Resoluciones (el usuario decide)

### Bloque 1 — Ambigüedades (resuelto el 2026-09-25)

| Hallazgo | Decisión | En la spec |
|---|---|---|
| [1][1] | El camino arranca en la primera vela de 1M que abre en el ancla o después; si no hay 1M, la siguiente más fina | N17, RF-4 |
| [1][2] | En cada instante, la TF más fina validada; el cambio de TF se hace en el borde de la vela más gruesa | N18, RF-4 |
| [1][3] | La hora del toque es la apertura de la vela que tocó | N19, RF-4 |
| [1][4] | La expansión se mide hasta lo que ocurra primero: 24 h o el toque de la invalidación | N20, RF-8 |
| [1][5] | El exceso del sweep se mide entre el primer cruce de la invalidación y el toque del objetivo | N21, RF-8b |
| [1][6] | Un retroactivo cuenta como B para el Overlap | N22, RF-5 |
| [1][7] | `specific_bias_compliance` sigue manual; en el reporte va como columna informativa | N23, RF-6 |
| [1][8] | Las cuentas reales son una lista fija en configuración; una cuenta nueva se agrega a la lista | N24, RF-2b, RF-6, RF-11 |
| [1][9] | El MAE/MFE y `could_hit_tp` del Tactical usan la TF más fina validada (1M, 5M, 15M), con mechas | N8 (cambiado), RF-9, RF-10 |

### Bloque 2 — Contradicciones (2026-09-27)

| Hallazgo | Decisión | En la spec |
|---|---|---|
| [2][1] | La reescritura de `resolution_time` aplica solo a las filas con `audit_registration_time` vacío. Los conflictos se muestran (antes → después) y se pueden aceptar uno por uno desde la pantalla del backfill | N27, RF-11c, RF-11e |
| [2][2] | El backfill llena los campos objetivos de los audits nunca hechos. Los manuales quedan para el wizard. Todo se muestra en una vista estilo `git log --decorate --oneline --graph`, con historial en `backfill_history` | N27, RF-11b, RF-18, RF-19 |
| [2][3] | En un retroactivo gana la hora tipeada | N26, RF-13b |
| [2][4] | `Resolution Time` puede quedar vacío; el motivo va en `resolution_time_source` | N25, RF-7g, RF-14b |
| [2][5] | **PENDIENTE** a pedido del usuario. Opción a evaluar: marcar Overlap y seguir evaluando validation/invalidation en 2 días. Evidencia en `analisis-overlap.md` | `[NECESITA ACLARACIÓN]` en RF-5 |
| [2][6] | Nombres `pending_candles` (temporal) y `no_history`. La regla "Overlap gana" queda atada a [2][5]. Aclaración: el banco crece con cada export, no con cada análisis | N28, RF-4c, RF-4g |

### Bloque 3 — Casos límite (2026-09-27)

| Hallazgo | Decisión | En la spec |
|---|---|---|
| [3][1] | Al menos 10 precios de referencia dentro de las fechas exportadas; si no, `clock_unverified` y no se fusiona | N29, RF-2 |
| [3][2] | `exit_time` ≤ `entry_time`: no se propone, `no_interval` (es un trade que no se ejecutó) | RF-9b |
| [3][3] | `entry_price == stop_loss`: `zero_r` | RF-9c |
| [3][4] | Trade sin velas: `pending_candles`/`no_history` | RF-9d |
| [3][5] | Activo sin símbolo de MT5: `no_mt5_symbol` | RF-4h, E8 |
| [3][6] | Candado por símbolo; el segundo export se cancela | RF-1d |
| [3][7] | TP del lado contrario: `invalid_tp` | RF-10d |
| [3][8] | **ABIERTO:** el usuario pide aplicar la escalera 1M → 5M → 15M… al chequeo del Mark Price. Falta definir hasta qué TF se sube y cuándo se avisa | RF-3 (sin cambios todavía) |

### Bloque 4 — Constitución (2026-09-27)

| Hallazgo | Decisión | En la spec |
|---|---|---|
| [4][1] | Todo lo que el sistema muestra o guarda va en inglés, incluidos los códigos de motivo y las claves de configuración. Docs y conversación en español. Principio 6 actualizado | N30, `docs/constitution.md` |

### Hallazgos nuevos (2026-09-27)

- [3][8] **Resuelto:** se agregan `mark_price_time` y `saved_at`. El Mark Price se chequea con la escalera 1M → 5M → 15M
  en su minuto exacto, y en los análisis viejos con el período de `created_at − 20 min` a `created_at` (N33, RF-3,
  RF-3b, RF-13d, RF-13e).
- **Export automático (pedido del usuario):** se dispara al guardar un análisis, al abrir un audit, antes del reporte
  y del backfill, y al abrir el CLI. Depende de un spike. Se verifica por superposición (N31, N32, RF-20 a RF-20f,
  RF-2d).
- [3][9] **RF-2, RF-2d, RF-4** — **Horario de verano.** El exportador aplica un solo desfase del servidor a toda la
  corrida (`windows_export/export_p2_ohlc.py:31-39`). Las velas de la estación opuesta a la del export quedan corridas
  1 h en todas las temporalidades.
  - **Hoy no afecta:** todos los análisis (mayo a septiembre de 2026) caen en horario de verano, igual que los exports
    del 2026-09-22.
  - **A fines de octubre sí va a afectar:** un export hecho en invierno convertiría mal las velas de verano que repite,
    y la superposición no coincidiría (bloquearía la fusión). Además, las velas viejas del banco quedarían corridas 1 h
    respecto de las nuevas.
  - ¿El exportador debería convertir cada vela con el desfase vigente en su fecha, según el calendario de horario de
    verano del broker? ¿O el banco solo acepta velas de la misma estación que el export?
- [3][9] **Resuelto (opción A):** el exportador convierte cada vela con el desfase vigente en su fecha
  (`BROKER_DST_RULE`, lo determina el spike). Mientras no esté verificado, solo se fusionan las velas de la misma
  estación que el export (N34, RF-15b, RF-15c).
