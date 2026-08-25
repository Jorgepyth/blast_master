#!/usr/bin/env python3
"""
backfill_r_multiple.py — Rellena retroactivamente r_multiple y captured_mfe
en tactical_audit usando calculate_algebraic_metrics() de core.math_engine
como fuente única de verdad (misma fórmula que usa el flujo de reparación
en cli/main.py).

No toca r_r, pnl_and_cost, notional_size ni ningún otro campo — solo
r_multiple y captured_mfe, que hoy se calculan en el wizard pero nunca se
persisten (ver hallazgo del cruce de análisis blast_master / notion_api_analysis).

Uso:
    python tools/backfill_r_multiple.py --db ruta/a/copia.db              # dry-run
    python tools/backfill_r_multiple.py --db ruta/a/copia.db --confirm    # aplica

Por defecto el script RECHAZA correr contra .data/flight_account_001_xauusd.db
(la cuenta real en producción). Para forzarlo de todos modos hay que pasar
--i-understand-this-is-production explícitamente.

PROHIBIDO: DROP TABLE, drop_all, ni ninguna DDL destructiva. La única DDL que
toca este flujo es la migración ADD COLUMN idempotente ya integrada en
tools.database.init_db().
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
from core.math_engine import calculate_algebraic_metrics

PRODUCTION_DB_PATH = os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_001_xauusd.db"))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backfill de r_multiple / captured_mfe en tactical_audit"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Ruta a la base de datos SQLite objetivo. Sin default deliberadamente "
             "— nunca debe apuntar por omisión a la cuenta de producción.",
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
        help="Requerido además de --confirm si --db resuelve a "
             ".data/flight_account_001_xauusd.db. No usar sin revisar primero "
             "los resultados sobre una copia.",
    )
    return parser.parse_args()


def guard_production_path(db_path: str, allow_production: bool):
    resolved = os.path.abspath(db_path)
    if resolved == PRODUCTION_DB_PATH and not allow_production:
        print(
            "\n  BLOQUEADO: --db apunta a la base de datos de producción "
            f"({PRODUCTION_DB_PATH}).\n"
            "  Corre este script primero contra una copia. Si de verdad "
            "quieres apuntar a producción, pasa también "
            "--i-understand-this-is-production.\n"
        )
        sys.exit(1)


def setup_logging(timestamp: str) -> str:
    log_dir = os.path.join(ROOT_DIR, ".data", "archives")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"backfill_r_multiple_{timestamp}.log")
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
    csv_path = os.path.join(backup_dir, f"backup_pre_r_multiple_backfill_{timestamp}.csv")
    fieldnames = [
        "id", "trade_decision", "entry_price", "closing_price", "stop_loss",
        "mfe_favorable", "r_multiple_old", "captured_mfe_old",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def format_table(rows: list) -> str:
    header = (
        f"{'ID':>36} | {'dir':>5} | "
        f"{'r_mult OLD':>10} | {'r_mult NEW':>10} | "
        f"{'cap_mfe OLD':>11} | {'cap_mfe NEW':>11}"
    )
    sep = "-" * len(header)
    lines = [sep, header, sep]
    for r in rows:
        old_rm = "NULL" if r["r_multiple_old"] is None else f"{r['r_multiple_old']:.4f}"
        old_cm = "NULL" if r["captured_mfe_old"] is None else f"{r['captured_mfe_old']:.4f}"
        lines.append(
            f"{r['id']:>36} | {r['trade_decision']:>5} | "
            f"{old_rm:>10} | {r['r_multiple_new']:>10.4f} | "
            f"{old_cm:>11} | {r['captured_mfe_new']:>11.4f}"
        )
    lines.append(sep)
    return "\n".join(lines)


def main():
    args = parse_args()
    guard_production_path(args.db, args.allow_production)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(timestamp)

    db_url = f"sqlite:///{os.path.abspath(args.db)}"
    logging.info(f"Base de datos: {db_url}")
    logging.info(f"Modo: {'COMMIT REAL' if args.confirm else 'DRY-RUN (sin cambios)'}")

    # init_db() aplica la migración ADD COLUMN idempotente si aún no existen
    # r_multiple / captured_mfe en esta DB.
    engine = init_db(db_url)

    with Session(engine) as session:
        try:
            all_records = session.scalars(select(TacticalAudit)).all()
            logging.info(f"Filas totales en tactical_audit: {len(all_records)}")

            candidates = [
                r for r in all_records
                if r.entry_price is not None
                and r.closing_price is not None
                and r.stop_loss is not None
                and r.size is not None
                and r.trade_decision in ("Long", "Short")
                and r.order_filled
            ]
            skipped_missing = [r for r in all_records if r not in candidates]
            logging.info(
                f"Candidatas con entry/closing/stop/size/direction completos: "
                f"{len(candidates)} (saltadas por datos incompletos: {len(skipped_missing)})"
            )

            # Snapshot CSV antes de tocar nada
            snapshot = [
                {
                    "id": r.id,
                    "trade_decision": r.trade_decision,
                    "entry_price": float(r.entry_price) if r.entry_price is not None else None,
                    "closing_price": float(r.closing_price) if r.closing_price is not None else None,
                    "stop_loss": float(r.stop_loss) if r.stop_loss is not None else None,
                    "mfe_favorable": float(r.mfe_favorable) if r.mfe_favorable is not None else None,
                    "r_multiple_old": float(r.r_multiple) if r.r_multiple is not None else None,
                    "captured_mfe_old": float(r.captured_mfe) if r.captured_mfe is not None else None,
                }
                for r in candidates
            ]
            csv_path = export_csv_backup(snapshot, timestamp)
            logging.info(f"Respaldo CSV exportado: {csv_path}")

            diff_rows = []
            mutations = 0
            errors = 0

            for record in candidates:
                old_rm = float(record.r_multiple) if record.r_multiple is not None else None
                old_cm = float(record.captured_mfe) if record.captured_mfe is not None else None

                try:
                    # Fuente única de verdad: core.math_engine.calculate_algebraic_metrics
                    result = calculate_algebraic_metrics(
                        direction=record.trade_decision,
                        ep=float(record.entry_price),
                        sl=float(record.stop_loss),
                        cp=float(record.closing_price),
                        tp=float(record.take_profit) if record.take_profit is not None else float(record.entry_price),
                        size=float(record.size),
                        mae=float(record.mae_adverse) if record.mae_adverse is not None else 0.0,
                        mfe=float(record.mfe_favorable) if record.mfe_favorable is not None else 0.0,
                        cost=0.0,
                    )
                except (ValueError, ArithmeticError) as exc:
                    errors += 1
                    logging.warning(f"[SKIP-ERROR] id={record.id} -> {exc}")
                    continue

                new_rm = float(result["r_multiple"])
                new_cm = float(result["captured_mfe"])

                diff_rows.append({
                    "id": record.id,
                    "trade_decision": record.trade_decision,
                    "r_multiple_old": old_rm,
                    "r_multiple_new": new_rm,
                    "captured_mfe_old": old_cm,
                    "captured_mfe_new": new_cm,
                })

                # Solo mutamos r_multiple y captured_mfe — nada más.
                record.r_multiple = new_rm
                record.captured_mfe = new_cm
                mutations += 1

                logging.info(
                    f"[{'WRITE' if args.confirm else 'DRY'}] id={record.id[:8]}... "
                    f"dir={record.trade_decision} | "
                    f"r_multiple: {old_rm} -> {new_rm:.4f} | "
                    f"captured_mfe: {old_cm} -> {new_cm:.4f}"
                )

            print("\n" + format_table(diff_rows))
            print(f"\n  Filas totales en tactical_audit : {len(all_records)}")
            print(f"  Candidatas (datos completos)     : {len(candidates)}")
            print(f"  Saltadas por datos incompletos    : {len(skipped_missing)}")
            print(f"  Saltadas por error de cálculo      : {errors}")
            print(f"  Filas a mutar                      : {mutations}")

            if args.confirm:
                session.commit()
                logging.info(f"COMMIT ejecutado — {mutations} filas actualizadas.")
                print(f"\n  COMMIT OK — {mutations} registros actualizados en {args.db}.")
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
