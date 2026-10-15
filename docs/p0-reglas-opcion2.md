# P0 por estructura (opción 2): borrador de reglas

**Versión 1.0 — 2026-10-09. CONGELADA.** El modelo es el código del commit `bb5f9d0` (`core/p0_tramos.py`,
`tools/p0_opcion2.py`) con sus valores por defecto. Cualquier cambio de reglas o de valores es una versión nueva, con su
propio congelamiento y su propia población (sección 9).

> **Nota del 2026-10-11, antes del primer push.** Se sacaron de la historia de git tres archivos con datos del journal,
> todos del commit del 2026-10-06 que traía los resultados de la evaluación. Por eso los commits posteriores cambiaron
> de identificador, con el mismo contenido, autor y fechas. El commit del modelo congelado, `bb5f9d0`, es ahora
> `e1a52ba`. La tabla completa está en la última sección.

- v0.1: primeras reglas, sacadas de los 24 tramos de 6 casos, con seis preguntas abiertas (P1–P6).
- v0.2: con las respuestas del operador a P1–P6 y su relectura de los 6 casos. Suma dos hallazgos:
  - la lectura por TF de la evaluación (D8) ya se parece a la del operador cuando el tramo es el correcto;
  - la regla de combinación del operador, con los tramos actuales, no cambia nada.
- v0.3: tres pruebas de mercado y una calibración contra los 24 tramos fijan los valores de Q2. Q1 queda descartada por
  los datos. Primer resultado del prototipo sobre los 97 análisis (secciones 7 y 8).
- v1.0: las reglas y los valores de v0.3, sin cambios, congelados con el plan de medición de la sección 9.

Objetivo: que el P0 sistemático lea la estructura como la lee el operador (quiebres por cierre, barridos de liquidez,
tramos estructurales), en vez de con los fractales de 2 velas de la evaluación (D3–D8 del pre-registro).

## 1. De dónde sale

- **6 casos × 4 TF = 24 tramos** elegidos por el operador en el visor (`jupyter/p0_visor.ipynb`, opción 3), con su regla
  en una línea y, en v0.2, su relectura de cada TF. Los casos: XAU `2834cbeb`, `4a166533`, `7afc4c20`, `68ff0f26`,
  `79a818c1` y BTC `c73353ad`.
- Las velas de la foto del 2026-10-06 (`.tmp/p0_foto_20261006`), con cada TF leída en sus propias velas.
- **Ojo:** esa relectura se hizo con los visores que mostraban las velas posteriores al ancla y el resultado. Desde v0.2
  el visor los oculta por defecto (`FUTURO = 0`, `MOSTRAR_RESULTADO = False`).

Los 24 tramos (A → B) y el estado de la relectura:

| Caso | 1D | 12H | 4H | 1H |
|---|---|---|---|---|
| 1 `2834cbeb` | 5238.59 → 4098.92, rango | 4833.30 → 4510.29, rango | 4718.80 → 4480.48, bajista | 4572.60 → 4480.48, bajista |
| 2 `4a166533` | 4382.49 → 3943.14, rango | = 1D, rango | 3959.69 → 4166.06, alcista | 4051.17 → 4022.08, rango |
| 3 `7afc4c20` | 4382.49 → 3943.14, rango | = 1D, rango | 3959.69 → 4166.06, alcista | 4089.48 → 4034.94, bajista |
| 4 `68ff0f26` | 4382.49 → 3943.14, rango con sesgo alcista | = 1D, rango con sesgo alcista | 4223.43 → 4371.78, alcista | 4229.72 → 4371.78, alcista |
| 5 `c73353ad` | 62457.37 → 79504.44, alcista | = 1D, alcista | 75540.32 → 81264.79, alcista | 79578.64 → 77833.17, bajista |
| 6 `79a818c1` | 4382.49 → 3943.14, bajista | = 1D, bajista | 3960.17 → 4202.99, alcista en rango (0.618–0.382) | 4120.94 → 4043.89, bajista |

## 2. Reglas

✅ = la sostienen los casos; ❓ = falta un valor o hay un caso que choca (sección 3).

