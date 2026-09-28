"""
tools/p2_backtest.py — P2_systematic: ensamblaje de la tabla de comparación
P2 discrecional vs. P2 sistemático (flight_account_001_xauusd.db).

Estado: NO se ha corrido contra los 47 trades reales en alcance todavía —
falta que exista un CSV real de MT5 en la ruta configurada. El mecanismo
de transferencia SÍ está resuelto (ver jupyter/p2_systematic_task_plan.md,
sección mecanismo_csv): WSL2 corre sobre el mismo equipo físico que MT5,
acceso directo vía /mnt/c/, sin transferencia de red. `load_ohlc_provider_
from_csv()` es una implementación real (`CsvOHLCProvider`) — la ruta base
se lee de la variable de entorno P2_SYSTEMATIC_OHLC_DIR (.env, ver
.env.template), nunca hardcodeada, porque es específica de cada máquina.

Formato de CSV asumido (documentado en jupyter/p2_systematic_findings.md,
a confirmar/ajustar contra un CSV de ejemplo real en la siguiente sesión,
no adivinado más allá de esto): un archivo por temporalidad,
`{P2_SYSTEMATIC_OHLC_DIR}/{timeframe}.csv` con `timeframe` en
`{"1W","1D","12H","4H","1H"}`, columnas mínimas `time, open, high, low,
close` (encabezado en la primera fila, nombres insensibles a mayúsculas),
`time` parseable por `pandas.to_datetime`.

Este módulo SÍ puede ensamblar hoy, contra la DB real, las columnas del
schema que NO dependen de OHLC: identificadores, timestamps, entry_price,
edge_validation_price, structural_invalidation, p2_discrecional,
calc_edge_original, bias_predicho_original. Las columnas que dependen de
OHLC (p2_sistematico, p2_sistematico_rescaled, calc_edge_contrafactual,
bias_predicho_contrafactual, ground_truth_direction, ground_truth_incompleto,
match_original, match_contrafactual, snapshot_incompleto) requieren un `ohlc_provider` real
— sin uno (default None), quedan en None marcadas incompletas. Ninguna fila
se omite nunca por esto (doctrina de esta tarea: documentar, no descartar
en silencio — ver ExclusionRecord más abajo para las filas fuera del scope
filter, que es un descarte distinto y sí documentado).

Solo lectura: `open_readonly_session()` abre la conexión SQLite en modo URI
`mode=ro` — cualquier intento de escritura falla a nivel del driver, no
solo por convención. Deliberadamente NO usa `tools.database.init_db()`
(que corre `create_all()`/`ALTER TABLE` de migración) para que este módulo
no pueda tocar el schema ni los datos de las DBs de cuenta reales bajo
ninguna circunstancia (ver principio de seguridad de DB en CLAUDE.md).

Reutiliza `calculate_edge_score` (core/math_engine.py) y
`determine_market_bias` (cli/main.py) tal cual — no las reimplementa.
"""
from __future__ import annotations

import os
import csv
import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field, fields as dataclasses_fields
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tools.database import AnalysisLayer, TacticalAudit, UnifiedDepartment
from core.math_engine import calculate_edge_score
from core.p2_ground_truth import OhlcBar, first_touch_direction, infer_thesis_direction

# Import aislado de cli.main: dispara efectos de módulo (registro del grupo
# Click, get_active_engine()) — ver jupyter/p2_systematic_findings.md,
# hallazgo 2. Aceptado, no se reimplementan los umbrales de bias.
from cli.main import determine_market_bias

DEFAULT_DB_PATH = os.path.join(ROOT_DIR, ".data", "flight_account_001_xauusd.db")

TIMEFRAMES: Tuple[str, ...] = ("1W", "1D", "12H", "4H", "1H")
TF_WEIGHTS: Dict[str, float] = {"1W": 0.30, "1D": 0.25, "12H": 0.20, "4H": 0.15, "1H": 0.10}

# Todas las temporalidades que algún modelo puede pedir, de mayor a menor.
ALL_TIMEFRAMES: Tuple[str, ...] = ("1W", "1D", "12H", "4H", "1H", "30M", "15M")

TIMEFRAME_MINUTES: Dict[str, int] = {
    "1W": 7 * 24 * 60, "1D": 24 * 60, "12H": 12 * 60, "4H": 4 * 60,
    "1H": 60, "30M": 30, "15M": 15,
    # 5M y 1M: spec 002 RF-15 (el banco de velas guarda hasta 1M) y RF-4f
    # (el resolvedor de spec 002 camina TF cada vez más finas para el primer
    # toque). `closed_bars`/`bar_containing` de CsvOHLCProvider ya funcionan
    # con cualquier entrada de este dict — no hace falta tocar nada más ahí.
    "5M": 5, "1M": 1,
}

# Cobertura mínima por TF para snapshot_incompleto, ya definida en el prompt
# original de P2_systematic (no es una decisión de este módulo).
MIN_BARS_PER_TF = 800

# Granularidad del path forward — RESUELTO, aprobado explícitamente (ver
# jupyter/p2_systematic_task_plan.md, sección granularidad_path_forward).
# Reusa el mismo archivo "1H.csv" que la temporalidad 1H de indicadores
# (TIMEFRAMES) — antes/al anchor para el snapshot, después del anchor para
# el path forward.
FORWARD_PATH_TIMEFRAME = "1H"
FORWARD_PATH_MAX_BARS = 2160  # ~90 días en H1

# Variable de entorno (.env, ver .env.template) que apunta al directorio
# con los CSV de OHLC exportados por MT5 -- no hardcodeada, es específica
# de cada máquina (ruta bajo /mnt/c/ en el caso WSL2 + MT5 en el mismo
# equipo físico, ver jupyter/p2_systematic_task_plan.md).
OHLC_DIR_ENV_VAR = "P2_SYSTEMATIC_OHLC_DIR"

REQUIRED_CSV_COLUMNS = {"time", "open", "high", "low", "close"}


# ---------------------------------------------------------------------------
# Registro de modelos
# ---------------------------------------------------------------------------
# Cada modelo es una VARIANTE de la misma mecánica (bias por alineación de
# EMAs x fuerza por ADX, ponderado por temporalidad). Lo único que cambia
# entre ellos son los 4 campos del ModelSpec -- no hay lógica duplicada.
#
# PESOS: el criterio pedido para los modelos nuevos (B..E) fue concentrar el
# peso mayor en la temporalidad del PUNTO MEDIO de la escalera, en vez de en
# la más alta como hace el Modelo A. Cada set suma exactamente 1.0.
#   - B/C (escalera 1W..1H): el medio posicional es 12H -> pico en 12H.
#   - D/E (la escalera baja a 30M/5M): el medio se corre a 1H -> pico en 1H.
# Son valores de diseño, no derivados de los datos: cambiar el pico es editar
# una línea del dict correspondiente.

@dataclass(frozen=True)
class ModelSpec:
    name: str
    label: str
    timeframes: Tuple[str, ...]
    weights: Dict[str, float]
    # Cadena de medias que debe venir ordenada para declarar tendencia limpia,
    # de la más rápida a la más lenta. El precio va implícito al principio:
    # (20, 200) exige precio > EMA20 > EMA200 para bias alcista.
    ema_chain: Tuple[int, ...]
    # Si True, +DI/-DI deben confirmar la dirección que dieron las EMAs; si
    # no la confirman, esa temporalidad aporta 0 aunque las EMAs estén
    # alineadas y el ADX sea alto.
    use_di: bool
    description: str


MODEL_A = ModelSpec(
    name="A",
    label="A — baseline (EMA20/200, ADX)",
    timeframes=("1W", "1D", "12H", "4H", "1H"),
    weights={"1W": 0.30, "1D": 0.25, "12H": 0.20, "4H": 0.15, "1H": 0.10},
    ema_chain=(20, 200),
    use_di=False,
    description="Modelo original. Peso decreciente desde 1W. No se toca: es la "
                "referencia contra la que se miden los demás.",
)

MODEL_B = ModelSpec(
    name="B",
    label="B — + EMA100",
    timeframes=("1W", "1D", "12H", "4H", "1H"),
    weights={"1W": 0.15, "1D": 0.20, "12H": 0.30, "4H": 0.20, "1H": 0.15},
    ema_chain=(20, 100, 200),
    use_di=False,
    description="Agrega la EMA100 a la cadena: exige precio > EMA20 > EMA100 > "
                "EMA200 (o al revés). Es un filtro MÁS estricto que A -- más "
                "temporalidades caen en bias 0. Pico de peso en 12H.",
)

