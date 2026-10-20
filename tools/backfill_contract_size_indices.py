#!/usr/bin/env python
"""Backfill del 2026-10-20: lotes hipotéticos mal cargados y montos en dólares de US500, US100 y BTC.

Contexto: hasta el 2026-10-20, BTCUSD, US500 y USTEC no tenían contract size verificado, así que sus campos monetarios
quedaban vacíos o con valores viejos sin multiplicar bien (ej. US100 1250b9ed: risk_usd 344.70 con 0.01 lotes; debería
ser 3.45). El 2026-10-20 el usuario verificó en MT5 (IC Markets) un contract size de 1 para los tres
(`config/contract_specs.py`, commit 085e794) y autorizó:

1. **Corregir 3 lotes hipotéticos mal cargados** (el mínimo en US500/USTEC es 0.1 lotes):
   - US500 b7aa8926 (fila ad8c14e1): 0.01 -> 0.1
   - US500 0c297c20 (fila 0758f56f): 0.01 -> 0.1
   - US100 e604ff68 (fila 227f82c4): 0.03 -> 0.3 (el usuario confirmó que fue hipotético)
2. **Recalcular los montos** de toda fila de esas tres cuentas con lotes > 0 y entrada/SL válidos, con la misma fórmula
   del wizard (`core.math_engine.calculate_algebraic_metrics`) y contract size 1:
   `notional_size`, `capital_at_risk`, `risk_usd` y `pnl_and_cost` (este solo si hay `closing_price`; el costo no se
   guarda en la DB, así que es 0, como en el validador cuando no se cargó).

Nota: las filas anteriores al cambio a IC Markets (~septiembre de 2026) se recalculan con el contract size de IC Markets.
En BTC el reporte del broker anterior también daba 1 (1.000213); en US100 no había dato confiable.

Uso, desde la raíz del checkout principal:
    python tools/backfill_contract_size_indices.py             # ensayo: muestra cada cambio, no escribe
    python tools/backfill_contract_size_indices.py --confirm   # respaldo VACUUM INTO + escribe + verifica
"""
import argparse
import datetime as _dt
import os
import sqlite3
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.math_engine import calculate_algebraic_metrics  # noqa: E402
from tools.pnl_calculator import resolve_spec  # noqa: E402

DBS = ("flight_account_000_us500.db", "flight_account_003_us100.db", "flight_account_002_btcusdtp.db")
SIZE_FIXES = {  # db -> {tactical_audit.id: (lote viejo, lote nuevo)}
    "flight_account_000_us500.db": {"ad8c14e1-0465-4a51-9132-7e411a62a8f8": (0.01, 0.1),
                                    "0758f56f-9b96-46df-a535-427b810e30d1": (0.01, 0.1)},
    "flight_account_003_us100.db": {"227f82c4-67a7-414b-8b4f-3280728ec6ab": (0.03, 0.3)},
}
MONEY = ("notional_size", "capital_at_risk", "risk_usd", "pnl_and_cost")
WATCHED = ("size", "size_source", "size_migrated_at") + MONEY


def _rows(con):
    con.row_factory = sqlite3.Row
    return con.execute(
        "SELECT t.id, t.trade_id, t.size, t.size_source, t.size_migrated_at, t.entry_price, t.stop_loss, "
        "t.closing_price, t.take_profit, t.notional_size, t.capital_at_risk, t.risk_usd, t.pnl_and_cost, u.asset "
        "FROM tactical_audit t JOIN unified_department u ON u.id = t.trade_id").fetchall()


def plan(db_name, rows, now):
    """Los cambios de una DB: {id: {columna: (viejo, nuevo)}}. No escribe."""
    changes = {}
    for r in rows:
        upd = {}
        size = r["size"]
        fix = SIZE_FIXES.get(db_name, {}).get(r["id"])
        if fix is not None:
            assert abs((size or 0) - fix[0]) < 1e-12, (r["id"], size, fix)
            size = fix[1]
            upd["size"] = (r["size"], size)
            upd["size_source"] = (r["size_source"], "manual_correction")
            upd["size_migrated_at"] = (r["size_migrated_at"], now)
        ep, sl = r["entry_price"], r["stop_loss"]
        if not size or size <= 0 or not ep or not sl or ep == sl:
            if upd:
                changes[r["id"]] = upd
            continue
        cs = resolve_spec(r["asset"])["contract_size"]
        direction = "Long" if ep > sl else "Short"
        cp = r["closing_price"]
        m = calculate_algebraic_metrics(direction, ep, sl, cp if cp else ep, r["take_profit"] or ep, size, 0.0, 0.0,
                                        0.0, contract_size=cs)
        new = {"notional_size": float(m["notional_size"]), "capital_at_risk": float(m["capital_at_risk"]),
               "risk_usd": float(m["risk_usd"]), "pnl_and_cost": float(m["pnl_and_cost"]) if cp else None}
        for col, value in new.items():
            old = r[col]
            same = (old is None and value is None) or (old is not None and value is not None and abs(old - value) < 1e-9)
            if not same:
                upd[col] = (old, value)
        if upd:
            changes[r["id"]] = upd
    return changes


def _snapshot(con):
    con.row_factory = sqlite3.Row
    return {r["id"]: dict(r) for r in con.execute("SELECT * FROM tactical_audit")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--confirm", action="store_true", help="respaldar y escribir (sin esto, solo ensayo)")
    ap.add_argument("--data-dir", default=os.path.join(ROOT_DIR, ".data"))
    args = ap.parse_args(argv)
    now = _dt.datetime.now().isoformat(sep=" ", timespec="seconds")
    total = 0
    for db_name in DBS:
        path = os.path.join(args.data_dir, db_name)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        changes = plan(db_name, _rows(con), now)
        con.close()
        print(f"== {db_name}: {len(changes)} filas cambian")
        for rid, upd in changes.items():
            cols = ", ".join(f"{c}: {o!r} -> {n!r}" for c, (o, n) in upd.items() if c != "size_migrated_at")
            print(f"   {rid[:8]}  {cols}")
        total += len(changes)
        if not args.confirm or not changes:
            continue
        backup = os.path.join(args.data_dir, "archives",
                              f"{db_name}.pre_contract_size_{_dt.datetime.now():%Y%m%d_%H%M%S}")
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        con = sqlite3.connect(path)
        con.execute("VACUUM INTO ?", (backup,))
        before = _snapshot(con)
        with con:
            for rid, upd in changes.items():
                cols = list(upd)
                con.execute(f"UPDATE tactical_audit SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                            [upd[c][1] for c in cols] + [rid])
        after = _snapshot(con)
        con.close()
        # Verificación: solo cambiaron las filas y columnas planeadas, con los valores planeados
        for rid in before:
            for col, old in before[rid].items():
                expected = changes.get(rid, {}).get(col, (old, old))[1]
                got = after[rid][col]
                assert got == expected or (isinstance(got, float) and isinstance(expected, float)
                                           and abs(got - expected) < 1e-9), (db_name, rid, col, old, got, expected)
        print(f"   escrito y verificado; respaldo: {backup}")
    print(f"Total: {total} filas" + ("" if args.confirm else " (ensayo: no se escribió nada)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
