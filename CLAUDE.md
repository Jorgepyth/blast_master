# CLAUDE.md — blast_master

Espejo operativo de [ARCHITECTURE.md](ARCHITECTURE.md) (Fase 3 de un research de arquitectura verificado por comando — ver ese documento para el detalle completo con evidencia). `gemini.md` está confirmado obsoleto (Fase 1: describe un esquema plano single-DB con `risk_tier`, que no existe en el código — 0 ocurrencias verificadas por grep) — no lo uses como referencia de arquitectura.

## Qué es este repo

CLI interactivo (Click + Rich) de trading journal, multi-cuenta/multi-activo. Cada cuenta tiene su propia SQLite en `.data/flight_account_{cuenta}_{activo}.db`. El core calcula un "edge" direccional (`calc_edge`/`i_cd`) que alimenta un modelo de probabilidades, pasa por dos auditorías (eficiencia + táctica) y opcionalmente sincroniza a Notion vía un proceso separado.

## Comandos

- Ejecutar el CLI: `python cli/main.py` (o vía el entrypoint `cli()`, vive en `cli/main.py:797`).
- Tests: `pytest` desde la raíz (`pytest.ini` solo fija `pythonpath = .`, sin `testpaths` — un `pytest` sin argumentos también intentará recolectar `scratch/test_*.py`, que son exploratorios, no la suite oficial; para correr solo la suite formal usa `pytest tests/`).
- Sync manual a Notion: `tools/notion_sync.py` se invoca como proceso separado (`conda run -n blast_master env PYTHONPATH=. python tools/notion_sync.py`), no como import — ver `cli/main.py:859`.

## Arquitectura (resumen — detalle en ARCHITECTURE.md §2-§6)

`cli/main.py` es el hub: importa directamente `cli/ui_manager.py`, `cli/schemas/*.py`, `core/math_engine.py` y `tools/database.py`. No es una cadena lineal — `ui_manager.py` también importa `tools/database.py` de forma independiente. `tools/notion_sync.py` corre como subproceso de SO aparte, no in-process.

Modelo de datos real: 7 tablas (`unified_department`, `analysis_layer`, `efficiency_audit`, `tactical_audit`, `asset_config`, `asset_balance`, `emotion_catalog`) — las últimas dos sin ninguna clase ORM referenciada fuera de su propia definición (ver "Antes de tocar" abajo). Diagrama ER y de estados completos en ARCHITECTURE.md §5.

## Antes de tocar código en este repo

- **La fórmula del edge (`i_cd`) está duplicada en 3 sitios** (`cli/main.py:2091-2100`, `2187-2196`, `3963-3972`). Si cambias la fórmula, cámbiala en los 3 o vas a introducir una divergencia silenciosa. El tercer sitio usa comparación por strings en vez de enums y está autoetiquetado `# Defect 3` en su propio comentario.
- **`get_active_engine()` (`cli/main.py:251`, guard de migración en `cli/main.py:263-268`) y `init_db()` (`tools/database.py:227`) hardcodean `flight_account_001_xauusd.db`** como cuenta por defecto/destino de migración legacy. Si trabajas en lógica multi-cuenta, no asumas que esta ruta se generaliza a otras cuentas — no lo hace.
- **`AssetBalance` y `EmotionCatalog`** (`tools/database.py:51`, `:58`) están definidas pero no se usan en ningún otro punto del código — no las asumas como parte de un flujo activo sin confirmar primero con grep.
- **Variables de entorno del modelo de probabilidades (`ICD_NO_TRADE_MAX`, `ICD_NO_TRADE_MIN`, `ICD_EDGE_CAP`) no están en `.env.template`** — si tocas `core/math_engine.py`, revisa el `.env` real del usuario, no solo la plantilla. A la inversa, `TACTICAL_SCALING_FACTOR` está en la plantilla pero no se lee en ningún sitio — no confíes en que cambiarla tenga efecto.
- **`jupyter/iterations.ipynb` ya no contiene la celda de `DROP TABLE`** que causó el incidente de producción del 27-jul-2026 — fue eliminada del notebook (verificado directamente: la celda ya no existe en el árbol de trabajo). Esto ya no es un riesgo activo; se deja la referencia solo para contexto histórico. Ver ARCHITECTURE.md §9(c) para el historial completo del incidente.
- **`TierSetup` es `A, B, C, D, F, SKIP`** (`cli/schemas/audit_tactical.py:13`) — no existe nivel "E". No asumas un rango continuo A-F.
- **3 de los 8 valores de `LifecycleState`** (`PENDING_TACTICS`, `OPEN`, `COMPLETED`) no tienen transición de escritura confirmada en el código — si necesitas usarlos, verifica primero si de verdad hay un flujo que los setea o si vas a ser el primero en escribir esa transición.

## Estándar de este repo para research/arquitectura

Este proyecto ha tenido descripciones de arquitectura contradictorias entre sesiones (ver ARCHITECTURE.md, encabezado). El estándar establecido: **ningún claim arquitectónico se acepta sin comando verificado**, citado con archivo:línea. No repitas narrativa de sesiones anteriores (incluida la de este mismo archivo) sin volver a verificarla si ha pasado tiempo o el código pudo haber cambiado — trata este documento como snapshot verificado en su fecha de generación, no como fuente perpetua de verdad.