| # | Regla | Estado | De dónde sale |
|---|---|---|---|
| R1 | **Cada TF con sus propias velas.** Pivotes, quiebres y cierres se miden con las velas de la TF. Las velas finas (1M) solo deciden qué barrera se tocó primero en un evento. | ✅ | Pedido del operador. |
| R2 | **Quiebre = dos cierres seguidos más allá del nivel** (margen calibrado: 0, sección 7). Una mecha más allá, o un solo cierre que vuelve adentro, es un **barrido**: no cambia la tendencia ni mueve A. | ✅ v0.3; 5-1H queda como excepción (Q1) | P2. BTC 12H del 08-24 (un cierre 400 arriba de `g`, "llevado por mechas"); XAU 1H `d` (un cierre 2 puntos arriba de `b`). |
| R3 | **B es la punta del tramo con mecha, cuando ya es un pivote confirmado.** Un barrido todavía sin confirmar no mueve B. | ✅ | P3. 3943.14 sí es B (pivote confirmado); 81264.79 en 12H no, porque en el ancla todavía no estaba confirmado. |
| R4 | **A es el origen del movimiento que quebró:** el extremo opuesto más alejado entre el pivote roto (sin contar su vela) y la vela que lo rompió. | ✅ 5 de 6; choca 5-1H (Q1) | 1-1D (5238.59), 1-1H (`d`), 2/3-4H (`b`/`a`), 5-4H (`e`), 6-4H (3960.17). |
| R5 | **El nivel protegido es el último pivote significativo; significativo es grande para su TF:** el precio tiene que girar al menos 2 ATR14 de la TF desde ahí (k calibrado, sección 7). Un pivote chico se ignora. | ✅ v0.3 | P1: "depende de ambos, pero principalmente del tamaño del pivote". 6-4H: el cambio a alcista llega al romper 4063.42, sin romper 4382.49. |
| R6 | **Rango dentro de un tramo:** si el precio oscila sin romper A ni B, el tramo sigue vigente. | ✅ | 1D y 12H de oro: 4382.49 → 3943.14 vigente del 06-29 al 08-09. |
| R7 | **La TF menor hereda los niveles de la mayor** cuando no tiene estructura propia. | ✅ | 12H = 1D en 5 de 6 casos. |
| R8 | **Cambio de tendencia:** romper el nivel protegido deja la TF en **rango**. Pasa a tendencia nueva si el giro ya estaba armado antes del quiebre: un piso más alto que B, si quiebra al alza, o un techo más bajo que B, si quiebra a la baja. | ✅ | P5. 2-1H: rompe `g` en V, sin piso más alto → rango. 6-4H: piso 3960.17 > 3943.14 → alcista. 5-1H: techo `g` < `e` → bajista. |
| R9 | **El estado de cada TF sale de dónde está el precio en el tramo**, como en D8: rechazo en un retroceso → sigue la tendencia; rechazo en 0 (B) → rango; ruptura de 0 → sigue la tendencia; sin evento → tendencia. **Agregado:** un evento de hace más de 10 velas de la TF deja de contar y el estado pasa a rango (sección 7). | ✅ v0.3 | Con tus tramos, D8 da tu estado en 17 de 24 (sección 4). Los 7 que no son de 1D o 12H, con rechazos de hace semanas. |
| R10 | **El P0 se arma de abajo hacia arriba:** manda la TF más chica que tenga dirección (1H; si está en rango, 4H, y así). Las TF mayores la confirman: si hay más TF mayores en contra que a favor, no opina. | ✅ | P6: "busco el movimiento más probable según la tendencia más chica y lo justifico con las mayores". Con tus estados de ahora da Short, Long, no opina, Long, no opina y Short en los casos 1 a 6. |

Fuerza del P0 (borrador, sin probar): Weak si ninguna TF mayor confirma, Mid si confirma alguna, Strong si confirman
todas.

## 3. Preguntas abiertas (v0.2; resueltas en v0.3, sección 7)

**Q1. El 1H del caso 5, ¿es una excepción o una regla?** v0.3: los datos la descartan. Un solo cierre con el giro ya
armado acierta menos, no más (48% contra 55%). El caso queda como excepción.
- El quiebre de `d` (78445.11) fue un solo cierre: la vela de las 14:00 cerró en 78205.30, y la de `h` (15:00) volvió a
  cerrar arriba, en 78546.65. Con R2 (dos cierres) todavía no habría quiebre.
- Con R4, A sería `e` (81264.79), el techo más alto entre `d` y el quiebre. Elegiste `g` (79578.64), el techo más bajo
  que se formó antes de romper.
- Posible regla: si antes del quiebre ya hay un techo más bajo (R8), alcanza con un cierre, y A es ese techo. ¿Es así?

**Q2. Tres valores para calibrar.** v0.3: k = 2 ATR, margen 0 con dos cierres, vencimiento 10 velas. Propuesta de
v0.2: probarlos contra tus 24 tramos y estados, congelarlos y no volver a tocarlos.
- **k**, el tamaño mínimo de un pivote, en ATR de la TF (R5). Entre 1.5 y 3, según la sección 4.
- **El margen del quiebre** (R2), en ATR de la TF. Ejemplo del caso 1: en 1H cerraron dos velas seguidas arriba de A
  (4572.60), la de las 16:00 por 1.41 y la de las 17:00 por 9.22. El ATR de 1H era 21. Con un margen de 0.05 ATR (≈ 1.1)
  es quiebre; con 0.1 ATR (≈ 2.1), la de las 16:00 no alcanza y es barrido.
- **El vencimiento de un evento** (R9), en velas de la TF.

## 4. Hallazgos

**Los tramos automáticos (exploratorio).** Cuenta en cuántos de tus 24 tramos da la misma dirección, con A y B a menos del
5% del tamaño del tramo. Es optimista: el umbral se eligió mirando estos mismos tramos.

| Variante | Tramos que reproduce |
|---|---|
| Motor de la evaluación (fractales de 2 velas, D4–D5) | 8 de 24, casi todos en 4H y 1H |
| Quiebre por cierre, con fractales de 2 velas | 3–4 de 24 |
| Quiebre por cierre, solo con pivotes grandes (zigzag de 2 a 3 ATR de la TF) | 11–12 de 24, casi todos en 1D y 12H |
| Lo anterior con dos cierres seguidos para quebrar | 9–11 de 24 |
| Una regla por TF: pivotes grandes en 1D y 12H, el motor de la evaluación en 1H | 14 de 24 |

**La lectura por TF ya está cerca (R9).** Con tus tramos, sin forzar el estado, D8 da el mismo estado que tu relectura en
17 de 24.
- Los 7 que no coinciden son de 1D o 12H en los casos 1 a 4. D8 dice bajista por un rechazo en un retroceso (0.5, 0.618
  o 1) que pasó hace semanas; vos decís rango.