MODEL_C = ModelSpec(
    name="C",
    label="C — + EMA100 + DI",
    timeframes=("1W", "1D", "12H", "4H", "1H"),
    weights={"1W": 0.15, "1D": 0.20, "12H": 0.30, "4H": 0.20, "1H": 0.15},
    ema_chain=(20, 100, 200),
    use_di=True,
    description="Lo de B más confirmación direccional por +DI/-DI: si las EMAs "
                "dicen alcista pero -DI > +DI, esa temporalidad aporta 0. "
                "Mismos pesos que B a propósito, para aislar el efecto del DI.",
)

MODEL_D = ModelSpec(
    name="D",
    label="D — B+C + 30M",
    timeframes=("1W", "1D", "12H", "4H", "1H", "30M"),
    weights={"1W": 0.08, "1D": 0.12, "12H": 0.17, "4H": 0.22, "1H": 0.26, "30M": 0.15},
    ema_chain=(20, 100, 200),
    use_di=True,
    description="Reglas de C, con 30M agregado. Al bajar la escalera el punto "
                "medio se corre a 1H, que toma el peso mayor.",
)

MODEL_E = ModelSpec(
    name="E",
    label="E — B+C+D + 15M",
    timeframes=("1W", "1D", "12H", "4H", "1H", "30M", "15M"),
    weights={"1W": 0.07, "1D": 0.10, "12H": 0.14, "4H": 0.19, "1H": 0.24,
             "30M": 0.16, "15M": 0.10},
    ema_chain=(20, 100, 200),
    use_di=True,
    description="Reglas de C/D, con 15M agregado. Pico sostenido en 1H; 15M entra "
                "con peso bajo por ser la más ruidosa de la escalera.",
)

MODEL_F = ModelSpec(
    name="F",
    label="F — pico 1H, sin 30M (D simplificado)",
    timeframes=("1W", "1D", "12H", "4H", "1H"),
    # Los pesos de D sin la temporalidad 30M, reescalados para sumar 1: mismo
    # reparto relativo entre 1W..1H, una temporalidad menos. Derivado, no elegido
    # a mano, para que quede trazado de dónde sale cada número.
    weights={tf: w / (1 - MODEL_D.weights["30M"])
             for tf, w in MODEL_D.weights.items() if tf != "30M"},
    ema_chain=(20, 100, 200),
    use_di=True,
    description="Surgió de aislar los dos cambios de D (2026-09-22): mover el pico a 1H "
                "explicaba toda la mejora y agregar 30M no aportaba nada. Es la versión más "
                "simple con el mismo resultado. Ojo: se definió DESPUÉS de mirar los datos, "
                "así que su resultado tiene sesgo de selección (ver el cuaderno de evaluación).",
)

MODELS: Tuple[ModelSpec, ...] = (MODEL_A, MODEL_B, MODEL_C, MODEL_D, MODEL_E, MODEL_F)
MODELS_BY_NAME: Dict[str, ModelSpec] = {m.name: m for m in MODELS}

for _m in MODELS:
    assert set(_m.weights) == set(_m.timeframes), f"Modelo {_m.name}: pesos y TFs no coinciden"
    assert abs(sum(_m.weights.values()) - 1.0) < 1e-9, f"Modelo {_m.name}: pesos no suman 1.0"


def open_readonly_session(db_path: str = DEFAULT_DB_PATH) -> Session:
    """
    Abre una sesión de SOLO LECTURA contra db_path vía el modo URI de
    SQLite (mode=ro). No usa tools.database.init_db() a propósito — ese
    helper corre create_all()/ALTER TABLE de migración, que este módulo no
    debe poder disparar contra una DB de cuenta real.
    """
    abs_path = os.path.abspath(db_path)
    engine = create_engine(f"sqlite:///file:{abs_path}?mode=ro&uri=true")
    return Session(engine)


@dataclass
class TFIndicatorSnapshot:
    """
    Indicadores ya calculados para una temporalidad, al momento del anchor.

    ema100/plus_di/minus_di son Optional con default None a propósito: el
    Modelo A (baseline original) no los usa, y mantenerlos opcionales deja
    que los tests y el código previo construyan snapshots con la firma
    vieja sin romperse. Un modelo que SÍ los necesita y los recibe en None
    marca esa temporalidad como incompleta (ver compute_score_p2_sistematico).
    """
    price: float
    ema20: float
    ema200: float
    adx14: float
    bars_available: int  # para snapshot_incompleto (< MIN_BARS_PER_TF)
    ema100: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None


class OHLCProvider(Protocol):
    """
    Interfaz que un provider de OHLC debe implementar. Implementación real:
    CsvOHLCProvider (más abajo), construida por load_ohlc_provider_from_csv().
    """

    def get_indicator_snapshot(self, timeframe: str, as_of: datetime) -> Optional[TFIndicatorSnapshot]:
        ...

    def get_forward_path(self, as_of: datetime, max_bars: int = FORWARD_PATH_MAX_BARS) -> Sequence[OhlcBar]:
        ...


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _wilder_smooth(series: pd.Series, length: int) -> pd.Series:
    """
    Suavizado de Wilder (usado por TR/+DM/-DM en ADX) — equivalente a una
    EMA con alpha=1/length. Aproximación estándar de práctica común;
    difiere del método de siembra original de Wilder (promedio simple de
    los primeros `length` valores) solo en los primeros ~length bares,
    despreciable dado que MIN_BARS_PER_TF exige >=800 velas de historia
    antes de que snapshot_incompleto=False.
    """
    return series.ewm(alpha=1 / length, adjust=False).mean()


def _adx_components(df: pd.DataFrame, length: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Componentes de Wilder: (ADX, +DI, -DI). El ADX mide FUERZA de tendencia
    sin dirección; +DI/-DI son los que aportan la dirección (+DI > -DI =
    presión compradora). Los modelos C/D/E usan los DI como confirmación
    direccional; A y B solo usan el ADX.

    Calculado con pandas/numpy puro — sin dependencia
    nueva (pandas_ta sigue sin agregarse a requirements.txt real, ver
    jupyter/p2_systematic_requirements_draft.txt). Determinístico, sin
    ML ni ajuste de parámetros — fórmula estándar fija.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )

    tr_smooth = _wilder_smooth(tr, length)
    plus_dm_smooth = _wilder_smooth(plus_dm, length)
    minus_dm_smooth = _wilder_smooth(minus_dm, length)

    plus_di = 100 * plus_dm_smooth / tr_smooth
    minus_di = 100 * minus_dm_smooth / tr_smooth
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    return _wilder_smooth(dx, length), plus_di, minus_di


