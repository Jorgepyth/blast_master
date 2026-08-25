#!/usr/bin/env python3
"""
backfill_tier_setup.py — Recalcula tactical_audit.tier_setup a partir de
gates_failed/confirmations_count ya cargados, para filas donde el backfill
de Motor B se hizo (o se editó) después de la creación del registro y
tier_setup quedó desfasado — tier_setup solo se deriva de Motor B en el
momento de creación (cli/main.py:3430-3438), nunca se recalcula solo.

Fórmula (traducida 1:1 de cli/main.py:3430-3438 — no existe un helper
compartido; ver nota de triplicación de fórmulas en CLAUDE.md):
    gates_failed >= 3        -> F
    gates_failed >= 1        -> D
    confirmations_count >= 5 -> A
    confirmations_count >= 4 -> B
    else                     -> C

Solo aplica a filas con gates_failed IS NOT NULL (Motor B evaluado). Filas
sin Motor B no se tocan. Es un recálculo mecánico y determinista (no hay
juicio subjetivo involucrado, a diferencia del backfill de Motor B en sí).

Uso:
    python tools/backfill_tier_setup.py --db ruta/a/copia.db              # dry-run
    python tools/backfill_tier_setup.py --db ruta/a/copia.db --confirm    # aplica

Por defecto el script RECHAZA correr contra cualquiera de las 3 DBs de cuenta
reales en producción. Para forzarlo hay que pasar
--i-understand-this-is-production explícitamente.

PROHIBIDO: DROP TABLE/COLUMN, drop_all, ni ninguna DDL destructiva. Este
script solo hace UPDATE sobre la columna tier_setup ya existente.
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

from tools.database import TacticalAudit, init_db

PRODUCTION_DB_PATHS = {
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_001_xauusd.db")),
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_002_btcusdtp.db")),
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_003_us100.db")),
}


def expected_tier(gates_failed: int, confirmations_count: int) -> str:
    if gates_failed >= 3:
        return "F"
    if gates_failed >= 1:
        return "D"
    if confirmations_count >= 5:
        return "A"
    if confirmations_count >= 4:
        return "B"
    return "C"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Recalcula tier_setup en tactical_audit desde gates_failed/confirmations_count"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Ruta a la base de datos SQLite objetivo. Sin default deliberadamente "
             "— nunca debe apuntar por omisión a una cuenta de producción.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Ejecutar commit real. Sin este flag el script corre en dry-run.",
    )
    parser.add_argument(
        "--i-understand-this-is-production",
        action="store_true",
        dest="allow_production",
        help="Requerido además de --confirm si --db resuelve a una de las 3 "
             "DBs de cuenta reales. No usar sin revisar primero los "
             "resultados sobre una copia.",
    )
    return parser.parse_args()


def guard_production_path(db_path: str, allow_production: bool):
    resolved = os.path.abspath(db_path)
    if resolved in PRODUCTION_DB_PATHS and not allow_production:
        print(
            "\n  BLOQUEADO: --db apunta a una base de datos de cuenta real "
            f"({resolved}).\n"
            "  Corre este script primero contra una copia. Si de verdad "
            "quieres apuntar a producción, pasa también "
            "--i-understand-this-is-production.\n"
        )
        sys.exit(1)


def setup_logging(timestamp: str) -> str:
    log_dir = os.path.join(ROOT_DIR, ".data", "archives")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"backfill_tier_setup_{timestamp}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def export_csv_backup(rows: list, timestamp: str, db_label: str) -> str:
    backup_dir = os.path.join(ROOT_DIR, ".data", "archives")
    os.makedirs(backup_dir, exist_ok=True)
    csv_path = os.path.join(backup_dir, f"backup_pre_tier_setup_backfill_{db_label}_{timestamp}.csv")
    fieldnames = ["id", "gates_failed", "confirmations_count", "old_tier_setup"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def format_table(rows: list) -> str:
    header = (
        f"{'ID':>36} | {'gates_failed':>12} | {'confirmations':>13} | "
        f"{'OLD':>5} | {'NEW':>5} | {'changed':>7}"
    )
    sep = "-" * len(header)
    lines = [sep, header, sep]
    for r in rows:
        lines.append(
            f"{r['id']:>36} | {str(r['gates_failed']):>12} | "
            f"{str(r['confirmations_count']):>13} | "
            f"{str(r['old_tier_setup']):>5} | {str(r['new_tier_setup']):>5} | "
            f"{str(r['changed']):>7}"
        )
    lines.append(sep)
    return "\n".join(lines)


def main():
    args = parse_args()
    guard_production_path(args.db, args.allow_production)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(timestamp)
    db_label = os.path.splitext(os.path.basename(args.db))[0]

    db_url = f"sqlite:///{os.path.abspath(args.db)}"
    logging.info(f"Base de datos: {db_url}")
    logging.info(f"Modo: {'COMMIT REAL' if args.confirm else 'DRY-RUN (sin cambios)'}")

    engine = init_db(db_url)

    with Session(engine) as session:
        try:
            records = session.scalars(
                select(TacticalAudit).where(TacticalAudit.gates_failed.is_not(None))
            ).all()
            logging.info(f"Filas con Motor B (gates_failed IS NOT NULL): {len(records)}")

            snapshot = [
                {
                    "id": r.id,
                    "gates_failed": r.gates_failed,
                    "confirmations_count": r.confirmations_count,
                    "old_tier_setup": r.tier_setup,
                }
                for r in records
            ]
            csv_path = export_csv_backup(snapshot, timestamp, db_label)
            logging.info(f"Respaldo CSV exportado: {csv_path}")

            diff_rows = []
            changed_ids = []
            errors = 0

            for record in records:
                old_tier = record.tier_setup
                new_tier = expected_tier(record.gates_failed, record.confirmations_count)
                changed = str(old_tier) != new_tier

                diff_rows.append({
                    "id": record.id,
                    "gates_failed": record.gates_failed,
                    "confirmations_count": record.confirmations_count,
                    "old_tier_setup": old_tier,
                    "new_tier_setup": new_tier,
                    "changed": changed,
                })

                if changed:
                    changed_ids.append(record.id)
                    record.tier_setup = new_tier

                logging.info(
                    f"[{'WRITE' if args.confirm else 'DRY'}] id={record.id[:8]}... "
                    f"gates_failed={record.gates_failed} confirmations_count={record.confirmations_count} | "
                    f"tier_setup: {old_tier} -> {new_tier} "
                    f"{'(CHANGED)' if changed else ''}"
                )

            print("\n" + format_table(diff_rows))
            print(f"\n  === Resumen ({db_label}) ===")
            print(f"  Filas con Motor B evaluadas                : {len(records)}")
            print(f"  Filas recalculadas (tier_setup cambia)     : {len(changed_ids)}")
            print(f"  Filas ya correctas, sin cambio              : {len(records) - len(changed_ids)}")
            print(f"  Errores                                     : {errors}")
            if changed_ids:
                print(f"\n  IDs recalculados: {', '.join(i[:8] + '...' for i in changed_ids)}")

            if args.confirm:
                session.commit()
                logging.info(f"COMMIT ejecutado — {len(changed_ids)} filas actualizadas.")
                print(f"\n  COMMIT OK — {len(changed_ids)} registros actualizados en {args.db}.")
            else:
                session.rollback()
                logging.info("DRY-RUN completado — ROLLBACK ejecutado. Sin cambios en BD.")
                print("\n  DRY-RUN — ROLLBACK ejecutado. Ningún dato fue modificado.")
                print("  Ejecuta con --confirm para aplicar los cambios.")

        except Exception as exc:
            session.rollback()
            logging.error(f"Error durante el backfill: {exc}", exc_info=True)
            print(f"\n  ERROR: {exc}")
            sys.exit(1)

    logging.info(f"Log persistente: {log_path}")


if __name__ == "__main__":
    main()