- Ejemplo: en los casos 2 y 3, el último evento del 1D es el rechazo de 0.5 de principios de julio. Después el precio
  volvió hasta 3959.69, casi al 0. Pero no entró a su zona, que llega hasta 3954.1, así que el sistema no vio ese regreso.
- De ahí el vencimiento de R9.

**La regla de combinación sola no cambia nada (R10).** Aplicada a los estados por TF del motor de la evaluación, en los 97
análisis:

| | S4 estricto | S1 |
|---|---|---|
| Tu P0 | 43/91 = 47%, neto −5 | 46/91 |
| Modelo B | 34/65 = 52%, neto +3 | 37/65 |
| Regla R10 con los estados del motor | 35/66 = 53%, neto +4 | 39/66 |

- R10 y B opinan juntos en 60 análisis y **nunca** apuntan para lados distintos.
- Lo que puede cambiar el resultado son los tramos y los estados (R2–R9), no la forma de combinar las TF.
- Es exploratorio y sobre los mismos datos de la evaluación.

## 5. Opción 3 en los 6 casos

Modelo B con los tramos del sistema y con los tuyos, y la regla R10 con tus estados de ahora. La verdad es S4 estricto.

| Caso | Tu P0 (entonces) | B, tramos del sistema | B, tus tramos | R10, tus estados de ahora | Qué pasó |
|---|---|---|---|---|---|
| 1 `2834cbeb` | Long ✓ | Short ✗ | Short ✗ | Short ✗ | tocó arriba a las 1.2 h |
| 2 `4a166533` | Long ✓ | no opina | Long ✓ | Long ✓ | arriba, 3.2 h |
| 3 `7afc4c20` | Long ✗ | Short ✓ | no opina | no opina | abajo, 25 h |
| 4 `68ff0f26` | Short ✗ | Long ✓ | Long ✓ | Long ✓ | arriba, 24.5 h |
| 5 `c73353ad` | Long ✗ | Long ✗ | Long ✗ | no opina | abajo, 178 h |
| 6 `79a818c1` | Short ✓ | Long ✗ | no opina | Short ✓ | abajo, 2.6 h |

R10 con tus estados da 3 aciertos y 1 error. Es circular: las reglas salieron de estos casos, y los releíste viendo lo que
pasó después.

## 6. Cómo se mide

**Qué pregunta responde la verdad.** Cada análisis se juzga con una carrera entre sus dos niveles: tu objetivo (EVP) y tu
invalidación (SI).
- Long acierta si el precio toca primero el de arriba; Short, si toca primero el de abajo.
- En S4 estricto, además, ese toque tiene que llegar dentro de las 48 h.
- No mide dónde terminó el precio ni si la estructura se cumplió.
- Ejemplo, caso 1:
  - partida 4561.66, arriba 4584.00 (+22.34), abajo 4520.00 (−41.66);
  - el precio tocó 4584 a la 1.2 h y llegó a 4589.5;
  - recién a las 24.5 h tocó 4520, y siguió hasta 4453.56;
  - por eso un Short pierde, aunque después el precio bajó 108.

**Pasos.**
1. ~~Cerrar Q1 y Q2.~~ Hecho en v0.3 (sección 7).
2. ~~Programar los tramos y estados (R1–R9) y la combinación (R10) como un modelo nuevo, sin tocar A–H.~~ Hecho:
   `core/p0_tramos.py` (motor de tramos) y `tools/p0_opcion2.py` (lectura y combinación), con tests.
3. ~~Compararlo contra tus 24 tramos y correrlo sobre los 97 análisis.~~ Hecho (sección 8). Es **optimista**.
4. Congelar las reglas (v1.0, con su commit) y medir con los análisis **creados después** del congelamiento, con S4
   estricto como cifra principal.

## 7. Calibración (v0.3, 2026-10-09)

**Tres pruebas de mercado.** Con toda la historia de velas anterior al primer análisis (antes del 2026-05-15), así no se
calibra con el período de los 97. Cada prueba es una carrera simétrica de ±1 ATR desde el momento de la decisión: el
azar da 50%.

| Prueba | Qué se midió | Resultado |
|---|---|---|
| Regla de quiebre | 2269 cruces de pivotes (1D a 1H, oro y BTC). Para cada regla: cuando dice "quiebre", ¿sigue el precio?; cuando dice "barrido", ¿vuelve? | Todas cerca del azar. Un cierre o dos, lo mismo. El margen ayuda un poco: con 0 los quiebres aciertan 52%, con 0.5–1 ATR, 55–56% (IC 51–59%). Los barridos nunca anticipan nada (47–51%). Con ±2 ATR, igual. |
| Q1 (un cierre con el giro armado) | Quiebres de un cierre con un techo más bajo (o piso más alto) ya formado, contra los demás | Aciertan **menos**: 48% contra 55%. Q1 queda descartada. |
| Tamaño del pivote | Los mismos cruces, por la profundidad del giro del pivote (en ATR) | Un pivote más grande no hace más confiable su quiebre (de 0.5 a 5 ATR, todo entre 46% y 58%). El tamaño sirve para leer como vos, no para predecir mejor. |
| Vencimiento de un rechazo | 541 rechazos en retrocesos (y 586 en placebo), desde n velas después | Al rechazar, 57% sigue la tendencia (placebo 56%). A las 3 velas, 46%; de ahí en adelante, entre 49% y 54%. El efecto dura menos de 3 velas y es igual en los niveles falsos. |

