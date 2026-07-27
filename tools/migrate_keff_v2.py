#!/usr/bin/env python3
"""
migrate_keff_v2.py — Migración retroactiva del modelo K_eff v2 (Lineal Conservador)
Recalcula long_prob, short_prob, no_trade_prob para todos los registros
de UnifiedDepartment usando calculate_probabilities() de core.math_engine.

Uso:
    python tools/migrate_keff_v2.py            # dry-run: muestra tabla, NO hace commit
    python tools/migrate_keff_v2.py --confirm  # aplica los cambios en la base de datos

PROHIBIDO: DROP TABLE, drop_all, ALTER TABLE, ni ninguna DDL.
"""
import argparse
import csv
import logging
import os
import sys
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))

from sqlalchemy.orm import Session
from sqlalchemy import select

# Importaciones con alias explícitos para evitar colisión de nombres entre
# TacticalAudit Pydantic (cli/schemas/audit_tactical.py) y ORM (tools/database.py)
from tools.database import UnifiedDepartment as UnifiedDepartmentORM, init_db
from core.math_engine import calculate_probabilities


def parse_args():
    parser = argparse.ArgumentParser(description="Migración K_eff v2 — Modelo Lineal Conservador")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Ejecutar commit real. Sin este flag el script corre en dry-run.",
    )
    parser.add_argument(
        "--db",
        default=".data/flight_account_001_xauusd.db",
        help="Ruta relativa a la base de datos SQLite.",
    )
    return parser.parse_args()


def setup_logging(timestamp: str) -> str:
    log_dir = os.path.join(ROOT_DIR, ".data", "archives")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"migracion_keff_v2_{timestamp}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def export_csv_backup(rows: list, timestamp: str) -> str:
    backup_dir = os.path.join(ROOT_DIR, ".data", "archives")
    os.makedirs(backup_dir, exist_ok=True)
    csv_path = os.path.join(backup_dir, f"backup_pre_keff_v2_{timestamp}.csv")
    fieldnames = ["id", "calc_edge", "long_prob", "short_prob", "no_trade_prob"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def format_table(rows: list) -> str:
    header = (
        f"{'ID':>36} | {'calc_edge':>10} | "
        f"{'lp OLD':>10} | {'lp NEW':>10} | "
        f"{'sp OLD':>10} | {'sp NEW':>10} | "
        f"{'nt OLD':>10} | {'nt NEW':>10}"
    )
    sep = "-" * len(header)
    lines = [sep, header, sep]
    for r in rows:
        lines.append(
            f"{r['id']:>36} | {r['calc_edge']:>10.6f} | "
            f"{r['long_prob_old']:>10.6f} | {r['long_prob_new']:>10.6f} | "
            f"{r['short_prob_old']:>10.6f} | {r['short_prob_new']:>10.6f} | "
            f"{r['no_trade_old']:>10.6f} | {r['no_trade_new']:>10.6f}"
        )
    lines.append(sep)
    return "\n".join(lines)


def main():
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(timestamp)

    db_url = f"sqlite:///{os.path.join(ROOT_DIR, args.db)}"
    logging.info(f"Base de datos: {db_url}")
    logging.info(f"Modo: {'COMMIT REAL' if args.confirm else 'DRY-RUN (sin cambios)'}")

    engine = init_db(db_url)

    with Session(engine) as session:
        try:
            records = session.scalars(select(UnifiedDepartmentORM)).all()
            logging.info(f"Registros encontrados: {len(records)}")

            # Snapshot CSV antes de tocar nada
            snapshot = [
                {
                    "id": r.id,
                    "calc_edge": float(r.calc_edge) if r.calc_edge is not None else 0.0,
                    "long_prob": float(r.long_prob) if r.long_prob is not None else 0.0,
                    "short_prob": float(r.short_prob) if r.short_prob is not None else 0.0,
                    "no_trade_prob": float(r.no_trade_prob) if r.no_trade_prob is not None else 0.0,
                }
                for r in records
            ]
            csv_path = export_csv_backup(snapshot, timestamp)
            logging.info(f"Respaldo CSV exportado: {csv_path}")

            diff_rows = []
            mutations = 0

            for record in records:
                calc_edge_val = record.calc_edge if record.calc_edge is not None else 0.0
                old_long  = float(record.long_prob)     if record.long_prob     is not None else 0.0
                old_short = float(record.short_prob)    if record.short_prob    is not None else 0.0
                old_nt    = float(record.no_trade_prob) if record.no_trade_prob is not None else 0.0

                # Fuente única de verdad: core.math_engine.calculate_probabilities
                probs = calculate_probabilities(calc_edge_val)
                new_long  = float(probs.long_prob)
                new_short = float(probs.short_prob)
                new_nt    = float(probs.no_trade_prob)

                diff_rows.append({
                    "id": record.id,
                    "calc_edge": float(calc_edge_val),
                    "long_prob_old": old_long,
                    "long_prob_new": new_long,
                    "short_prob_old": old_short,
                    "short_prob_new": new_short,
                    "no_trade_old": old_nt,
                    "no_trade_new": new_nt,
                })

                # Asignación directa ORM — campo 'id' en ORM (NO 'tactical_id')
                record.long_prob     = new_long
                record.short_prob    = new_short
                record.no_trade_prob = new_nt
                mutations += 1

                logging.info(
                    f"[{'WRITE' if args.confirm else 'DRY'}] id={record.id[:8]}... "
                    f"edge={float(calc_edge_val):.6f} | "
                    f"nt: {old_nt:.6f} -> {new_nt:.6f} | "
                    f"lp: {old_long:.6f} -> {new_long:.6f} | "
                    f"sp: {old_short:.6f} -> {new_short:.6f}"
                )

            print("\n" + format_table(diff_rows))
            print(f"\n  Total filas analizadas : {len(records)}")
            print(f"  Total filas a mutar    : {mutations}")

            if args.confirm:
                session.commit()
                logging.info(f"COMMIT ejecutado — {mutations} filas actualizadas.")
                print(f"\n  COMMIT OK — {mutations} registros actualizados en la BD.")
            else:
                session.rollback()
                logging.info("DRY-RUN completado — ROLLBACK ejecutado. Sin cambios en BD.")
                print("\n  DRY-RUN — ROLLBACK ejecutado. Ningún dato fue modificado.")
                print("  Ejecuta con --confirm para aplicar los cambios.")

        except Exception as exc:
            session.rollback()
            logging.error(f"Error durante la migración: {exc}", exc_info=True)
            print(f"\n  ERROR: {exc}")
            sys.exit(1)

    logging.info(f"Log persistente: {log_path}")


if __name__ == "__main__":
    main()
