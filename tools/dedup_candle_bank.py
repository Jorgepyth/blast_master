"""
tools/dedup_candle_bank.py — limpieza única de las velas duplicadas del banco (spec 002, N44, T16c).

Hasta N44, el import legacy dejaba entrar completas las TF de 4H o más de CSV exportados con un solo desfase (+3 h)
todo el año. El export nuevo pone +2 h en invierno (N34), así que cada vela de invierno quedó dos veces en el banco,
con los mismos precios y la hora corrida 1 h. Esta herramienta quita, en 4H, 12H, 1D y 1W, la copia de 1 h antes de
cada par (`tools.candle_bank.find_relabeled_duplicates`). 1H y las TF menores no se tocan.

Uso, desde la raíz del repo:
    python tools/dedup_candle_bank.py            # solo informa; no escribe nada
    python tools/dedup_candle_bank.py --apply    # respalda los archivos afectados, limpia y verifica

Con `--apply`, por cada símbolo: toma su candado (si hay un export en curso, lo saltea), copia los CSV que va a
cambiar a `{--backup-dir}/candle_bank_pre_dedup_{fecha}/{SÍMBOLO}/`, los reescribe de forma atómica y verifica que no
quede ningún par y que el resto de las velas sea idéntico. Si la verificación falla, restaura el original desde la
copia. Sale con 0 si todo salió bien (también si no había nada que hacer) y con 1 si algo se salteó o falló.
"""
import argparse
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pandas as pd  # noqa: E402

import config.auto_resolution as cfg  # noqa: E402
from tools.candle_bank import (  # noqa: E402
    OVERLAP_PRICE_TOLERANCE,
    RELABEL_DEDUP_TIMEFRAMES,
    CandleBankLockedError,
    acquire_bank_lock,
    bank_csv_path,
    find_relabeled_duplicates,
    read_candle_csv,
    write_candle_csv_atomic,
)

BACKUP_PREFIX = "candle_bank_pre_dedup_"
_PRICE_COLUMNS = ("open", "high", "low", "close")


@dataclass
class FileDedup:
    """Un CSV del banco con pares duplicados. `rows_after` solo se llena si se limpió y verificó."""
    symbol: str
    timeframe: str
    rows_before: int
    pairs: List[Tuple[pd.Timestamp, pd.Timestamp]]
    rows_after: Optional[int] = None


@dataclass
class DedupReport:
    applied: bool
    files: List[FileDedup] = field(default_factory=list)
    backup_dir: Optional[str] = None
    skipped: Dict[str, str] = field(default_factory=dict)             # símbolo -> motivo
    errors: Dict[Tuple[str, str], str] = field(default_factory=dict)  # (símbolo, TF) -> motivo; se restauró

    @property
    def total_pairs(self) -> int:
        return sum(len(f.pairs) for f in self.files)

    @property
    def cleaned(self) -> List[FileDedup]:
        return [f for f in self.files if f.rows_after is not None]


def _symbols(bank_root: str) -> List[str]:
    if not os.path.isdir(bank_root):
        return []
    return sorted(name for name in os.listdir(bank_root) if os.path.isdir(os.path.join(bank_root, name)))


def _plan_symbol(bank_root: str, symbol: str) -> List[Tuple[FileDedup, pd.DataFrame]]:
    planned = []
    for tf in RELABEL_DEDUP_TIMEFRAMES:
        path = bank_csv_path(os.path.join(bank_root, symbol), tf)
        if not os.path.exists(path):
            continue
        df = read_candle_csv(path)
        pairs = find_relabeled_duplicates(df, tf)
        if pairs:
            planned.append((FileDedup(symbol, tf, len(df), pairs), df))
    return planned


def _verification_problem(before: pd.DataFrame, after: pd.DataFrame, entry: FileDedup) -> Optional[str]:
    """Qué salió mal al limpiar `entry`, o None si quedó exactamente como tenía que quedar."""
    dropped = {drop for drop, _ in entry.pairs}
    expected = before.loc[~before["time"].isin(dropped)].sort_values("time").reset_index(drop=True)
    after = after.sort_values("time").reset_index(drop=True)
    if len(after) != len(expected):
        return f"expected {len(expected)} candles, found {len(after)}"
    if find_relabeled_duplicates(after, entry.timeframe):
        return "relabeled duplicates are still there"
    if not (after["time"] == expected["time"]).all():
        return "the candle times changed"
    for col in _PRICE_COLUMNS:
        if not ((after[col] - expected[col]).abs() <= OVERLAP_PRICE_TOLERANCE * expected[col].abs()).all():
            return f"the {col} of some candle changed"
    return None


