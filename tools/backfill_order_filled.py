#!/usr/bin/env python3
"""
backfill_order_filled.py — Deriva retroactivamente tactical_audit.order_filled
a partir de unified_department.trade_status (fuente de verdad "¿se tomó el
trade o no?") y entry_time (señal secundaria de respaldo), como paso previo
a retirar por completo compliance/trade_status.

Regla "sticky-false" (deliberada, no la regla ingenua): solo permite
True -> False, nunca False -> True. Se descubrió en producción que 12 filas
de la cuenta 001 ya tienen order_filled=False por correcciones manuales
previas aunque trade_status diga "trade tomado" — la regla ingenua
(order_filled = trade_status != 'Trade_no_taken') revertiría esas
correcciones. Este script nunca lo hace: una vez False, siempre False.

    new_order_filled = old_order_filled
                        AND (trade_status IS DISTINCT FROM 'Trade_no_taken')
                        AND (entry_time IS NOT NULL)

Uso:
    python tools/backfill_order_filled.py --db ruta/a/copia.db              # dry-run
    python tools/backfill_order_filled.py --db ruta/a/copia.db --confirm    # aplica

Por defecto el script RECHAZA correr contra cualquiera de las 3 DBs de cuenta
reales en producción. Para forzarlo hay que pasar
--i-understand-this-is-production explícitamente.

PROHIBIDO: DROP TABLE/COLUMN, drop_all, ni ninguna DDL destructiva. Este
script solo hace UPDATE sobre la columna order_filled ya existente.
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

KNOWN_FLIP_ID = "a4d31d37-7fcb-4296-ab57-53787c960e19"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backfill de order_filled en tactical_audit desde trade_status/entry_time"
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
    log_path = os.path.join(log_dir, f"backfill_order_filled_{timestamp}.log")
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
    csv_path = os.path.join(backup_dir, f"backup_pre_order_filled_backfill_{db_label}_{timestamp}.csv")
    fieldnames = ["id", "trade_status", "entry_time_is_null", "old_order_filled"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def format_table(rows: list) -> str:
    header = (
        f"{'ID':>36} | {'trade_status':>27} | {'entry_null':>10} | "
        f"{'OLD':>5} | {'NEW':>5} | {'changed':>7}"
    )
    sep = "-" * len(header)
    lines = [sep, header, sep]
    for r in rows:
        lines.append(
            f"{r['id']:>36} | {str(r['trade_status']):>27} | "
            f"{str(r['entry_time_is_null']):>10} | "
            f"{str(r['old_order_filled']):>5} | {str(r['new_order_filled']):>5} | "
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
            all_records = session.scalars(select(TacticalAudit)).all()
            logging.info(f"Filas totales en tactical_audit: {len(all_records)}")

            # Snapshot CSV antes de tocar nada
            snapshot = [
                {
                    "id": r.id,
                    "trade_status": r.unified_department.trade_status if r.unified_department else None,
                    "entry_time_is_null": r.entry_time is None,
                    "old_order_filled": bool(r.order_filled),
                }
                for r in all_records
            ]
            csv_path = export_csv_backup(snapshot, timestamp, db_label)
            logging.info(f"Respaldo CSV exportado: {csv_path}")

            diff_rows = []
            flipped = []
            preserved_false = []
            unchanged_true = []
            errors = 0

            for record in all_records:
                old_filled = bool(record.order_filled)
                trade_status = record.unified_department.trade_status if record.unified_department else None
                entry_is_null = record.entry_time is None

                # Regla sticky-false: nunca revierte una fila ya en False.
                new_filled = old_filled and (trade_status != "Trade_no_taken") and not entry_is_null
                changed = old_filled != new_filled

                diff_rows.append({
                    "id": record.id,
                    "trade_status": trade_status,
                    "entry_time_is_null": entry_is_null,
                    "old_order_filled": old_filled,
                    "new_order_filled": new_filled,
                    "changed": changed,
                })

                if changed:
                    flipped.append(record.id)
                elif not old_filled:
                    preserved_false.append(record.id)
                else:
                    unchanged_true.append(record.id)

                record.order_filled = new_filled

                logging.info(
                    f"[{'WRITE' if args.confirm else 'DRY'}] id={record.id[:8]}... "
                    f"trade_status={trade_status} entry_null={entry_is_null} | "
                    f"order_filled: {old_filled} -> {new_filled} "
                    f"{'(CHANGED)' if changed else ''}"
                )

            print("\n" + format_table(diff_rows))
            print(f"\n  === Resumen ({db_label}) ===")
            print(f"  Filas totales en tactical_audit           : {len(all_records)}")
            print(f"  Flips True->False (el fix esperado)       : {len(flipped)}")
            print(f"  Ya en False, preservadas sin cambio        : {len(preserved_false)}")
            print(f"  En True, sin cambio                        : {len(unchanged_true)}")
            print(f"  Errores                                    : {errors}")
            if flipped:
                print(f"\n  IDs flipeados: {', '.join(i[:8] + '...' for i in flipped)}")
            if KNOWN_FLIP_ID in [r["id"] for r in diff_rows]:
                known_flipped = KNOWN_FLIP_ID in flipped
                print(
                    f"\n  Verificación fila conocida {KNOWN_FLIP_ID}: "
                    f"{'FLIPEADA correctamente' if known_flipped else 'NO FLIPEADA -- REVISAR'}"
                )

            if args.confirm:
                session.commit()
                logging.info(f"COMMIT ejecutado — {len(flipped)} filas actualizadas.")
                print(f"\n  COMMIT OK — {len(flipped)} registros actualizados en {args.db}.")
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
