"""
tools/auto_backfill.py — el plan del backfill (spec 002, T47; RF-11 a RF-11c, N2, N9, N27; plan.md §3.11).

Para cada cuenta de `REAL_ACCOUNTS` compara lo que proponen las velas con lo que hay en la DB y arma una lista de
cambios, sin escribir nada:
  - `fill`: el campo está vacío (o es un "Open" de un audit nunca hecho, N9) y las velas proponen un valor;
  - `unchanged`: ya tiene el valor propuesto (los precios a 0.1% y los valores en R a 0.1R, como el reporte);
  - `conflict`: tiene otro valor; solo se aplica si el usuario lo acepta uno por uno (RF-11e, T51);
  - `legacy_move` (RF-11c, N2): un audit viejo guarda en `resolution_time` la hora en que se guardó; esa hora pasa a
    `audit_registration_time` y `resolution_time` pasa a la hora del primer toque, o queda vacía con el motivo.

Campos: en `efficiency_audit`, `resolution_type`, `structural_resolution`, `failure_reason`, `structural_mae`,
`structural_mfe`, `resolution_time` y su `resolution_time_source`; en `tactical_audit`, de las órdenes llenadas,
`mae_adverse`, `mfe_favorable` y `could_hit_tp`. Los campos manuales de INV-8 nunca entran. Las DBs se leen en
`mode=ro` y con columnas explícitas (RF-6b).

`apply_plan` (T49, RF-11b, RF-18) aplica los `fill`, los `legacy_move` y los conflictos aceptados uno por uno (como
`accepted_conflict`), en una transacción por cuenta, e inserta una fila de `backfill_history` por cambio. Nunca
modifica ni borra filas del historial.

`run_apply` (T50, RF-11d, R9) pone las puertas antes de escribir: el ensayo de todas las cuentas sobre copias
temporales (código 3), un backup local de las últimas 24 h de cada DB (código 4) y la confirmación escribiendo
`APPLY` (código 5). Si una puerta falla, no se escribe en ninguna DB real.
"""
from __future__ import annotations

import dataclasses
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from sqlalchemy import create_engine, text

from config.auto_resolution import MARK_PRICE_TOLERANCE, REAL_ACCOUNTS
from tools.auto_resolution import AccountResolver, AutoProposal, parse_datetime, parse_float, tactical_from_candles
from tools.p2_backtest import open_readonly_session

KIND_FILL, KIND_UNCHANGED, KIND_CONFLICT, KIND_LEGACY_MOVE = "fill", "unchanged", "conflict", "legacy_move"
KIND_ACCEPTED_CONFLICT = "accepted_conflict"  # un conflicto que el usuario aceptó, ya aplicado (RF-11e)
SOURCE_CANDLES, SOURCE_LEGACY = "candles", "legacy"
PRICE_TOLERANCE = MARK_PRICE_TOLERANCE  # 0.1% del precio, la misma que usa el reporte para MAE/MFE
R_TOLERANCE = 0.1                       # en R, para el MAE/MFE táctico

EFFICIENCY_COLUMNS = ("id", "resolution_type", "real_bias_b", "structural_resolution", "failure_reason",
                      "structural_mae", "structural_mfe", "resolution_time", "audit_registration_time",
                      "resolution_time_source")
TACTICAL_COLUMNS = ("id", "trade_id", "order_filled", "entry_time", "exit_time", "entry_price", "stop_loss",
                    "take_profit", "mae_adverse", "mfe_favorable", "could_hit_tp")
_PRICE_FIELDS = {"structural_mae", "structural_mfe"}
_R_FIELDS = {"mae_adverse", "mfe_favorable"}
_TIME_FIELDS = {"resolution_time"}


@dataclass(frozen=True)
class PlannedChange:
    """Un cambio del plan. `record_id` es el id de la fila (en `tactical_audit`, el de la ejecución); `source`,
    `candles`, `legacy` o el motivo por el que el valor nuevo queda vacío."""
    account: str
    table_name: str
    record_id: str
    trade_id: str
    field: str
    kind: str
    old_value: Any
    new_value: Any
    source: str


