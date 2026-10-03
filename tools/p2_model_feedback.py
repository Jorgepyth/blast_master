"""
tools/p2_model_feedback.py — Registro prospectivo del P2 sistemático (spec 002, T55, RF-12, RF-12c, RF-12e, N38, N40,
N42; plan.md §2.5).

Para cada modelo de `P2_LOG_MODELS` (nombre → fecha de alta) calcula su P2 en el ancla de cada análisis nuevo, con
velas cerradas del banco y la receta de `tools.p2_backtest.MODELS_BY_NAME` sin modificarla, y agrega **una línea JSON
por análisis y modelo** a `${ACCOUNTS_DATA_DIR}/p2_model_log.jsonl`. El archivo solo crece: una línea que se reintenta
(`pending_candles`, o un reloj sin verificar) se reemplaza con otra que lleva `supersedes: <n.º de línea>`, y el lector
se queda con la última de cada par (`trade_id`, `model`).

Qué análisis entran (N40, N42): los que tienen `analysis_start_time`, no son retroactivos ni clones [2] (los dos se
guardan con `is_backdated`), y empezaron el día de alta del modelo o después. El ancla es `analysis_start_time`. Las
DBs se leen en `mode=ro` con columnas explícitas: nada de esto escribe en una DB, ni muestra nada en el wizard (D3).

El banco solo guarda velas cerradas (el exportador descarta la que está en formación), así que el ancla está cubierta
cuando alguna TF del modelo tiene una vela que cierra en el ancla o después: el export se hizo después del ancla y
trae todas las velas que ya habían cerrado.
"""
import fcntl
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

import config.auto_resolution as cfg
import tools.p2_backtest as p2_backtest
from tools.candle_bank import ALL_BANK_TIMEFRAMES, bank_csv_path, read_candle_csv
from tools.auto_resolution import bank_clock_reason, parse_datetime

STATUS_OK = "ok"
# Estados que se reintentan en el catch-up siguiente (RF-12c): el banco todavía no llega al ancla, o su reloj todavía
# no se verificó. Cualquier otro estado es definitivo.
RETRY_STATUSES = frozenset({cfg.REASON_PENDING_CANDLES, cfg.REASON_CLOCK_UNVERIFIED, cfg.REASON_CLOCK_MISALIGNED})
_GT = timezone(timedelta(hours=-6))


def log_path() -> str:
    return os.path.join(cfg.ACCOUNTS_DATA_DIR, cfg.P2_MODEL_LOG_NAME)


def _now_gt() -> datetime:
    return datetime.now(_GT).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Receta y huella (plan.md §2.5)
# --------------------------------------------------------------------------

def model_recipe(model: "p2_backtest.ModelSpec") -> dict:
    """Lo que define el cálculo del modelo. `label` y `description` no entran: son solo texto."""
    return {"timeframes": list(model.timeframes), "weights": {tf: model.weights[tf] for tf in model.timeframes},
            "ema_chain": list(model.ema_chain), "use_di": model.use_di}


def model_hash(model: "p2_backtest.ModelSpec") -> str:
    canonical = json.dumps(model_recipe(model), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# El registro
# --------------------------------------------------------------------------

@dataclass
class LogLine:
    number: int  # 1 = la primera línea del archivo
    data: dict


def parse_log(text: str) -> List[LogLine]:
    """Las líneas del registro con su número. Una línea vacía o ilegible se saltea, pero cuenta para la numeración."""
    lines = []
    for number, raw in enumerate(text.splitlines(), start=1):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict):
            lines.append(LogLine(number, data))
    return lines


def read_log(path: Optional[str] = None) -> List[LogLine]:
    path = log_path() if path is None else path
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return parse_log(handle.read())


def latest_lines(lines: Sequence[LogLine]) -> Dict[Tuple[str, str], LogLine]:
    """La última línea de cada par (`trade_id`, `model`): la vigente."""
    latest: Dict[Tuple[str, str], LogLine] = {}
    for line in lines:
        latest[(line.data.get("trade_id"), line.data.get("model"))] = line
    return latest


# --------------------------------------------------------------------------
# Chequeo de la lista de modelos (RF-12e)
# --------------------------------------------------------------------------

@dataclass
class ModelToLog:
    name: str
    since: datetime
    spec: "p2_backtest.ModelSpec"
    hash: str


