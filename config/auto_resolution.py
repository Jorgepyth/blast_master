"""
config/auto_resolution.py — Constantes versionadas de la spec 002 (auto-resolución
con velas): reglas de negocio (horizontes, tolerancias), mapeos de cuentas/símbolos,
códigos de motivo, y rutas de la máquina resueltas desde `.env`.

Reglas de negocio (horizontes, tolerancias, cuentas, símbolos, modelos registrados)
van fijas acá, versionadas con el código -- son decisiones del proyecto, no de la
máquina (plan.md §5, decisión T16). Rutas específicas de la máquina (dónde vive el
Python de Windows, el export de MT5) se leen de `.env`, con default absoluto
anclado al repo cuando tiene sentido (mismo patrón que `tools/backup.py:38-43` --
`ROOT_DIR` vía `__file__`, nunca vía `cwd`, y `load_dotenv()` propio del módulo,
igual que el resto de `tools/`) para que sean absolutas sin importar desde dónde
se importe este módulo -- spec.md, tarea T8: "siempre como rutas absolutas".

Ver specs/002-auto-resolucion-velas/spec.md ("Configuración") y plan.md (§2.5,
decisión T16).
"""
import os

from dotenv import load_dotenv

# Ancla al repo vía __file__, nunca vía cwd (mismo patrón que tools/backup.py:38)
# -- así el default de cualquier ruta de abajo es absoluto sin importar desde
# dónde se corra o se importe este módulo (tests incluidos, aunque estén
# chdir'ados a tmp_path).
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))

# --------------------------------------------------------------------------
# Reglas de negocio (spec.md, sección "Configuración")
# --------------------------------------------------------------------------

# Horizonte de resolución: 2160 velas de 1H después del ancla (~90 días en un
# mercado 24/7), igual que el backtest de P2.
MAX_HORIZON = 2160

# Minutos antes de "Confirm & Save" que se usan como ancla cuando no hay
# `analysis_start_time` (N4).
ANCHOR_FALLBACK_MIN = 20

# Tolerancia relativa del chequeo del Mark Price contra la escalera 1M/5M/15M (N16).
MARK_PRICE_TOLERANCE = 0.001  # 0.1%

# Reglas de Structural Resolution (N13, N14, N15) -- ver definición de R en
# spec.md: R = |precio de partida - structural_invalidation|.
REVERT_H = 24        # horas para "revertido" (vuelta al Mark Price)
EXPANSION_H = 24      # horas para "expansión significativa"
EXPANSION_R = 0.5     # umbral de expansión, en R
SWEEP_R = 1.0         # umbral de Liquidity Sweep, en R
SWEEP_H = 48          # horas del horizonte de Liquidity Sweep

# Superposición mínima (en velas por TF) para verificar un export SIN pasar por
# la regla de referencias de `tools.p2_backtest.CLOCK_MIN_ENTRIES` (N32).
OVERLAP_MIN_BARS = 10

# Herencia del reloj (N43, RF-2e): un símbolo con menos de CLOCK_MIN_ENTRIES
# referencias hereda el reloj verificado de otro del mismo servidor solo si
# tiene al menos estas referencias propias en velas exportadas, y calzan todas.
INHERIT_MIN_OWN_REFERENCES = 5

# Ventana del criterio de acierto secundario S4 (docs/criterios-de-acierto.md,
# N35). Coincide numéricamente con SWEEP_H, pero es una constante conceptualmente
# distinta -- no fusionar.
S4_WINDOW_H = 48

# --------------------------------------------------------------------------
# Cuentas y símbolos (N24, N7)
# --------------------------------------------------------------------------

# account_id -> nombre de archivo de la DB, dentro de ACCOUNTS_DATA_DIR.
REAL_ACCOUNTS = {
    "000": "flight_account_001_xauusd.db",
    "001": "flight_account_000_us500.db",
    "002": "flight_account_002_btcusdtp.db",
    "003": "flight_account_003_us100.db",
}

# ticker de la DB de cuenta -> símbolo MT5 real.
MT5_SYMBOL_MAP = {
    "XAUUSDT.P": "XAUUSD",
    "BTCUSDT.P": "BTCUSD",
    "US100": "USTEC",
    "US500": "US500",
}

# --------------------------------------------------------------------------
# Excepciones de toque (N46)
# --------------------------------------------------------------------------

