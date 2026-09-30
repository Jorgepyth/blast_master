---
verdict: APPROVED
---
# Consistencia — Spec 002 (F6)

## Historia de esta auditoría

- **Primera pasada (2026-09-27):** `BLOCKED` por dos hallazgos críticos:
  - **K1:** la migración de las DBs reales ocurría sin las puertas de R9.
  - **K2:** el catch-up del P2 del modelo D registraba los análisis históricos en el registro "prospectivo".
- **Resolución (2026-09-28), aprobada por el usuario:**
  - **K1:** tarea 🖐 nueva **T58**, entre la integración (T57) y el banco real (T59). Migra las 4 DBs con las 3
    puertas, desde un comando de Python aislado, antes de usar el CLI nuevo. Quedó registrada como decisión N41 y en
    NFR-1. Las tareas siguientes se renumeraron: el banco real pasó a T59 y la validación final a T64.
  - **K2:** opción (a). El catch-up (RF-12b) solo registra análisis **nuevos**: con `analysis_start_time`, no
    retroactivos ni clones [2] (decisión N40). El "Hecho cuando" de T56 exige un test que lo verifique.
- **Segunda pasada (2026-09-28):** sin fallos críticos. Veredicto `APPROVED`.
- **Tercera pasada (2026-09-28), por un cambio aprobado por el usuario después de F6:** decisión **N42**. Los modelos
  del registro prospectivo salen de `P2_LOG_MODELS` (nombre → fecha de alta) en lugar de `MODEL_D` fijo, con una
  línea por análisis y modelo, y la receta con su huella en cada línea. Cambios:
  - **spec:** RF-12, RF-12b, RF-12c y RF-12d reescritos; RF-12e nuevo (70 RF); `P2_LOG_MODELS` en la configuración;
    una línea más en "Fuera de alcance";
  - **plan:** §2.5, módulos, contrato de `p2-model`, decisión T23 y cobertura;
  - **tareas:** T8, T55, T56 y la tabla de cobertura. No hay tareas nuevas: siguen siendo 64, consecutivas.

  Se volvieron a correr C1, C2, C4 y la contradicción interna: los 70 RF y los 8 INV aparecen en `plan.md` y en
  `tasks.md` (script que expande grupos). RF-12e depende de T8 (la constante) y de T55 (los chequeos), en ese orden.
  N38 y N40 siguen valiendo: N42 los amplía de "modelo D" a "cada modelo de la lista". Veredicto: sigue `APPROVED`.
- **Cuarta pasada (2026-09-30), por un cambio aprobado por el usuario ("ejecutemos N43"):** decisión **N43**. Un
  símbolo sin superposición y con menos de 10 referencias puede heredar el reloj verificado de otro símbolo del
  mismo servidor, con el mismo desfase base y la misma regla, si tiene al menos 5 referencias propias y calzan
  todas. Cambios:
  - **spec:** RF-2 pasa de dos caminos a tres; RF-2e nuevo (71 RF); E2b, los casos límite de US500 y US100, y la
    clave `INHERIT_MIN_OWN_REFERENCES`;
  - **plan:** §2.3 (`verified_export` en `status.json`), §2.4 (líneas `SERVER:` y `BASE_UTC_OFFSET:`), §3.8 paso 4b,
    decisión T24 y cobertura;
  - **tareas:** T22b (código) y T59b (🖐, banco real), agregadas sin renumerar; tabla de cobertura.

  Antes de editar se revisó el cambio contra la spec (regla anti-deriva de F9): N29 ("solo si al menos 10") queda
  ampliada por N43, sin contradicción. RF-4e no cambia, porque "verificado" incluye el heredado. RF-2c queda fuera de
  la herencia a propósito. La herencia nunca pasa por encima de un `clock_misaligned`. Se volvieron a correr C1, C2 y
  la contradicción interna: RF-2e aparece en `plan.md` y en `tasks.md`. Sin dependencias nuevas (constitución,
  principio 1): `mt5.account_info()` es de solo lectura, como el resto del exportador. Veredicto: sigue `APPROVED`.