**Calibración contra tus tramos.** Como el mercado no elige casi nada, los valores salen de imitar tu lectura. Se probaron
4 tamaños de pivote (1.5 a 3 ATR) × 4 márgenes (0 a 0.5 ATR) × 1 o 2 cierres, y 5 vencimientos.

| Valor | Elegido | Por qué |
|---|---|---|
| Tamaño del pivote (k) | 2 ATR | Reproduce 16 de tus 24 tramos. Con 1.5 ATR, entre 9 y 12; con 2.5 o 3, entre 6 y 15, según el margen. |
| Quiebre | 2 cierres, margen 0 | Con margen 0.5 ATR bajan a 9 de 24: se pierde, por ejemplo, el quiebre del 1D del 06-23 que hace A = 4382.49. La ganancia del margen en el mercado (unos 3 puntos) no es significativa. |
| Vencimiento | 10 velas | Reproduce 17 de tus 24 estados (con 3 velas, 16). |

El 1.5–3 ATR de v0.2 salía de un prototipo previo que probó 1, 1.5, 2, 3, 4, 6 y 8 ATR: con 1 y con 4 o más casi no
reproducía ningún tramo.

Los 8 tramos que el prototipo todavía no reproduce:
- el 1D, el 12H y el 1H del caso 1;
- los 1H chicos de los casos 2 y 3;
- el 1D y el 12H del caso 4: cerraron dos velas seguidas arriba de su último techo significativo (en 1D, 4202.99) y
  para el motor eso ya es un cambio a alcista; vos seguís leyendo el tramo 4382.49 → 3943.14;
- el 1H del caso 5 (la excepción de Q1).

## 8. Primer resultado del prototipo (optimista)

Los 97 análisis de la evaluación, S4 estricto.

| | Opina | Aciertos | Neto |
|---|---|---|---|
| **Opción 2** (k 2, dos cierres, vencimiento 10) | 71 | 39/71 = 55% (IC 43–66%) | +7 |
| Modelo B de la evaluación | 65 | 34/65 = 52% (IC 40–64%) | +3 |
| Tu P0 | 91 | 43/91 = 47% | −5 |

- Por activo: oro 30/53 (neto +7), BTC 9/18 (neto 0).
- Contra B: opinan juntos en 54 análisis y apuntan para lados distintos en 5. La opción 2 tiene 12 aciertos que B no
  tiene, y B tiene 7 que la opción 2 no tiene. McNemar p = 0.36: la diferencia es compatible con el azar.
- Sensibilidad: con vencimiento 3, 38/68 (neto +8); con margen 0.5 ATR, 37/68 (neto +6).
- **Es optimista:** 6 de estos 97 análisis se usaron para escribir las reglas y calibrarlas, y los 97 ya se habían visto
  en la evaluación.
- Para confirmar un acierto real de 55% harían falta unos 780 análisis en los que opine; para 60%, unos 190.

## 9. Congelamiento v1.0 y plan de medición (2026-10-09, escrito antes de mirar ningún análisis nuevo)

**El modelo.** Commit `bb5f9d0`: `tools/p0_opcion2.Opcion2(market)` con sus valores por defecto. Son pivotes
significativos de 2 ATR14, quiebre con 2 cierres seguidos y margen 0, eventos que vencen a las 10 velas y la combinación
R10. No se cambia nada: una regla o un valor distinto es una v1.1, con su propio congelamiento y su propia población.

**Qué análisis entran.**
- Cuentas de XAU y de BTC, con **ancla posterior a 2026-10-09 17:00 GT**, después del commit de este congelamiento.
- Los mismos filtros de la evaluación (`tools/p0_sistematico.load_analyses`, pre-registro §3): sin retroactivos, con
  velas en el ancla, con EVP y SI de lados distintos del precio de partida.
- El ancla es la de la evaluación (`analysis_anchor`).
- Las velas son las del banco, en solo lectura.

**Qué se mide.**
- La verdad es la de la evaluación (D12): qué nivel del análisis toca primero el precio.
- La cifra principal es **S4 estricto**, con S1 al lado.
- La opción 2 se compara contra el 50%, contra el modelo B de la evaluación y contra tu P0, en los mismos análisis:
  aciertos sobre opiniones, neto y McNemar por pares.
- Si para entonces existe el registro en segundo plano, las predicciones salen de ahí. Si no, se recalculan con el
  commit congelado.

**Cuándo se mira y qué se decide.**
- Miradas descriptivas cada 3 meses: la primera, a partir del 2027-01-09. No deciden nada.
- La decisión formal llega cuando la opción 2 haya opinado en **190 análisis**. Con ese número se puede detectar un
  acierto real de 60%.
  - **Sirve:** el límite inferior del IC 95% (Wilson) de S4 estricto supera 50%, y su neto es mayor que el de B en los
    mismos análisis.
  - **No sirve:** el límite superior del IC 95% queda debajo de 55%.
  - **Si no pasa ninguna de las dos:** insuficiente, y se sigue acumulando.

### Adenda del 2026-10-09: dos hipótesis de asistente (escrita antes de mirar ningún análisis nuevo)

Salieron de mirar los 97 análisis después del congelamiento, así que son **hipótesis, no hallazgos**. Se miden en la misma
población de v1.0, con la misma verdad (S4 estricto). No cambian el modelo.

