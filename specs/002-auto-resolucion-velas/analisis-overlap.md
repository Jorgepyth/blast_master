# Análisis de trazabilidad: Overlap (F3 [2][5] y [2][6])

- **Fecha:** 2026-09-26. Solo lectura sobre las DBs reales (`mode=ro&immutable=1`) y los CSV de MT5.
- **Alcance:** XAU y BTC, que son las cuentas con reloj verificado. US500 no tiene velas y US100 no calibra.
- **Regla evaluada:** N11 más N22. A es Overlap si otro análisis B de la misma cuenta, retroactivo o no, empieza
  después del inicio de A y antes del primer toque de A.
- **Inicio (ancla):** `created_at − 20 min` (N4). En los retroactivos, la hora tipeada (`created_at`, N5).
- **Precio de partida:** el cierre de la última vela 15M cerrada en el ancla.
- **Camino hacia adelante:** velas 15M desde el ancla y, cuando se acaban, 1H. **No hay 1M ni 5M validados
  todavía**, así que la hora del toque tiene una precisión de ±15 min.
- **Columnas:**
  - "Primer toque (velas)" dice qué nivel tocó primero el precio **aunque el análisis sea Overlap**;
  - "h A→B" es el tiempo desde el inicio de A hasta el inicio de B;
  - "h A→toque" es el tiempo desde el inicio de A hasta el primer toque.
- Script: los scripts originales estaban en el scratchpad de la sesión y se perdieron. La versión reproducible es
  `analisis/overlap_2d.py`, que regenera la lista de candidatos y el win rate S1/S2 con los mismos números.

## Estado de todos los análisis de XAU y BTC

| Estado | n |
|---|---|
| Resuelto por velas, sin Overlap | 70 |
| **Candidato a Overlap** (un B empezó antes del primer toque) | **21** |
| Sin niveles (EVP o SI vacío) | 6 |
| Sin velas en el inicio (BTC antes del 2026-07-12, sin 15M) | 6 |
| Niveles del mismo lado del precio de partida | 1 |
| Sin toque dentro de las velas y sin B (caso [2][6] sin B) | 1 (XAU `792518cd`, 2026-09-16 11:32) |
| **Sin toque dentro de las velas, con B (caso [2][6])** | **0** |

## Los 21 candidatos a Overlap

