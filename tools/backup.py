#!/usr/bin/env python3
"""
backup.py — Backup 3-2-1 diario de las cuentas activas descubiertas vía
.data/flight_sessions.json + el propio flight_sessions.json.

Medio 1: snapshot local vía sqlite3 VACUUM INTO (nunca copia de archivo cruda
-- las DBs de cuenta corren en WAL y pueden tener transacciones committeadas
sin checkpoint; una copia cruda las perdería).
Medio 2: copia del snapshot a USB_BACKUP_PATH.
Medio 3: subida del snapshot a Backblaze B2 con Object Lock (Governance,
30 días), verificada descargándola de vuelta y comparando sha256.

Diseñado para invocarse desde cron del SO, NO desde cli/main.py. Descubre las
cuentas activas leyendo .data/flight_sessions.json directamente -- no importa
tools/database.py (init_db() hardcodea la cuenta 001, ver CLAUDE.md) ni
cli/main.py (FlightSessionManager arrastra Click/Rich/InquirerPy, indeseable
en un script de cron).

Subcomandos:
    python tools/backup.py backup [--dry-run]
    python tools/backup.py restore --source {local|usb|b2} [--date YYYY-MM-DD] [--target-dir DIR]
    python tools/backup.py list --source {local|usb|b2}
    python tools/backup.py verify --db-path PATH
"""
import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(ROOT_DIR, ".env"))

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from b2sdk.v2 import B2Api, InMemoryAccountInfo, FileRetentionSetting, RetentionMode

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------
DATA_DIR = os.path.join(ROOT_DIR, ".data")
FLIGHT_SESSIONS_FILE = os.path.join(DATA_DIR, "flight_sessions.json")
BACKUP_STAGING_ROOT = os.path.join(DATA_DIR, "backups")
LOG_DIR = os.path.join(DATA_DIR, "archives")
LOCK_FILE_PATH = os.path.join(BACKUP_STAGING_ROOT, ".lock")
DEFAULT_RESTORE_DIR = os.path.join(DATA_DIR, "restored")

USB_BACKUP_PATH = os.getenv("USB_BACKUP_PATH")
B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APP_KEY = os.getenv("B2_APP_KEY")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME")

OBJECT_LOCK_RETENTION_DAYS = 30
LOCK_STALE_HOURS = 2

# journal.db, blast_master.db, backup.json (0 bytes, sin referencias en
# código) quedan fuera de alcance a propósito -- ver CLAUDE.md y el plan de
# este feature. No se derivan cuentas de un glob de .data/*.db precisamente
# para no arrastrarlos (ni verification_copy.db, paused_audits.json, etc.).


# --------------------------------------------------------------------------
# Descubrimiento de cuentas
# --------------------------------------------------------------------------
@dataclass
class AccountEntry:
    account_index: str
    name: str
    db_name: str
    db_path: str


def load_active_accounts(sessions_file: str = FLIGHT_SESSIONS_FILE) -> list[AccountEntry]:
    """
    Lee .data/flight_sessions.json y devuelve un AccountEntry por cada
    account_index presente, en orden ascendente de índice.

    Duplica ~10 líneas de FlightSessionManager.load_sessions (cli/main.py)
    en vez de importar cli/main.py -- ese módulo arrastra Click/Rich/
    InquirerPy, indeseable en un script de cron, y tools/database.py no
    referencia flight_sessions.json en absoluto (confirmado por grep).

    Si el archivo falta o el JSON es inválido, propaga la excepción --
    sin flight_sessions.json no hay nada que descubrir ni que respaldar,
    así que el caller debe abortar todo el run, no solo saltarse cuentas.
    """
    if not os.path.exists(sessions_file):
        raise FileNotFoundError(f"flight_sessions.json no encontrado en {sessions_file}")

    with open(sessions_file, "r", encoding="utf-8") as f:
        sessions = json.load(f)

    if not isinstance(sessions, dict):
        raise ValueError(f"{sessions_file} no contiene un objeto JSON")

    accounts = []
    for account_index in sorted(sessions.keys()):
        entry = sessions[account_index]
        db_name = entry.get("db_name")
        if not db_name:
            raise ValueError(f"La sesión '{account_index}' en {sessions_file} no tiene db_name")
        accounts.append(AccountEntry(
            account_index=account_index,
            name=entry.get("name", account_index),
            db_name=db_name,
            db_path=os.path.join(DATA_DIR, db_name),
        ))
    return accounts


# --------------------------------------------------------------------------
# Lock file -- previene ejecución concurrente / doble disparo el mismo día
# --------------------------------------------------------------------------
def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # el proceso existe, solo no es nuestro
    return True