@dataclass
class AccountPlan:
    account: str
    db_name: str
    changes: List[PlannedChange] = field(default_factory=list)

    def count(self, kind: str) -> int:
        return sum(1 for change in self.changes if change.kind == kind)


def _read_rows(db_path: str, table: str, columns) -> List[Dict[str, Any]]:
    session = open_readonly_session(db_path)
    try:
        tables = {row[0] for row in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if table not in tables:
            return []
        existing = {row[1] for row in session.execute(text(f"PRAGMA table_info({table})"))}
        present = [column for column in columns if column in existing]
        rows = session.execute(text(f"SELECT {', '.join(present)} FROM {table}")).all()
    finally:
        session.close()
    result = []
    for row in rows:
        values = {column: None for column in columns}
        values.update(zip(present, row))
        result.append(values)
    return result


def _same(field_name: str, current: Any, proposed: Any) -> bool:
    if field_name in _PRICE_FIELDS:
        return abs(float(current) - float(proposed)) <= PRICE_TOLERANCE * abs(float(proposed))
    if field_name in _R_FIELDS:
        return abs(float(current) - float(proposed)) <= R_TOLERANCE + 1e-9
    if field_name in _TIME_FIELDS:
        return current.replace(second=0, microsecond=0) == proposed.replace(second=0, microsecond=0)
    return current == proposed


def _compare(account, table, record_id, trade_id, field_name, current, proposed, empty=None) -> PlannedChange:
    """`fill`, `unchanged` o `conflict` de un campo con valor propuesto."""
    is_empty = current is None if empty is None else empty
    if is_empty:
        kind = KIND_FILL
    elif _same(field_name, current, proposed):
        kind = KIND_UNCHANGED
    else:
        kind = KIND_CONFLICT
    return PlannedChange(account, table, record_id, trade_id, field_name, kind, current, proposed, SOURCE_CANDLES)


def _efficiency_changes(account: str, row: Dict[str, Any], proposal: AutoProposal) -> List[PlannedChange]:
    trade_id = row["id"]
    changes = []

    def add(field_name, current, proposed, empty=None):
        if proposed is not None:
            changes.append(_compare(account, "efficiency_audit", trade_id, trade_id, field_name, current, proposed,
                                    empty))

    never_done = row["resolution_type"] == "Open" and row["real_bias_b"] is None  # N9
    add("resolution_type", row["resolution_type"], proposal.resolution_type,
        empty=row["resolution_type"] is None or never_done)
    add("structural_resolution", row["structural_resolution"], proposal.structural_resolution)
    add("failure_reason", row["failure_reason"], proposal.failure_reason)
    add("structural_mae", parse_float(row["structural_mae"]), proposal.structural_mae)
    add("structural_mfe", parse_float(row["structural_mfe"]), proposal.structural_mfe)
    changes += _resolution_time_changes(account, row, proposal)
    return changes


def _resolution_time_changes(account: str, row: Dict[str, Any], proposal: AutoProposal) -> List[PlannedChange]:
    """RF-11c: el `legacy_move` de los audits viejos y la hora del primer toque, con su `resolution_time_source`."""
    trade_id = row["id"]
    current = parse_datetime(row["resolution_time"])
    registered = parse_datetime(row["audit_registration_time"])
    proposed = proposal.resolution_time
    source = SOURCE_CANDLES if proposed is not None else proposal.reason

    def change(field_name, kind, old, new, origin):
        return PlannedChange(account, "efficiency_audit", trade_id, trade_id, field_name, kind, old, new, origin)

    # La hora vieja del guardado (N2): con hora, sin `audit_registration_time` y sin origen. Una hora que ya puso el
    # wizard (T43) o un backfill anterior siempre tiene origen, así que nunca se vuelve a mover (idempotencia).
    if current is not None and registered is None and row["resolution_time_source"] is None:
        changes = [change("audit_registration_time", KIND_LEGACY_MOVE, None, current, SOURCE_LEGACY),
                   change("resolution_time", KIND_FILL, current, proposed, source)]
        if source is not None:
            changes.append(change("resolution_time_source", KIND_FILL, row["resolution_time_source"], source, source))
        return changes
    if current is None:
        if source is None or row["resolution_time_source"] is not None:
            return []
        changes = [change("resolution_time", KIND_FILL, None, proposed, source)] if proposed is not None else []
        return changes + [change("resolution_time_source", KIND_FILL, None, source, source)]
    if proposed is None:
        return []
    return [_compare(account, "efficiency_audit", trade_id, trade_id, "resolution_time", current, proposed)]


def _tactical_changes(account: str, row: Dict[str, Any], asset: Optional[str],
                      resolver: AccountResolver) -> List[PlannedChange]:
    if not row["order_filled"] or row["entry_price"] is None or row["stop_loss"] is None:
        return []
    symbol = resolver.symbol_map.get(asset)
    if symbol is None:
        return []
    clock_reason, candles = resolver.bank_for(symbol)
    if clock_reason:
        return []
    excursion, tp = tactical_from_candles(
        candles, parse_datetime(row["entry_time"]), parse_datetime(row["exit_time"]), float(row["entry_price"]),
        float(row["stop_loss"]), parse_float(row["take_profit"]))
    proposals = []
    if excursion is not None and excursion.reason is None:
        proposals += [("mae_adverse", parse_float(row["mae_adverse"]), excursion.mae_r),
                      ("mfe_favorable", parse_float(row["mfe_favorable"]), excursion.mfe_r)]
    if tp is not None and tp.answer is not None:
        proposals.append(("could_hit_tp", row["could_hit_tp"], tp.answer))
    return [_compare(account, "tactical_audit", row["id"], row["trade_id"], name, current, proposed)
            for name, current, proposed in proposals]


def plan_account(account: str, db_path: str, bank_root: str,
                 symbol_map: Optional[Mapping[str, str]] = None) -> AccountPlan:
    """El plan de una cuenta. Nunca escribe."""
    resolver = AccountResolver(db_path, bank_root, account=account, symbol_map=symbol_map)
    proposals = {proposal.trade_id: proposal for proposal in resolver.propose_all()}
    assets = {row.trade_id: row.asset for row in resolver.rows}
    plan = AccountPlan(account, os.path.basename(db_path))
    for row in _read_rows(db_path, "efficiency_audit", EFFICIENCY_COLUMNS):
        if row["id"] in proposals:
            plan.changes += _efficiency_changes(account, row, proposals[row["id"]])
    for row in _read_rows(db_path, "tactical_audit", TACTICAL_COLUMNS):
        plan.changes += _tactical_changes(account, row, assets.get(row["trade_id"]), resolver)
    return plan


def build_plan(accounts_data_dir: str, bank_root: str,
               real_accounts: Optional[Mapping[str, str]] = None) -> List[AccountPlan]:
    """Un `AccountPlan` por cuenta de `real_accounts` (por defecto `REAL_ACCOUNTS`) cuyo archivo exista."""
    real_accounts = REAL_ACCOUNTS if real_accounts is None else real_accounts
    plans = []
    for account, db_name in real_accounts.items():
        db_path = os.path.join(accounts_data_dir, db_name)
        if os.path.exists(db_path):
            plans.append(plan_account(account, db_path, bank_root))
    return plans


# --- Aplicación (T49; RF-11b, RF-11c, RF-18) ---------------------------------------------------------------------------

# Lo único que el backfill puede escribir. Los campos manuales de INV-8 nunca están acá.
WRITABLE_FIELDS = {
    "efficiency_audit": {"resolution_type", "structural_resolution", "failure_reason", "structural_mae",
                         "structural_mfe", "resolution_time", "resolution_time_source", "audit_registration_time"},
    "tactical_audit": {"mae_adverse", "mfe_favorable", "could_hit_tp"},
}
# El formato en que SQLAlchemy guarda un DateTime en SQLite; el historial guarda el valor legible.
_DB_DATETIME = "%Y-%m-%d %H:%M:%S.%f"


def _now_gt() -> datetime:
    return datetime.now(timezone(timedelta(hours=-6))).replace(tzinfo=None)


def _db_value(value: Any) -> Any:
    return value.strftime(_DB_DATETIME) if isinstance(value, datetime) else value


def _history_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def changes_to_apply(plan: AccountPlan, accepted=()) -> List[PlannedChange]:
    """Los cambios que se escriben: los `fill`, los `legacy_move` y los conflictos aceptados (marcados
    `accepted_conflict`). `accepted` son tuplas `(tabla, record_id, campo)`."""
    accepted = set(accepted)
    result = []
    for change in plan.changes:
        if change.kind in (KIND_FILL, KIND_LEGACY_MOVE):
            result.append(change)
        elif change.kind == KIND_CONFLICT and (change.table_name, change.record_id, change.field) in accepted:
            result.append(dataclasses.replace(change, kind=KIND_ACCEPTED_CONFLICT))
    for change in result:
        if change.field not in WRITABLE_FIELDS.get(change.table_name, ()):
            raise ValueError(f"the backfill cannot write {change.table_name}.{change.field}")
    return result


def apply_plan(db_path: str, plan: AccountPlan, accepted=(), run_id: Optional[str] = None,
               run_at: Optional[datetime] = None) -> List[PlannedChange]:
    """
    Aplica el plan de una cuenta en una sola transacción: un `UPDATE` por campo (sobre una fila que tiene que existir)
    y un `INSERT` en `backfill_history` por cambio. Si algo falla, no queda nada escrito. Sin cambios para aplicar, ni
    abre la DB. Devuelve los cambios aplicados.
    """
    to_apply = changes_to_apply(plan, accepted)
    if not to_apply:
        return []
    run_id = run_id or str(uuid.uuid4())
    run_at = run_at or _now_gt()
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            for change in to_apply:
                result = conn.execute(text(f"UPDATE {change.table_name} SET {change.field} = :value WHERE id = :id"),
                                      {"value": _db_value(change.new_value), "id": change.record_id})
                if result.rowcount != 1:
                    raise ValueError(f"{change.table_name} {change.record_id} not found")
                conn.execute(text(
                    "INSERT INTO backfill_history (run_id, run_at, kind, table_name, record_id, field, old_value, "
                    "new_value, source) VALUES (:run_id, :run_at, :kind, :table_name, :record_id, :field, :old_value, "
                    ":new_value, :source)"),
                    {"run_id": run_id, "run_at": run_at.strftime(_DB_DATETIME), "kind": change.kind,
                     "table_name": change.table_name, "record_id": change.record_id, "field": change.field,
                     "old_value": _history_text(change.old_value), "new_value": _history_text(change.new_value),
                     "source": change.source})
    finally:
        engine.dispose()
    return to_apply


# --- Puertas (T50; RF-11d, R9; plan.md §4) ------------------------------------------------------------------------------

EXIT_APPLIED, EXIT_REHEARSAL_FAILED, EXIT_NO_BACKUP, EXIT_CANCELLED = 0, 3, 4, 5
CONFIRM_WORD = "APPLY"
BACKUP_MAX_AGE_H = 24
_BACKUP_RUN_FORMAT = "%Y%m%d_%H%M%S"  # las carpetas de `tools/backup.py` en `.data/backups/`, en hora local


class RehearsalError(Exception):
    """El ensayo sobre la copia de una DB falló."""


def integrity_check(db_path: str) -> str:
    """El resultado de `PRAGMA integrity_check` ("ok" si la DB está sana)."""
    with sqlite3.connect(db_path) as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]


