# v1.1 — 2026-09-23 · Adenda 1 al prompt `2026-09-23-p2-consenso-modelo-f.md`

> Cómo usarla: en la sesión que está ejecutando el feature, escribí
> `Leé @docs/prompts/2026-09-23-p2-consenso-modelo-f-adenda-1.md y aplicala antes de seguir con B2`.
> Si esa sesión corre en el worktree `.claude/worktrees/consenso-modelo-f-471f7d`, pasale la ruta absoluta:
> `/home/jorgecg/projects/trading/blast_master/docs/prompts/2026-09-23-p2-consenso-modelo-f-adenda-1.md`.

Esta adenda **tiene prioridad** sobre el prompt original donde se contradigan. Trae dos correcciones de
evidencia descubiertas después de entregarlo (C1, C2) y las respuestas del usuario a las preguntas H2–H13 de
la entrevista de B1/B2 (D7–D13). Lo marcado `[NECESITA ACLARACIÓN]` sigue abierto: preguntalo, no lo asumas.

<correcciones>

## C1 — La regla R6 del prompt original es falsa tal como está escrita

R6 dice que las velas se usan "estrictamente anteriores" al ancla, "como ya garantiza
`CsvOHLCProvider.get_indicator_snapshot`". **No lo garantiza.** `tools/p2_backtest.py:412` filtra con
`df["time"] < as_of`, y `time` es la hora de **apertura** de la vela (convención de MT5). La vela que está en
formación en el ancla entra con su cierre, máximo y mínimo **finales**, que ocurren después del ancla. En 1W
eso es el cierre del viernes visto desde un miércoles. `get_past_closes` (`:386-391`) tiene el mismo defecto.

**R6 corregida:** el instante del cálculo es el ancla de D13. En cada temporalidad se usan **solo velas
cerradas**: `time + duración(TF) <= ancla`. Una vela que cierra exactamente en el ancla cuenta como cerrada.
En producción, la última fila que devuelve MT5 es la vela en formación y se descarta.

Ejemplo, ancla 06:15 GT, XAUUSD, con el servidor en UTC+3 (verano). En invierno las etiquetas de 4H/12H/1D/1W
se corren una hora.

| TF | Vela en formación (NO se usa) | Última vela cerrada (SÍ se usa) |
|---|---|---|
| 15M | 06:15 | 06:00 (cerró 06:15) |
| 30M | 06:00 | 05:30 (cerró 06:00) |
| 1H | 06:00 | 05:00 (cerró 06:00) |
| 4H | 03:00 (cierra 07:00) | 23:00 del día anterior (cerró 03:00) |
| 12H | 03:00 (cierra 15:00) | 15:00 del día anterior (cerró 03:00) |
| 1D | 15:00 del día anterior (cierra 15:00 de hoy) | 15:00 de anteayer (cerró ayer 15:00) |
| 1W | la de este sábado 15:00 | la de la semana anterior |

El fix del proveedor, con su test de regresión, se hace en la rama base (`feature/tactical-tier-gate-df`),
fuera de esta sesión, y llega al worktree por fast-forward. **No lo reimplementes acá.** Hasta que llegue, el
cálculo en el ancla es una dependencia abierta: no marques RF-2 como hecho.

## C2 — La evidencia del `<contexto>` está inflada: reemplazá los números

Los números del prompt original (F 71%, consenso 83%, "el modelo gana 6 a 2", "la ventaja viene de 1H")
tenían dos sesgos que se suman:
1. **Ancla tardía:** en 61 de 89 análisis se anclaba en la primera `entry_time`, una mediana de 30 minutos
   después del análisis y después de que el operador ya había decidido P2. De esos 61, 25 eran órdenes nunca
   llenadas (13 con placeholder).
2. **Look-ahead de C1.**

Medidos de nuevo el 2026-09-23 en `jupyter/p2_edge_evaluation.ipynb` (v2, §1.3 y §6–§9), con el ancla en
`created_at` − 15 min y solo velas cerradas. Son 89 análisis resueltos de XAUUSD + BTC, sin retroactivos, y
el precio de partida es el cierre de la última vela cerrada. Esta versión incluye los análisis sin ejecución
ni Mark Price, que el ancla anterior dejaba fuera.

