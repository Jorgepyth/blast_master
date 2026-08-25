#!/usr/bin/env python3
"""
migrate_tactical_audit_1n.py — Migra `tactical_audit` de 1:1 con
`unified_department` (PK compartida: `tactical_audit.id` era a la vez su
única PK y el FK a `unified_department.id`) a 1:many (PK propia `id`
[UUID], nuevo FK `trade_id` no-único). Habilita registrar varios trades
(ejecuciones) sobre un mismo Unified Analysis/Efficiency Audit — ver
ARCHITECTURE.md y CLAUDE.md para el detalle del cambio.

SQLite no permite alterar una PK in-place: el script reconstruye la tabla
completa (`CREATE TABLE tactical_audit_new` con el esquema nuevo, generado
directamente desde el modelo ORM vigente en tools/database.py para que no
pueda desincronizarse a mano, `INSERT ... SELECT` preservando cada `id`
existente como su propio nuevo `id` [no se re-numera nada] y copiándolo
también a `trade_id`, `DROP TABLE` + `RENAME`). También traslada
`unified_department.tactical_page_id` (una columna por análisis) a
`tactical_audit.notion_page_id` (una por ejecución) y luego elimina esa
columna de `unified_department` con `ALTER TABLE ... DROP COLUMN` nativo.

Uso:
    python tools/migrate_tactical_audit_1n.py --db ruta/a/copia.db              # dry-run
    python tools/migrate_tactical_audit_1n.py --db ruta/a/copia.db --confirm    # aplica

Por defecto el script RECHAZA correr contra cualquiera de las 3 DBs de cuenta
reales en producción. Para forzarlo hay que pasar
--i-understand-this-is-production explícitamente, y solo después de haber
corrido este mismo script (con --confirm) contra una COPIA de esa misma DB
y verificado el resultado (suite de tests + smoke test manual del CLI).
Se recomienda correr tools/backup.py justo antes.

Requiere SQLite 3.35+ (soporte nativo de `ALTER TABLE ... DROP COLUMN`).

Idempotente: si `tactical_audit.trade_id` ya existe, asume que la migración
ya corrió y no hace nada.
"""
import argparse
import csv
import logging
import os
import sqlite3
import sys
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

