# Criterios de acierto (win rate) de blast_master

- **Decididos:** 2026-09-27, por el usuario.
- **Origen:** spec 002, F3 [2][5], decisiones N35, N36 y N37.
- **Evidencia:** `specs/002-auto-resolucion-velas/analisis-overlap-2d.md`.
- **Implementación:** `core/outcome_metrics.py` (RF-21 de la spec 002), la única. La usan el reporte
  `python cli/main.py resolution-report` y cualquier cuaderno (ver "Uso desde un cuaderno"). El script
  `specs/002-auto-resolucion-velas/analisis/overlap_2d.py` queda solo como la evidencia de la decisión.

**Todo análisis de acierto de este proyecto, sea en un cuaderno, un reporte o una auditoría, usa estos criterios.** Si
un análisis usa otro criterio, tiene que decirlo explícitamente y explicar por qué.

## Definiciones base

- **Ancla (inicio del análisis):** `unified_department.analysis_start_time` si existe. Si no existe, en un análisis
  normal es `created_at − 20 min`, y en un retroactivo es `created_at`, que ya es la hora tipeada.
- **Precio de partida:** el cierre de la última vela **ya cerrada** en el ancla (`time + duración(TF) <= ancla`), en la
  temporalidad más fina con reloj verificado.
- **Primer toque:** el primero de los dos niveles del análisis que toca el precio, recorriendo las velas desde el
  ancla, de la temporalidad más fina a la más gruesa (1M → 5M → 15M → 30M → 1H). Los niveles son
  `edge_validation_price` (objetivo) y `structural_invalidation` (invalidación). Las mechas cuentan.
  - **Ganó:** el precio tocó primero el objetivo.
  - **Perdió:** el precio tocó primero la invalidación.
  - **Ambiguo:** una misma vela tocó los dos niveles, aun en la temporalidad más fina. No cuenta ni como ganado ni
    como perdido.
- **Horizonte:** 2160 velas de 1H desde el ancla (`MAX_HORIZON`). Si no hay ningún toque dentro del horizonte, el
  análisis queda abierto (`open`) y no cuenta.
- **Retroactivos** (`is_backdated = 1`): **fuera de toda cifra de acierto por defecto** (R11), y se informa cuántos se
  excluyeron. Se cargaron conociendo el resultado: en la auditoría del 2026-09-24 acertaron 6 de 6.
- **Direccionales contra Choppy:** hay que reportar por separado los análisis Bullish/Bearish y los
  `Choppy / Neutral`, o al menos informar cuáles entran.

## S1: win rate principal

**Definición:** de todos los análisis con primer toque, la proporción que **ganó**. **Sin límite de tiempo**, dentro del
horizonte.

**Ejemplo:** `d62ab4d1` (BTC, Bullish, 2026-08-27) tocó su objetivo 176.9 h después (7 días). Cuenta como ganado.

**Por qué es el principal:** es la muestra más grande, no esconde pérdidas y no depende de ningún corte arbitrario.

## S4: win rate secundario ("win rate en 2 días")

**Definición:** igual que S1, pero un análisis cuenta **solo si** el primer toque ocurrió dentro de las **48 h** desde
el ancla. Los demás quedan fuera, y **siempre se informa cuántos quedaron fuera**.

**Ejemplo:** el mismo `d62ab4d1` (176.9 h) queda fuera de S4.

**Por qué va al lado de S1:** aplica la misma regla a todos los análisis y mide si las tesis se resuelven rápido. Si S1
y S4 se separan mucho, los análisis empezaron a tardar más en resolverse.

## Overlap: solo una etiqueta

**Definición:** un análisis A es "Overlap" si otro análisis B de la **misma cuenta** empezó después del inicio de A y
**antes del primer toque** de A. B puede ser retroactivo; en ese caso su inicio es la hora tipeada.

**Regla:** la etiqueta es **informativa** ("hubo un análisis nuevo antes de la resolución"). **Nunca saca a A de las
cifras de acierto:** el primer toque de A cuenta igual que en cualquier otro análisis. Todo reporte muestra, para cada
Overlap, qué nivel tocó primero y cuál es el B.