def acquire_backup_lock(lock_path: str = LOCK_FILE_PATH, stale_hours: float = LOCK_STALE_HOURS) -> None:
    """
    Si ya hay un lock vigente (proceso vivo y antigüedad < stale_hours),
    aborta el run con sys.exit(2) -- código distinto de 1, para que
    cron/monitoring distinga "saltado por lock" de "corrió y falló".

    Si el lock es huérfano (proceso muerto o supera stale_hours), lo
    sobrescribe. El lock se libera en el finally de main(); este chequeo de
    PID-vivo + TTL es la red de seguridad secundaria para un crash que ni
    siquiera llega al finally (SIGKILL, corte de energía).
    """
    if os.path.exists(lock_path):
        pid = None
        started_at = None
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                lock_data = json.load(f)
            pid = lock_data["pid"]
            started_at = datetime.fromisoformat(lock_data["started_at"])
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            logging.warning(f"Lock file en {lock_path} está corrupto/ilegible; se trata como huérfano")

        if pid is not None:
            pid_alive = _is_pid_alive(pid)
            age = datetime.now(timezone.utc) - started_at if started_at else None
            is_stale = (not pid_alive) or (age is not None and age > timedelta(hours=stale_hours))

            if not is_stale:
                logging.error(
                    f"Backup ya en ejecución (pid={pid}, started_at={started_at}). "
                    f"Abortando este run."
                )
                sys.exit(2)

            logging.warning(
                f"Lock huérfano encontrado (pid={pid}, vivo={pid_alive}, "
                f"antigüedad={age}); se recupera y sobrescribe."
            )

    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}, f)


def release_backup_lock(lock_path: str = LOCK_FILE_PATH) -> None:
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------
# Medio 1: snapshot local WAL-safe
# --------------------------------------------------------------------------
def snapshot_db_vacuum_into(source_path: str, dest_path: str) -> None:
    """
    Crea un snapshot consistente y WAL-safe de source_path en dest_path
    usando VACUUM INTO nativo de SQLite. dest_path no debe existir ya
    (VACUUM INTO lanza sqlite3.OperationalError si existe) -- cada run usa
    un directorio de staging nuevo, así que esto se cumple naturalmente.
    """
    conn = sqlite3.connect(source_path)
    try:
        conn.execute("VACUUM INTO ?", (dest_path,))
    finally:
        conn.close()


def sha256_of_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Medio 2: USB
# --------------------------------------------------------------------------
def copy_to_usb(local_snapshot_path: str, usb_base_path: str, relative_name: str) -> str:
    """
    Copia local_snapshot_path a usb_base_path/relative_name. shutil.copy2
    es correcto aquí (a diferencia de la DB fuente) porque el origen ya es
    un snapshot cerrado y estático -- no hay riesgo de WAL en este paso.
    """
    if not usb_base_path or not os.path.isdir(usb_base_path):
        raise FileNotFoundError(f"USB_BACKUP_PATH no existe o no está montado: {usb_base_path}")
    dest = os.path.join(usb_base_path, relative_name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        shutil.copy2(local_snapshot_path, dest)
    except PermissionError:
        # drvfs (FAT32/exFAT/NTFS) en USBs montadas de Windows no soporta copystat/utime POSIX.
        # copyfile transfiere los bytes íntegros sin intentar replicar timestamps/permisos Unix.
        shutil.copyfile(local_snapshot_path, dest)
    return dest


# --------------------------------------------------------------------------
# Medio 3: Backblaze B2 (Object Lock Governance)
# --------------------------------------------------------------------------
class B2UploadError(Exception):
    pass


def get_b2_bucket(api: Optional[B2Api] = None):
    if api is None:
        info = InMemoryAccountInfo()
        api = B2Api(info)
        api.authorize_account("production", B2_KEY_ID, B2_APP_KEY)
    return api.get_bucket_by_name(B2_BUCKET_NAME)


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(B2UploadError),
)
def upload_to_b2(bucket, local_path: str, object_key: str, retention_days: int = OBJECT_LOCK_RETENTION_DAYS):
    """
    Sube local_path a object_key bajo Object Lock Governance con
    retención de retention_days. Envuelve las excepciones del SDK en
    B2UploadError para que tenacity solo reintente fallos de subida
    genuinos, no un bug local (p.ej. FileNotFoundError del snapshot).

    NOTA sobre idempotencia: como el bucket tiene versioning (requisito de
    Object Lock), reintentar bajo la misma object_key el mismo día no
    sobreescribe en el sitio los bytes ya subidos -- crea una nueva versión
    bajo el mismo nombre. El lock file (acquire_backup_lock) es la
    mitigación primaria contra que esto ocurra por doble disparo accidental;
    no hay limpieza automatizada de versiones duplicadas (la key de
    producción no tiene capability bypassGovernance).
    """
    retain_until_millis = int((time.time() + retention_days * 86400) * 1000)
    retention = FileRetentionSetting(RetentionMode.GOVERNANCE, retain_until_millis)
    try:
        return bucket.upload_local_file(
            local_file=local_path,
            file_name=object_key,
            file_retention=retention,
        )
    except Exception as exc:
        raise B2UploadError(f"Subida a B2 falló para {object_key}: {exc}") from exc