| | H1: tu P0 cuando el sistema no opina | H2: tu P0 contra tu propia tesis |
|---|---|---|
| Casos | Tu P0 opina (Long o Short) y la opción 2 v1.0 no opina | Tu P0 opina en contra de la dirección de tu tesis, el lado del EVP respecto del precio de partida |
| En los 97 (post hoc) | 8 de 24 = 33% | 1 de 10 = 10% |
| Qué se espera | Que acierte menos de 50% | Que acierte menos de 50% |
| Cuándo se decide | A los **66 casos**. Con eso se detecta un acierto real de 33% | A los **29 casos**. Con eso se detecta un 25% |
| Confirmada si | el límite superior del IC 95% (Wilson) queda debajo de 50% | lo mismo |
| Regla que se adoptaría | Si el sistema no opina, tu P0 queda en neutral | Si tu P0 va contra tu tesis, queda en neutral |

- Antes de esos números, las miradas trimestrales son solo descriptivas: no se adopta ninguna regla antes.
- Si no se confirma, la regla no se adopta.

### Adenda del 2026-10-09: US500 entra a la población (escrita antes de mirar ningún resultado)

- **Qué cambia:** a pedido del operador, la población de v1.0 y la de las hipótesis H1 y H2 suman la cuenta de US500
  (símbolo MT5 `US500`). Las reglas son las mismas: ancla posterior a 2026-10-09 17:00 GT, los mismos filtros y la misma
  verdad.
- **Por qué:** desde septiembre, los análisis nuevos son de US500. En oro no hay desde el 2026-09-16 y en BTC desde el
  2026-09-01.
- **El modelo no cambia y no usa 1W.** Que US500 tenga 739 velas semanales (el P2 D pide 800) no lo afecta.
- **Cómo se reporta:** cada activo por separado y todos juntos. Las decisiones (190 opiniones; 66 y 29 casos) se toman
  con todos juntos.
- **Advertencia:** el modelo se calibró con oro y BTC, así que en US500 queda fuera de muestra también por el activo.
- **USTEC (US100)** queda fuera mientras su reloj no esté verificado y no tenga velas en el banco. Sumarlo sería otra
  adenda.

### Adenda del 2026-10-09: la decisión se compara contra el azar justo (escrita antes de mirar ningún análisis nuevo)

- **Por qué.** En S4 estricto, si en 48 h el precio no toca ningún nivel, la opinión pierde, sea Long o Short.
  - En los 97 análisis de la evaluación pasó en 12, el 12%. Por eso, repartir al azar la misma mezcla de Long y Short
    acierta alrededor de 44%, no 50%.
  - Con 190 opiniones, el criterio de §9 ("el límite inferior del IC 95% supera 50%") pedía 109 aciertos, el 57.4%. Si
    el acierto real fuera 55%, el modelo pasaría 28 de cada 100 veces.
  - Contra el azar justo alcanzan 96 aciertos, el 50.5%, y pasaría 91 de cada 100 veces.
- **Cómo se calcula el azar justo.** Sobre la misma población medida se hacen 10,000 sorteos, con semilla 20261009.
  Cada sorteo reparte al azar, entre los análisis en los que opinó el modelo, la misma cantidad de Long y Short que dio
  el modelo. El azar justo es el acierto promedio de los sorteos.
- **Criterio nuevo.** Reemplaza "Sirve" y "No sirve" de §9; el resto queda igual.
  - **Sirve:** menos del 5% de los sorteos aciertan tanto o más que el modelo (p unilateral < 0.05), y su neto supera al
    de B en los mismos análisis.
  - **No sirve:** el límite superior del IC 95% (Wilson) del acierto queda debajo del azar justo + 5 puntos.
  - **Si no pasa ninguna de las dos:** insuficiente, y se sigue acumulando.
  - No cambian la población, el momento de la decisión (190 opiniones) ni la comparación con B y con el P0 del operador
    (McNemar por pares).
- **H1 y H2 no cambian.** Su pregunta es otra: si sacar esas opiniones mejora el neto. Eso pasa cuando aciertan menos de
  50%, sin importar cuánto acierte el azar.

### Adenda del 2026-10-09: hipótesis H3, el edge con P0 sistemático (escrita antes de mirar ningún análisis nuevo)

- **Qué propone.** Calcular el edge del operador con su P1, P3 y P4, pero con el P0 sistemático (O2-v1.0) en lugar
  del P0 manual. El cálculo:
  - el edge se recalcula con `core.math_engine.calculate_edge_score(x0, x1, x2, x3, x4)`;
  - x1 a x4 salen de las capas cargadas por el operador: dirección × fuerza, con Strong = 2, Mid = 1 y el resto 0, como
    en `cli.main.get_str_val`;
  - x0 sale de la línea del registro en segundo plano: la dirección del modelo × su fuerza, con la misma escala;
  - el sesgo se decide con `cli.main.determine_market_bias`: ±0.26.
- **De dónde sale.** Se probó después de la evaluación sobre los mismos 97 análisis, así que es una hipótesis, no un
  hallazgo. Ahí, el edge con P0 sistemático opinó en 55 análisis y acertó 65% en S4 estricto, con neto +17. El edge
  real opinó en 61, acertó 59% y tuvo neto +11. Cambió el sesgo en 24 análisis y no dio vuelta ninguna dirección: sacó 15
  en los que el edge real había opinado (acertó 5) y sumó 9 (acertó 5).