PRODUCTION_DB_PATHS = {
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_001_xauusd.db")),
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_002_btcusdtp.db")),
    os.path.abspath(os.path.join(ROOT_DIR, ".data", "flight_account_003_us100.db")),
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Migra tactical_audit de 1:1 (PK compartida con unified_department) a 1:many (PK propia + FK trade_id)."
    )
    parser.add_argument("--db", required=True, help="Ruta a la base de datos SQLite objetivo.")
    parser.add_argument("--confirm", action="store_true", help="Ejecutar la migración real. Sin este flag, solo verifica y reporta.")
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
    log_path = os.path.join(log_dir, f"migrate_tactical_audit_1n_{timestamp}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    return log_path


def build_new_table_ddl_and_columns():
    """Genera la DDL de la tabla nueva directamente desde el modelo ORM vigente
    (tools/database.py) en vez de transcribirla a mano, para que no pueda
    desincronizarse silenciosamente si el modelo cambia."""
    from tools.database import TacticalAudit, UnifiedDepartment
    from sqlalchemy import MetaData
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import sqlite as sqlite_dialect

    meta = MetaData()
    UnifiedDepartment.__table__.to_metadata(meta)  # para resolver el FK trade_id -> unified_department.id
    new_table = TacticalAudit.__table__.to_metadata(meta, name="tactical_audit_new")
    ddl = str(CreateTable(new_table).compile(dialect=sqlite_dialect.dialect())).strip()
    columns = [c.name for c in new_table.columns]
    return ddl, columns


def export_csv_backup(rows: list, columns: list, timestamp: str, db_label: str) -> str:
    backup_dir = os.path.join(ROOT_DIR, ".data", "archives")
    os.makedirs(backup_dir, exist_ok=True)
    csv_path = os.path.join(backup_dir, f"backup_pre_tactical_audit_1n_{db_label}_{timestamp}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    return csv_path


def main():
    args = parse_args()
    guard_production_path(args.db, args.allow_production)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(timestamp)

    db_path = os.path.abspath(args.db)
    db_label = os.path.splitext(os.path.basename(db_path))[0]
    logging.info(f"Base de datos: {db_path}")
    logging.info(f"Modo: {'MIGRACION REAL' if args.confirm else 'DRY-RUN (solo verificacion)'}")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        cur.execute("SELECT sqlite_version()")
        version = cur.fetchone()[0]
        logging.info(f"SQLite version: {version}")

        ta_cols_before = [row[1] for row in cur.execute("PRAGMA table_info(tactical_audit)")]
        ud_cols_before = [row[1] for row in cur.execute("PRAGMA table_info(unified_department)")]

        if "trade_id" in ta_cols_before:
            logging.info("tactical_audit.trade_id ya existe -- la migracion ya corrio. Nada que hacer (idempotente).")
            print("\n  Esta base de datos ya fue migrada. Nada que hacer.")
            return

        cur.execute("SELECT COUNT(*) FROM tactical_audit")
        ta_count_before = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM unified_department")
        ud_count_before = cur.fetchone()[0]
        logging.info(f"Filas antes: tactical_audit={ta_count_before}, unified_department={ud_count_before}")

        old_ids = {row[0] for row in cur.execute("SELECT id FROM tactical_audit")}

        # Backup CSV pre-migracion (tabla completa + el mapeo de tactical_page_id que se va a trasladar)
        cur.execute(f"SELECT * FROM tactical_audit")
        rows = cur.fetchall()
        csv_path = export_csv_backup(rows, ta_cols_before, timestamp, db_label)
        logging.info(f"Backup CSV pre-migracion: {csv_path} ({len(rows)} filas)")

        ddl, new_columns = build_new_table_ddl_and_columns()
        logging.info(f"DDL generada para tactical_audit_new ({len(new_columns)} columnas).")

        if not args.confirm:
            print(f"\n  DRY-RUN -- se reconstruiria tactical_audit con {len(new_columns)} columnas.")
            print(f"  Filas actuales: tactical_audit={ta_count_before}, unified_department={ud_count_before}")
            print(f"  Backup ya escrito en: {csv_path}")
            print("  Ejecuta con --confirm para aplicar la migracion.")
            return

        select_exprs = []
        for col in new_columns:
            if col == "id":
                select_exprs.append("tactical_audit.id")
            elif col == "trade_id":
                select_exprs.append("tactical_audit.id")
            elif col == "created_at":
                select_exprs.append("COALESCE((SELECT created_at FROM unified_department WHERE id = tactical_audit.id), CURRENT_TIMESTAMP)")
            elif col == "updated_at":
                select_exprs.append("COALESCE((SELECT updated_at FROM unified_department WHERE id = tactical_audit.id), CURRENT_TIMESTAMP)")
            elif col == "notion_page_id":
                if "tactical_page_id" in ud_cols_before:
                    select_exprs.append("(SELECT tactical_page_id FROM unified_department WHERE id = tactical_audit.id)")
                else:
                    select_exprs.append("NULL")
            else:
                select_exprs.append(f"tactical_audit.{col}")

        insert_sql = (
            f"INSERT INTO tactical_audit_new ({', '.join(new_columns)})\n"
            f"SELECT {', '.join(select_exprs)}\nFROM tactical_audit"
        )

        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute(ddl)
        logging.info("CREATE TABLE tactical_audit_new ejecutado.")
        cur.execute(insert_sql)
        logging.info(f"INSERT INTO tactical_audit_new ... SELECT ejecutado ({cur.rowcount} filas).")
        cur.execute("DROP TABLE tactical_audit")
        cur.execute("ALTER TABLE tactical_audit_new RENAME TO tactical_audit")
        logging.info("DROP + RENAME de tactical_audit ejecutado.")

        if "tactical_page_id" in ud_cols_before:
            cur.execute("ALTER TABLE unified_department DROP COLUMN tactical_page_id")
            logging.info("DROP COLUMN unified_department.tactical_page_id ejecutado.")

        conn.commit()

        # --- Verificacion post-migracion ---
        cur.execute("PRAGMA foreign_keys=ON")
        fk_violations = cur.execute("PRAGMA foreign_key_check(tactical_audit)").fetchall()
        assert not fk_violations, f"PRAGMA foreign_key_check encontro violaciones: {fk_violations}"

        ta_cols_after = [row[1] for row in cur.execute("PRAGMA table_info(tactical_audit)")]
        ud_cols_after = [row[1] for row in cur.execute("PRAGMA table_info(unified_department)")]
        cur.execute("SELECT COUNT(*) FROM tactical_audit")
        ta_count_after = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM unified_department")
        ud_count_after = cur.fetchone()[0]

        assert ta_count_after == ta_count_before, f"Conteo de filas cambio en tactical_audit: {ta_count_before} -> {ta_count_after}"
        assert ud_count_after == ud_count_before, f"Conteo de filas cambio en unified_department: {ud_count_before} -> {ud_count_after}"
        assert "trade_id" in ta_cols_after, "trade_id no quedo presente tras la migracion"
        assert "tactical_page_id" not in ud_cols_after, "tactical_page_id sigue presente en unified_department"

        new_ids = {row[0] for row in cur.execute("SELECT id FROM tactical_audit")}
        assert new_ids == old_ids, "El conjunto de ids de tactical_audit cambio -- se perdio o renumero una fila"

        orphans = cur.execute(
            "SELECT COUNT(*) FROM tactical_audit t LEFT JOIN unified_department u ON t.trade_id = u.id WHERE u.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0, f"{orphans} filas de tactical_audit quedaron con trade_id huerfano"

        logging.info(f"Verificacion post-migracion OK. Filas: tactical_audit={ta_count_after}, unified_department={ud_count_after}")
        print(f"\n  COMMIT OK -- tactical_audit migrado a 1:many en {db_path}.")
        print(f"  Filas sin cambios: tactical_audit={ta_count_after}, unified_department={ud_count_after}")
        print(f"  Backup pre-migracion: {csv_path}")

    except Exception as exc:
        conn.rollback()
        logging.error(f"Error durante la migracion: {exc}", exc_info=True)
        print(f"\n  ERROR: {exc}")
        sys.exit(1)
    finally:
        conn.close()

    logging.info(f"Log persistente: {log_path}")


if __name__ == "__main__":
    main()