| | Aciertos | IC95 | p contra 50% |
|---|---|---|---|
| P2 del operador | 26/46 = 57% | 42–70 | 0.46 |
| F | 26/43 = 60% | 46–74 | 0.22 |
| A | 29/56 = 52% | 39–64 | 0.89 |
| Consenso operador + F | 14/20 = 70% | 48–85 | 0.115 |
| Consenso operador + A | 11/18 = 61% | 39–80 | 0.48 |
| F en momentos al azar (XAUUSD) | 112/206 = 54% | 48–61 | 0.24 |
| Edge P0–P4 completo (3 cuentas) | 36/60 = 60% | 47–71 | p Holm 0.47 |

Además, el mejor de las 7 variantes de P2 ya no sobrevive a la corrección por selección (permutación
p = 0.105, antes 0.023). Entre 0 y 60 minutos antes de `created_at`, los resultados casi no cambian.

Consecuencias para el diseño:
- **Ya no hay evidencia retrospectiva significativa de que F o el consenso acierten más que el azar.** El test
  prospectivo (D5, RF-10) pasa a ser la evidencia principal. No es una confirmación.
- D2 decía que la captura a ciegas era "la condición bajo la cual se midió el 83%". Sigue siendo obligatoria,
  por otro motivo: sin ella el test prospectivo no mide nada.
- F le va ganando a A (mejora 17 análisis y empeora 10, p = 0.25: no concluyente) y coinciden en los 36
  casos donde ambos opinan. F se definió mirando los datos: A se mantiene como control.
- `[NECESITA ACLARACIÓN]` para D5: el cuaderno recomienda agregar a la regla una segunda pregunta, "¿F y el
  consenso aciertan más que el 50%?", porque la regla original solo compara F contra A. Que lo decida el
  usuario.
- D1 (opción 4) sigue vigente mientras el usuario no diga lo contrario. No cambies el alcance por tu cuenta.

</correcciones>

<decisiones_nuevas>
Respuestas del usuario del 2026-09-23 a la entrevista (H2–H13). Son insumos de la spec.

- **D7 — US500.** El símbolo MT5 es `US500`. Agregá `US500→US500` a `{{MT5_SYMBOL_MAP}}`. Si su historia 1W
  no llega a 800 velas, aplica E3 (`historia_insuficiente:1W`); lo confirma el primer export del spike. El
  usuario va a priorizar análisis de US500 en los próximos días.
- **D8 — Clon: opción A, con dos modos de hora.** En un clon, P2 **no se copia**: el wizard lo pide de nuevo, a
  ciegas (R3). El resto se sigue copiando como hoy. Según el modo de hora que elija el operador en
  "Select Timestamp mode for cloned trade":
  - `[1] Use Current System Time`: el ancla es la hora actual y el export actualiza el banco de velas (D10),
    igual que un análisis nuevo.
  - `[2] Enter Custom/Backdated Time`: el ancla es la hora elegida.
    - Si el banco ya cubre esa hora con velas cerradas suficientes, se usa sin exportar.
    - Si no la cubre, se exporta para extender el banco.
    - Si no se puede extender (límite de historia de MT5, MT5 cerrado u otro motivo), se muestra un mensaje
      claro al operador, P2 queda el del operador (R9) y se registra el motivo.
  - `[NECESITA ACLARACIÓN]`: ¿la misma lógica de cobertura aplica a un análisis retroactivo que no es clon
    (RF-7)? Supuesto razonable: sí. Confirmalo con el usuario.
- **D9 — Modelo apagado por cuenta hasta calibrar el reloj.** Mientras una cuenta tenga menos de 10 precios de
  referencia (fills con hora y precio + análisis no retroactivos con Mark Price), el modelo no corre en esa
  cuenta: P2 queda el del operador y se registra `reloj_sin_calibrar`. Al llegar a 10, se habilita solo. Hoy:
  XAUUSD 105, BTC 19, US100 9, US500 2.
- **D10 — Banco de velas acumulativo.** Reemplaza y endurece R8: **nunca se borra una vela histórica.** Cada
  export fusiona lo nuevo con lo existente (sin duplicar por `time`) y el banco crece con los meses y los años.
  Si un export falla o no valida, el banco queda intacto. Se guardan todas las temporalidades que exporta hoy
  el script, 15M incluida. El plan decide el formato y la ubicación; el cuaderno y los reportes deben poder
  seguir leyendo los CSV actuales, o migrar con su propio test.