- **Cómo se mide.** En la misma población de v1.0, con la misma verdad (S4 estricto):
  - para cada análisis se compara el puntaje del edge con P0 sistemático y el del edge real: +1 si acierta, −1 si
    pierde, 0 si no opina (Choppy);
  - se cuentan los análisis en que el edge con P0 sistemático queda mejor y los que queda peor. Los empates no cuentan.
- **Cuándo se decide.** En el mismo momento que la decisión principal: a las 190 opiniones de la opción 2.
- **Confirmada si:** el edge con P0 sistemático queda mejor más veces que peor, con p unilateral < 0.05 en una prueba
  de signos. Si no, no se adopta.
- **Qué se informa al lado:** el acierto contra el azar justo, el resultado en R y el escenario con P0 y P2
  sistemáticos (el P2 casi no cambia el edge: en los 97 movió 4 análisis).

### Adenda del 2026-10-11: H3 también con P0 y P2 sistemáticos (escrita antes de mirar ningún análisis nuevo)

- **Qué cambia.** H3 pasa a tener dos partes, que se miden igual y al mismo tiempo:
  - **H3a:** el edge con el P0 sistemático en lugar del manual. Es la de la adenda anterior, sin cambios.
  - **H3b:** el edge con P0 y P2 sistemáticos en lugar de los manuales.
    - x0 es el de H3a.
    - x2 es el P2 del modelo D600 (`p2_rescaled`, de −2 a 2) del registro del P2. D600 es igual a D en oro y bitcoin,
      y además corre en US500.
    - Si D600 no tiene valor para un análisis, x2 = 0.
    - P1, P3 y P4 siguen siendo los del operador.
- **Por qué.** El operador pidió medir el reemplazo de las dos capas sistemáticas.
  - En los 97 análisis, sin haberlo registrado antes, H3b acertó 37 de 57 (65%), con neto +17, lo mismo que H3a.
  - Cambiar solo el P2 movió 4 análisis: pesa 15% del edge, y el modelo D no opinó en 55 de los 97.
- **Cómo se decide.** Cada parte se decide con su propia prueba de signos, en el momento de la decisión principal.
  - Como son dos pruebas, se corrige con Holm: la de p más chico tiene que quedar debajo de 0.025 y la otra, debajo de
    0.05.
  - Lo esperable es que den casi lo mismo.

### Adenda del 2026-10-11: tres cortes para la decisión principal (escrita antes de mirar ningún análisis nuevo)

- **Qué cambia.** La decisión principal ya no se mira una sola vez, a las 190 opiniones: se mira en tres cortes, a las
  **63, 127 y 190 opiniones**, con una vara más exigente en los primeros. Es un diseño de O'Brien-Fleming con error
  total de 5%, unilateral.
- **Por qué.** A ritmo normal, 190 opiniones son unos 11 meses. Si el modelo es claramente bueno, se nota antes. Mirar
  varias veces con la misma vara aumentaría las chances de confirmar algo por suerte; los umbrales de abajo lo
  compensan.
- **Qué entra en cada corte.**
  - Una opinión es un análisis de la población de v1.0 en el que O2-v1.0 da Long o Short.
  - Cada corte usa exactamente las primeras 63, 127 o 190 opiniones por orden de ancla, aunque al mirar ya haya más.
  - Se mira cuando todas las opiniones del corte están resueltas: pasaron las 48 h y hay velas.
- **Sirve en un corte** si se cumplen las dos condiciones de la adenda del azar justo, con el umbral de p de ese corte:

  | Corte | Opiniones | p unilateral menor que | Aciertos que hacen falta si el azar justo fuera 44% |
  |---|---|---|---|
  | 1 | 63 | 0.0015 | 40 (63.5%) |
  | 2 | 127 | 0.0182 | 69 (54.3%) |
  | 3 | 190 | 0.0436 | 96 (50.5%) |

  - La otra condición no cambia: el neto supera al de B en las mismas opiniones.
  - El p sale de 100,000 sorteos, no de 10,000, con la misma semilla (20261009). En el primer corte el umbral es chico
    y hace falta más precisión.
  - Los umbrales salen de z = 2.970, 2.092 y 1.710: C = 1.710 con las fracciones de información 63/190, 127/190 y 1.
  - Los aciertos de la última columna son una ilustración, con la cola exacta de la binomial. El azar justo real
    depende de la mezcla de Long y Short del modelo en cada corte.
- **Si en el corte 1 o 2 no sirve, se sigue acumulando.** "No sirve" e "insuficiente" se deciden solo en el corte 3, con
  el criterio de la adenda del azar justo.
- **Qué cambia en las chances.** Simulación con azar justo de 44%:
  - Error total (confirmar un modelo sin ventaja): 4.5%. Con una sola mirada a las 190 era 4.1%.
  - Un acierto real de 55% se confirma 91 de cada 100 veces, igual que con una sola mirada: 11 en el corte 1, 49 en el
    2 y 31 en el 3.
  - Un acierto real de 60% se confirma casi siempre: 33 de cada 100 veces en el corte 1, y 92 hasta el corte 2.
  - El corte 3 pide los mismos 96 aciertos que una sola mirada.
- **Lo que no cambia.**
  - H1 y H2 se deciden a los 66 y 29 casos.
  - H3a y H3b se deciden a las 190 opiniones, aunque la decisión principal llegue antes: el registro sigue acumulando.
  - La población, la verdad (S4 estricto) y la comparación con B y con el P0 del operador.
  - Las miradas trimestrales siguen siendo descriptivas: no adelantan ningún corte.
