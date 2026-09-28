# Overlap con ventana de 2 días (F3 [2][5], opción del usuario)

- **Fecha:** 2026-09-27. Solo lectura sobre las DBs reales y los CSV de MT5.
- **Script:** `specs/002-auto-resolucion-velas/analisis/overlap_2d.py`, reproducible con
  `conda run -n blast_master python overlap_2d.py <salida.md>`.
- **Reglas:**
  - ancla `created_at − 20 min`; en los retroactivos, la hora tipeada;
  - velas 15M y, cuando se acaban, 1H (todavía no hay 1M ni 5M validados), así que la hora del toque tiene una
    precisión de ±15 min;
  - B puede ser retroactivo (N22).
- **Tu opción:** marcar Overlap y **seguir evaluando** si el precio llega a validation o invalidation dentro de
  2 días. Hay dos formas de medir esos 2 días, y las muestro las dos:
  - **desde A:** 48 h desde el inicio de A. Es una "vida útil" de 2 días para la tesis;
  - **desde B:** 48 h desde que empezó B, el momento del Overlap.
- **Retroactivos:** fuera de las cifras de acierto (R11).

## Los 21 candidatos a Overlap, con la ventana de 2 días

| # | Cuenta | ID A | Creado A | Retro | Market bias A | Tipo manual | Compliance | ID B | h A→B | Primer toque (sin límite) | h A→toque | ¿Tocó en 48 h desde A? | ¿Tocó en 48 h desde B? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | XAU | 386ea615 | 2026-06-08 06:51 |  | Choppy / Neutral | Invalidated | Invalid | 7b6e9c10 | 24.1 | INVALIDATION | 26.2 | INVALIDATION | INVALIDATION |
| 2 | XAU | 00e2e31b | 2026-06-23 07:24 |  | Bearish | Confirmed | Valid | 9fb9e581 | 0.9 | VALIDATION | 23.2 | VALIDATION | VALIDATION |
| 3 | XAU | d55f09b2 | 2026-06-26 05:26 |  | Bullish | Confirmed | Valid | 478d3315 | 73.1 | INVALIDATION | 74.9 | — | INVALIDATION |
| 4 | XAU | f24b9653 | 2026-07-09 05:30 | sí | Bearish | Confirmed | Valid | 7ae2bfe8 | 24.0 | VALIDATION | 98.8 | — | — |
| 5 | XAU | 85d8f20c | 2026-07-26 16:58 |  | Bullish | Confirmed | Valid | 2237dc83 | 12.6 | INVALIDATION | 15.9 | INVALIDATION | INVALIDATION |
| 6 | XAU | 12e03eed | 2026-08-02 18:31 |  | Choppy / Neutral | Overlap Invalidation | Invalid | d6318133 | 11.1 | INVALIDATION | 13.1 | INVALIDATION | INVALIDATION |
| 7 | XAU | 68ff0f26 | 2026-08-09 18:02 |  | Choppy / Neutral | Confirmed | Valid | 211a3584 | 11.5 | INVALIDATION | 24.3 | INVALIDATION | INVALIDATION |
| 8 | XAU | 8812cccd | 2026-08-11 05:35 |  | Choppy / Neutral | Invalidated | Invalid | 9b0f4eef | 24.6 | VALIDATION | 61.7 | — | VALIDATION |
| 9 | XAU | 65c7627b | 2026-08-17 06:32 |  | Choppy / Neutral | Invalidated | Invalid | fdb60a42 | 23.8 | VALIDATION | 48.8 | — | VALIDATION |
| 10 | XAU | fdb60a42 | 2026-08-18 06:20 |  | Bearish | Invalidated | Invalid | 285714b9 | 24.1 | INVALIDATION | 25.0 | INVALIDATION | INVALIDATION |
| 11 | XAU | cf43465b | 2026-08-20 06:32 |  | Bullish | Confirmed | Valid | 0df23244 | 12.1 | VALIDATION | 18.3 | VALIDATION | VALIDATION |
| 12 | XAU | 47947184 | 2026-08-24 06:14 |  | Bullish | Overlap Invalidation | Invalid | fe37ea56 | 36.7 | INVALIDATION | 51.1 | — | INVALIDATION |
| 13 | XAU | cbac26ff | 2026-08-27 06:22 |  | Choppy / Neutral | Confirmed | Valid | 464efa51 | 12.1 | VALIDATION | 26.0 | VALIDATION | VALIDATION |
| 14 | XAU | 4aa4b9a3 | 2026-08-30 16:35 |  | Choppy / Neutral | Overlap Invalidation | Valid | 21e49870 | 13.7 | VALIDATION | 34.2 | VALIDATION | VALIDATION |
| 15 | BTC | 5971536e | 2026-08-05 06:58 |  | Bullish | Confirmed | Valid | 6793c370 | 22.6 | VALIDATION | 46.6 | VALIDATION | VALIDATION |
| 16 | BTC | 22bb5efc | 2026-08-12 07:00 |  | Choppy / Neutral | Overlap Invalidation | Invalid | 4fbd26c0 | 23.7 | INVALIDATION | 27.8 | INVALIDATION | INVALIDATION |
| 17 | BTC | 4fbd26c0 | 2026-08-13 06:39 |  | Choppy / Neutral | Overlap Invalidation | Invalid | b0c8c373 | 96.0 | INVALIDATION | 101.9 | — | INVALIDATION |
| 18 | BTC | ecf1153a | 2026-08-18 07:05 |  | Bullish | Confirmed | Valid | 57c1dd0c | 23.7 | VALIDATION | 25.2 | VALIDATION | VALIDATION |
| 19 | BTC | c73353ad | 2026-08-25 19:12 |  | Choppy / Neutral | Invalidated | Invalid | 3565cf47 | 11.1 | INVALIDATION | 177.9 | — | — |
| 20 | BTC | d62ab4d1 | 2026-08-27 06:57 |  | Bullish | Invalidated | Invalid | ad5f44a6 | 11.6 | VALIDATION | 176.9 | — | — |
| 21 | BTC | 4eb0c637 | 2026-08-30 16:48 |  | Bearish | Invalidated | Invalid | c77654ac | 49.9 | INVALIDATION | 88.3 | — | INVALIDATION |