- **D11 — 15M no entra en F.** El usuario abrió la puerta a sumar 15M a F si no pesa demasiado. **No lo hagas:**
  R7 sigue vigente. Cambiar F después de activar el test prospectivo invalida D5, y en el análisis retrospectivo
  agregar 30M no aportó nada (ablación del cuaderno, §7; medida con los sesgos de C2, así que es orientativa). `[NECESITA ACLARACIÓN]`: si el usuario quiere medir
  15M, la forma compatible es registrar un modelo con 15M como un sombra más, junto a A. Solo con su aprobación.
- **D12 — Campos para poder calificar un análisis.** Con el ancla de D13, el precio de partida es el cierre de
  la última vela 15M cerrada, no el Mark Price. Para calificar un análisis hacen falta **Edge Validation Price**
  y **Structural Invalidation**. Mark Price sigue sirviendo para calibrar el reloj (D9).
  `[NECESITA ACLARACIÓN]`: ¿se vuelven obligatorios en los análisis nuevos? El usuario no respondió esto
  explícitamente. Recordale que un análisis sin esos dos niveles no cuenta para los 40 de D5.
- **D13 — Ancla = inicio del análisis.** El momento de referencia es el instante en que el operador **inicia**
  el análisis, en GT naive, y se usan las velas cerradas de C1 a ese instante. Hoy la DB no guarda esa hora:
  `created_at` es el instante de "Confirm & Save" (`cli/main.py:2880`), después de P2, Mark Price, niveles y
  Edge Description. En un retroactivo o un clon en modo [2], el ancla es la hora elegida.

</decisiones_nuevas>

<requisitos_nuevos>
Agregalos a `spec.md` en B2 (en EARS, verificados contra el baseline).

- RF-12: CUANDO el operador inicie `flow_new_analysis` (análisis nuevo o clon en modo [1]), EL SISTEMA
  registrará la hora de inicio en GT naive y la usará como ancla (D13).
- RF-13: CUANDO el operador confirme P2, EL SISTEMA registrará la hora de esa confirmación. Cada fila del
  registro de RF-5 guarda, además de lo ya listado: la hora de inicio del análisis (ancla), la hora de captura
  de P2, la hora del cálculo del modelo y la hora de apertura de la última vela cerrada usada **en cada
  temporalidad**. Estas horas son solo para auditoría: no cambian `created_at`.
- RF-14: EL SISTEMA calculará F y A solo con velas cerradas en el ancla (C1), en producción y en el reporte
  prospectivo. Un test debe fallar si la vela en formación influye en el resultado.
- RF-15: CUANDO un export termine y valide, EL SISTEMA fusionará sus velas con el banco sin borrar ninguna
  existente (D10). Un test debe fallar si una vela previa desaparece o cambia la cobertura hacia atrás.
- RF-16: DONDE el análisis sea clonado, EL SISTEMA pedirá P2 de nuevo a ciegas y resolverá el ancla y la
  cobertura según D8.
- RF-17: MIENTRAS una cuenta tenga menos de 10 precios de referencia, EL SISTEMA no correrá el modelo en esa
  cuenta y registrará `reloj_sin_calibrar` (D9).

Nuevas clases de error (se suman a `<errores>`):
- E8 — Hora de un clon en modo [2] o de un retroactivo fuera del banco, y MT5 no la puede entregar.
  → Mensaje al operador, P2 del operador, motivo `sin_velas_para_ancla`.
- E9 — Cuenta sin calibrar (D9). → Motivo `reloj_sin_calibrar`.

</requisitos_nuevos>

<configuracion_actualizada>
| Variable | Cambio |
|---|---|
| `{{MT5_SYMBOL_MAP}}` | `XAUUSDT.P→XAUUSD`, `BTCUSDT.P→BTCUSD`, `US100→USTEC`, **`US500→US500`** |
| `{{MIN_PRECIOS_REFERENCIA}}` | nueva: `10` (D9) |
</configuracion_actualizada>