def download_and_verify(bucket, object_key: str, expected_sha256: str, tmp_download_path: str) -> bool:
    """
    Descarga object_key de vuelta desde B2 a tmp_download_path y confirma
    que su sha256 coincide con expected_sha256. Devuelve True/False en vez
    de lanzar, para que el caller lo registre como resultado por artefacto
    sin try/except en el sitio de llamada.
    """
    try:
        downloaded_file = bucket.download_file_by_name(object_key)
        with open(tmp_download_path, "wb") as f:
            downloaded_file.save(f)
        return sha256_of_file(tmp_download_path) == expected_sha256
    except Exception:
        logging.exception(f"Verificación de descarga falló para {object_key}")
        return False


# --------------------------------------------------------------------------
# Aislamiento de fallos por artefacto (cuenta o flight_sessions.json)
# --------------------------------------------------------------------------
@dataclass
class ArtifactResult:
    label: str
    local_ok: bool = False
    local_sha256: Optional[str] = None
    usb_ok: bool = False
    b2_ok: bool = False
    b2_verified_ok: bool = False
    errors: list[str] = field(default_factory=list)

    local_path: Optional[str] = None

    @property
    def any_failure(self) -> bool:
        return not (self.local_ok and self.usb_ok and self.b2_ok and self.b2_verified_ok)


def _finish_backup_media(result: ArtifactResult, local_snapshot: str, usb_relative_path: str,
                          object_key: str, run_staging_dir: str, bucket) -> None:
    """
    Pasos compartidos de Medio 2 (USB) y Medio 3 (B2) para cualquier
    artefacto que ya tenga un snapshot local + sha256 calculado. Usado
    tanto por backup_account() como por backup_flight_sessions_json() para
    no duplicar la lógica de aislamiento de fallos.
    """
    try:
        copy_to_usb(local_snapshot, USB_BACKUP_PATH, usb_relative_path)
        result.usb_ok = True
    except Exception as exc:
        result.errors.append(f"USB copy: {exc}")
        logging.exception(f"[{result.label}] copia a USB falló")

    try:
        upload_to_b2(bucket, local_snapshot, object_key)
        result.b2_ok = True
        verify_path = os.path.join(run_staging_dir, f".verify_{os.path.basename(local_snapshot)}")
        result.b2_verified_ok = download_and_verify(bucket, object_key, result.local_sha256, verify_path)
        if not result.b2_verified_ok:
            result.errors.append("B2 download-back sha256 no coincide")
    except Exception as exc:
        result.errors.append(f"B2 upload: {exc}")
        logging.exception(f"[{result.label}] subida a B2 falló")


def backup_account(account: AccountEntry, run_staging_dir: str, date_str: str, bucket) -> ArtifactResult:
    result = ArtifactResult(label=f"{account.account_index} ({account.db_name})")

    try:
        local_snapshot = os.path.join(run_staging_dir, account.db_name)
        snapshot_db_vacuum_into(account.db_path, local_snapshot)
        result.local_sha256 = sha256_of_file(local_snapshot)
        result.local_ok = True
        result.local_path = local_snapshot
    except Exception as exc:
        result.errors.append(f"snapshot local: {exc}")
        logging.exception(f"[{account.account_index}] snapshot local falló")
        return result  # sin snapshot local no tiene sentido intentar USB/B2

    object_key = f"{account.db_name.removesuffix('.db')}/{date_str}.db"
    _finish_backup_media(result, local_snapshot, os.path.join(date_str, account.db_name), object_key, run_staging_dir, bucket)
    return result


