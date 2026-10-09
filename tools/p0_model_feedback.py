"""
tools/p0_model_feedback.py — Registro prospectivo del P0 sistemático, opción 2 (`docs/p0-reglas-opcion2.md` §9).

Funciona como el registro del P2 (`tools/p2_model_feedback.py`), del que reusa el formato de las líneas, la lectura y el
candado. Para cada modelo de `P0_LOG_MODELS` (nombre → fecha de alta), calcula el P0 en el ancla de cada análisis nuevo
de las cuentas reales y agrega **una línea JSON por análisis y modelo** a `${ACCOUNTS_DATA_DIR}/p0_model_log.jsonl`.
- El archivo solo crece. Una línea que se reintenta (`pending_candles` o un reloj sin verificar) se reemplaza con otra
  que lleva `supersedes: <n.º de línea>`, y la vigente es la última de cada par (`trade_id`, `model`).
- **Cuándo corre:** solo en el catch-up después de cada export fusionado, dentro del proceso de fondo del export. Al
  guardar no corre, así el guardado no espera nada. Un análisis se registra en el primer export que cubre su ancla.
- **Qué velas usa:** solo las del banco que ya habían cerrado en el ancla (`candles_until`). Así el resultado no depende
  de cuándo corra.
- **Qué análisis entran:** los mismos que en el P2 (N40, N42): con `analysis_start_time`, no retroactivos ni clones, y
  del día de alta del modelo en adelante. El ancla es `analysis_start_time`.
- **Qué no hace:** nunca escribe en una DB, que lee en `mode=ro` con columnas explícitas, ni muestra en el wizard lo que
  dice el modelo.
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
from tools import p0_opcion2 as o2
from tools import p0_sistematico as ps
from tools.auto_resolution import bank_clock_reason, parse_datetime
from tools.candle_bank import bank_csv_path, read_candle_csv
from tools.p2_model_feedback import RETRY_STATUSES, STATUS_OK, LogLine, LogResult, latest_lines, parse_log, read_log

_GT = timezone(timedelta(hours=-6))
MODEL_TFS = o2.ORDER                 # las TF que lee el modelo
CANDLE_TFS = ps.ALL_TFS              # las que necesita el mercado (las finas, para los eventos)


def log_path() -> str:
    return os.path.join(cfg.ACCOUNTS_DATA_DIR, cfg.P0_MODEL_LOG_NAME)


def _now_gt() -> datetime:
    return datetime.now(_GT).replace(tzinfo=None)


def model_hash(params: "o2.Opcion2Params") -> str:
    canonical = json.dumps(o2.model_recipe(params), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


@dataclass
class ModelToLog:
    name: str
    since: datetime
    params: "o2.Opcion2Params"
    hash: str


def check_models(models: Mapping[str, str], lines: Sequence[LogLine]) -> Tuple[List[ModelToLog], List[str]]:
    """Los modelos que se pueden registrar, y un aviso por cada uno que se saltea: `unknown_model:<NAME>` o
    `model_recipe_changed:<NAME>`. Uno que falla no frena a los demás."""
    valid: List[ModelToLog] = []
    warnings: List[str] = []
    for name, since in models.items():
        params = o2.MODELS_BY_NAME.get(name)
        if params is None:
            warnings.append(cfg.reason_unknown_model(name))
            continue
        current = model_hash(params)
        if any(line.data.get("model") == name and line.data.get("model_hash") != current for line in lines):
            warnings.append(cfg.reason_model_recipe_changed(name))
            continue
        valid.append(ModelToLog(name, datetime.fromisoformat(since), params, current))
    return valid, warnings


@dataclass
class NewAnalysis:
    trade_id: str
    asset: Optional[str]
    analysis_start_time: datetime
    operator_p0: dict = field(default_factory=dict)


def read_new_analyses(db_path: str, trade_ids: Optional[Sequence[str]] = None) -> List[NewAnalysis]:
    """Los análisis nuevos de la DB, con el P0 del operador, en `mode=ro` y con columnas explícitas."""
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
        out = []
        for trade_id, asset, start in rows:
            if wanted is not None and trade_id not in wanted:
                continue
            p0 = conn.execute(
                "SELECT direction, strength FROM analysis_layer "
                "WHERE trade_id = ? AND department = 'EFFICIENCY' AND layer_name = 'P0'", (trade_id,)).fetchone()
            operator_p0 = {"direction": p0[0], "strength": p0[1]} if p0 else {"direction": None, "strength": None}
            out.append(NewAnalysis(trade_id, asset, parse_datetime(start), operator_p0))
        return out
    finally:
        conn.close()


def _covers(bank_dir: str, anchor: datetime) -> bool:
    """¿El banco llega hasta el ancla? Sí, si alguna TF del modelo tiene una vela que cierra en el ancla o después."""
    for tf in MODEL_TFS:
        df = read_candle_csv(bank_csv_path(bank_dir, tf))
        if not df.empty:
            last_close = pd.Timestamp(df["time"].max()) + pd.Timedelta(minutes=ps.TF_MINUTES[tf])
            if last_close >= pd.Timestamp(anchor):
                return True
    return False


def _leg(leg) -> Optional[dict]:
    if leg is None:
        return None
    return {"a": leg.a.price, "a_time": leg.a.time.isoformat(), "b": leg.b.price, "b_time": leg.b.time.isoformat()}


def model_p0(params: "o2.Opcion2Params", symbol: Optional[str], anchor: datetime,
             bank_root: str) -> Tuple[str, Optional[int], Optional[str], Dict[str, dict]]:
    """`(estado, dirección, fuerza, detalle por TF)` del modelo en el ancla. El estado es `ok` o el motivo:
    `no_mt5_symbol`, el del reloj, `pending_candles` o `timeframe_not_in_bank:<TF>`."""
    if symbol is None:
        return cfg.REASON_NO_MT5_SYMBOL, None, None, {}
    bank_dir = os.path.join(bank_root, symbol)
    clock = bank_clock_reason(bank_dir)
    if clock:
        return clock, None, None, {}
    missing = [tf for tf in CANDLE_TFS if not os.path.exists(bank_csv_path(bank_dir, tf))]
    if missing:
        return cfg.reason_timeframe_not_in_bank(missing[0]), None, None, {}
    if not _covers(bank_dir, anchor):
        return cfg.REASON_PENDING_CANDLES, None, None, {}
    candles = {tf: ps.read_candles(bank_csv_path(bank_dir, tf)) for tf in CANDLE_TFS}
    market = ps.Market(symbol, ps.candles_until(candles, anchor))
    out = o2.Opcion2(market, params).predict_at(anchor)
    detail = {}
    for tf, reading in out["readings"].items():
        last = reading.last_event
        detail[tf] = {"score": out["scores"][tf], "state": reading.state, "situation": reading.situation,
                      "leg": _leg(reading.leg),
                      "last_event": None if last is None else {"r": last.r, "outcome": last.outcome}}
    return STATUS_OK, int(out["direction"]), out["strength"], detail


def build_line(account: str, analysis: NewAnalysis, model: ModelToLog, bank_root: str,
               symbol_map: Mapping[str, str], logged_at: datetime) -> dict:
    symbol = symbol_map.get(analysis.asset)
    status, direction, strength, detail = model_p0(model.params, symbol, analysis.analysis_start_time, bank_root)
    return {
        "trade_id": analysis.trade_id, "account": account, "asset": analysis.asset, "symbol": symbol,
        "anchor": analysis.analysis_start_time.isoformat(), "logged_at": logged_at.isoformat(timespec="seconds"),
        "operator_p0": analysis.operator_p0,
        "model": model.name, "model_since": model.since.date().isoformat(), "model_hash": model.hash,
        "model_spec": o2.model_recipe(model.params),
        "status": status, "direction": direction, "strength": strength, "by_tf": detail,
    }


def log_account(db_path: str, account: str, bank_root: Optional[str] = None, path: Optional[str] = None,
                trade_ids: Optional[Sequence[str]] = None, symbol: Optional[str] = None,
                models: Optional[Mapping[str, str]] = None, symbol_map: Optional[Mapping[str, str]] = None,
                now: Optional[datetime] = None, write_pending: bool = False) -> LogResult:
    """
    Registra los análisis nuevos de una cuenta que todavía no tienen la línea de cada modelo, o cuya línea se reintenta.
    Misma lógica que `p2_model_feedback.log_account`: `symbol` limita a ese símbolo MT5 (el catch-up), una línea que se
    reintenta con el mismo motivo no se reescribe, y el archivo se toma con un candado para no duplicar. Con
    `write_pending=False` (el valor del catch-up) un análisis sin línea cuyo banco todavía no cubre el ancla no se escribe.
    """
    bank_root = cfg.CANDLE_BANK_DIR if bank_root is None else bank_root
    path = log_path() if path is None else path
    models = cfg.P0_LOG_MODELS if models is None else models
    symbol_map = cfg.MT5_SYMBOL_MAP if symbol_map is None else symbol_map
    now = _now_gt() if now is None else now

    analyses = [a for a in read_new_analyses(db_path, trade_ids)
                if symbol is None or symbol_map.get(a.asset) == symbol]
    result = LogResult()
    lines = read_log(path)
    valid, result.warnings = check_models(models, lines)
    latest = latest_lines(lines)
    pending: List[Tuple[Tuple[str, str], Optional[int], dict]] = []
    for analysis in analyses:
        for model in valid:
            if analysis.analysis_start_time < model.since:
                continue
            key = (analysis.trade_id, model.name)
            previous = latest.get(key)
            if previous is not None and previous.data.get("status") not in RETRY_STATUSES:
                continue
            line = build_line(account, analysis, model, bank_root, symbol_map, now)
            if previous is None and not write_pending and line["status"] in RETRY_STATUSES:
                continue
            if previous is not None:
                if line["status"] == previous.data.get("status"):
                    continue
                line["supersedes"] = previous.number
            pending.append((key, previous.number if previous is not None else None, line))
    if not pending:
        return result
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.seek(0)
            text = handle.read()
            current = latest_lines(parse_log(text))
            next_number = len(text.splitlines()) + 1
            if text and not text.endswith("\n"):
                handle.write("\n")
            for key, previous_number, line in pending:
                now_there = current.get(key)
                if (now_there.number if now_there is not None else None) != previous_number:
                    continue
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
                current[key] = LogLine(next_number, line)
                next_number += 1
                result.written.append(line)
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return result


def catch_up(symbol: str, accounts_data_dir: Optional[str] = None, bank_root: Optional[str] = None,
             real_accounts: Optional[Mapping[str, str]] = None, symbol_map: Optional[Mapping[str, str]] = None,
             models: Optional[Mapping[str, str]] = None) -> LogResult:
    """Después de fusionar velas de `symbol`: los análisis nuevos de ese símbolo en todas las cuentas reales que todavía
    no tienen su línea. Una DB que no existe se saltea; los avisos se juntan sin repetir."""
    accounts_data_dir = cfg.ACCOUNTS_DATA_DIR if accounts_data_dir is None else accounts_data_dir
    real_accounts = cfg.REAL_ACCOUNTS if real_accounts is None else real_accounts
    path = os.path.join(accounts_data_dir, cfg.P0_MODEL_LOG_NAME)
    total = LogResult()
    for account, db_name in real_accounts.items():
        db_path = os.path.join(accounts_data_dir, db_name)
        if not os.path.exists(db_path):
            continue
        result = log_account(db_path, account, bank_root=bank_root, path=path, symbol=symbol, models=models,
                             symbol_map=symbol_map)
        total.written += result.written
        total.warnings += [warning for warning in result.warnings if warning not in total.warnings]
    return total