- **Si el modelo sirve en el corte 1 o 2,** el asistente no cambia hasta que se decidan H1, H2 y H3. Mostrar el P0
  sistemático antes cambiaría el P0 del operador, que esas hipótesis miden. Si el operador decide mostrarlo antes, las
  hipótesis pendientes se cierran con los análisis anteriores al cambio, y se informa así.

### Adenda del 2026-10-11: el script de la medición (escrita antes de mirar ningún análisis nuevo)

- **Qué es.** `tools/p0_prospectivo.py`, con sus pruebas en `tests/test_p0_prospectivo.py`. Calcula los tres cortes, H1,
  H2, H3a y H3b como dicen §9 y sus adendas. Se escribió cuando la población todavía estaba vacía, así que el cálculo
  también queda fijado antes de ver datos.
- **Cómo se usa.** Desde la raíz del checkout principal, `python tools/p0_prospectivo.py`. Solo lee.
  - Por defecto dice cuánto falta para cada corte y cada hipótesis, y muestra el resultado solo de los que ya llegaron a
    su número con todos sus análisis resueltos.
  - `--descriptivo` agrega las cifras acumuladas, para las miradas trimestrales. No deciden nada.
- **Lo que el plan no detallaba y el script fija.**
  - **O2-v1.0** sale del registro en segundo plano: la línea vigente, con estado `ok` y la huella `0265dd400d07`. Si falta,
    se recalcula con el código congelado y las velas cerradas en el ancla, como el registro. El informe dice cuántas
    salieron de cada lado.
  - **B** no se registra: se recalcula con el código de la evaluación (`predictions_for` sobre el banco).
  - **Cuándo cuenta un análisis:** cuando su S4 estricto está resuelto, con las reglas de `core/outcome_metrics.py`. Los
    que no se pueden resolver nunca, como una vela que toca los dos niveles, quedan fuera, como en la evaluación.
  - **Cuándo se mira un corte:** cuando sus N opiniones están resueltas y ningún análisis anterior tiene el modelo
    pendiente de cálculo. Si no, ese análisis podría sumarse después y cambiar el conjunto. Lo mismo vale para H1 y H3.
  - **El P0 del operador** es la capa P0 cargada: Long o Short. Neutral o vacío no opina. **La tesis** es el lado del
    EVP respecto del precio de partida.
  - **H1, "el sistema no opina":** O2-v1.0 tiene una línea válida con dirección 0. Sin línea válida, el análisis no
    cuenta.
  - **H3** usa los análisis de la población hasta el que trae la opinión 190, inclusive. Los que no tienen una línea
    válida de O2-v1.0 quedan fuera. El edge real se recalcula con las capas guardadas, P0 a P4, y el informe dice en
    cuántos análisis difiere del guardado.
  - **La prueba de signos** es la binomial exacta unilateral, sin empates.
  - **Los sorteos** son 100,000, en bloques de 10,000, con `numpy.random.default_rng(20261009)` y `permuted` por fila.
  - **R** se mide desde el precio de partida, y es descriptivo:
    - si toca primero su nivel en 48 h, gana la distancia a ese nivel dividida por la distancia al contrario;
    - si toca primero el contrario, −1;
    - si en 48 h no toca ninguno, se cierra al cierre de la vela 1H de las 48 h.
  - **Una vez tomada la decisión principal,** los cortes siguientes no se miran.
- **Validación.** Corrido sobre los 97 análisis de la evaluación, moviendo el inicio de la población (ninguno de la
  población real), reproduce exactamente las cifras ya publicadas:
  - O2-v1.0 39/71 (+7), B 34/65 (+3), el P0 del operador 43/91 (−5) y la tesis 52/97;
  - el edge real 36/61 (+11), y H3a 36/55 (+17), con 15 análisis mejores y 9 peores;
  - H1 8/24 y H2 1/10;
  - el edge recalculado coincide con el guardado en los 97.

  H3b no se puede reproducir así: el registro de D600 empieza el 2026-10-09, y en esos análisis x2 vale 0.

### Nota del 2026-10-11: identificadores de commit después de sacar los datos del journal

Antes del primer push se sacaron de la historia los tres archivos con datos del journal del commit `d1e5c36`: el
notebook ejecutado de la evaluación (`jupyter/p0_sistematico.ipynb`), su HTML y `jupyter/p0_sistematico/resultados.csv`.
Quedan solo en el checkout del operador, sin seguimiento. `d1e5c36` no traía nada más y desapareció.

Los commits siguientes tienen el mismo contenido, autor, fecha y mensaje, con otro identificador. Las adendas de arriba
citan los identificadores viejos; estos son los nuevos:

| Viejo | Nuevo | Qué trae |
|---|---|---|
| `5486a3c` | `0b93a5d` | Visor (opción 3) |
| `bb5f9d0` | `e1a52ba` | El modelo congelado O2-v1.0 |
| `ddb4b42` | `ac00ae8` | Reglas v1.0 y plan de medición (§9) |
| `4ae98de` | `7ab24e1` | H1 y H2 |
| `c421eb7` | `1bcd240` | Registro en segundo plano |
| `18f4752` | `80bc853` | US500 en la población |
| `c755b64` | `f67b102` | CLAUDE.md |
| `f973060` | `ec0a65c` | Modelo P2 D600 |
| `cfadf00` | `62a1e31` | CLAUDE.md |
| `038234b` | `8e52447` | Criterio contra el azar justo |
| `a359bcc` | `c3392db` | CLAUDE.md |
| `907f255` | `bf74600` | H3 |
| `669c992` | `0cb5365` | CLAUDE.md |
| `7960190` | `8fd328f` | H3b |
| `823f28c` | `60b3ced` | CLAUDE.md |
| `d423bf7` | `f0fade9` | Tres cortes |
| `2f0a77c` | `034a37a` | CLAUDE.md |
| `f527259` | `ab611bb` | El script de la medición |
| `0761fa6` | `739ed4c` | CLAUDE.md |