def backup_flight_sessions_json(run_staging_dir: str, date_str: str, bucket) -> ArtifactResult:
    """
    flight_sessions.json es JSON plano, no SQLite -- no necesita
    VACUUM INTO. Copia + sha256 son suficientes (no hay riesgo de
    WAL/escritura sin checkpoint para un archivo que la app escribe con un
    único json.dump, no una conexión de DB de larga vida).
    """
    result = ArtifactResult(label="flight_sessions.json")
    try:
        local_snapshot = os.path.join(run_staging_dir, "flight_sessions.json")
        shutil.copy2(FLIGHT_SESSIONS_FILE, local_snapshot)
        result.local_sha256 = sha256_of_file(local_snapshot)
        result.local_ok = True
        result.local_path = local_snapshot
    except Exception as exc:
        result.errors.append(f"copia local: {exc}")
        logging.exception("[flight_sessions.json] copia local falló")
        return result

    object_key = f"flight_sessions/{date_str}.json"
    _finish_backup_media(result, local_snapshot, os.path.join(date_str, "flight_sessions.json"), object_key, run_staging_dir, bucket)
    return result


# --------------------------------------------------------------------------
# Logging, resumen, orquestación
# --------------------------------------------------------------------------
def setup_logging(timestamp: str) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"backup_{timestamp}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def print_summary(results: list, log_path: str) -> None:
    ok_count = sum(1 for r in results if not r.any_failure)
    logging.info("=== Resumen del backup 3-2-1 ===")
    for r in results:
        status = "OK" if not r.any_failure else "CON FALLOS"
        logging.info(
            f"  {r.label:40s} local={r.local_ok} usb={r.usb_ok} "
            f"b2={r.b2_ok} b2_verified={r.b2_verified_ok}  [{status}]"
        )
        for err in r.errors:
            logging.info(f"      - {err}")
    logging.info(f"{ok_count}/{len(results)} artefactos respaldados completamente (3-2-1).")
    logging.info(f"Log completo: {log_path}")


# --------------------------------------------------------------------------
# Restore — funciones de restauración desde los 3 medios
# --------------------------------------------------------------------------
EXPECTED_TABLES = ("unified_department", "tactical_audit")


@dataclass
class RestoreResult:
    label: str
    source: str
    restored_path: str
    sha256: Optional[str] = None
    integrity_ok: bool = False
    tables_found: list[str] = field(default_factory=list)
    row_counts: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.integrity_ok and not self.errors


def _validate_restore_target(target_dir: str) -> None:
    """
    Protección: no permitir que el restore sobrescriba el directorio de
    datos reales (.data/). El operador debe copiar manualmente si quiere
    reemplazar una DB de producción — esa es una decisión deliberada, no
    un accidente de un flag mal puesto.
    """
    real_data = os.path.realpath(DATA_DIR)
    target_real = os.path.realpath(target_dir)
    if target_real == real_data:
        raise ValueError(
            f"El directorio de restore no puede ser el directorio de datos real "
            f"({DATA_DIR}). Usa un directorio separado como {DEFAULT_RESTORE_DIR}"
        )


def verify_restored_db(db_path: str) -> RestoreResult:
    """
    Abre una DB restaurada, corre PRAGMA integrity_check, verifica que las
    tablas esperadas (unified_department, tactical_audit) existen, y cuenta
    filas. Devuelve un RestoreResult con todo el detalle.
    """
    result = RestoreResult(
        label=os.path.basename(db_path),
        source="verify",
        restored_path=db_path,
    )

    if not os.path.exists(db_path):
        result.errors.append(f"Archivo no encontrado: {db_path}")
        return result

    try:
        result.sha256 = sha256_of_file(db_path)
    except Exception as exc:
        result.errors.append(f"sha256 falló: {exc}")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            result.integrity_ok = (integrity == "ok")
            if not result.integrity_ok:
                result.errors.append(f"integrity_check: {integrity}")

            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()]
            result.tables_found = tables

            for table in EXPECTED_TABLES:
                if table in tables:
                    count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
                    result.row_counts[table] = count
                else:
                    result.errors.append(f"Tabla esperada no encontrada: {table}")
        finally:
            conn.close()
    except Exception as exc:
        result.errors.append(f"Error abriendo DB: {exc}")

    return result


def list_local_backups() -> list[dict]:
    """
    Lista los backups disponibles en el directorio de staging local
    (.data/backups/). Cada subdirectorio con timestamp es un run.
    """
    entries = []
    if not os.path.isdir(BACKUP_STAGING_ROOT):
        return entries

    for name in sorted(os.listdir(BACKUP_STAGING_ROOT)):
        run_dir = os.path.join(BACKUP_STAGING_ROOT, name)
        if not os.path.isdir(run_dir) or name.startswith("."):
            continue
        files = [f for f in os.listdir(run_dir) if not f.startswith(".")]
        total_size = sum(
            os.path.getsize(os.path.join(run_dir, f))
            for f in files if os.path.isfile(os.path.join(run_dir, f))
        )
        entries.append({
            "timestamp": name,
            "path": run_dir,
            "files": files,
            "total_size_bytes": total_size,
        })
    return entries