- **Quinta pasada (2026-09-30), por un defecto encontrado en el banco real y una corrección aprobada por el usuario:**
  decisión **N44**. N39 dejaba entrar completas las TF de 4H o más también en el import legacy, cuyos CSV tienen un
  solo desfase, y el banco real quedó con 3.824 velas de invierno duplicadas, corridas 1 h. Lo encontró otra sesión (P2
  banco v2) y se confirmó en solo lectura. Es un defecto de la spec (N39 decía algo equivocado para el import legacy),
  así que la spec cambia primero (F9). Además, contradecía a la decisión T17 del plan, que ya pedía el filtro en todo
  el import. Cambios:
  - **spec:** N39 corregida, N44 nueva, RF-15c;
  - **plan:** §3.8 (import inicial y limpieza única) y decisión T17;
  - **tareas:** T16c (código) y T59c (🖐, limpieza del banco real), agregadas sin renumerar.

  Contradicción interna: la limpieza quita filas, y R4 dice "el banco nunca pierde velas". N44 lo registra como
  única excepción: cada vela queda una vez, con su hora correcta. `_assert_bank_not_regressed` sigue protegiendo toda
  fusión; la limpieza no es una fusión, y verifica aparte que solo se quitaron las copias. Veredicto: sigue
  `APPROVED`.

## Resultado de la segunda pasada

| # | Comprobación | Resultado | Detalle |
|---|---|---|---|
| C1 | Cobertura RF → plan | OK | Los 69 RF (70 desde la tercera pasada) y los 8 INV aparecen en `plan.md` (verificado con un script que expande grupos) |
| C2 | Cobertura RF → tareas | OK | Los 77 identificadores tienen al menos una tarea. Hay 64 tareas, numeradas de forma consecutiva |
| C3 | Tareas huérfanas | OK | T1 (punto de partida) y T57 (integración) son andamiaje declarado. T58 cita NFR-1 e INV-3 |
| C4 | Orden de dependencia | OK | K1 está resuelto: T58 migra antes de que T59 y siguientes usen el CLI nuevo. Ver la observación O1 |
| C5 | "Hecho cuando" ejecutable | OK, con observación | O4 |
| C6 | Constitución | OK, con observación | El principio 5 se cumple con T58 y las puertas del backfill (T50, T63). Ver O2 (principio 3) |
| C7 | Duplicación de requisitos | OK | RF-1d y RF-20f comparten el candado, y RF-20f remite a RF-1d. No se contradicen |
| C8 | Datos sin ubicación | OK, con observación | O3 |
| C9 | Colisión entre specs | OK | Solo existe la 002 en `specs/`; la 001 está archivada. Sin choque con los gates Tier D/F, emocional ni Stop Deviation |
| — | Contradicción interna | OK | K2 está resuelto: RF-12, RF-12b y N38/N40 coinciden en "solo análisis nuevos". La spec no tiene `[NECESITA ACLARACIÓN]` |

## Observaciones no críticas, a tener en cuenta en sus tareas

- **O1 (C4), en T14, T30 y T32:** leen columnas que recién crea T35. Funcionan porque RF-6b trata una columna
  ausente como vacía; sus tests tienen que cubrir DBs de fixture **con** y **sin** esas columnas.
- **O2 (C6, principio 3), en T33 y T50/T51:** el resumen en consola del reporte y la confirmación `APPLY` del
  backfill tienen que vivir en `cli/` (el subcomando), no en `tools/`.
- **O3 (C8), en T33:** hay que definir la carpeta de salida por defecto de `resolution-report` y documentarla en el
  plan al implementar la tarea.
- **O4 (C5), en T60:** la parte ejecutable es generar el `.md` y mostrar el S1; comparar con el 61% es una lectura
  manual.