def rehearse(db_path: str, plan: AccountPlan, bank_root: str, accepted=()) -> None:
    """
    El ensayo de una cuenta (RF-11d): copia la DB a una carpeta temporal con la API de backup de SQLite (solo lee el
    original), corre `init_db` sobre la copia, aplica el plan, y comprueba `integrity_check` y que un plan nuevo sobre
    la copia ya no tenga nada para llenar. La carpeta temporal se borra siempre. Levanta `RehearsalError`.
    """
    from tools.database import init_db
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, os.path.basename(db_path))
        source = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        try:
            init_db(f"sqlite:///{copy}").dispose()
            apply_plan(copy, plan, accepted)
            integrity = integrity_check(copy)
            if integrity != "ok":
                raise RehearsalError(f"integrity_check: {integrity}")
            again = plan_account(plan.account, copy, bank_root)
            if again.count(KIND_FILL) or again.count(KIND_LEGACY_MOVE):
                raise RehearsalError("a second plan on the copy still has changes")
        except RehearsalError:
            raise
        except Exception as exc:
            raise RehearsalError(f"{type(exc).__name__}: {exc}") from exc


def recent_backup(db_name: str, backups_root: str, now: datetime,
                  max_age_h: float = BACKUP_MAX_AGE_H) -> Optional[str]:
    """La copia más nueva de `db_name` en las carpetas de `tools/backup.py` de las últimas `max_age_h` horas, o `None`.
    Un archivo vacío no cuenta."""
    if not os.path.isdir(backups_root):
        return None
    best: Optional[Tuple[datetime, str]] = None
    for name in os.listdir(backups_root):
        try:
            stamp = datetime.strptime(name, _BACKUP_RUN_FORMAT)
        except ValueError:
            continue
        if not now - timedelta(hours=max_age_h) <= stamp <= now:
            continue
        path = os.path.join(backups_root, name, db_name)
        if os.path.isfile(path) and os.path.getsize(path) > 0 and (best is None or stamp > best[0]):
            best = (stamp, path)
    return best[1] if best else None