| # | Cuenta | ID A | Creado A | Retro A | Market bias A | Bias A | Real bias B | Compliance | Tipo manual | Structural manual | Failure manual | ID B | Inicio B | Market bias B | Retro B | ¿Mismo bias? | h A→B | Primer toque (velas) | h A→toque |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | XAU | 386ea615 | 2026-06-08 06:51 |  | Choppy / Neutral | CHOCH | No_Bias(Choppy) | Invalid | Invalidated  |  | Regime Decay --  | 7b6e9c10 | 2026-06-09 06:35 | Bullish |  | no | 24.1 | INVALIDATION | 26.2 |
| 2 | XAU | 00e2e31b | 2026-06-23 07:24 |  | Bearish | BOS | BOS | Valid | Confirmed (A | Confirmed + expansión si |  | 9fb9e581 | 2026-06-23 08:00 | Bearish | sí | sí | 0.9 | VALIDATION | 23.2 |
| 3 | XAU | d55f09b2 | 2026-06-26 05:26 |  | Bullish | Trend Reversal | Trend Reversal | Valid | Confirmed (A | Confirmed pero mínima |  | 478d3315 | 2026-06-29 06:10 | Bearish |  | no | 73.1 | INVALIDATION | 74.9 |
| 4 | XAU | f24b9653 | 2026-07-09 05:30 | sí | Bearish | BOS | BOS | Valid | Confirmed (A | Confirmed + expansión si |  | 7ae2bfe8 | 2026-07-10 05:30 | Bearish | sí | sí | 24.0 | VALIDATION | 98.8 |
| 5 | XAU | 85d8f20c | 2026-07-26 16:58 |  | Bullish | BOS | BOS | Valid | Confirmed (A | Confirmed pero mínima |  | 2237dc83 | 2026-07-27 05:17 | Choppy / Neutral |  | no | 12.6 | INVALIDATION | 15.9 |
| 6 | XAU | 12e03eed | 2026-08-02 18:31 |  | Choppy / Neutral | BOS | CHOCH | Invalid | Overlap Inva |  | Overlap -- nuevo | d6318133 | 2026-08-03 05:20 | Bearish |  | no | 11.1 | INVALIDATION | 13.1 |
| 7 | XAU | 68ff0f26 | 2026-08-09 18:02 |  | Choppy / Neutral | No_Bias(Choppy) | No_Bias(Choppy) | Valid | Confirmed (A | Confirmed pero mínima |  | 211a3584 | 2026-08-10 05:14 | Choppy / Neutral |  | sí | 11.5 | INVALIDATION | 24.3 |
| 8 | XAU | 8812cccd | 2026-08-11 05:35 |  | Choppy / Neutral | Trend Reversal | BOS | Invalid | Invalidated  |  | Reversal --prefi | 9b0f4eef | 2026-08-12 05:49 | Bullish |  | no | 24.6 | VALIDATION | 61.7 |
| 9 | XAU | 65c7627b | 2026-08-17 06:32 |  | Choppy / Neutral | BOS | Choppy-Bullish Range Rotation | Invalid | Invalidated  |  | Overlap -- nuevo | fdb60a42 | 2026-08-18 06:00 | Bearish |  | no | 23.8 | VALIDATION | 48.8 |
| 10 | XAU | fdb60a42 | 2026-08-18 06:20 |  | Bearish | BOS | Choppy-Bearish Range Rotation | Invalid | Invalidated  |  | Reversal --prefi | 285714b9 | 2026-08-19 06:05 | Choppy / Neutral |  | no | 24.1 | INVALIDATION | 25.0 |
| 11 | XAU | cf43465b | 2026-08-20 06:32 |  | Bullish | BOS | BOS | Valid | Confirmed (A | Confirmed + expansión si |  | 0df23244 | 2026-08-20 18:17 | Bullish |  | sí | 12.1 | VALIDATION | 18.3 |
| 12 | XAU | 47947184 | 2026-08-24 06:14 |  | Bullish | BOS | Trend Reversal | Invalid | Overlap Inva | Confirmed pero mínima | Overlap -- nuevo | fe37ea56 | 2026-08-25 18:34 | Bearish |  | no | 36.7 | INVALIDATION | 51.1 |
| 13 | XAU | cbac26ff | 2026-08-27 06:22 |  | Choppy / Neutral | BOS | BOS | Valid | Confirmed (A | Confirmed + expansión si |  | 464efa51 | 2026-08-27 18:07 | Choppy / Neutral |  | sí | 12.1 | VALIDATION | 26.0 |
| 14 | XAU | 4aa4b9a3 | 2026-08-30 16:35 |  | Choppy / Neutral | BOS | BOS | Valid | Overlap Inva | Confirmed pero mínima |  | 21e49870 | 2026-08-31 05:58 | Bullish |  | no | 13.7 | VALIDATION | 34.2 |
| 15 | BTC | 5971536e | 2026-08-05 06:58 |  | Bullish | BOS | BOS | Valid | Confirmed (A | Confirmed pero mínima |  | 6793c370 | 2026-08-06 05:16 | Choppy / Neutral |  | no | 22.6 | VALIDATION | 46.6 |
| 16 | BTC | 22bb5efc | 2026-08-12 07:00 |  | Choppy / Neutral | CHOCH | BOS | Invalid | Overlap Inva |  | Overlap -- nuevo | 4fbd26c0 | 2026-08-13 06:19 | Choppy / Neutral |  | sí | 23.7 | INVALIDATION | 27.8 |
| 17 | BTC | 4fbd26c0 | 2026-08-13 06:39 |  | Choppy / Neutral | No_Bias(Choppy) | BOS | Invalid | Overlap Inva |  | Regime Decay --  | b0c8c373 | 2026-08-17 06:20 | Bullish |  | no | 96.0 | INVALIDATION | 101.9 |
| 18 | BTC | ecf1153a | 2026-08-18 07:05 |  | Bullish | BOS | BOS | Valid | Confirmed (A | Confirmed + expansión si |  | 57c1dd0c | 2026-08-19 06:26 | Bullish |  | sí | 23.7 | VALIDATION | 25.2 |
| 19 | BTC | c73353ad | 2026-08-25 19:12 |  | Choppy / Neutral | BOS | No_Bias(Choppy) | Invalid | Invalidated  |  | Regime Decay --  | 3565cf47 | 2026-08-26 05:58 | Bearish |  | no | 11.1 | INVALIDATION | 177.9 |
| 20 | BTC | d62ab4d1 | 2026-08-27 06:57 |  | Bullish | BOS | Trend Reversal | Invalid | Invalidated  |  | Reversal --prefi | ad5f44a6 | 2026-08-27 18:16 | Bullish |  | sí | 11.6 | VALIDATION | 176.9 |
| 21 | BTC | 4eb0c637 | 2026-08-30 16:48 |  | Bearish | BOS | Validated Range Expansion | Invalid | Invalidated  |  | Reversal --prefi | c77654ac | 2026-09-01 18:19 | Choppy / Neutral |  | no | 49.9 | INVALIDATION | 88.3 |

**Resumen de los 21:**
- **Primer toque según velas:** VALIDATION 10 e INVALIDATION 11.
- **Lo que marcaste a mano:** Confirmed 9, Invalidated 7 y Overlap 5.
- **B con el mismo market bias que A:** 8. Con un bias distinto: 13.
- **B retroactivo:** 2.

## Efecto sobre el win rate (sin retroactivos, R11)

| Grupo | Cuenta | Win rate por velas (tocó validation primero) | Compliance manual (Valid) |
|---|---|---|---|
| Todos los resueltos (sin retroactivos) | XAU | 38/67 = 57% | 39/67 = 58% |
| Sin los candidatos a Overlap (sin retroactivos) | XAU | 32/54 = 59% | 32/54 = 59% |
| Solo candidatos a Overlap (sin retroactivos) | XAU | 6/13 = 46% | 7/13 = 54% |
| Todos los resueltos (sin retroactivos) | BTC | 9/17 = 53% | 8/17 = 47% |
| Sin los candidatos a Overlap (sin retroactivos) | BTC | 6/10 = 60% | 6/10 = 60% |
| Solo candidatos a Overlap (sin retroactivos) | BTC | 3/7 = 43% | 2/7 = 29% |

**Lectura:** los candidatos a Overlap tienen **peor** win rate que el resto: 46% contra 59% en XAU, y 43% contra
60% en BTC. Sacarlos del cálculo **sube** el win rate de 57% a 59% en XAU y de 53% a 60% en BTC. O sea, dejar
afuera los Overlap hace que el edge parezca mejor de lo que es.