- **48 h desde A:** VALIDATION 6, INVALIDATION 6 y sin toque 9.
- **48 h desde B:** VALIDATION 8, INVALIDATION 10 y sin toque 3.

## Win rate por velas según cada criterio (sin retroactivos)

Win rate = tocó validation primero, sobre los análisis que tocaron un nivel. "Fuera" son los que el criterio deja sin resultado.

| Criterio | Cuenta | Win rate | Fuera |
|---|---|---|---|
| S1: primer toque de todos, sin límite | XAU | 38/67 = 57% | 0 |
| S2: sin los Overlap | XAU | 32/54 = 59% | 0 |
| S3: Overlap solo si tocó en 48 h **desde A**; el resto sin límite | XAU | 36/63 = 57% | 4 |
| S3b: Overlap solo si tocó en 48 h **desde B**; el resto sin límite | XAU | 38/67 = 57% | 0 |
| S4: ventana de 48 h desde A para **todos** | XAU | 36/62 = 58% | 5 |
| S1: primer toque de todos, sin límite | BTC | 9/17 = 53% | 0 |
| S2: sin los Overlap | BTC | 6/10 = 60% | 0 |
| S3: Overlap solo si tocó en 48 h **desde A**; el resto sin límite | BTC | 8/13 = 62% | 4 |
| S3b: Overlap solo si tocó en 48 h **desde B**; el resto sin límite | BTC | 8/15 = 53% | 2 |
| S4: ventana de 48 h desde A para **todos** | BTC | 8/13 = 62% | 4 |

## Win rate solo de análisis direccionales (Bullish o Bearish), sin retroactivos

Mismos criterios que la tabla anterior, pero sin los análisis `Choppy / Neutral`. **Win rate manual** es la proporción de `specific_bias_compliance = Valid` sobre **los mismos análisis** que cuenta cada criterio.

| Criterio | Activo | Win rate por velas | Win rate manual (mismos análisis) | Fuera |
|---|---|---|---|---|
| S1: primer toque de todos, sin límite | XAU | 23/38 = 61% | 24/38 = 63% | 0 |
| S2: sin los Overlap | XAU | 21/32 = 66% | 20/32 = 62% | 0 |
| S3: Overlap solo si tocó en 48 h desde A | XAU | 23/36 = 64% | 23/36 = 64% | 2 |
| S3b: Overlap solo si tocó en 48 h desde B | XAU | 23/38 = 61% | 24/38 = 63% | 0 |
| S4: ventana de 48 h desde A para todos | XAU | 23/36 = 64% | 23/36 = 64% | 2 |
| S1: primer toque de todos, sin límite | BTC | 9/12 = 75% | 8/12 = 67% | 0 |
| S2: sin los Overlap | BTC | 6/8 = 75% | 6/8 = 75% | 0 |
| S3: Overlap solo si tocó en 48 h desde A | BTC | 8/10 = 80% | 8/10 = 80% | 2 |
| S3b: Overlap solo si tocó en 48 h desde B | BTC | 8/11 = 73% | 8/11 = 73% | 1 |
| S4: ventana de 48 h desde A para todos | BTC | 8/10 = 80% | 8/10 = 80% | 2 |