def check_models(models: Mapping[str, str], lines: Sequence[LogLine]) -> Tuple[List[ModelToLog], List[str]]:
    """Los modelos de la lista que se pueden registrar, y un aviso por cada uno que se saltea: `unknown_model:<NAME>`,
    `timeframe_not_in_bank:<TF>` o `model_recipe_changed:<NAME>`. Uno que falla no frena a los demás."""
    valid: List[ModelToLog] = []
    warnings: List[str] = []
    registry = p2_backtest.MODELS_BY_NAME
    for name, since in models.items():
        spec = registry.get(name)
        if spec is None:
            warnings.append(cfg.reason_unknown_model(name))
            continue
        missing = [tf for tf in spec.timeframes if tf not in ALL_BANK_TIMEFRAMES]
        if missing:
            warnings.append(cfg.reason_timeframe_not_in_bank(missing[0]))
            continue
        current = model_hash(spec)
        if any(line.data.get("model") == name and line.data.get("model_hash") != current for line in lines):
            warnings.append(cfg.reason_model_recipe_changed(name))
            continue
        valid.append(ModelToLog(name, datetime.fromisoformat(since), spec, current))
    return valid, warnings


# --------------------------------------------------------------------------
# Los análisis nuevos de una cuenta (solo lectura)
# --------------------------------------------------------------------------

@dataclass
class NewAnalysis:
    trade_id: str
    asset: Optional[str]
    analysis_start_time: datetime
    operator_p2: dict = field(default_factory=dict)


def read_new_analyses(db_path: str, trade_ids: Optional[Sequence[str]] = None) -> List[NewAnalysis]:
    """Los análisis nuevos (con `analysis_start_time`, no retroactivos ni clones [2]) de la DB, en `mode=ro` y con
    columnas explícitas. Una DB sin la columna nueva no tiene análisis nuevos."""
    conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(unified_department)")}
        if "analysis_start_time" not in columns:
            return []
        rows = conn.execute(
            "SELECT id, asset, analysis_start_time FROM unified_department "
            "WHERE analysis_start_time IS NOT NULL AND COALESCE(is_backdated, 0) = 0 ORDER BY analysis_start_time"
        ).fetchall()
        wanted = None if trade_ids is None else set(trade_ids)
        analyses = []
        for trade_id, asset, start in rows:
            if wanted is not None and trade_id not in wanted:
                continue
            p2 = conn.execute(
                "SELECT direction, strength, score FROM analysis_layer "
                "WHERE trade_id = ? AND department = 'EFFICIENCY' AND layer_name = 'P2'", (trade_id,)).fetchone()
            operator_p2 = ({"direction": p2[0], "strength": p2[1], "score": p2[2]} if p2
                           else {"direction": None, "strength": None, "score": None})
            analyses.append(NewAnalysis(trade_id, asset, parse_datetime(start), operator_p2))
        return analyses
    finally:
        conn.close()


# --------------------------------------------------------------------------
# El P2 de un modelo en el ancla
# --------------------------------------------------------------------------

def _covers(bank_dir: str, timeframes: Sequence[str], anchor: datetime) -> bool:
    """¿El banco llega hasta el ancla? Sí, si alguna TF tiene una vela que cierra en el ancla o después."""
    for tf in timeframes:
        df = read_candle_csv(bank_csv_path(bank_dir, tf))
        if not df.empty:
            last_close = pd.Timestamp(df["time"].max()) + pd.Timedelta(minutes=p2_backtest.TIMEFRAME_MINUTES[tf])
            if last_close >= pd.Timestamp(anchor):
                return True
    return False


def _tf_detail(snap, model, tf) -> dict:
    bias = p2_backtest.bias_from_emas(snap, model.ema_chain)
    if model.use_di and bias is not None:
        bias = p2_backtest.confirm_with_di(bias, snap)
    return {"bias": bias, "price": snap.price, "ema20": snap.ema20, "ema100": snap.ema100, "ema200": snap.ema200,
            "adx": snap.adx14, "plus_di": snap.plus_di, "minus_di": snap.minus_di, "weight": model.weights[tf],
            "bars": snap.bars_available}


