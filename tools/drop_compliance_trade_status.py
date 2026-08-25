#!/usr/bin/env python3
"""
drop_compliance_trade_status.py — Elimina físicamente las columnas
`tactical_audit.compliance` y `unified_department.trade_status`, ya
retiradas del código (reemplazadas por `tactical_audit.order_filled`,
única señal de ejecución). Último paso del retiro (Fase H) — solo debe
correr después de que el backfill de `order_filled` (`backfill_order_filled.py`)
ya se ejecutó y fue aprobado, y de que un backup fresco de la DB ya existe.

Uso:
    python tools/drop_compliance_trade_status.py --db ruta/a/copia.db              # dry-run
    python tools/drop_compliance_trade_status.py --db ruta/a/copia.db --confirm    # aplica

Por defecto el script RECHAZA correr contra cualquiera de las 3 DBs de cuenta
reales en producción. Para forzarlo hay que pasar
--i-understand-this-is-production explícitamente.

Requiere SQLite 3.35+ (soporte nativo de `ALTER TABLE ... DROP COLUMN`,
sin reconstrucción manual de tabla).
"""
import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

PRODUCTION_DB_PATHS = {
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_001_xauusd.db")),
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_002_btcusdtp.db")),
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_003_us100.db")),
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="DROP COLUMN compliance (tactical_audit) / trade_status (unified_department)"
    )
    parser.add_argument("--db", required=True, help="Ruta a la base de datos SQLite objetivo.")
    parser.add_argument("--confirm", action="store_true", help="Ejecutar el DROP real. Sin este flag, solo verifica y reporta.")
    parser.add_argument(
        "--i-understand-this-is-production", action="store_true", dest="allow_production",
        help="Requerido además de --confirm si --db resuelve a una de las 3 DBs de cuenta reales.",
    )
    return parser.parse_args()


def guard_production_path(db_path: str, allow_production: bool):
    resolved = os.path.abspath(db_path)
    if resolved in PRODUCTION_DB_PATHS and not allow_production:
        print(
            f"\n  BLOQUEADO: --db apunta a una base de datos de cuenta real ({resolved}).\n"
            "  Corre este script primero contra una copia. Si de verdad quieres apuntar "
            "a producción, pasa también --i-understand-this-is-production.\n"
        )
        sys.exit(1)


def setup_logging(timestamp: str) -> str:
    log_dir = os.path.join(ROOT_DIR, ".data", "archives")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"drop_compliance_trade_status_{timestamp}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    return log_path


def main():
    args = parse_args()
    guard_production_path(args.db, args.allow_production)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(timestamp)

    db_path = os.path.abspath(args.db)
    logging.info(f"Base de datos: {db_path}")
    logging.info(f"Modo: {'DROP REAL' if args.confirm else 'DRY-RUN (solo verificación)'}")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        cur.execute("SELECT sqlite_version()")
        version = cur.fetchone()[0]
        logging.info(f"SQLite version: {version}")

        cur.execute("SELECT COUNT(*) FROM tactical_audit")
        ta_count_before = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM unified_department")
        ud_count_before = cur.fetchone()[0]
        logging.info(f"Filas antes: tactical_audit={ta_count_before}, unified_department={ud_count_before}")

        ta_cols = [row[1] for row in cur.execute("PRAGMA table_info(tactical_audit)")]
        ud_cols = [row[1] for row in cur.execute("PRAGMA table_info(unified_department)")]
        has_compliance = "compliance" in ta_cols
        has_trade_status = "trade_status" in ud_cols
        logging.info(f"tactical_audit.compliance presente: {has_compliance}")
        logging.info(f"unified_department.trade_status presente: {has_trade_status}")

        if not has_compliance and not has_trade_status:
            logging.info("Ambas columnas ya no existen — nada que hacer (idempotente).")
            print("\n  Ambas columnas ya fueron eliminadas previamente. Nada que hacer.")
            return

        if not args.confirm:
            print(f"\n  DRY-RUN — se eliminarían:")
            if has_compliance:
                print(f"    - tactical_audit.compliance")
            if has_trade_status:
                print(f"    - unified_department.trade_status")
            print(f"  Filas actuales: tactical_audit={ta_count_before}, unified_department={ud_count_before}")
            print("  Ejecuta con --confirm para aplicar el DROP.")
            return

        if has_compliance:
            cur.execute("ALTER TABLE tactical_audit DROP COLUMN compliance")
            logging.info("DROP COLUMN tactical_audit.compliance ejecutado.")
        if has_trade_status:
            cur.execute("ALTER TABLE unified_department DROP COLUMN trade_status")
            logging.info("DROP COLUMN unified_department.trade_status ejecutado.")

        conn.commit()

        # Verificación post-drop
        ta_cols_after = [row[1] for row in cur.execute("PRAGMA table_info(tactical_audit)")]
        ud_cols_after = [row[1] for row in cur.execute("PRAGMA table_info(unified_department)")]
        cur.execute("SELECT COUNT(*) FROM tactical_audit")
        ta_count_after = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM unified_department")
        ud_count_after = cur.fetchone()[0]

        assert "compliance" not in ta_cols_after, "compliance sigue presente tras el DROP"
        assert "trade_status" not in ud_cols_after, "trade_status sigue presente tras el DROP"
        assert ta_count_after == ta_count_before, f"Conteo de filas cambió en tactical_audit: {ta_count_before} -> {ta_count_after}"
        assert ud_count_after == ud_count_before, f"Conteo de filas cambió en unified_department: {ud_count_before} -> {ud_count_after}"

        logging.info(f"Verificación post-drop OK. Filas: tactical_audit={ta_count_after}, unified_department={ud_count_after}")
        print(f"\n  COMMIT OK — columnas eliminadas de {db_path}.")
        print(f"  Filas sin cambios: tactical_audit={ta_count_after}, unified_department={ud_count_after}")

    except Exception as exc:
        conn.rollback()
        logging.error(f"Error durante el DROP: {exc}", exc_info=True)
        print(f"\n  ERROR: {exc}")
        sys.exit(1)
    finally:
        conn.close()

    logging.info(f"Log persistente: {log_path}")


if __name__ == "__main__":
    main()