def _adx14(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Solo el ADX. Wrapper de _adx_components(), que además da +DI/-DI."""
    return _adx_components(df, length)[0]


class CsvOHLCProvider:
    """
    Implementación real de OHLCProvider sobre un directorio de CSVs, uno
    por temporalidad, exportados por un script en el lado Windows del
    mismo equipo físico (acceso vía /mnt/c/, sin transferencia de red —
    mecanismo_csv RESUELTO, ver jupyter/p2_systematic_task_plan.md).

    SUPUESTO DE FORMATO, no verificado todavía contra un CSV real de MT5
    (documentado en jupyter/p2_systematic_findings.md — se ajusta en la
    siguiente sesión contra un CSV de ejemplo real, no se adivina aquí):
      - Un archivo por temporalidad: {base_dir}/{timeframe}.csv, con
        timeframe en TIMEFRAMES ("1W","1D","12H","4H","1H").
      - Columnas mínimas: time, open, high, low, close (encabezado en la
        primera fila, nombres insensibles a mayúsculas/espacios).
      - `time` parseable por pandas.to_datetime.
      - No se asume orden cronológico — se ordena explícitamente al cargar.
    """

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._cache: Dict[str, pd.DataFrame] = {}

    def has_timeframe(self, timeframe: str) -> bool:
        """
        ¿Existe el CSV de esta temporalidad? Permite que un modelo que pide
        30M/5M se reporte como "sin datos" en vez de reventar, mientras el
        export de Windows no las incluya.
        """
        return os.path.exists(os.path.join(self.base_dir, f"{timeframe}.csv"))

    def _load_tf(self, timeframe: str) -> pd.DataFrame:
        if timeframe in self._cache:
            return self._cache[timeframe]

        path = os.path.join(self.base_dir, f"{timeframe}.csv")
        df = pd.read_csv(path)
        df.columns = [str(c).strip().lower() for c in df.columns]

        missing = REQUIRED_CSV_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(
                f"CSV {path} no tiene las columnas requeridas {sorted(missing)} "
                f"— formato asumido: {sorted(REQUIRED_CSV_COLUMNS)}. Si el CSV "
                "real de MT5 usa otros nombres, ajustar aquí contra un ejemplo "
                "real, no adivinar un mapeo."
            )

        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)
        self._cache[timeframe] = df
        return df

    def closed_bars(self, timeframe: str, as_of: datetime) -> pd.DataFrame:
        """
        Velas YA CERRADAS en as_of: time + duración(TF) <= as_of. `time` es la
        hora de APERTURA (convención de MT5), así que filtrar por time < as_of
        dejaría entrar la vela en formación con su cierre, máximo y mínimo
        finales, que ocurren después de as_of. Ese fue el bug de look-ahead
        corregido el 2026-09-23: en 1W, un análisis de un miércoles veía el
        cierre del viernes. Una vela que cierra exactamente en as_of cuenta.
        """
        df = self._load_tf(timeframe)
        return df[df["time"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe]) <= pd.Timestamp(as_of)]

    def get_past_closes(self, timeframe: str, as_of: datetime, n: int) -> List[float]:
        """Últimos n cierres de velas ya cerradas en as_of (mismo candado que el snapshot)."""
        if not self.has_timeframe(timeframe):
            return []
        return [float(v) for v in self.closed_bars(timeframe, as_of)["close"].tail(n)]

    def last_closed_close(self, timeframe: str, as_of: datetime) -> Optional[float]:
        """Cierre de la última vela ya cerrada en as_of: el precio que se veía en ese momento."""
        closes = self.get_past_closes(timeframe, as_of, 1)
        return closes[-1] if closes else None

    def bar_containing(self, timeframe: str, t: datetime) -> Optional[Tuple[float, float]]:
        """(high, low) de la vela cuyo intervalo [open, open+duración) contiene t."""
        if not self.has_timeframe(timeframe):
            return None
        df = self._load_tf(timeframe)
        idx = int(df["time"].searchsorted(pd.Timestamp(t), side="right")) - 1
        if idx < 0:
            return None
        row = df.iloc[idx]
        if pd.Timestamp(t) >= row["time"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe]):
            return None
        return float(row["high"]), float(row["low"])

    def get_indicator_snapshot(self, timeframe: str, as_of: datetime) -> Optional[TFIndicatorSnapshot]:
        if not self.has_timeframe(timeframe):
            return None
        # Candado point-in-time: solo velas cerradas en el anchor (ver closed_bars).
        past = self.closed_bars(timeframe, as_of)
        bars_available = len(past)
        if bars_available == 0:
            return None

        ema20 = _ema(past["close"], 20)
        ema100 = _ema(past["close"], 100)
        ema200 = _ema(past["close"], 200)
        adx14, plus_di, minus_di = _adx_components(past, 14)

        def _last(series: pd.Series) -> float:
            v = series.iloc[-1]
            return float(v) if pd.notna(v) else 0.0

        return TFIndicatorSnapshot(
            price=float(past["close"].iloc[-1]),
            ema20=float(ema20.iloc[-1]),
            ema200=float(ema200.iloc[-1]),
            adx14=_last(adx14),
            bars_available=bars_available,
            ema100=float(ema100.iloc[-1]),
            plus_di=_last(plus_di),
            minus_di=_last(minus_di),
        )

    def get_forward_path(self, as_of: datetime, max_bars: int = FORWARD_PATH_MAX_BARS) -> Sequence[OhlcBar]:
        df = self._load_tf(FORWARD_PATH_TIMEFRAME)
        # Estrictamente después del anchor -- la vela de entrada misma se
        # excluye aquí (contrato de first_touch_direction).
        future = df[df["time"] > as_of].head(max_bars)
        return [
            OhlcBar(time=row.time, high=float(row.high), low=float(row.low))
            for row in future.itertuples(index=False)
        ]


def load_ohlc_provider_from_csv(base_dir: Optional[str] = None) -> CsvOHLCProvider:
    """
    Construye un CsvOHLCProvider real. Si no se pasa base_dir explícito
    (uso normal: tests con un directorio de fixtures), se lee de la
    variable de entorno P2_SYSTEMATIC_OHLC_DIR (.env, ver .env.template)
    — nunca hardcodeada, porque la ruta bajo /mnt/c/ es específica de cada
    máquina que corre MT5 + WSL2.
    """
    if base_dir is None:
        load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))
        base_dir = os.getenv(OHLC_DIR_ENV_VAR)
        if not base_dir:
            raise RuntimeError(
                f"{OHLC_DIR_ENV_VAR} no está configurada en .env — ver "
                ".env.template y jupyter/p2_systematic_findings.md para el "
                "formato de CSV esperado."
            )
    return CsvOHLCProvider(base_dir)


def bias_from_emas(snap: TFIndicatorSnapshot, ema_chain: Tuple[int, ...]) -> Optional[int]:
    """
    +1 si precio > EMA(a) > EMA(b) > ... (todas alineadas, de rápida a lenta),
    -1 si el orden está exactamente invertido, 0 en cualquier otro orden.
    None si al modelo le falta alguna EMA que pidió (snapshot incompleto).

    Generaliza los dos casos: ema_chain=(20,200) es el Modelo A;
    ema_chain=(20,100,200) agrega la EMA100 en el medio (Modelos B..E) y por
    construcción es MÁS estricto -- toda cadena que pasa con 3 EMAs también
    habría pasado con 2, pero no al revés.
    """
    valores = [snap.price]
    for n in ema_chain:
        v = getattr(snap, f"ema{n}", None)
        if v is None:
            return None
        valores.append(float(v))

    if all(valores[i] > valores[i + 1] for i in range(len(valores) - 1)):
        return 1
    if all(valores[i] < valores[i + 1] for i in range(len(valores) - 1)):
        return -1
    return 0


def strength_from_adx(adx14: float) -> float:
    """ADX > 25 -> 1.0 (tendencia fuerte); 20-25 -> 0.5; < 20 -> 0.0."""
    if adx14 > 25:
        return 1.0
    if adx14 >= 20:
        return 0.5
    return 0.0


def confirm_with_di(bias: int, snap: TFIndicatorSnapshot) -> Optional[int]:
    """
    Confirmación direccional por +DI/-DI (Modelos C/D/E). El ADX dice cuán
    fuerte es la tendencia pero no hacia dónde; los DI sí. Si las EMAs dicen
    alcista y -DI > +DI, la señal se contradice a sí misma -> bias 0.
    None si faltan los DI (snapshot incompleto para un modelo que los pide).
    """
    if snap.plus_di is None or snap.minus_di is None:
        return None
    if bias == 1 and not (snap.plus_di > snap.minus_di):
        return 0
    if bias == -1 and not (snap.minus_di > snap.plus_di):
        return 0
    return bias


def compute_score_p2_sistematico(
    snapshots: Dict[str, Optional[TFIndicatorSnapshot]],
    model: ModelSpec = MODEL_A,
) -> Tuple[Optional[float], bool]:
    """
    score_P2 = Sum(peso_TF * bias_TF * factor_fuerza_TF), sobre las
    temporalidades del modelo.

    bias_TF: alineación de la cadena de EMAs del modelo (ver bias_from_emas),
    opcionalmente filtrada por +DI/-DI si model.use_di. factor_fuerza_TF:
    ADX14 > 25 -> x1, 20-25 -> x0.5, <20 -> fuerza 0 (independiente del bias).

    snapshot_incompleto=True si falta cualquier TF del modelo, si bars_available
    de alguna es menor a MIN_BARS_PER_TF (800), o si al modelo le falta un
    indicador que pidió (EMA100 / DI). En ese caso devuelve (None, True) -- no
    se puntúa un modelo con datos parciales.

    model=MODEL_A por default: preserva exactamente el comportamiento previo
    para todo el código y los tests que llamaban a esta función sin modelo.
    """
    total = 0.0
    for tf in model.timeframes:
        snap = snapshots.get(tf)
        if snap is None or snap.bars_available < MIN_BARS_PER_TF:
            return None, True

        bias = bias_from_emas(snap, model.ema_chain)
        if bias is None:
            return None, True

        if model.use_di:
            bias = confirm_with_di(bias, snap)
            if bias is None:
                return None, True

        total += model.weights[tf] * bias * strength_from_adx(snap.adx14)

    return total, False


def round_half_away_from_zero(value: float) -> int:
    """
    Redondea al entero más cercano con empates en .5 yendo siempre lejos de
    cero — NUNCA el round() nativo de Python, que usa banker's rounding
    (redondeo al par más cercano) y puede dar resultados distintos en
    empates .5, además de no ser lo que se aprobó explícitamente para esta
    tarea. Vía decimal.Decimal con ROUND_HALF_UP, que en el módulo decimal
    de Python significa exactamente "ties away from zero" (para negativos
    también se aleja de cero, no es "hacia arriba" en sentido coloquial).

    Conversión por str(value) antes de construir el Decimal, a propósito
    — evita que el binario de un float como 2.5 (representable exacto) se
    confunda con casos donde la aritmética previa (p2_sistematico * 2)
    produjo un float ligeramente distinto de .5 por error de redondeo
    binario; construir el Decimal desde la representación decimal más
    corta de Python (str) es la práctica estándar para este tipo de
    redondeo.
    """
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def rescale_p2_sistematico(p2_sistematico: Optional[float]) -> Optional[int]:
    """
    p2_sistematico_rescaled = clamp(round_half_away_from_zero(p2_sistematico * 2), -2, 2).

    Lleva p2_sistematico (rango ~[-1,1], pesos que suman 1.0 x bias
    {-1,0,1} x fuerza {0,0.5,1}) a la misma escala discreta {-2,-1,0,1,2}
    que p2_discrecional (dirección x fuerza, cli/schemas/efficiency.py:
    60-69) — regla aprobada explícitamente por el usuario para resolver la
    nota de escala señalada en jupyter/p2_systematic_task_plan.md. El
    clamp es defensivo: la fórmula de score_P2 ya acota matemáticamente el
    resultado a [-2,2] tras *2 y redondeo, pero se aplica igual por si un
    error de precisión de punto flotante lo empujara fuera de rango.
    """
    if p2_sistematico is None:
        return None
    return int(clamp(round_half_away_from_zero(p2_sistematico * 2), -2, 2))


# Vocabularios distintos que hay que reconciliar antes de comparar:
# determine_market_bias() (cli/main.py:681) devuelve "Bullish"/"Bearish"/
# "Choppy / Neutral"; el ground truth geométrico (core/p2_ground_truth.py)
# devuelve "long"/"short". Comparar los dos strings directamente daba
# SIEMPRE False -- bug real, no hipotético: sin este mapeo los 50 trades en
# alcance se reportaban como fallo del modelo.
BIAS_TO_DIRECTION: Dict[str, Optional[str]] = {
    "Bullish": "long",
    "Bearish": "short",
    "Choppy / Neutral": None,  # abstención, ver bias_to_direction()
}


def bias_to_direction(bias: Optional[str]) -> Optional[str]:
    """
    Traduce un bias de determine_market_bias() al vocabulario direccional
    del ground truth. "Choppy / Neutral" -> None: el modelo NO tomó
    posición direccional, así que no hay nada que comparar contra un
    ground truth que solo tiene dos respuestas (long/short).

    Decisión aprobada explícitamente (opción 1 de 3, 2026-09-21): la
    abstención NO cuenta como fallo. Contarla como fallo castigaría al
    modelo por ser prudente y hundiría artificialmente su puntería; son
    14 de los 50 trades en alcance (28%), así que la elección mueve el
    resultado de forma material. Se reporta aparte, como tasa de
    abstención, en vez de mezclarse con los aciertos.
    """
    if bias is None:
        return None
    return BIAS_TO_DIRECTION.get(bias)


def evaluate_match(
    bias_predicho: Optional[str],
    ground_truth_direction: Optional[str],
    ground_truth_incompleto: bool,
) -> Tuple[Optional[bool], bool]:
    """
    Devuelve (match, abstuvo).

    - match=None    -> no medible (ground truth incompleto, bias ausente, o
                       el modelo se abstuvo).
    - match=True/False -> el modelo apostó direccionalmente y acertó/falló.
    - abstuvo=True  -> el modelo dijo "Choppy / Neutral" pudiendo haberse
                       medido (ground truth resuelto). Es lo que permite
                       separar "no apostó" de "no se pudo medir" en el
                       reporte; ambos casos dan match=None.
    """
    if bias_predicho is None:
        return None, False
    direction = bias_to_direction(bias_predicho)
    if ground_truth_incompleto or ground_truth_direction is None:
        return None, False
    if direction is None:
        return None, True
    return direction == ground_truth_direction, False


@dataclass
class AnchorResolution:
    timestamp_entry: datetime
    # "tactical_audit" | "created_at_fallback" | "mark_price" | "analysis_time"
    timestamp_source: str
    entry_price: Optional[float]


ANCHOR_MODE_EXECUTION = "execution"
ANCHOR_MODE_ANALYSIS = "analysis"
# created_at es el "Confirm & Save" del wizard, que llega después de cargar P2,
# Mark Price, niveles y Edge Description (cli/main.py, flow_new_analysis). El
# ancla se retrasa hacia el momento en que se decidió P2. Decidido por el
# usuario el 2026-09-23; entre 0 y 30 min los resultados casi no cambian.
ANALYSIS_ANCHOR_LEAD = timedelta(minutes=15)
ANCHOR_PRICE_TIMEFRAMES: Tuple[str, ...] = ("15M", "30M", "1H")


def describe_anchor_mode(anchor_mode: str) -> str:
    if anchor_mode == ANCHOR_MODE_ANALYSIS:
        minutes = int(ANALYSIS_ANCHOR_LEAD.total_seconds() // 60)
        return (f"hora del análisis (`created_at` − {minutes} min), con el cierre de la última vela "
                "cerrada como precio de partida; los retroactivos quedan fuera.")
    return ("primera `entry_time` del Tactical Audit (o `created_at` + `mark_price` sin ejecución). "
            "Llega una mediana de 30 min tarde respecto de la decisión de P2.")


def _analysis_anchor_price(provider: Optional["OHLCProvider"], as_of: datetime) -> Optional[float]:
    """Precio visible en as_of: cierre de la última vela cerrada de la menor TF disponible."""
    if provider is None:
        raise ValueError("anchor_mode='analysis' necesita un ohlc_provider para el precio de partida")
    for tf in ANCHOR_PRICE_TIMEFRAMES:
        if hasattr(provider, "has_timeframe") and not provider.has_timeframe(tf):
            continue
        getter = getattr(provider, "last_closed_close", None)
        price = getter(tf, as_of) if getter else None
        if price is not None:
            return price
    return None


def resolve_anchor(session: Session, trade_id: str, created_at: datetime) -> AnchorResolution:
    """
    Regla aprobada (sin cambios): MIN(tactical_audit.entry_time) WHERE
    entry_time IS NOT NULL para trade_id; sin filas o todas NULL ->
    fallback a unified_department.created_at. entry_price viene de esa
    misma fila ancla cuando existe — None en el caso fallback, porque no
    hay ejecución real que ancle un precio de entrada.
    """
    # Solo las 2 columnas necesarias, no la entidad completa: una DB de cuenta
    # que el CLI no abrió desde la última migración aditiva de init_db() no
    # tiene las columnas nuevas, y SELECT de la entidad falla ("no such column"
    # en US100 el 2026-09-22). Este módulo es de solo lectura: no migra nada.
    stmt = (
        select(TacticalAudit.entry_time, TacticalAudit.entry_price)
        .where(TacticalAudit.trade_id == trade_id, TacticalAudit.entry_time.isnot(None))
        .order_by(TacticalAudit.entry_time.asc())
        .limit(1)
    )
    earliest = session.execute(stmt).first()
    if earliest is not None:
        return AnchorResolution(
            timestamp_entry=earliest.entry_time,
            timestamp_source="tactical_audit",
            entry_price=float(earliest.entry_price) if earliest.entry_price is not None else None,
        )
    return AnchorResolution(
        timestamp_entry=created_at,
        timestamp_source="created_at_fallback",
        entry_price=None,
    )


@dataclass
class ExclusionRecord:
    trade_id: str
    reason: str


@dataclass
class ModelResult:
    """Resultado de un modelo para UN trade."""
    model_name: str
    p2_sistematico: Optional[float]
    p2_sistematico_rescaled: Optional[int]
    calc_edge_contrafactual: Optional[float]
    bias_predicho: Optional[str]
    match: Optional[bool]
    abstencion: bool
    snapshot_incompleto: bool
    # True = al modelo le faltan CSVs de alguna temporalidad que pide (p.ej.
    # 30M/5M todavía no exportadas). Distinto de snapshot_incompleto, que es
    # "hay datos pero no alcanzan".
    sin_datos: bool = False


@dataclass
class P2SystematicRow:
    trade_id: str
    timestamp_entry: datetime
    timestamp_source: str
    entry_price: float
    edge_validation_price: float
    structural_invalidation: float
    p2_discrecional: Optional[int]
    p2_sistematico: Optional[float]
    p2_sistematico_rescaled: Optional[int]
    calc_edge_original: float
    calc_edge_contrafactual: Optional[float]
    bias_predicho_original: str
    bias_predicho_contrafactual: Optional[str]
    ground_truth_direction: Optional[str]
    ground_truth_incompleto: bool
    match_original: Optional[bool]
    match_contrafactual: Optional[bool]
    # True = el modelo pudo medirse pero se abstuvo ("Choppy / Neutral").
    # Distingue "no apostó" de "no se pudo medir" -- ambos dan match=None.
    abstencion_original: bool
    abstencion_contrafactual: bool
    snapshot_incompleto: bool
    # Resultado de CADA modelo del registro, incluido A. Los campos planos de
    # arriba son el Modelo A, conservados para compatibilidad.
    model_results: Dict[str, ModelResult] = field(default_factory=dict)
    # Scores P0-P4 del análisis (para re-ponderar el edge sin volver a la DB)
    # y la dirección de la tesis inferida de los niveles.
    layer_scores: Dict[str, int] = field(default_factory=dict)
    thesis_direction: Optional[str] = None
    asset: Optional[str] = None


def assemble_p2_systematic_rows(
    session: Session,
    ohlc_provider: Optional[OHLCProvider] = None,
    include_no_execution: bool = False,
    anchor_mode: str = ANCHOR_MODE_EXECUTION,
    analysis_lead: Optional[timedelta] = None,
) -> Tuple[List[P2SystematicRow], List[ExclusionRecord]]:
    """
    anchor_mode (2026-09-23):
      - ANCHOR_MODE_EXECUTION (default, regla original): primera entry_time
        del Tactical Audit, o created_at + mark_price con include_no_execution.
        Llega tarde: la entrada ocurre una mediana de 30 min DESPUÉS de que el
        operador decidió P2, así que el modelo y el ground truth ven un tramo
        del movimiento posterior a la decisión.
      - ANCHOR_MODE_ANALYSIS (la que usan el cuaderno y los reportes): el
        momento del análisis, created_at - ANALYSIS_ANCHOR_LEAD, para todo
        análisis con niveles, se haya ejecutado o no. created_at es el instante
        de "Confirm & Save", posterior a la carga de P2; la DB no guarda la
        hora de inicio. El precio de partida es el cierre de la última vela
        cerrada (15M, o la menor disponible). Los retroactivos quedan fuera:
        su created_at no es la hora del análisis. include_no_execution no
        aplica en este modo. analysis_lead reemplaza los 15 min por defecto
        (para medir la sensibilidad al minuto exacto).

    Recorre unified_department, aplica el scope filter (entry_price,
    edge_validation_price y structural_invalidation no nulos — n=47 de 81
    confirmado empíricamente esta sesión) y arma una fila por trade en
    alcance. Las filas fuera de alcance se devuelven en la segunda lista,
    con motivo — nunca se omiten en silencio.

    Motivos de exclusión posibles (34 de 81 total, ya enumerados por
    trade_id en jupyter/p2_systematic_bias_field_verification.md y el
    turno de verificación previo a este):
      - "missing_edge_validation_price_or_structural_invalidation" (5)
      - "tactical_audit_rows_exist_but_entry_time_all_null" (24)
      - "zero_tactical_audit_rows" (5)

    Sin ohlc_provider (default None), p2_sistematico/
    calc_edge_contrafactual/bias_predicho_contrafactual/ground_truth_*
    quedan en None con snapshot_incompleto=True / ground_truth_incompleto=
    True — estado esperado hasta que exista un CSV real (ver docstring de
    módulo). Esta función NO debe invocarse contra la DB real todavía
    (ver jupyter/p2_systematic_task_plan.md, halt-gate) — construida y
    testeable, pero no ejecutada end-to-end en esta sesión.

    Reescalamiento de p2_sistematico — RESUELTO, aprobado explícitamente
    (ver jupyter/p2_systematic_task_plan.md): p2_sistematico (rango
    ~[-1,1]) y p2_sistematico_rescaled (entero en {-2,-1,0,1,2}, vía
    rescale_p2_sistematico()) se conservan AMBOS en la fila de salida —
    ninguno sobreescribe al otro. calc_edge_contrafactual usa
    p2_sistematico_rescaled (no el valor crudo) como input x2 de
    calculate_edge_score, para quedar en la misma escala discreta que
    p2_discrecional (analysis_layer.score para P2, {-2,-1,0,1,2},
    cli/schemas/efficiency.py:60-69).
    """
    rows: List[P2SystematicRow] = []
    exclusions: List[ExclusionRecord] = []

    trades = session.scalars(select(UnifiedDepartment)).all()
    for trade in trades:
        anchor = resolve_anchor(session, trade.id, trade.created_at)

        evp = float(trade.edge_validation_price) if trade.edge_validation_price is not None else None
        si = float(trade.structural_invalidation) if trade.structural_invalidation is not None else None

        if evp is None or si is None:
            exclusions.append(ExclusionRecord(
                trade_id=trade.id,
                reason="missing_edge_validation_price_or_structural_invalidation",
            ))
            continue

        if anchor_mode == ANCHOR_MODE_ANALYSIS:
            if trade.is_backdated:
                exclusions.append(ExclusionRecord(trade_id=trade.id, reason="backdated_no_analysis_time"))
                continue
            analysis_at = trade.created_at - (ANALYSIS_ANCHOR_LEAD if analysis_lead is None else analysis_lead)
            price = _analysis_anchor_price(ohlc_provider, analysis_at)
            if price is None:
                exclusions.append(ExclusionRecord(trade_id=trade.id, reason="no_closed_bar_at_analysis_time"))
                continue
            anchor = AnchorResolution(
                timestamp_entry=analysis_at,
                timestamp_source="analysis_time",
                entry_price=price,
            )
        elif anchor_mode != ANCHOR_MODE_EXECUTION:
            raise ValueError(f"anchor_mode desconocido: {anchor_mode!r}")

        if anchor.entry_price is None:
            sin_ejecucion = anchor.timestamp_source == "created_at_fallback"
            mark = float(trade.mark_price) if trade.mark_price is not None else None

            # Modo opcional (edge_evaluation, fase 0a): un análisis sin ejecución
            # se evalúa desde el momento en que se registró, con mark_price como
            # precio de referencia ("precio del activo al completar el análisis").
            # Retroactivos no: su created_at no es el momento del análisis.
            if include_no_execution and sin_ejecucion and mark and mark > 0 and not trade.is_backdated:
                anchor = AnchorResolution(
                    timestamp_entry=trade.created_at,
                    timestamp_source="mark_price",
                    entry_price=mark,
                )
            else:
                # Etiquetas corregidas 2026-09-22: antes "zero_tactical_audit_rows"
                # cubría también análisis CON filas tácticas pero sin entry_time
                # (25 de 26 en XAUUSD), y la otra etiqueta se usaba para el caso
                # opuesto.
                if not sin_ejecucion:
                    reason = "anchor_execution_missing_entry_price"
                elif session.scalar(
                    select(TacticalAudit.id).where(TacticalAudit.trade_id == trade.id).limit(1)
                ) is None:
                    reason = "zero_tactical_audit_rows"
                else:
                    reason = "tactical_audit_rows_exist_but_entry_time_all_null"
                if include_no_execution and sin_ejecucion and trade.is_backdated:
                    reason = "no_execution_backdated"
                exclusions.append(ExclusionRecord(trade_id=trade.id, reason=reason))
                continue

        layer_rows = session.scalars(
            select(AnalysisLayer).where(AnalysisLayer.trade_id == trade.id)
        ).all()
        scores_by_layer: Dict[str, int] = {
            layer.layer_name: layer.score for layer in layer_rows if layer.score is not None
        }
        p2_discrecional = scores_by_layer.get("P2")

        calc_edge_original = float(trade.calc_edge)
        bias_predicho_original = determine_market_bias(calc_edge_original)

        thesis_direction = infer_thesis_direction(anchor.entry_price, evp, si)

        p2_sistematico: Optional[float] = None
        p2_sistematico_rescaled: Optional[int] = None
        calc_edge_contrafactual: Optional[float] = None
        bias_predicho_contrafactual: Optional[str] = None
        ground_truth_direction: Optional[str] = None
        ground_truth_incompleto = True
        snapshot_incompleto = True

        model_results: Dict[str, ModelResult] = {}

        if ohlc_provider is not None:
            # Un solo snapshot por TF, reusado por todos los modelos que la piden.
            snapshots = {
                tf: ohlc_provider.get_indicator_snapshot(tf, anchor.timestamp_entry)
                for tf in ALL_TIMEFRAMES
            }

            forward_path = ohlc_provider.get_forward_path(anchor.timestamp_entry, FORWARD_PATH_MAX_BARS)
            ground_truth_direction, ground_truth_incompleto = first_touch_direction(
                thesis_direction, forward_path, anchor.entry_price, evp, si
            )

            x0 = scores_by_layer.get("P0")
            x1 = scores_by_layer.get("P1")
            x3 = scores_by_layer.get("P3")
            x4 = scores_by_layer.get("P4")

            for model in MODELS:
                falta_tf = any(snapshots.get(tf) is None for tf in model.timeframes)
                score, incompleto = compute_score_p2_sistematico(snapshots, model)
                rescaled = rescale_p2_sistematico(score)

                edge_c: Optional[float] = None
                bias_c: Optional[str] = None
                if rescaled is not None and None not in (x0, x1, x3, x4):
                    edge_c = calculate_edge_score(x0, x1, rescaled, x3, x4)
                    bias_c = determine_market_bias(edge_c)

                m_match, m_abst = evaluate_match(bias_c, ground_truth_direction, ground_truth_incompleto)
                model_results[model.name] = ModelResult(
                    model_name=model.name,
                    p2_sistematico=score,
                    p2_sistematico_rescaled=rescaled,
                    calc_edge_contrafactual=edge_c,
                    bias_predicho=bias_c,
                    match=m_match,
                    abstencion=m_abst,
                    snapshot_incompleto=incompleto,
                    sin_datos=falta_tf,
                )

            # Campos planos = Modelo A (compatibilidad hacia atrás).
            a = model_results[MODEL_A.name]
            p2_sistematico = a.p2_sistematico
            p2_sistematico_rescaled = a.p2_sistematico_rescaled
            snapshot_incompleto = a.snapshot_incompleto
            calc_edge_contrafactual = a.calc_edge_contrafactual
            bias_predicho_contrafactual = a.bias_predicho

        match_original, abstencion_original = evaluate_match(
            bias_predicho_original, ground_truth_direction, ground_truth_incompleto
        )
        match_contrafactual, abstencion_contrafactual = evaluate_match(
            bias_predicho_contrafactual, ground_truth_direction, ground_truth_incompleto
        )

        rows.append(P2SystematicRow(
            trade_id=trade.id,
            timestamp_entry=anchor.timestamp_entry,
            timestamp_source=anchor.timestamp_source,
            entry_price=anchor.entry_price,
            edge_validation_price=evp,
            structural_invalidation=si,
            p2_discrecional=p2_discrecional,
            p2_sistematico=p2_sistematico,
            p2_sistematico_rescaled=p2_sistematico_rescaled,
            calc_edge_original=calc_edge_original,
            calc_edge_contrafactual=calc_edge_contrafactual,
            bias_predicho_original=bias_predicho_original,
            bias_predicho_contrafactual=bias_predicho_contrafactual,
            ground_truth_direction=ground_truth_direction,
            ground_truth_incompleto=ground_truth_incompleto,
            match_original=match_original,
            match_contrafactual=match_contrafactual,
            abstencion_original=abstencion_original,
            abstencion_contrafactual=abstencion_contrafactual,
            model_results=model_results,
            layer_scores=dict(scores_by_layer),
            thesis_direction=thesis_direction,
            asset=trade.asset,
            snapshot_incompleto=snapshot_incompleto,
        ))

    return rows, exclusions


# ---------------------------------------------------------------------------
# Chequeo de reloj
# ---------------------------------------------------------------------------
# Una orden llenada tiene que caer, por definición, dentro del rango de su
# vela. Si los precios de entrada reales no cuadran con las velas del CSV y sí
# cuadran desplazándolas unas horas, el CSV está en otro reloj. Es exactamente
# el bug del 2026-09-22 (MT5 devolvía hora de servidor UTC+3 y se la trataba
# como UTC): con +3h, 26/34 entradas quedaban dentro de su vela 15M; sin
# desplazar, 2/29. Todo lo que depende de "antes/después del anchor" (el
# snapshot point-in-time y el ground truth) queda contaminado si esto falla.

CLOCK_TIMEFRAME_PREFERENCE: Tuple[str, ...] = ("15M", "30M", "1H")
CLOCK_MIN_ENTRIES = 10
CLOCK_MISALIGNMENT_MARGIN = 0.20


@dataclass
class ClockCalibration:
    timeframe: Optional[str]
    n_entries: int
    rate_by_offset: Dict[int, float]
    best_offset: Optional[int]
    aligned: Optional[bool]  # None = muestra insuficiente para decidir

    @property
    def rate_at_zero(self) -> Optional[float]:
        return self.rate_by_offset.get(0)

    def describe(self) -> str:
        if self.aligned is None:
            return (f"Sin calibrar: {self.n_entries} precios de referencia "
                    f"(mínimo {CLOCK_MIN_ENTRIES}).")
        r0 = self.rate_by_offset.get(0, 0.0)
        rb = self.rate_by_offset.get(self.best_offset, 0.0)
        if self.aligned:
            return (f"OK en {self.timeframe}: {r0 * 100:.0f}% de {self.n_entries} precios de "
                    f"referencia (fills + mark_price) caen dentro de su vela sin desplazar.")
        return (f"DESALINEADO en {self.timeframe}: sin desplazar cuadran {r0 * 100:.0f}% de las "
                f"entradas; desplazando las velas {self.best_offset:+d}h cuadran {rb * 100:.0f}%. "
                "El CSV está en otro reloj: re-exportar con el offset de servidor correcto "
                "(windows_export/export_p2_ohlc.py --server-utc-offset).")


def evaluate_clock_entries(
    entries: Sequence[Tuple[datetime, float]],
    provider: object,
    tf: str,
    offsets: Sequence[int],
) -> Dict[int, float]:
    """
    Para cada desplazamiento `off` (horas), la fracción de `entries` cuyo precio
    cae dentro de la vela de `tf` que contiene `entry_time + off`h (sobre las
    entradas para las que esa vela existe -- `found`, no sobre el total de
    `entries`). Extraído de `calibrate_clock_offset` (spec 002, T10) sin cambiar
    el resultado -- lo reusan `calibrate_clock_offset` y (para verificar un
    export del banco de velas por referencias, N29) `tools/candle_bank.py`.
    """
    rates: Dict[int, float] = {}
    for off in offsets:
        inside = found = 0
        for entry_time, entry_price in entries:
            bar = provider.bar_containing(tf, entry_time + pd.Timedelta(hours=off))
            if bar is None:
                continue
            found += 1
            high, low = bar
            if low <= float(entry_price) <= high:
                inside += 1
        rates[off] = inside / found if found else 0.0
    return rates


def count_entries_in_range(
    entries: Sequence[Tuple[datetime, float]],
    start: datetime,
    end: datetime,
) -> int:
    """
    Cuántas `entries` (mismo formato que `evaluate_clock_entries`: pares
    `(timestamp, precio)`) tienen su timestamp dentro de `[start, end]`,
    inclusive en los dos bordes. Usado por `tools/candle_bank.py` para la
    regla de "al menos 10 referencias dentro del rango exportado" (N29,
    plan.md §3.8 paso 4) -- distinto de `CLOCK_MIN_ENTRIES`, que exige 10
    referencias en TODA la cuenta, sin acotar a un rango exportado.
    """
    return sum(1 for entry_time, _ in entries if start <= entry_time <= end)


def calibrate_clock_offset(
    session: Session,
    provider: object,
    offsets: Sequence[int] = range(-6, 7),
    include_mark_price: bool = True,
) -> ClockCalibration:
    """
    Para cada desplazamiento s (horas), cuenta cuántas entradas llenadas caen
    dentro de la vela que contiene entry_time + s. Si el CSV está bien, el
    máximo está en s=0. best_offset=+3 significa que las velas del CSV están
    etiquetadas 3h más tarde que las entradas.
    """
    tf = next((t for t in CLOCK_TIMEFRAME_PREFERENCE
               if hasattr(provider, "has_timeframe") and provider.has_timeframe(t)), None)
    if tf is None or not hasattr(provider, "bar_containing"):
        return ClockCalibration(tf, 0, {}, None, None)

    entries = list(session.execute(
        select(TacticalAudit.entry_time, TacticalAudit.entry_price).where(
            TacticalAudit.order_filled == True,  # noqa: E712
            TacticalAudit.entry_time.isnot(None),
            TacticalAudit.entry_price > 0,
        )
    ).all())
    if include_mark_price:
        # mark_price = "precio del activo al completar el análisis" (cli/main.py,
        # prompt de flow_new_analysis), tipeado a mano en created_at. Más ruidoso
        # que un fill, pero con el reloj bien cuadra en ~79% de los casos
        # (XAUUSD, 2026-09-22) y permite calibrar cuentas con pocos fills.
        entries += list(session.execute(
            select(UnifiedDepartment.created_at, UnifiedDepartment.mark_price).where(
                UnifiedDepartment.mark_price > 0,
                UnifiedDepartment.is_backdated.isnot(True),
            )
        ).all())
    if len(entries) < CLOCK_MIN_ENTRIES:
        return ClockCalibration(tf, len(entries), {}, None, None)

    rates = evaluate_clock_entries(entries, provider, tf, offsets)

    best = max(rates, key=lambda o: (rates[o], -abs(o)))
    aligned = best == 0 or rates.get(0, 0.0) >= rates[best] - CLOCK_MISALIGNMENT_MARGIN
    return ClockCalibration(tf, len(entries), rates, best, aligned)


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------

@dataclass
class ArmSummary:
    """Desempeño de un 'brazo' (original o contrafactual) del experimento."""
    nombre: str
    apostados: int          # tomó posición direccional Y el ground truth se resolvió
    aciertos: int
    abstenciones: int       # dijo "Choppy / Neutral" pudiendo medirse
    no_medibles: int        # ground truth incompleto, o bias no calculado

    @property
    def punteria(self) -> Optional[float]:
        return (self.aciertos / self.apostados) if self.apostados else None


def summarize_model(rows: Sequence[P2SystematicRow], model_name: str) -> ArmSummary:
    """Mismo cálculo que summarize_arm, pero leyendo de model_results."""
    res = [r.model_results.get(model_name) for r in rows]
    res = [x for x in res if x is not None]
    apostados = sum(1 for x in res if x.match is not None)
    return ArmSummary(
        nombre=model_name,
        apostados=apostados,
        aciertos=sum(1 for x in res if x.match is True),
        abstenciones=sum(1 for x in res if x.abstencion),
        no_medibles=len(res) - apostados - sum(1 for x in res if x.abstencion),
    )


def model_is_runnable(rows: Sequence[P2SystematicRow], model_name: str) -> bool:
    """False si al modelo le faltan CSVs de alguna temporalidad que pide."""
    res = [r.model_results.get(model_name) for r in rows]
    res = [x for x in res if x is not None]
    return bool(res) and not all(x.sin_datos for x in res)


def missing_timeframes(
    rows: Sequence[P2SystematicRow],
    model: ModelSpec,
    provider: Optional[object] = None,
) -> List[str]:
    """
    Temporalidades del modelo que NO tienen CSV. Se consulta al provider real
    en vez de asumir que "todo lo que no está en la escalera base falta" -- esa
    suposición reportaba 30M como ausente cuando ya estaba exportada.
    """
    if provider is None or not hasattr(provider, "has_timeframe"):
        return []
    return [tf for tf in model.timeframes if not provider.has_timeframe(tf)]


def summarize_arm(rows: Sequence[P2SystematicRow], arm: str) -> ArmSummary:
    """arm: "original" | "contrafactual"."""
    match_attr = f"match_{arm}"
    abst_attr = f"abstencion_{arm}"
    matches = [getattr(r, match_attr) for r in rows]
    abstenciones = sum(1 for r in rows if getattr(r, abst_attr))
    apostados = sum(1 for m in matches if m is not None)
    return ArmSummary(
        nombre=arm,
        apostados=apostados,
        aciertos=sum(1 for m in matches if m is True),
        abstenciones=abstenciones,
        no_medibles=len(rows) - apostados - abstenciones,
    )


def _pct(n: Optional[float]) -> str:
    return "N/A" if n is None else f"{n * 100:.1f}%"


def render_report(
    rows: Sequence[P2SystematicRow],
    exclusions: Sequence[ExclusionRecord],
    generated_at: datetime,
    provider: Optional[object] = None,
    clock: Optional[ClockCalibration] = None,
    anchor_mode: str = ANCHOR_MODE_EXECUTION,
) -> str:
    """
    Markdown de solo lectura. Responde la pregunta del proyecto: ¿el P2
    sistemático predice la dirección mejor que el P2 discrecional?
    """
    orig = summarize_arm(rows, "original")
    contra = summarize_arm(rows, "contrafactual")

    # Head-to-head: solo trades donde AMBOS brazos apostaron -- comparar
    # punterías calculadas sobre muestras distintas no diría nada.
    ambos = [r for r in rows if r.match_original is not None and r.match_contrafactual is not None]
    solo_orig = sum(1 for r in ambos if r.match_original and not r.match_contrafactual)
    solo_contra = sum(1 for r in ambos if r.match_contrafactual and not r.match_original)
    ambos_ok = sum(1 for r in ambos if r.match_original and r.match_contrafactual)
    ambos_mal = sum(1 for r in ambos if not r.match_original and not r.match_contrafactual)
    delta = solo_contra - solo_orig

    excl_por_motivo = Counter(e.reason for e in exclusions)
    incompletos_gt = sum(1 for r in rows if r.ground_truth_incompleto)
    incompletos_snap = sum(1 for r in rows if r.snapshot_incompleto)

    L = []
    L.append("# P2_systematic — Reporte comparativo")
    L.append("")
    L.append(f"_Generado {generated_at:%Y-%m-%d %H:%M:%S}. Cuenta `flight_account_001_xauusd` (XAUUSD)._")
    L.append("")
    L.append("**Pregunta:** ¿el P2 calculado sistemáticamente (EMA20/EMA200/ADX14 sobre 5 "
             "temporalidades) predice la dirección del mercado mejor que el P2 que el "
             "operador puso a ojo?")
    L.append("")
    L.append("**Ground truth:** geométrico, sin juicio humano — qué nivel tocó primero el "
             "precio después del ancla (`edge_validation_price` = tesis confirmada, "
             "`structural_invalidation` = tesis rota), sobre velas "
             f"{FORWARD_PATH_TIMEFRAME} reales de MT5, tope {FORWARD_PATH_MAX_BARS} velas.")
    L.append("")
    L.append(f"**Ancla:** {describe_anchor_mode(anchor_mode)} Los indicadores usan solo velas "
             "ya cerradas en el ancla (corregido 2026-09-23: antes entraba la vela en formación "
             "con su cierre final).")
    L.append("")
    L.append("**Abstenciones:** un bias `Choppy / Neutral` no es apuesta direccional, así que "
             "no cuenta ni como acierto ni como fallo (decisión aprobada 2026-09-21). Se "
             "reporta aparte.")
    L.append("")
    if clock is not None:
        L.append(f"**Chequeo de reloj de las velas:** {clock.describe()}")
        L.append("")
    L.append("## Resultado")
    L.append("")
    L.append("| Brazo | Apostó | Acertó | Puntería | Se abstuvo | No medible |")
    L.append("|---|---|---|---|---|---|")
    for a, etiqueta in ((orig, "P2 discrecional (actual)"), (contra, "P2 sistemático")):
        L.append(f"| {etiqueta} | {a.apostados} | {a.aciertos} | {_pct(a.punteria)} "
                 f"| {a.abstenciones} | {a.no_medibles} |")
    L.append("")
    L.append(f"### Comparación directa ({len(ambos)} trades donde ambos apostaron)")
    L.append("")
    L.append(f"- Ambos acertaron: **{ambos_ok}**")
    L.append(f"- Ambos fallaron: **{ambos_mal}**")
    L.append(f"- Solo acertó el discrecional: **{solo_orig}**")
    L.append(f"- Solo acertó el sistemático: **{solo_contra}**")
    L.append(f"- Diferencia neta a favor del sistemático: **{delta:+d}** trades")
    L.append("")
    if len(ambos) == 0:
        L.append("> **Sin datos comparables.** Ningún trade tiene ambos brazos medibles.")
    elif abs(delta) <= 4:
        L.append(f"> ⚠️ **No concluyente.** Una diferencia de {delta:+d} sobre {len(ambos)} "
                 "trades cae dentro del ruido estadístico esperable a esta escala. Este "
                 "reporte NO permite afirmar que un método sea mejor que el otro — hace "
                 "falta más muestra.")
    else:
        mejor = "sistemático" if delta > 0 else "discrecional"
        L.append(f"> **Señal a favor del {mejor}** ({delta:+d} sobre {len(ambos)} trades). "
                 "Sigue siendo una muestra chica: confirmar antes de cambiar el proceso.")
    L.append("")

    # --- Apalancamiento de P2 dentro de la fórmula del edge -----------------
    # Antes de interpretar cualquier diferencia de puntería hay que saber si
    # sustituir P2 puede siquiera cambiar la decisión. Si no puede, comparar
    # punterías no mide nada.
    comparables = [r for r in rows if r.bias_predicho_contrafactual is not None]
    cambios = [r for r in comparables if r.bias_predicho_original != r.bias_predicho_contrafactual]
    direccionales = [
        r for r in cambios
        if bias_to_direction(r.bias_predicho_original) is not None
        and bias_to_direction(r.bias_predicho_contrafactual) is not None
    ]
    deltas = [
        abs(r.calc_edge_original - r.calc_edge_contrafactual)
        for r in comparables if r.calc_edge_contrafactual is not None
    ]
    max_delta = max(deltas) if deltas else 0.0

    L.append("## ¿Puede P2 cambiar la decisión?")
    L.append("")
    L.append(f"- El P2 sistemático dio un valor distinto al discrecional en "
             f"**{sum(1 for r in comparables if r.p2_discrecional != r.p2_sistematico_rescaled)}** "
             f"de {len(comparables)} trades.")
    L.append(f"- Pero el bias resultante solo cambió en **{len(cambios)}**.")
    L.append(f"- Y cambió de dirección (Bullish ↔ Bearish) en **{len(direccionales)}**.")
    L.append("")
    L.append(f"P2 pesa `0.15/2 = 0.075` por punto en `calculate_edge_score` "
             f"(`core/math_engine.py`), y el umbral que separa Bullish/Bearish de "
             f"`Choppy / Neutral` es `±0.26`. El mayor cambio observado en `calc_edge` "
             f"al sustituir P2 fue **{max_delta:.3f}**.")
    L.append("")
    if not direccionales:
        L.append("> 🔑 **Hallazgo principal: P2 no tiene apalancamiento suficiente para "
                 "invertir una decisión direccional.** En esta muestra, sustituirlo nunca "
                 "convirtió un Bullish en Bearish ni al revés — a lo sumo movió un trade "
                 "dentro o fuera de la zona neutral. Comparar las punterías de los dos "
                 "brazos, entonces, no mide cuál 'lee' mejor el mercado: mide casi el "
                 "mismo modelo dos veces.")
        L.append("")
        L.append("> **Implicación práctica:** la pregunta original del proyecto no se puede "
                 "responder subiendo la muestra. Si querés saber si el P2 sistemático es "
                 "mejor, primero hay que decidir si P2 debería pesar más en la fórmula. "
                 "Ese es un cambio de diseño del modelo, no de este backtest.")
    else:
        L.append(f"> Sustituir P2 sí puede invertir la decisión ({len(direccionales)} casos), "
                 "así que la comparación de punterías de arriba es informativa.")
    L.append("")
    L.append("## Comparación de modelos")
    L.append("")
    L.append("Todos los modelos comparten la misma mecánica (bias por alineación de EMAs × "
             "fuerza por ADX, ponderado por temporalidad). Cambian solo las variables y los pesos.")
    L.append("")
    L.append("| Modelo | Variables | TF con mayor peso | Apostó | Acertó | Puntería | Se abstuvo |")
    L.append("|---|---|---|---|---|---|---|")
    disc = summarize_arm(rows, "original")
    L.append(f"| _P2 discrecional (referencia)_ | juicio del operador | — | {disc.apostados} "
             f"| {disc.aciertos} | {_pct(disc.punteria)} | {disc.abstenciones} |")
    for model in MODELS:
        if not model_is_runnable(rows, model.name):
            faltan = ", ".join(missing_timeframes(rows, model, provider)) or "temporalidades faltantes"
            L.append(f"| **{model.label}** | — | — | — | — | **sin datos** ({faltan} sin exportar) | — |")
            continue
        m = summarize_model(rows, model.name)
        top_tf = max(model.weights, key=model.weights.get)
        variables = "EMA" + "/".join(str(n) for n in model.ema_chain) + " + ADX"
        if model.use_di:
            variables += " + DI"
        L.append(f"| **{model.label}** | {variables} | {top_tf} ({model.weights[top_tf]:.2f}) "
                 f"| {m.apostados} | {m.aciertos} | {_pct(m.punteria)} | {m.abstenciones} |")
    L.append("")
    for model in MODELS:
        L.append(f"- **{model.name}**: {model.description}")
    L.append("")

    L.append("## Cobertura")
    L.append("")
    L.append(f"- Trades en alcance: **{len(rows)}**")
    L.append(f"- Excluidos: **{len(exclusions)}**")
    for motivo, n in sorted(excl_por_motivo.items(), key=lambda kv: -kv[1]):
        L.append(f"  - `{motivo}`: {n}")
    L.append(f"- Ground truth no resuelto (nunca tocó un nivel, o una misma vela tocó "
             f"ambos): **{incompletos_gt}**")
    L.append(f"- Snapshot de indicadores incompleto (<{MIN_BARS_PER_TF} velas en alguna "
             f"temporalidad): **{incompletos_snap}**")
    L.append("")
    L.append("## Detalle por trade")
    L.append("")
    L.append("| Trade | Entrada | P2 disc. | P2 sist. | Bias disc. | Bias sist. | Realidad | Disc. | Sist. |")
    L.append("|---|---|---|---|---|---|---|---|---|")

    def mark(m: Optional[bool], abst: bool) -> str:
        if abst:
            return "—"
        return {True: "✅", False: "❌"}.get(m, "?")

    for r in sorted(rows, key=lambda r: r.timestamp_entry):
        L.append(
            f"| `{r.trade_id[:8]}` | {r.timestamp_entry:%Y-%m-%d %H:%M} "
            f"| {r.p2_discrecional if r.p2_discrecional is not None else '—'} "
            f"| {r.p2_sistematico_rescaled if r.p2_sistematico_rescaled is not None else '—'} "
            f"| {r.bias_predicho_original} | {r.bias_predicho_contrafactual or '—'} "
            f"| {r.ground_truth_direction or '—'} "
            f"| {mark(r.match_original, r.abstencion_original)} "
            f"| {mark(r.match_contrafactual, r.abstencion_contrafactual)} |"
        )
    L.append("")
    L.append("Leyenda: ✅ acertó · ❌ falló · — se abstuvo o no medible.")
    L.append("")
    return "\n".join(L)


def write_rows_csv(rows: Sequence[P2SystematicRow], path: str) -> None:
    """Vuelca cada fila cruda, para reanálisis sin volver a correr el pipeline."""
    if not rows:
        return
    fields = [f.name for f in dataclasses_fields(rows[0])]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({f: getattr(r, f) for f in fields})


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="P2_systematic: compara el P2 discrecional contra uno calculado, "
                    "usando un ground truth geométrico sobre velas reales de MT5."
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH,
                        help="SQLite de la cuenta. Se abre en SOLO LECTURA.")
    parser.add_argument("--ohlc-dir", default=None,
                        help=f"Directorio con los CSV de MT5. Default: ${OHLC_DIR_ENV_VAR}.")
    parser.add_argument("--out", default=os.path.join(ROOT_DIR, "jupyter", "p2_systematic_report.md"),
                        help="Markdown de salida.")
    parser.add_argument("--csv-out", default=None,
                        help="Opcional: CSV con una fila por trade.")
    parser.add_argument("--allow-clock-misalignment", action="store_true",
                        help="Genera el reporte aunque el chequeo de reloj falle. Solo para diagnóstico.")
    parser.add_argument("--anchor", choices=(ANCHOR_MODE_ANALYSIS, ANCHOR_MODE_EXECUTION),
                        default=ANCHOR_MODE_ANALYSIS,
                        help="analysis (default): created_at - 15 min, todo análisis con niveles. "
                             "execution: regla original, primera entry_time (llega tarde).")
    args = parser.parse_args(argv)

    try:
        provider = load_ohlc_provider_from_csv(args.ohlc_dir)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"ERROR cargando los CSV de OHLC: {exc}", file=sys.stderr)
        print(f"Revisar ${OHLC_DIR_ENV_VAR} en .env, o pasar --ohlc-dir. Los CSV los "
              "produce windows_export/export_p2_ohlc.py en la máquina Windows con MT5.",
              file=sys.stderr)
        return 1

    session = open_readonly_session(args.db_path)
    try:
        clock = calibrate_clock_offset(session, provider)
        print(f"Chequeo de reloj: {clock.describe()}")
        if clock.aligned is False and not args.allow_clock_misalignment:
            print("ABORTADO: las velas no están alineadas con las entradas reales; cualquier "
                  "resultado estaría contaminado. Usar --allow-clock-misalignment solo para "
                  "diagnóstico.", file=sys.stderr)
            return 2
        rows, exclusions = assemble_p2_systematic_rows(session, provider, anchor_mode=args.anchor)
    finally:
        session.close()

    report = render_report(rows, exclusions, datetime.now(), provider, clock, anchor_mode=args.anchor)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"Reporte escrito en {args.out} ({len(rows)} trades en alcance, "
          f"{len(exclusions)} excluidos).")

    if args.csv_out:
        write_rows_csv(rows, args.csv_out)
        print(f"CSV crudo escrito en {args.csv_out}.")

    orig = summarize_arm(rows, "original")
    contra = summarize_arm(rows, "contrafactual")
    print(f"  discrecional: {orig.aciertos}/{orig.apostados} ({_pct(orig.punteria)}), "
          f"{orig.abstenciones} abstenciones")
    print(f"  sistemático : {contra.aciertos}/{contra.apostados} ({_pct(contra.punteria)}), "
          f"{contra.abstenciones} abstenciones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
