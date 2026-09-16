#!/usr/bin/env python3
"""
audit_stop_deviation.py — Reporte de solo lectura sobre Stop Deviation Journaling
(tactical_audit.stop_slippage_r / stop_deviation_reason / stop_deviation_note).

Nunca escribe en la DB (sin session.add/commit/execute(text("UPDATE...")) en
ningún punto de este script) -- por eso, a diferencia de los scripts de backfill/
migración de tools/, no tiene --confirm ni guard de producción: no hay nada
destructivo o mutante que proteger.

Separa siempre 3 grupos, nunca fusionados (regla R5 de la feature):
  1. stop_slippage_r > 0   -- stop más angosto que structural_invalidation,
                              desglosado por stop_deviation_reason.
  2. stop_slippage_r <= 0  -- cumplió (igual o más ancho que estructural).
  3. stop_slippage_r IS NULL -- sin dato base en Fase 1 (structural_invalidation
                              nulo) -- problema de completitud, no de disciplina
                              de ejecución.

Uso:
    python tools/audit_stop_deviation.py --db .data/flight_account_001_xauusd.db
"""
import argparse
import os
import sys
from collections import Counter

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sqlalchemy import select
from sqlalchemy.orm import Session

from tools.database import init_db, TacticalAudit


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="Ruta a la DB de cuenta a auditar (ej. .data/flight_account_001_xauusd.db)")
    args = parser.parse_args()

    engine = init_db(f"sqlite:///{args.db}")
    with Session(engine) as session:
        all_rows = session.scalars(select(TacticalAudit)).all()

    with_slippage = [r for r in all_rows if r.stop_slippage_r is not None]
    without_structural_data = [r for r in all_rows if r.stop_slippage_r is None]
    positive = [r for r in with_slippage if r.stop_slippage_r > 0]
    compliant = [r for r in with_slippage if r.stop_slippage_r <= 0]

    print(f"Total tactical_audit rows: {len(all_rows)}")
    print()
    print("--- Grupo 1: stop_slippage_r > 0 (stop más angosto que structural_invalidation) ---")
    print(f"  n = {len(positive)}")
    if positive:
        counts = Counter(r.stop_deviation_reason or "N/A (sin razón registrada)" for r in positive)
        print("  Desglose por stop_deviation_reason:")
        for reason, n in counts.most_common():
            print(f"    {reason}: {n}")
        print("  Detalle:")
        for r in positive:
            note_preview = (r.stop_deviation_note or "")[:60]
            asset = r.unified_department.asset if r.unified_department else "N/A"
            print(f"    trade_id={r.trade_id} asset={asset} "
                  f"stop_slippage_r={float(r.stop_slippage_r):.4f} "
                  f"reason={r.stop_deviation_reason or 'N/A'} note={note_preview!r}")
    print()
    print("--- Grupo 2: stop_slippage_r <= 0 (cumplió, igual o más ancho que estructural) ---")
    print(f"  n = {len(compliant)}")
    print()
    print("--- Grupo 3: stop_slippage_r IS NULL (sin structural_invalidation en Fase 1 -- problema de completitud) ---")
    print(f"  n = {len(without_structural_data)}")


if __name__ == "__main__":
    main()