def run_apply(plans: List[AccountPlan], accounts_data_dir: str, bank_root: str, backups_root: str,
              confirm: Callable[[], str], accepted: Optional[Mapping[str, Any]] = None,
              now: Optional[datetime] = None,
              report: Callable[[str], None] = lambda message: None) -> Tuple[int, Dict[str, List[PlannedChange]]]:
    """
    Las puertas y la escritura (plan.md §4). `accepted` es, por cuenta, el conjunto de conflictos aceptados (RF-11e);
    `confirm` devuelve lo que tipeó el usuario; `report` recibe una línea por paso. Devuelve el código de salida y,
    si escribió, los cambios aplicados por cuenta. Sin nada para aplicar, devuelve 0 sin pasar por las puertas.
    """
    accepted = accepted or {}
    now = now or datetime.now()
    if not any(changes_to_apply(plan, accepted.get(plan.account, ())) for plan in plans):
        report("Nothing to apply.")
        return EXIT_APPLIED, {}
    for plan in plans:
        try:
            rehearse(os.path.join(accounts_data_dir, plan.db_name), plan, bank_root, accepted.get(plan.account, ()))
        except RehearsalError as exc:
            report(f"Rehearsal failed for {plan.account} {plan.db_name}: {exc}. No database was written.")
            return EXIT_REHEARSAL_FAILED, {}
    report("Rehearsal on temporary copies: OK.")
    for plan in plans:
        if recent_backup(plan.db_name, backups_root, now) is None:
            report(f"No backup of {plan.db_name} from the last {BACKUP_MAX_AGE_H} h in {backups_root}. Run "
                   "tools/backup.py backup first. No database was written.")
            return EXIT_NO_BACKUP, {}
    report(f"Backups from the last {BACKUP_MAX_AGE_H} h: OK.")
    if confirm() != CONFIRM_WORD:
        report("Cancelled. No database was written.")
        return EXIT_CANCELLED, {}
    run_id, run_at = str(uuid.uuid4()), _now_gt()
    applied = {}
    for plan in plans:
        applied[plan.account] = apply_plan(os.path.join(accounts_data_dir, plan.db_name), plan,
                                           accepted.get(plan.account, ()), run_id=run_id, run_at=run_at)
        report(f"{plan.account} {plan.db_name}: {len(applied[plan.account])} changes written.")
    return EXIT_APPLIED, applied