def list_usb_backups() -> list[dict]:
    """
    Lista los backups disponibles en USB_BACKUP_PATH. Estructura esperada:
    USB_BACKUP_PATH/YYYY-MM-DD/*.db + flight_sessions.json.
    """
    entries = []
    if not USB_BACKUP_PATH or not os.path.isdir(USB_BACKUP_PATH):
        return entries

    for name in sorted(os.listdir(USB_BACKUP_PATH)):
        date_dir = os.path.join(USB_BACKUP_PATH, name)
        if not os.path.isdir(date_dir):
            continue
        files = os.listdir(date_dir)
        total_size = sum(
            os.path.getsize(os.path.join(date_dir, f))
            for f in files if os.path.isfile(os.path.join(date_dir, f))
        )
        entries.append({
            "date": name,
            "path": date_dir,
            "files": files,
            "total_size_bytes": total_size,
        })
    return entries


def list_b2_backups(bucket=None) -> list[dict]:
    """
    Lista los object keys presentes en el bucket B2 de backup.
    Agrupa por prefijo (nombre de cuenta) y fecha.
    """
    entries = []
    if bucket is None:
        try:
            bucket = get_b2_bucket()
        except Exception as exc:
            logging.error(f"No se pudo conectar a B2 para listar: {exc}")
            return entries

    by_date = {}
    for file_version, _ in bucket.ls(latest_only=True, recursive=True):
        parts = file_version.file_name.split("/")
        if len(parts) >= 2:
            date_key = parts[1].replace(".db", "").replace(".json", "")
        else:
            date_key = "unknown"
        if date_key not in by_date:
            by_date[date_key] = []
        by_date[date_key].append({
            "file_name": file_version.file_name,
            "size": file_version.size,
            "upload_timestamp": file_version.upload_timestamp,
        })

    for date_key in sorted(by_date.keys()):
        entries.append({
            "date": date_key,
            "files": by_date[date_key],
            "total_size_bytes": sum(f["size"] for f in by_date[date_key]),
        })
    return entries


def restore_from_local(timestamp: str, target_dir: str) -> list[RestoreResult]:
    """
    Restaura todos los artefactos del run identificado por `timestamp`
    desde el directorio de staging local a `target_dir`.
    """
    _validate_restore_target(target_dir)
    run_dir = os.path.join(BACKUP_STAGING_ROOT, timestamp)
    if not os.path.isdir(run_dir):
        raise FileNotFoundError(f"No se encontró el run de backup: {run_dir}")

    os.makedirs(target_dir, exist_ok=True)
    results = []

    for filename in sorted(os.listdir(run_dir)):
        if filename.startswith("."):
            continue
        src = os.path.join(run_dir, filename)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(target_dir, filename)
        shutil.copy2(src, dest)

        result = RestoreResult(
            label=filename,
            source="local",
            restored_path=dest,
            sha256=sha256_of_file(dest),
        )

        if filename.endswith(".db"):
            verification = verify_restored_db(dest)
            result.integrity_ok = verification.integrity_ok
            result.tables_found = verification.tables_found
            result.row_counts = verification.row_counts
            result.errors = verification.errors
        elif filename.endswith(".json"):
            try:
                with open(dest, "r", encoding="utf-8") as f:
                    json.load(f)
                result.integrity_ok = True
            except Exception as exc:
                result.errors.append(f"JSON inválido: {exc}")

        results.append(result)

    if not results:
        raise FileNotFoundError(f"El directorio de backup {run_dir} está vacío")

    return results


def restore_from_usb(date_str: str, target_dir: str) -> list[RestoreResult]:
    """
    Restaura los artefactos del backup con fecha `date_str` (YYYY-MM-DD)
    desde USB_BACKUP_PATH a `target_dir`.
    """
    _validate_restore_target(target_dir)
    if not USB_BACKUP_PATH or not os.path.isdir(USB_BACKUP_PATH):
        raise FileNotFoundError(f"USB_BACKUP_PATH no existe o no está montado: {USB_BACKUP_PATH}")

    date_dir = os.path.join(USB_BACKUP_PATH, date_str)
    if not os.path.isdir(date_dir):
        raise FileNotFoundError(f"No se encontró backup para {date_str} en USB: {date_dir}")

    os.makedirs(target_dir, exist_ok=True)
    results = []

    for filename in sorted(os.listdir(date_dir)):
        src = os.path.join(date_dir, filename)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(target_dir, filename)
        shutil.copy2(src, dest)

        result = RestoreResult(
            label=filename,
            source="usb",
            restored_path=dest,
            sha256=sha256_of_file(dest),
        )

        if filename.endswith(".db"):
            verification = verify_restored_db(dest)
            result.integrity_ok = verification.integrity_ok
            result.tables_found = verification.tables_found
            result.row_counts = verification.row_counts
            result.errors = verification.errors
        elif filename.endswith(".json"):
            try:
                with open(dest, "r", encoding="utf-8") as f:
                    json.load(f)
                result.integrity_ok = True
            except Exception as exc:
                result.errors.append(f"JSON inválido: {exc}")

        results.append(result)

    return results