Los commits del 2026-10-06 anteriores a `d1e5c36` (del prompt `76c9a53` al protocolo `60f9171`) no cambiaron.

### Adenda del 2026-10-11: el P2 sistemático se ve siempre (escrita antes de mirar ningún análisis nuevo)

- **Qué cambia.** Desde que se integra la vista P2 (`tools/p2_vista.py`, Ctrl+J en el detalle del análisis), el operador
  puede ver el P2 sistemático (D y D600) de cualquier análisis en cualquier momento, también antes de cargar el Tactical
  Audit. Es decisión del operador: se le ofreció mostrarlo solo con el análisis ya resuelto, y eligió mostrarlo siempre.
- **Qué no cambia.**
  - El P0 sistemático sigue oculto: la vista no lo muestra.
  - La decisión principal (O2-v1.0 contra el azar justo) no depende de lo que vea el operador.
  - Las reglas de H1, H2, H3a y H3b, y el script de la medición.
- **Qué significa para las conclusiones.** La población empieza después de esta adenda (no tiene ningún análisis
  todavía), así que se mide entera con el P2 sistemático a la vista.
  - En H3, las capas P1, P3 y P4 del operador pueden dejar de ser independientes del P2 sistemático, si lo mira antes de
    cargarlas. H3 responde entonces si el edge mejora con el P0 sistemático **en ese régimen**, no a ciegas.
  - Lo mismo vale para el P0 del operador en H1 y H2, aunque lo que ve es el modelo de P2, no el de P0.
  - La comparación del P2 del operador contra D (evaluación "P2 banco v2") deja de ser a ciegas desde esta fecha.

### Adenda del 2026-10-14: USTEC (US100) entra a la población (escrita antes de mirar ningún resultado de USTEC)

- **Qué cambia.** La población de v1.0, la de H1 y H2, y la de H3a y H3b suman la cuenta de US100 (símbolo MT5 `USTEC`).
  Las reglas son las mismas: ancla posterior a 2026-10-09 17:00 GT, los mismos filtros y la misma verdad (S4 estricto).
- **Por qué ahora.** La adenda de US500 dejó fuera a USTEC mientras su reloj no estuviera verificado. Se verificó el
  2026-10-13 a las 07:01 con referencias propias, y el banco recibió todo su historial (740 velas semanales).
- **Entran los análisis ya guardados.** Desde el 2026-10-09 17:00 hay dos de US100: `c01c9e93` (13-10) y `d4f26e3b`
  (14-10). No se miró el resultado de ninguno. Del primero solo se vio que O2-v1.0 no opina y que D600 da 0.
- **El modelo no cambia.** O2-v1.0 no usa 1W. Se calibró con oro y BTC, así que en USTEC queda fuera de muestra también
  por el activo, como en US500.
- **P2 sistemático.** D600 corre en USTEC (740 velas semanales; pide 600). D no corre (pide 800). H3b usa D600, así que
  cubre USTEC.
- **Cómo se reporta.** Cada activo por separado y todos juntos. Las decisiones se toman con todos juntos: los tres cortes,
  H1 y H2 a los 66 y 29 casos, y H3 a las 190 opiniones.
- **El script.** `tools/p0_prospectivo.py` suma la cuenta 003 a su lista. Nada más cambia.

### Adenda del 2026-10-15: el P0 sistemático se ve en la vista P2 con el análisis ya resuelto

- **Qué cambia.** La vista P2 (Ctrl+J en el detalle del análisis) muestra también el P0 sistemático (O2-v1.0), pero
  solo cuando el análisis ya se resolvió: tocó un nivel o pasaron las 48 h de S4 estricto. Antes de eso la vista dice
  cuánto falta, y el dato no entra al HTML. Es la opción A que eligió el operador; las otras eran mostrarlo siempre o no
  mostrarlo hasta decidir H1, H2 y H3.
- **Corrección de la adenda "el P2 sistemático se ve siempre".** Esa adenda decía que P1, P3 y P4 podían dejar de ser
  independientes si el operador miraba la vista antes de cargarlos. No es así: P0 a P4 se cargan en el asistente del
  análisis nuevo, antes de guardar, y la vista solo existe para análisis guardados. Una vista no puede tocar las capas
  de su propio análisis.
- **Lo que sí puede pasar es un aprendizaje de un análisis a otro.** Al ver cómo leyó el sistema los análisis ya
  cerrados, el operador puede ir ajustando sus lecturas siguientes, justo en los casos que miden H1 y H2 (cuando el
  sistema no opina, o cuando su P0 va contra su tesis). Por eso H1, H2 y H3 responden, desde esta fecha, para el régimen
  "con el P0 sistemático visible después de resolver", no a ciegas.
- **Lo que no cambia.** El asistente sigue sin mostrar nada sistemático mientras se carga un análisis. La decisión
  principal no depende de lo que vea el operador. Las reglas de las hipótesis y el script quedan igual.