# Análisis en los que el operador confirmó en su gráfico que un nivel se tocó, aunque el banco (el feed del broker en
# MT5) no llegue: id completo -> (nivel, nota). El nivel es "validation" o "invalidation" (los LEVEL_* de
# core/p2_ground_truth.py). El resolvedor toma como toque la vela de máximo acercamiento a ese nivel, y el reporte la
# lista. Los niveles guardados en la DB no se cambian. La nota se muestra en el reporte (en inglés, N30).
TOUCH_EXCEPTIONS = {
    "4b17b903-407d-4a5e-b238-1491ab64679b": (
        "validation",
        "Touched 4370 on the operator's chart; the broker's high was 4369.62 at 08:06 (confirmed 2026-10-03).",
    ),
}

# Retroactivos recuperados (N50): análisis hechos en su momento y vueltos a cargar después del DROP del 2026-07-27
# (todos de XAU, del 2026-06-23 al 2026-07-14). El usuario confirmó que son correctos, así que cuentan en el win rate.
# Los retroactivos nuevos siguen fuera por defecto (R11): se cargan con el resultado a la vista.
RECOVERED_BACKDATED = frozenset({
    "9fb9e581-95fc-4539-9d2f-407eab042bbc",
    "45c7ffc0-4523-48e5-a542-b6dce424532f",
    "f24b9653-ad3b-4eb0-9a78-badb9a64a09f",
    "7ae2bfe8-eccf-4bf7-ab12-a7e1532ec32e",
    "b867b660-07f5-4197-ab3a-da76d1f4d70b",
    "79a818c1-d199-4d3f-a481-9587c3784e23",
    "5e8fc526-d7a0-4029-9764-7e772371f355",
})

# --------------------------------------------------------------------------
# Registro prospectivo del P2 sistemático (N38, N42)
# --------------------------------------------------------------------------

# nombre del modelo en tools.p2_backtest.MODELS_BY_NAME -> fecha de alta (ISO,
# "YYYY-MM-DD"). Un modelo solo se registra en análisis con
# analysis_start_time >= esa fecha (evita sesgo de selección). Sumar un modelo
# nuevo es agregar una entrada acá, nunca cambiar la receta ni la fecha de una
# existente (T23).
P2_LOG_MODELS = {
    "D": "2026-09-27",
}

# --------------------------------------------------------------------------
# Export automático (N31, RF-20 a RF-20f)
# --------------------------------------------------------------------------

AUTO_EXPORT_WAIT_S = 30    # espera acotada al abrir un audit (valor inicial, T8)
EXPORT_TIMEOUT_S = 180     # timeout del export en segundo plano (valor inicial, T8)

BROKER_DST_RULE_CHOICES = ("us", "eu", "none")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Apagado hasta que el spike (T22) lo confirme -- si falla, el export sigue
# siendo manual (N31).
AUTO_EXPORT = _env_bool("AUTO_EXPORT", False)

# Candidato [NO VERIFICADO] -- lo fija el spike (plan.md §7, T22). "us" son las
# fechas de horario de verano de EE.UU.
BROKER_DST_RULE = os.getenv("BROKER_DST_RULE", "us")
if BROKER_DST_RULE not in BROKER_DST_RULE_CHOICES:
    raise ValueError(
        f"BROKER_DST_RULE={BROKER_DST_RULE!r} inválido; debe ser uno de "
        f"{BROKER_DST_RULE_CHOICES}"
    )

# --------------------------------------------------------------------------
# Rutas de la máquina (.env, siempre absolutas -- ver docstring del módulo)
# --------------------------------------------------------------------------

# Directorio con las DBs de cuenta y el resto de .data/ (por defecto <repo>/.data).
ACCOUNTS_DATA_DIR = os.path.abspath(
    os.getenv("ACCOUNTS_DATA_DIR", os.path.join(ROOT_DIR, ".data"))
)

# Banco de velas nuevo (plan.md §2.3, T1). Por defecto, dentro de ACCOUNTS_DATA_DIR.
CANDLE_BANK_DIR = os.path.abspath(
    os.getenv("CANDLE_BANK_DIR", os.path.join(ACCOUNTS_DATA_DIR, "candle_bank"))
)

# Carpeta donde el exportador de Windows deja cada corrida, en un directorio
# nuevo por run_id (plan.md §2.4, T2). Es una ruta WSL (bajo /mnt/c/), no del
# repo -- el default documentado en plan.md es la máquina real del usuario.
MT5_INCOMING_DIR = os.path.abspath(
    os.getenv("MT5_INCOMING_DIR", "/mnt/c/Users/jcifu/MT5Exports/_incoming")
)

# Corridas de `_incoming` que se conservan por símbolo; las más viejas se borran al final de cada export (N47).
INCOMING_RUNS_KEEP = 3