def restore_from_b2(date_str: str, target_dir: str, bucket=None) -> list[RestoreResult]:
    """
    Descarga los artefactos con fecha `date_str` (YYYY-MM-DD) desde B2
    a `target_dir`. Busca objetos cuyo file_name contenga la fecha.
    """
    _validate_restore_target(target_dir)
    if bucket is None:
        bucket = get_b2_bucket()

    os.makedirs(target_dir, exist_ok=True)
    results = []

    for file_version, _ in bucket.ls(latest_only=True, recursive=True):
        if date_str not in file_version.file_name:
            continue

        # Derive filename: "flight_account_001_xauusd/2026-09-18.db" -> "flight_account_001_xauusd.db"
        parts = file_version.file_name.split("/")
        if len(parts) >= 2:
            prefix = parts[0]  # e.g., "flight_account_001_xauusd"
            ext = os.path.splitext(parts[-1])[1]  # e.g., ".db"
            local_name = f"{prefix}{ext}"
        else:
            local_name = os.path.basename(file_version.file_name)

        dest = os.path.join(target_dir, local_name)
        try:
            downloaded = bucket.download_file_by_name(file_version.file_name)
            with open(dest, "wb") as f:
                downloaded.save(f)

            result = RestoreResult(
                label=local_name,
                source="b2",
                restored_path=dest,
                sha256=sha256_of_file(dest),
            )

            if local_name.endswith(".db"):
                verification = verify_restored_db(dest)
                result.integrity_ok = verification.integrity_ok
                result.tables_found = verification.tables_found
                result.row_counts = verification.row_counts
                result.errors = verification.errors
            elif local_name.endswith(".json"):
                try:
                    with open(dest, "r", encoding="utf-8") as f:
                        json.load(f)
                    result.integrity_ok = True
                except Exception as exc:
                    result.errors.append(f"JSON inválido: {exc}")

            results.append(result)
        except Exception as exc:
            results.append(RestoreResult(
                label=local_name,
                source="b2",
                restored_path=dest,
                errors=[f"Descarga falló: {exc}"],
            ))

    return results


def print_restore_summary(results: list[RestoreResult]) -> None:
    """Imprime un resumen legible del resultado de un restore."""
    ok_count = sum(1 for r in results if r.ok)
    logging.info("=== Resumen del restore ===")
    for r in results:
        status = "OK" if r.ok else "CON ERRORES"
        logging.info(
            f"  {r.label:45s} source={r.source:5s} integrity={r.integrity_ok}  [{status}]"
        )
        if r.row_counts:
            for table, count in r.row_counts.items():
                logging.info(f"      {table}: {count} filas")
        for err in r.errors:
            logging.info(f"      ERROR: {err}")
    logging.info(f"{ok_count}/{len(results)} artefactos restaurados exitosamente.")