**Ejemplo:** `cf43465b` (XAU, Bullish, 2026-08-20) tuvo un B 12.1 h después, y tocó su objetivo a las 18.3 h. Es Overlap
**y** cuenta como ganado en S1 y en S4.

**`resolution_time`** es siempre la hora del primer toque, también en un Overlap. El momento del Overlap (ID de B y su
inicio) va al reporte, no a ese campo.

## Criterios descartados (no usar)

| Criterio | Qué hacía | Por qué se descartó |
|---|---|---|
| S2 | Sacar los Overlap | **Maquilla:** los Overlap tienen peor win rate. En XAU direccional, sacarlos sube el win rate de 61% a 66% sin haber operado mejor |
| S3 | Ventana de 48 h solo para los Overlap | **Inconsistente:** dos reglas distintas según si hubo Overlap |
| S3b | Ventana de 48 h desde el inicio de B | Más complejo, y no cambia el resultado (XAU igual a S1) |

## Otros cuidados al medir acierto

- **La etiqueta manual no es ground truth.** `specific_bias_compliance` va solo como columna informativa al lado del
  resultado de las velas, sin porcentaje de coincidencia. Compara bias estructurales, no niveles de precio.
- **Tamaño de muestra.** Con decenas de análisis, diferencias de 10 puntos suelen ser ruido
  (`core/stats_tests.py`). Siempre hay que mostrar n.
- **Precisión.** Hay que indicar con qué temporalidad se midió el toque (±1 min con 1M, ±15 min con 15M) y si el
  reloj de las velas está verificado.

## Uso desde un cuaderno

Con la raíz del repo en `sys.path`, como en los demás cuadernos de `jupyter/`. Todo es de solo lectura: la DB se abre
en `mode=ro` y el banco de velas solo se lee. Las rutas salen de `config/auto_resolution.py`, así que funciona con
cualquier carpeta de trabajo. `"000"` es la cuenta de XAU en `REAL_ACCOUNTS`.

```python
import os

from config.auto_resolution import ACCOUNTS_DATA_DIR, CANDLE_BANK_DIR, REAL_ACCOUNTS
from core.outcome_metrics import s1, s4
from tools.auto_resolution import AccountResolver

resolver = AccountResolver(os.path.join(ACCOUNTS_DATA_DIR, REAL_ACCOUNTS["000"]), CANDLE_BANK_DIR, account="000")
proposals = resolver.propose_all()   # el primer toque de cada análisis, según las velas
outcomes = resolver.outcomes(proposals)

for name, rate in (("S1", s1(outcomes, directional_only=True)), ("S4", s4(outcomes, directional_only=True))):
    pct = f"{rate.rate:.0%}" if rate.n else "n/a"
    print(f"{name}: {rate.wins}/{rate.n} = {pct}, outside 48 h {rate.outside}, "
          f"backdated excluded {rate.excluded_backdated}")

for p in proposals:   # Overlap: solo una etiqueta, no cambia las cifras de arriba
    if p.overlap is not None:
        print(f"{p.trade_id[:8]} Overlap: B = {p.overlap.b_id[:8]}, first touch {p.resolution_time}")
```

- `directional_only=True` deja solo los Bullish/Bearish. Sin ese argumento entran también los `Choppy / Neutral`.
- `include_backdated=True` mete a los retroactivos. Por defecto quedan fuera y se cuentan en `excluded_backdated`.
- Un análisis sin toque (abierto, ambiguo, sin niveles, sin reloj verificado) no cuenta. El motivo está en
  `proposal.reason`.

## Foto al 2026-09-27

Direccionales, sin retroactivos, con velas de 15M y 1H:

| | XAU | BTC |
|---|---|---|
| S1 | 23/38 = 61% | 9/12 = 75% |
| S4 | 23/36 = 64% (2 fuera) | 8/10 = 80% (2 fuera) |
| Etiqueta manual, mismos análisis que S1 | 24/38 = 63% | 8/12 = 67% |

Esta foto es de esa fecha. Hay que recalcularla antes de citarla, con `python cli/main.py resolution-report` o con
el ejemplo de arriba.