# CSV que ya existían antes de esta spec, `{LEGACY_EXPORTS_DIR}/{SIMBOLO}/{TF}.csv`
# (los que lee el cuaderno; INV-6: nunca se tocan). Solo los lee
# `candles import-legacy` (RF-2c). Por defecto, la carpeta que contiene a
# MT5_INCOMING_DIR (`.../MT5Exports`).
LEGACY_EXPORTS_DIR = os.path.abspath(
    os.getenv("LEGACY_EXPORTS_DIR", os.path.dirname(MT5_INCOMING_DIR))
)

# Python de Windows con MetaTrader5 instalado, y ruta del exportador tal como
# la ve ESE Python (típicamente un path estilo Windows, C:\...) -- no se les
# aplica os.path.abspath: no son rutas POSIX, y os.path.isabs() de Linux no las
# reconocería. Sin default: los fija el spike (T22, plan.md §7). None hasta
# entonces -- el export sigue siendo manual (N31).
WINDOWS_PYTHON = os.getenv("WINDOWS_PYTHON")
EXPORTER_WIN_PATH = os.getenv("EXPORTER_WIN_PATH")

# --------------------------------------------------------------------------
# Códigos de motivo (spec.md, "Clases de error" E1-E8; nunca bloquean, RF-*)
# --------------------------------------------------------------------------

REASON_EXPORT_FAILED = "export_failed"        # E1
REASON_CLOCK_MISALIGNED = "clock_misaligned"  # E2
REASON_CLOCK_UNVERIFIED = "clock_unverified"  # E2b
REASON_PENDING_CANDLES = "pending_candles"    # E3 (temporal)
REASON_NO_HISTORY = "no_history"              # E3b
REASON_NO_LEVELS = "no_levels"                # E4
REASON_AMBIGUOUS = "ambiguous"                # E5
REASON_NO_MT5_SYMBOL = "no_mt5_symbol"        # E8

# Valores de efficiency_audit.resolution_time_source (plan.md §2.1, RF-14b).
# "candles" y "corrected" son propios de esta columna; "open" también; el resto
# son los REASON_* de arriba, reusados.
RESOLUTION_TIME_SOURCE_CANDLES = "candles"
RESOLUTION_TIME_SOURCE_CORRECTED = "corrected"
RESOLUTION_TIME_SOURCE_OPEN = "open"
RESOLUTION_TIME_SOURCE_VALUES = (
    RESOLUTION_TIME_SOURCE_CANDLES,
    RESOLUTION_TIME_SOURCE_CORRECTED,
    REASON_PENDING_CANDLES,
    REASON_NO_HISTORY,
    REASON_CLOCK_UNVERIFIED,
    REASON_CLOCK_MISALIGNED,
    REASON_AMBIGUOUS,
    REASON_NO_LEVELS,
    REASON_NO_MT5_SYMBOL,
    RESOLUTION_TIME_SOURCE_OPEN,
)

# Prefijos de los motivos con parámetro (RF-12c, RF-12e) -- se arman con las
# funciones de abajo, nunca a mano, para que el formato "<prefijo>:<valor>" no
# diverja entre sitios.
_REASON_INSUFFICIENT_HISTORY_PREFIX = "insufficient_history"
_REASON_UNKNOWN_MODEL_PREFIX = "unknown_model"
_REASON_TIMEFRAME_NOT_IN_BANK_PREFIX = "timeframe_not_in_bank"
_REASON_MODEL_RECIPE_CHANGED_PREFIX = "model_recipe_changed"


def reason_insufficient_history(timeframe: str) -> str:
    """RF-12c: el modelo no se pudo calcular por falta de velas cerradas en `timeframe`."""
    return f"{_REASON_INSUFFICIENT_HISTORY_PREFIX}:{timeframe}"


def reason_unknown_model(name: str) -> str:
    """RF-12e: `name` en P2_LOG_MODELS no existe en tools.p2_backtest.MODELS_BY_NAME."""
    return f"{_REASON_UNKNOWN_MODEL_PREFIX}:{name}"


def reason_timeframe_not_in_bank(timeframe: str) -> str:
    """RF-12e: el modelo usa una TF que el banco de velas no guarda."""
    return f"{_REASON_TIMEFRAME_NOT_IN_BANK_PREFIX}:{timeframe}"


def reason_model_recipe_changed(name: str) -> str:
    """RF-12e: la receta actual de `name` no coincide con la huella de sus líneas ya registradas."""
    return f"{_REASON_MODEL_RECIPE_CHANGED_PREFIX}:{name}"