# --------------------------------------------------------------------------
# Dry-run — verifica la cadena sin ejecutar copias
# --------------------------------------------------------------------------
def dry_run_backup() -> None:
    """
    Valida toda la cadena de backup sin ejecutar copias ni subidas:
    - Descubre las cuentas y verifica que los archivos .db existen
    - Verifica que USB_BACKUP_PATH existe y es escribible
    - Intenta autenticarse contra B2 (sin subir nada)
    - Crea un snapshot local de prueba con VACUUM INTO en tmp
    - Reporta el estado de cada componente
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(f"dryrun_{timestamp}")
    logging.info("=== DRY RUN — backup.py ===")

    # 1. Descubrir cuentas
    try:
        accounts = load_active_accounts()
        logging.info(f"✓ flight_sessions.json: {len(accounts)} cuentas descubiertas")
        for a in accounts:
            exists = os.path.isfile(a.db_path)
            marker = "✓" if exists else "✗"
            size = os.path.getsize(a.db_path) if exists else 0
            logging.info(f"  {marker} {a.account_index} {a.name:15s} {a.db_name:45s} {'existe' if exists else 'NO EXISTE':10s} {size:>10,} bytes")
    except Exception:
        logging.exception("✗ No se pudo cargar flight_sessions.json")
        return

    # 2. VACUUM INTO de prueba (solo la primera cuenta que exista)
    test_account = next((a for a in accounts if os.path.isfile(a.db_path)), None)
    if test_account:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            snap_path = os.path.join(tmpdir, test_account.db_name)
            try:
                snapshot_db_vacuum_into(test_account.db_path, snap_path)
                snap_sha = sha256_of_file(snap_path)
                snap_size = os.path.getsize(snap_path)
                logging.info(f"✓ VACUUM INTO de prueba: {test_account.db_name} → {snap_size:,} bytes, sha256={snap_sha[:16]}...")
            except Exception:
                logging.exception(f"✗ VACUUM INTO de prueba falló para {test_account.db_name}")
    else:
        logging.warning("✗ Ningún archivo .db de cuenta existe — no se puede probar VACUUM INTO")

    # 3. USB
    if USB_BACKUP_PATH:
        if os.path.isdir(USB_BACKUP_PATH):
            writable = os.access(USB_BACKUP_PATH, os.W_OK)
            logging.info(f"{'✓' if writable else '✗'} USB_BACKUP_PATH={USB_BACKUP_PATH} {'(escribible)' if writable else '(NO escribible)'}")
        else:
            logging.warning(f"✗ USB_BACKUP_PATH={USB_BACKUP_PATH} no existe o no está montado")
    else:
        logging.warning("✗ USB_BACKUP_PATH no configurado en .env")

    # 4. B2
    if B2_KEY_ID and B2_APP_KEY and B2_BUCKET_NAME:
        try:
            bucket = get_b2_bucket()
            logging.info(f"✓ B2 autenticado, bucket '{B2_BUCKET_NAME}' accesible")
        except Exception:
            logging.exception("✗ B2 autenticación/acceso al bucket falló")
    else:
        missing = [v for v, val in [("B2_KEY_ID", B2_KEY_ID), ("B2_APP_KEY", B2_APP_KEY), ("B2_BUCKET_NAME", B2_BUCKET_NAME)] if not val]
        logging.warning(f"✗ Variables de B2 no configuradas en .env: {', '.join(missing)}")

    logging.info(f"=== DRY RUN completado. Log: {log_path} ===")


# --------------------------------------------------------------------------
# Orquestación principal (backup)
# --------------------------------------------------------------------------
def main_backup(dry_run: bool = False):
    if dry_run:
        dry_run_backup()
        return

    if sqlite3.sqlite_version_info < (3, 27, 0):
        print(f"FATAL: SQLite {sqlite3.sqlite_version} no soporta VACUUM INTO (requiere >=3.27.0)")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_path = setup_logging(timestamp)
    logging.info(f"=== backup.py iniciado (timestamp={timestamp}) ===")

    acquire_backup_lock()
    try:
        run_staging_dir = os.path.join(BACKUP_STAGING_ROOT, timestamp)
        os.makedirs(run_staging_dir, exist_ok=True)

        try:
            accounts = load_active_accounts()
        except Exception:
            logging.exception("Fatal: no se pudo cargar flight_sessions.json")
            sys.exit(1)

        try:
            bucket = get_b2_bucket()
        except Exception:
            logging.exception("Fatal: no se pudo autorizar contra Backblaze B2")
            sys.exit(1)

        results = []
        for account in accounts:
            results.append(backup_account(account, run_staging_dir, date_str, bucket))
        results.append(backup_flight_sessions_json(run_staging_dir, date_str, bucket))

        print_summary(results, log_path)

        if any(r.any_failure for r in results):
            sys.exit(1)
    finally:
        release_backup_lock()


def main_restore(source: str, date_str: Optional[str], target_dir: str):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    setup_logging(f"restore_{timestamp}")
    logging.info(f"=== Restore desde {source} (date={date_str}, target={target_dir}) ===")

    try:
        if source == "local":
            if not date_str:
                # Usar el backup local más reciente
                backups = list_local_backups()
                if not backups:
                    logging.error("No hay backups locales disponibles")
                    sys.exit(1)
                date_str = backups[-1]["timestamp"]
                logging.info(f"Usando backup local más reciente: {date_str}")
            results = restore_from_local(date_str, target_dir)
        elif source == "usb":
            if not date_str:
                usb_backups = list_usb_backups()
                if not usb_backups:
                    logging.error("No hay backups en USB disponibles")
                    sys.exit(1)
                date_str = usb_backups[-1]["date"]
                logging.info(f"Usando backup USB más reciente: {date_str}")
            results = restore_from_usb(date_str, target_dir)
        elif source == "b2":
            if not date_str:
                logging.error("--date es requerido para restore desde B2")
                sys.exit(1)
            results = restore_from_b2(date_str, target_dir)
        else:
            logging.error(f"Fuente no reconocida: {source}")
            sys.exit(1)

        print_restore_summary(results)

        if not all(r.ok for r in results):
            sys.exit(1)
    except (FileNotFoundError, ValueError) as exc:
        logging.error(str(exc))
        sys.exit(1)


def main_list(source: str):
    if source == "local":
        entries = list_local_backups()
        if not entries:
            print("No hay backups locales en staging.")
            return
        print(f"{'Timestamp':<25s} {'Archivos':>8s} {'Tamaño':>12s}")
        print("-" * 50)
        for e in entries:
            size_kb = e['total_size_bytes'] / 1024
            print(f"{e['timestamp']:<25s} {len(e['files']):>8d} {size_kb:>10.1f} KB")
            for f in e['files']:
                print(f"    {f}")

    elif source == "usb":
        entries = list_usb_backups()
        if not entries:
            print(f"No hay backups en USB ({USB_BACKUP_PATH or 'no configurado'}).")
            return
        print(f"{'Fecha':<15s} {'Archivos':>8s} {'Tamaño':>12s}")
        print("-" * 40)
        for e in entries:
            size_kb = e['total_size_bytes'] / 1024
            print(f"{e['date']:<15s} {len(e['files']):>8d} {size_kb:>10.1f} KB")

    elif source == "b2":
        entries = list_b2_backups()
        if not entries:
            print("No hay backups en B2 (o no se pudo conectar).")
            return
        print(f"{'Fecha':<15s} {'Archivos':>8s} {'Tamaño':>12s}")
        print("-" * 40)
        for e in entries:
            size_kb = e['total_size_bytes'] / 1024
            print(f"{e['date']:<15s} {len(e['files']):>8d} {size_kb:>10.1f} KB")
            for f in e['files']:
                print(f"    {f['file_name']}")


def main_verify(db_path: str):
    result = verify_restored_db(db_path)
    status = "OK" if result.ok else "FALLÓ"
    print(f"\n=== Verificación de {result.label}: {status} ===")
    print(f"  Ruta:      {result.restored_path}")
    print(f"  SHA256:    {result.sha256 or 'N/A'}")
    print(f"  Integridad: {'OK' if result.integrity_ok else 'FALLÓ'}")
    if result.tables_found:
        print(f"  Tablas:    {', '.join(result.tables_found)}")
    if result.row_counts:
        for table, count in result.row_counts.items():
            print(f"  {table}: {count} filas")
    for err in result.errors:
        print(f"  ERROR: {err}")

    if not result.ok:
        sys.exit(1)


# --------------------------------------------------------------------------
# CLI con argparse
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup.py",
        description="Backup 3-2-1 y restore de las cuentas de blast_master.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # backup
    p_backup = subparsers.add_parser("backup", help="Ejecutar backup 3-2-1 completo")
    p_backup.add_argument("--dry-run", action="store_true",
                          help="Validar la cadena sin ejecutar copias ni subidas")

    # restore
    p_restore = subparsers.add_parser("restore", help="Restaurar desde un backup")
    p_restore.add_argument("--source", required=True, choices=["local", "usb", "b2"],
                           help="Medio desde el cual restaurar")
    p_restore.add_argument("--date", default=None,
                           help="Fecha/timestamp del backup (YYYY-MM-DD para usb/b2, "
                                "timestamp YYYYMMDD_HHMMSS para local). Si se omite, "
                                "usa el más reciente disponible.")
    p_restore.add_argument("--target-dir", default=DEFAULT_RESTORE_DIR,
                           help=f"Directorio destino del restore (default: {DEFAULT_RESTORE_DIR})")

    # list
    p_list = subparsers.add_parser("list", help="Listar backups disponibles")
    p_list.add_argument("--source", required=True, choices=["local", "usb", "b2"],
                        help="Medio en el cual buscar backups")

    # verify
    p_verify = subparsers.add_parser("verify", help="Verificar integridad de una DB restaurada")
    p_verify.add_argument("--db-path", required=True,
                          help="Ruta a la DB a verificar")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "backup":
        main_backup(dry_run=args.dry_run)
    elif args.command == "restore":
        main_restore(source=args.source, date_str=args.date, target_dir=args.target_dir)
    elif args.command == "list":
        main_list(source=args.source)
    elif args.command == "verify":
        main_verify(db_path=args.db_path)


if __name__ == "__main__":
    main()