def dedup_bank(
    bank_root: str,
    apply: bool = False,
    backup_root: Optional[str] = None,
    stamp: Optional[str] = None,
) -> DedupReport:
    """
    Busca (y con `apply=True` quita) las copias de 1 h antes de las velas duplicadas de cada símbolo de `bank_root`.
    Con `apply=True` hace falta `backup_root`: la copia de los originales va a
    `{backup_root}/candle_bank_pre_dedup_{stamp}/{SÍMBOLO}/{TF}.csv`, y solo se crea si hay algo que cambiar.
    """
    report = DedupReport(applied=apply)
    if not apply:
        for symbol in _symbols(bank_root):
            report.files += [entry for entry, _ in _plan_symbol(bank_root, symbol)]
        return report

    if backup_root is None:
        raise ValueError("backup_root is required with apply=True")
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(backup_root, BACKUP_PREFIX + stamp)

    for symbol in _symbols(bank_root):
        bank_dir = os.path.join(bank_root, symbol)
        try:
            with acquire_bank_lock(bank_dir):
                for entry, before in _plan_symbol(bank_root, symbol):  # se lee con el candado tomado
                    report.files.append(entry)
                    path = bank_csv_path(bank_dir, entry.timeframe)
                    backup_path = os.path.join(backup_dir, symbol, os.path.basename(path))
                    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                    shutil.copy2(path, backup_path)
                    report.backup_dir = backup_dir

                    dropped = {drop for drop, _ in entry.pairs}
                    write_candle_csv_atomic(path, before.loc[~before["time"].isin(dropped)].reset_index(drop=True))
                    after = read_candle_csv(path)
                    problem = _verification_problem(before, after, entry)
                    if problem:
                        shutil.copy2(backup_path, path)
                        report.errors[(symbol, entry.timeframe)] = problem
                    else:
                        entry.rows_after = len(after)
        except CandleBankLockedError as exc:
            report.skipped[symbol] = f"export_in_progress: {exc}"
    return report


def _file_line(entry: FileDedup) -> str:
    n = len(entry.pairs)
    drop, keep = entry.pairs[0]
    return (f"{entry.symbol} {entry.timeframe}: {n} relabeled duplicate{'' if n == 1 else 's'} "
            f"(e.g. {drop:%Y-%m-%d %H:%M} is {keep:%Y-%m-%d %H:%M} labeled 1h early)")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time cleanup of the candle bank's relabeled duplicates (spec 002, N44). "
                    "Without --apply it only reports.")
    parser.add_argument("--apply", action="store_true",
                        help="Back up the affected files, remove the duplicates and verify.")
    parser.add_argument("--bank-dir", default=None, help="Candle bank root (default: CANDLE_BANK_DIR).")
    parser.add_argument("--backup-dir", default=None,
                        help="Where the backup folder is created (default: <ACCOUNTS_DATA_DIR>/archives).")
    args = parser.parse_args(argv)

    bank_root = args.bank_dir or cfg.CANDLE_BANK_DIR
    backup_root = args.backup_dir or os.path.join(cfg.ACCOUNTS_DATA_DIR, "archives")
    report = dedup_bank(bank_root, apply=args.apply, backup_root=backup_root)

    for entry in report.files:
        print(_file_line(entry))
    for symbol, reason in report.skipped.items():
        print(f"{symbol}: skipped, {reason}")
    for (symbol, tf), problem in report.errors.items():
        print(f"{symbol} {tf}: verification failed, original file restored ({problem})")

    if not report.files and not report.skipped:
        print("No relabeled duplicates found.")
    elif not args.apply:
        print(f"Total: {report.total_pairs} relabeled duplicates in {len(report.files)} file(s). "
              "Dry run: nothing was written; run with --apply to remove them.")
    elif report.cleaned:
        removed = sum(len(f.pairs) for f in report.cleaned)
        print(f"Removed {removed} relabeled duplicates from {len(report.cleaned)} file(s). "
              f"Backup of the original files: {report.backup_dir}")
        print("Verified: no relabeled duplicates left, and every other candle is unchanged.")
    return 1 if report.skipped or report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