def model_p2(model: "p2_backtest.ModelSpec", symbol: Optional[str], anchor: datetime,
             bank_root: str) -> Tuple[str, Optional[float], Optional[int], Dict[str, dict]]:
    """`(estado, p2 crudo, p2 reescalado, detalle por TF)` del modelo en el ancla. El estado es `ok` o el motivo:
    `no_mt5_symbol`, el del reloj, `pending_candles` o `insufficient_history:<TF>` (RF-12c)."""
    if symbol is None:
        return cfg.REASON_NO_MT5_SYMBOL, None, None, {}
    bank_dir = os.path.join(bank_root, symbol)
    clock = bank_clock_reason(bank_dir)
    if clock:
        return clock, None, None, {}
    if not _covers(bank_dir, model.timeframes, anchor):
        return cfg.REASON_PENDING_CANDLES, None, None, {}
    provider = p2_backtest.CsvOHLCProvider(bank_dir)
    snapshots = {tf: provider.get_indicator_snapshot(tf, anchor) for tf in model.timeframes}
    for tf in model.timeframes:
        snap = snapshots[tf]
        if snap is None or snap.bars_available < p2_backtest.MIN_BARS_PER_TF:
            return cfg.reason_insufficient_history(tf), None, None, {}
    raw, incomplete = p2_backtest.compute_score_p2_sistematico(snapshots, model)
    if incomplete:  # no pasa con este proveedor, que siempre calcula EMA100 y DI; por las dudas, nunca se inventa
        return cfg.reason_insufficient_history(model.timeframes[0]), None, None, {}
    detail = {tf: _tf_detail(snapshots[tf], model, tf) for tf in model.timeframes}
    return STATUS_OK, raw, p2_backtest.rescale_p2_sistematico(raw), detail


def build_line(account: str, analysis: NewAnalysis, model: ModelToLog, bank_root: str,
               symbol_map: Mapping[str, str], logged_at: datetime) -> dict:
    symbol = symbol_map.get(analysis.asset)
    status, raw, rescaled, detail = model_p2(model.spec, symbol, analysis.analysis_start_time, bank_root)
    return {
        "trade_id": analysis.trade_id, "account": account, "asset": analysis.asset, "symbol": symbol,
        "anchor": analysis.analysis_start_time.isoformat(), "logged_at": logged_at.isoformat(timespec="seconds"),
        "operator_p2": analysis.operator_p2,
        "model": model.name, "model_since": model.since.date().isoformat(), "model_hash": model.hash,
        "model_spec": model_recipe(model.spec),
        "p2_raw": raw, "p2_rescaled": rescaled, "status": status, "by_tf": detail,
    }


# --------------------------------------------------------------------------
# Registrar
# --------------------------------------------------------------------------

@dataclass
class LogResult:
    written: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def log_account(db_path: str, account: str, bank_root: Optional[str] = None, path: Optional[str] = None,
                trade_ids: Optional[Sequence[str]] = None, symbol: Optional[str] = None,
                models: Optional[Mapping[str, str]] = None, symbol_map: Optional[Mapping[str, str]] = None,
                now: Optional[datetime] = None) -> LogResult:
    """
    Registra los análisis nuevos de una cuenta que todavía no tienen la línea de cada modelo, o cuya línea se reintenta
    (RF-12, RF-12b, RF-12c). `trade_ids` limita a esos análisis (el guardado), y `symbol`, a los de ese símbolo MT5 (el
    catch-up después de fusionar). Una línea que se reintenta y da el mismo motivo no se vuelve a escribir. El
    archivo se toma con un candado mientras se lee y se agrega, para que el guardado y el catch-up no dupliquen.
    """
    bank_root = cfg.CANDLE_BANK_DIR if bank_root is None else bank_root
    path = log_path() if path is None else path
    models = cfg.P2_LOG_MODELS if models is None else models
    symbol_map = cfg.MT5_SYMBOL_MAP if symbol_map is None else symbol_map
    now = _now_gt() if now is None else now

    analyses = [analysis for analysis in read_new_analyses(db_path, trade_ids)
                if symbol is None or symbol_map.get(analysis.asset) == symbol]
    result = LogResult()
    if not analyses:
        result.warnings = check_models(models, read_log(path))[1]
        return result
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.seek(0)
            text = handle.read()
            lines = parse_log(text)
            next_number = len(text.splitlines()) + 1
            if text and not text.endswith("\n"):
                handle.write("\n")  # una escritura anterior quedó cortada: esa línea queda sola y no se pega a la nueva
            valid, result.warnings = check_models(models, lines)
            latest = latest_lines(lines)
            for analysis in analyses:
                for model in valid:
                    if analysis.analysis_start_time < model.since:
                        continue
                    previous = latest.get((analysis.trade_id, model.name))
                    if previous is not None and previous.data.get("status") not in RETRY_STATUSES:
                        continue
                    line = build_line(account, analysis, model, bank_root, symbol_map, now)
                    if previous is not None:
                        if line["status"] == previous.data.get("status"):
                            continue  # sigue igual: nada nuevo que registrar
                        line["supersedes"] = previous.number
                    handle.write(json.dumps(line, ensure_ascii=False) + "\n")
                    latest[(analysis.trade_id, model.name)] = LogLine(next_number, line)
                    next_number += 1
                    result.written.append(line)
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return result