**Estado original** (win rate manual de **todos** los análisis Bullish/Bearish sin retroactivos, tengan o no resultado por velas):

- **XAU:** 25/39 = 64%. Por estado: {'resolved': 32, 'overlap': 6, 'levels_same_side': 1, 'no_touch': 1}.
- **BTC:** 9/17 = 53%. Por estado: {'resolved': 8, 'no_history': 5, 'overlap': 4}.

**Coherencia bias ↔ niveles:** en 0 de 50 análisis direccionales, la dirección que marcan tus niveles (validation arriba = long) contradice el market bias.

## Tu etiqueta manual contra las velas (todos los resueltos, sin retroactivos)

`specific_bias_compliance` compara bias estructurales (A contra B), y las velas miran qué nivel se tocó primero. La correspondencia Valid ↔ VALIDATION es **aproximada**. Estos son los casos donde no coinciden, para tu revisión:

| # | Cuenta | ID | Creado | Market bias | Bias A | Real bias B | Compliance | Tipo manual | Primer toque (velas) | h A→toque | ¿Overlap? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | XAU | 4b17b903 | 2026-06-15 05:34 | Choppy / Neutral | BOS | BOS | Valid | Confirmed | INVALIDATION | 6.8 |  |
| 2 | XAU | d55f09b2 | 2026-06-26 05:26 | Bullish | Trend Reversal | Trend Reversal | Valid | Confirmed | INVALIDATION | 74.9 | sí |
| 3 | XAU | 85d8f20c | 2026-07-26 16:58 | Bullish | BOS | BOS | Valid | Confirmed | INVALIDATION | 15.9 | sí |
| 4 | XAU | 68ff0f26 | 2026-08-09 18:02 | Choppy / Neutral | No_Bias(Choppy) | No_Bias(Choppy) | Valid | Confirmed | INVALIDATION | 24.3 | sí |
| 5 | XAU | 8812cccd | 2026-08-11 05:35 | Choppy / Neutral | Trend Reversal | BOS | Invalid | Invalidated | VALIDATION | 61.7 | sí |
| 6 | XAU | 65c7627b | 2026-08-17 06:32 | Choppy / Neutral | BOS | Choppy-Bullish Range Rotation | Invalid | Invalidated | VALIDATION | 48.8 | sí |
| 7 | XAU | 887dbdc1 | 2026-09-01 18:26 | Bearish | BOS | Trend Reversal | Invalid | Confirmed | VALIDATION | 3.4 |  |
| 8 | BTC | d62ab4d1 | 2026-08-27 06:57 | Bullish | BOS | Trend Reversal | Invalid | Invalidated | VALIDATION | 176.9 | sí |

- **XAU:** coinciden 60 de 67. Marcaste Valid y las velas dicen INVALIDATION: 4. Marcaste Invalid y las velas dicen VALIDATION: 3.
- **BTC:** coinciden 16 de 17. Marcaste Valid y las velas dicen INVALIDATION: 0. Marcaste Invalid y las velas dicen VALIDATION: 1.

## Lectura (interpretación, no dato)

1. **La ventana de 2 días deja muchos Overlap sin resultado si se cuenta desde A:** 9 de 21. Contada desde B, solo 3 de 21.
2. **Con todos los análisis, el win rate de XAU casi no cambia** con ningún criterio: entre 57% y 59%. BTC oscila entre 53% y 62%, pero con solo 13 a 17 análisis. Con esa muestra, 9 puntos son ruido (CLAUDE.md, checklist punto 4).
3. **Solo direccionales:** en XAU, las velas dan 61% (S1) y tu etiqueta manual 63% sobre los mismos 38 análisis. La diferencia es de 1 análisis. Sin retroactivos, tu etiqueta da 64% (25/39); con los 7 retroactivos daba 68.9% (31/45), la cifra de la auditoría. **La mayor parte de esa diferencia venía de los retroactivos.** En BTC, con 8 a 12 análisis, cualquier porcentaje es ruido.
4. **El hallazgo más útil:** de los 8 casos en que tu etiqueta manual no coincide con las velas, **6 son candidatos a Overlap**. Cuando existía un análisis B posterior, tu etiqueta de A tendió a seguir la vista nueva (la de B) y no lo que hizo el precio con los niveles de A.
5. **Caso a revisar:** `887dbdc1` (XAU, 2026-09-01) tiene tipo manual "Confirmed" y compliance "Invalid". Las dos etiquetas se contradicen entre sí.
