import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from b2sdk.v2 import B2Api, B2HttpApiConfig, InMemoryAccountInfo, RawSimulator
from b2sdk.v2.exception import AccessDenied

import tools.backup as backup


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def fake_source_db(tmp_path):
    """Una DB SQLite real en disco, standing-in por una cuenta -- creada
    con sqlite3 crudo, no el ORM, porque backup.py nunca toca SQLAlchemy."""
    db_path = tmp_path / "flight_account_001_xauusd.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO trades (note) VALUES ('seed')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def fake_usb_dir(tmp_path):
    d = tmp_path / "usb"
    d.mkdir()
    return d


@pytest.fixture
def b2_bucket():
    """Bucket real de b2sdk.v2.RawSimulator, autorizado con la master key
    (todas las capabilities) -- suficiente para los tests que solo
    necesitan upload/download normales, no el mecanismo de Governance en
    sí (ver test_governance_lock_* más abajo, que construye su propia
    key restringida)."""
    shared_sim = RawSimulator()
    api_config = B2HttpApiConfig(_raw_api_class=lambda http: shared_sim)
    info = InMemoryAccountInfo()
    api = B2Api(info, api_config=api_config)
    account_id, master_key = shared_sim.create_account()
    api.authorize_account("production", account_id, master_key)
    bucket = api.create_bucket("test-backup-bucket", "allPrivate", is_file_lock_enabled=True)
    return bucket


# --------------------------------------------------------------------------
# load_active_accounts()
# --------------------------------------------------------------------------
def test_load_active_accounts_missing_file_raises(tmp_path):
    missing = tmp_path / "flight_sessions.json"
    with pytest.raises(FileNotFoundError):
        backup.load_active_accounts(sessions_file=str(missing))


def test_load_active_accounts_malformed_json_raises(tmp_path):
    bad = tmp_path / "flight_sessions.json"
    bad.write_text("{not valid json")
    with pytest.raises(json.JSONDecodeError):
        backup.load_active_accounts(sessions_file=str(bad))


def test_load_active_accounts_missing_db_name_raises(tmp_path):
    sessions_path = tmp_path / "flight_sessions.json"
    sessions_path.write_text(json.dumps({"001": {"name": "Main"}}))
    with pytest.raises(ValueError):
        backup.load_active_accounts(sessions_file=str(sessions_path))


def test_load_active_accounts_parses_valid_sessions(tmp_path):
    sessions = {
        "002": {"name": "Crypto", "db_name": "flight_account_002_btcusdtp.db"},
        "001": {"name": "Main", "db_name": "flight_account_001_xauusd.db"},
    }
    sessions_path = tmp_path / "flight_sessions.json"
    sessions_path.write_text(json.dumps(sessions))
    accounts = backup.load_active_accounts(sessions_file=str(sessions_path))
    assert [a.account_index for a in accounts] == ["001", "002"]
    assert accounts[0].name == "Main"
    assert accounts[0].db_name == "flight_account_001_xauusd.db"


# --------------------------------------------------------------------------
# test_plan #2: snapshot local WAL-safe
# --------------------------------------------------------------------------
def test_wal_uncheckpointed_rows_captured_by_vacuum_into_not_by_raw_copy(tmp_path):
    db_path = tmp_path / "flight_account_001_xauusd.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, note TEXT)")
    conn.commit()

    # Insertado y commiteado SIN checkpoint -- deja filas en el -wal.
    conn.execute("INSERT INTO trades (note) VALUES ('wal_pending_1')")
    conn.execute("INSERT INTO trades (note) VALUES ('wal_pending_2')")
    conn.commit()
    committed_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert committed_count == 2

    snap_path = tmp_path / "snapshot.db"
    backup.snapshot_db_vacuum_into(str(db_path), str(snap_path))
    snap_conn = sqlite3.connect(str(snap_path))
    assert snap_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == committed_count
    snap_conn.close()

    # Contraste: una copia cruda de SOLO los bytes del .db (ignorando -wal)
    # no debe ver las filas pendientes en WAL -- esta es la evidencia de la
    # pérdida que VACUUM INTO evita.
    naive_copy_path = tmp_path / "naive_copy.db"
    import shutil as _shutil
    _shutil.copy(str(db_path), str(naive_copy_path))
    naive_conn = sqlite3.connect(str(naive_copy_path))
    try:
        # En WAL, hasta el propio CREATE TABLE puede seguir sin checkpoint --
        # la copia cruda puede no tener ni el esquema todavía (evidencia aun
        # más contundente de la pérdida que VACUUM INTO evita).
        naive_count = naive_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    except sqlite3.OperationalError:
        naive_count = 0
    naive_conn.close()
    assert naive_count < committed_count

    conn.close()


def test_snapshot_integrity_under_concurrent_writer(tmp_path):
    db_path = tmp_path / "flight_account_002_btcusdtp.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, note TEXT)")
    conn.commit()

    stop = threading.Event()

    def writer():
        w_conn = sqlite3.connect(str(db_path), timeout=15)
        i = 0
        while not stop.is_set():
            w_conn.execute("INSERT INTO trades (note) VALUES (?)", (f"row_{i}",))
            w_conn.commit()
            i += 1
        w_conn.close()

    t = threading.Thread(target=writer)
    t.start()
    snap_path = tmp_path / "snapshot.db"
    try:
        time.sleep(0.05)  # deja que el writer arranque antes del snapshot
        backup.snapshot_db_vacuum_into(str(db_path), str(snap_path))
    finally:
        stop.set()
        t.join()
        conn.close()

    snap_conn = sqlite3.connect(str(snap_path))
    result = snap_conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert result == "ok"
    snap_conn.close()


# --------------------------------------------------------------------------
# test_plan #1: sha256 idéntico local == USB == B2-descargado-de-vuelta
# --------------------------------------------------------------------------
def test_sha256_matches_across_local_usb_and_b2_roundtrip(fake_source_db, fake_usb_dir, b2_bucket, tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    local_snap = staging / fake_source_db.name
    backup.snapshot_db_vacuum_into(str(fake_source_db), str(local_snap))
    local_sha = backup.sha256_of_file(str(local_snap))

    usb_dest = backup.copy_to_usb(str(local_snap), str(fake_usb_dir), fake_source_db.name)
    assert backup.sha256_of_file(usb_dest) == local_sha

    object_key = f"{fake_source_db.stem}/2026-08-23.db"
    backup.upload_to_b2(b2_bucket, str(local_snap), object_key)
    verify_path = staging / "verify.db"
    assert backup.download_and_verify(b2_bucket, object_key, local_sha, str(verify_path))


# --------------------------------------------------------------------------
# test_plan #4: aislamiento de fallos por cuenta
# --------------------------------------------------------------------------
def test_one_account_b2_failure_isolated_from_others(fake_usb_dir, b2_bucket, tmp_path, monkeypatch):
    def make_db(name):
        db_path = tmp_path / name
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO trades (note) VALUES ('seed')")
        conn.commit()
        conn.close()
        return db_path

    db_001 = make_db("flight_account_001_xauusd.db")
    db_002 = make_db("flight_account_002_btcusdtp.db")
    account_001 = backup.AccountEntry("001", "Main", db_001.name, str(db_001))
    account_002 = backup.AccountEntry("002", "Crypto", db_002.name, str(db_002))

    staging = tmp_path / "staging"
    staging.mkdir()

    original_upload = backup.upload_to_b2

    def selective_upload(bucket, local_path, object_key, retention_days=backup.OBJECT_LOCK_RETENTION_DAYS):
        if "001" in object_key:
            raise Exception("simulated B2 connection failure for account 001")
        return original_upload(bucket, local_path, object_key, retention_days)

    monkeypatch.setattr(backup, "USB_BACKUP_PATH", str(fake_usb_dir))
    monkeypatch.setattr(backup, "upload_to_b2", selective_upload)

    result_001 = backup.backup_account(account_001, str(staging), "2026-08-23", b2_bucket)
    result_002 = backup.backup_account(account_002, str(staging), "2026-08-23", b2_bucket)

    assert result_001.local_ok is True
    assert result_001.usb_ok is True
    assert result_001.b2_ok is False
    assert result_001.any_failure is True
    assert any("B2" in e for e in result_001.errors)

    assert result_002.local_ok is True
    assert result_002.usb_ok is True
    assert result_002.b2_ok is True
    assert result_002.b2_verified_ok is True
    assert result_002.any_failure is False


# --------------------------------------------------------------------------
# test_plan #5: flight_sessions.json
# --------------------------------------------------------------------------
def test_flight_sessions_json_roundtrips_after_backup(fake_usb_dir, b2_bucket, tmp_path, monkeypatch):
    sessions = {
        "001": {"name": "Main", "db_name": "flight_account_001_xauusd.db"},
        "002": {"name": "Crypto", "db_name": "flight_account_002_btcusdtp.db"},
    }
    sessions_path = tmp_path / "flight_sessions.json"
    sessions_path.write_text(json.dumps(sessions))
    staging = tmp_path / "staging"
    staging.mkdir()

    monkeypatch.setattr(backup, "FLIGHT_SESSIONS_FILE", str(sessions_path))
    monkeypatch.setattr(backup, "USB_BACKUP_PATH", str(fake_usb_dir))

    result = backup.backup_flight_sessions_json(str(staging), "2026-08-23", b2_bucket)

    assert result.any_failure is False
    restored = json.loads((staging / "flight_sessions.json").read_text())
    assert restored["001"]["name"] == "Main"
    assert restored["002"]["db_name"] == "flight_account_002_btcusdtp.db"


# --------------------------------------------------------------------------
# test_plan: rechazo de Object Lock Governance (+ control negativo)
# --------------------------------------------------------------------------
def test_governance_lock_rejects_delete_without_bypass(tmp_path):
    """
    Verificación real (no mock manual) de que RawSimulator rechaza
    delete_file_version sobre un objeto con Object Lock Governance cuando
    la key que intenta borrar NO tiene la capability 'bypassGovernance'.
    Ya ejecutado y confirmado en PASS en la sesión de auditoría de este
    feature -- ver plan en .claude/plans/.

    LIMITACIÓN CONOCIDA DEL SIMULADOR (b2sdk==2.12.0, no es un bug de
    backup.py): RawSimulator.delete_file_version nunca reenvía bucket_id
    al chequeo de autorización (b2sdk/_internal/raw_simulator.py:1540) --
    por lo tanto CUALQUIER key con scope restringido a un bucket específico
    recibe Unauthorized en cualquier intento de borrado, sin importar sus
    capabilities. Esto no refleja la API real de B2 (ahí una key con scope
    de bucket + deleteFiles sí puede borrar dentro de su bucket). Se usa
    aquí una key de scope de CUENTA COMPLETA (bucket_id=None) para aislar
    específicamente el mecanismo de Governance, que es el objetivo real de
    este test -- no reproduce fielmente una key bucket-scoped como la real
    de producción. Cierre de ese gap: verificación manual contra B2 real
    (ver verification_protocol del plan), no automatizable con este SDK.
    """
    shared_sim = RawSimulator()
    api_config = B2HttpApiConfig(_raw_api_class=lambda http: shared_sim)

    info = InMemoryAccountInfo()
    api = B2Api(info, api_config=api_config)
    account_id, master_key = shared_sim.create_account()
    api.authorize_account("production", account_id, master_key)
    bucket = api.create_bucket("test-backup-bucket", "allPrivate", is_file_lock_enabled=True)

    retain_until_millis = int((time.time() + 30 * 86400) * 1000)
    from b2sdk.v2 import FileRetentionSetting, RetentionMode
    retention = FileRetentionSetting(RetentionMode.GOVERNANCE, retain_until_millis)

    local_path = tmp_path / "fake_snapshot.db"
    local_path.write_bytes(b"fake sqlite snapshot bytes")

    uploaded_file = bucket.upload_local_file(
        local_file=str(local_path),
        file_name="flight_account_001_xauusd/2026-08-23.db",
        file_retention=retention,
    )

    # Capabilities reales que tendría la key de produccion -- deleteFiles
    # incluida a propósito, para que el rechazo sea específicamente por el
    # lock, no un 403 trivial por falta de permiso general de borrado.
    restricted_capabilities = [
        "listBuckets", "listFiles", "readFiles", "writeFiles",
        "writeFileRetentions", "deleteFiles",
    ]
    created_key = shared_sim.create_key(
        api_url=api.session.account_info.get_api_url(),
        account_auth_token=api.session.account_info.get_account_auth_token(),
        account_id=account_id,
        capabilities=restricted_capabilities,
        key_name="restricted-backup-key",
        valid_duration_seconds=None,
        bucket_id=None,  # ver docstring: limitación del simulador con bucket_id
        name_prefix=None,
    )

    info2 = InMemoryAccountInfo()
    api2 = B2Api(info2, api_config=api_config)
    api2.authorize_account("production", created_key["applicationKeyId"], created_key["applicationKey"])
    bucket2 = api2.get_bucket_by_name("test-backup-bucket")

    with pytest.raises(AccessDenied):
        bucket2.delete_file_version(uploaded_file.id_, "flight_account_001_xauusd/2026-08-23.db")


def test_governance_lock_delete_succeeds_with_bypass_capability(tmp_path):
    """Control negativo del test anterior: la MISMA key pero CON
    bypassGovernance sí debe poder borrar -- confirma que el rechazo de
    arriba es específico del mecanismo de Governance, no otro bloqueo."""
    shared_sim = RawSimulator()
    api_config = B2HttpApiConfig(_raw_api_class=lambda http: shared_sim)

    info = InMemoryAccountInfo()
    api = B2Api(info, api_config=api_config)
    account_id, master_key = shared_sim.create_account()
    api.authorize_account("production", account_id, master_key)
    bucket = api.create_bucket("test-backup-bucket", "allPrivate", is_file_lock_enabled=True)

    retain_until_millis = int((time.time() + 30 * 86400) * 1000)
    from b2sdk.v2 import FileRetentionSetting, RetentionMode
    retention = FileRetentionSetting(RetentionMode.GOVERNANCE, retain_until_millis)

    local_path = tmp_path / "fake_snapshot.db"
    local_path.write_bytes(b"fake sqlite snapshot bytes")

    uploaded_file = bucket.upload_local_file(
        local_file=str(local_path),
        file_name="control_test/2026-08-23.db",
        file_retention=retention,
    )

    capabilities_with_bypass = [
        "listBuckets", "listFiles", "readFiles", "writeFiles",
        "writeFileRetentions", "deleteFiles", "bypassGovernance",
    ]
    created_key = shared_sim.create_key(
        api_url=api.session.account_info.get_api_url(),
        account_auth_token=api.session.account_info.get_account_auth_token(),
        account_id=account_id,
        capabilities=capabilities_with_bypass,
        key_name="bypass-key",
        valid_duration_seconds=None,
        bucket_id=None,
        name_prefix=None,
    )
    info2 = InMemoryAccountInfo()
    api2 = B2Api(info2, api_config=api_config)
    api2.authorize_account("production", created_key["applicationKeyId"], created_key["applicationKey"])
    bucket2 = api2.get_bucket_by_name("test-backup-bucket")

    result = bucket2.delete_file_version(uploaded_file.id_, "control_test/2026-08-23.db", bypass_governance=True)
    assert result.file_id == uploaded_file.id_


# --------------------------------------------------------------------------
# Lock file
# --------------------------------------------------------------------------
def test_lock_prevents_concurrent_run(tmp_path):
    lock_path = tmp_path / ".lock"
    lock_path.write_text(json.dumps({
        "pid": os.getpid(),  # el propio proceso de test -- garantizado vivo
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))

    with pytest.raises(SystemExit) as exc_info:
        backup.acquire_backup_lock(lock_path=str(lock_path), stale_hours=2)
    assert exc_info.value.code == 2


def test_stale_lock_dead_pid_is_recovered(tmp_path):
    # Subproceso real que ya terminó -- PID garantizado muerto (reaped).
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = proc.pid
    proc.wait()

    lock_path = tmp_path / ".lock"
    lock_path.write_text(json.dumps({
        "pid": dead_pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))

    backup.acquire_backup_lock(lock_path=str(lock_path), stale_hours=2)  # no debe lanzar

    new_lock = json.loads(lock_path.read_text())
    assert new_lock["pid"] == os.getpid()


def test_stale_lock_old_ttl_is_recovered(tmp_path):
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    lock_path = tmp_path / ".lock"
    lock_path.write_text(json.dumps({
        "pid": os.getpid(),  # vivo, pero el lock es viejo
        "started_at": old_timestamp,
    }))

    backup.acquire_backup_lock(lock_path=str(lock_path), stale_hours=2)  # no debe lanzar

    new_lock = json.loads(lock_path.read_text())
    assert new_lock["pid"] == os.getpid()


def test_release_backup_lock_removes_file(tmp_path):
    lock_path = tmp_path / ".lock"
    lock_path.write_text(json.dumps({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}))
    backup.release_backup_lock(lock_path=str(lock_path))
    assert not lock_path.exists()


def test_release_backup_lock_missing_file_is_noop(tmp_path):
    lock_path = tmp_path / ".lock"
    backup.release_backup_lock(lock_path=str(lock_path))  # no debe lanzar


# --------------------------------------------------------------------------
# Restore: verify_restored_db
# --------------------------------------------------------------------------
def test_verify_restored_db_valid(fake_source_db, tmp_path):
    """Snapshot → verify → integridad OK, tablas y filas reportadas."""
    snap_path = tmp_path / "snapshot.db"
    backup.snapshot_db_vacuum_into(str(fake_source_db), str(snap_path))

    # La fake_source_db solo tiene tabla 'trades', no las tablas reales.
    # Creamos una DB con las tablas esperadas.
    db = tmp_path / "real_structure.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE unified_department (id INTEGER PRIMARY KEY, state TEXT)")
    conn.execute("CREATE TABLE tactical_audit (id INTEGER PRIMARY KEY, trade_id TEXT)")
    conn.execute("INSERT INTO unified_department (state) VALUES ('COMPLETED')")
    conn.execute("INSERT INTO unified_department (state) VALUES ('FAILED')")
    conn.execute("INSERT INTO tactical_audit (trade_id) VALUES ('abc123')")
    conn.commit()
    conn.close()

    snap2 = tmp_path / "snap_real.db"
    backup.snapshot_db_vacuum_into(str(db), str(snap2))
    result = backup.verify_restored_db(str(snap2))

    assert result.integrity_ok is True
    assert "unified_department" in result.tables_found
    assert "tactical_audit" in result.tables_found
    assert result.row_counts["unified_department"] == 2
    assert result.row_counts["tactical_audit"] == 1
    assert result.ok is True
    assert result.sha256 is not None


def test_verify_restored_db_detects_corruption(tmp_path):
    """Una DB con bytes basura falla el integrity_check."""
    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_bytes(b"this is not a sqlite database at all!!")

    result = backup.verify_restored_db(str(corrupt_db))
    assert result.integrity_ok is False
    assert result.ok is False
    assert len(result.errors) > 0


def test_verify_restored_db_missing_file(tmp_path):
    """Verificar un archivo que no existe."""
    result = backup.verify_restored_db(str(tmp_path / "nonexistent.db"))
    assert result.ok is False
    assert any("no encontrado" in e for e in result.errors)


# --------------------------------------------------------------------------
# Restore: _validate_restore_target
# --------------------------------------------------------------------------
def test_restore_target_rejects_data_dir(monkeypatch):
    """El restore nunca debe poder sobrescribir .data/ directamente."""
    monkeypatch.setattr(backup, "DATA_DIR", "/fake/project/.data")
    with pytest.raises(ValueError, match="directorio de datos real"):
        backup._validate_restore_target("/fake/project/.data")


def test_restore_target_accepts_separate_dir(monkeypatch):
    """Un directorio diferente a .data/ es válido."""
    monkeypatch.setattr(backup, "DATA_DIR", "/fake/project/.data")
    # No debe lanzar
    backup._validate_restore_target("/fake/project/.data/restored")


# --------------------------------------------------------------------------
# Restore: roundtrip local
# --------------------------------------------------------------------------
def test_restore_from_local_roundtrip(tmp_path):
    """Backup → restore from local → verify: cadena completa."""
    # Setup: crear DB con estructura real
    source_db = tmp_path / "flight_account_001_xauusd.db"
    conn = sqlite3.connect(str(source_db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE unified_department (id INTEGER PRIMARY KEY, state TEXT)")
    conn.execute("CREATE TABLE tactical_audit (id INTEGER PRIMARY KEY, trade_id TEXT)")
    conn.execute("INSERT INTO unified_department (state) VALUES ('COMPLETED')")
    conn.execute("INSERT INTO unified_department (state) VALUES ('PENDING_AUDITS')")
    conn.execute("INSERT INTO tactical_audit (trade_id) VALUES ('trade_001')")
    conn.execute("INSERT INTO tactical_audit (trade_id) VALUES ('trade_002')")
    conn.execute("INSERT INTO tactical_audit (trade_id) VALUES ('trade_003')")
    conn.commit()
    conn.close()

    # Paso 1: crear snapshot (simula el backup)
    staging_dir = tmp_path / "staging" / "20260918_080000"
    staging_dir.mkdir(parents=True)
    snap_path = staging_dir / "flight_account_001_xauusd.db"
    backup.snapshot_db_vacuum_into(str(source_db), str(snap_path))
    original_sha = backup.sha256_of_file(str(snap_path))

    # Paso 2: restaurar
    restore_dir = tmp_path / "restored"
    monkeypatch_staging = str(tmp_path / "staging")

    # Monkeypatch BACKUP_STAGING_ROOT temporalmente para el test
    original_staging = backup.BACKUP_STAGING_ROOT
    original_data_dir = backup.DATA_DIR
    try:
        backup.BACKUP_STAGING_ROOT = monkeypatch_staging
        backup.DATA_DIR = str(tmp_path / "real_data_dir")  # para que la validación no bloquee
        results = backup.restore_from_local("20260918_080000", str(restore_dir))
    finally:
        backup.BACKUP_STAGING_ROOT = original_staging
        backup.DATA_DIR = original_data_dir

    assert len(results) == 1
    r = results[0]
    assert r.ok is True
    assert r.integrity_ok is True
    assert r.sha256 == original_sha
    assert r.row_counts["unified_department"] == 2
    assert r.row_counts["tactical_audit"] == 3

    # Paso 3: la DB restaurada debe ser abruble y correcta
    restored_conn = sqlite3.connect(str(restore_dir / "flight_account_001_xauusd.db"))
    count = restored_conn.execute("SELECT COUNT(*) FROM tactical_audit").fetchone()[0]
    assert count == 3
    restored_conn.close()


# --------------------------------------------------------------------------
# Restore: roundtrip USB
# --------------------------------------------------------------------------
def test_restore_from_usb_roundtrip(tmp_path, monkeypatch):
    """Backup to USB → restore from USB → verify."""
    # Setup USB con un backup
    usb_dir = tmp_path / "usb"
    date_dir = usb_dir / "2026-09-18"
    date_dir.mkdir(parents=True)

    source_db = tmp_path / "flight_account_001_xauusd.db"
    conn = sqlite3.connect(str(source_db))
    conn.execute("CREATE TABLE unified_department (id INTEGER PRIMARY KEY, state TEXT)")
    conn.execute("CREATE TABLE tactical_audit (id INTEGER PRIMARY KEY, trade_id TEXT)")
    conn.execute("INSERT INTO unified_department (state) VALUES ('READY')")
    conn.commit()
    conn.close()

    snap = date_dir / "flight_account_001_xauusd.db"
    backup.snapshot_db_vacuum_into(str(source_db), str(snap))

    # Restore
    restore_dir = tmp_path / "restored"
    monkeypatch.setattr(backup, "USB_BACKUP_PATH", str(usb_dir))
    monkeypatch.setattr(backup, "DATA_DIR", str(tmp_path / "real_data"))
    results = backup.restore_from_usb("2026-09-18", str(restore_dir))

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].integrity_ok is True


# --------------------------------------------------------------------------
# Restore: roundtrip B2
# --------------------------------------------------------------------------
def test_restore_from_b2_roundtrip(tmp_path, b2_bucket, monkeypatch):
    """Upload to B2 → restore from B2 → verify."""
    source_db = tmp_path / "flight_account_001_xauusd.db"
    conn = sqlite3.connect(str(source_db))
    conn.execute("CREATE TABLE unified_department (id INTEGER PRIMARY KEY, state TEXT)")
    conn.execute("CREATE TABLE tactical_audit (id INTEGER PRIMARY KEY, trade_id TEXT)")
    conn.execute("INSERT INTO unified_department (state) VALUES ('COMPLETED')")
    conn.execute("INSERT INTO tactical_audit (trade_id) VALUES ('abc')")
    conn.commit()
    conn.close()

    snap = tmp_path / "snapshot.db"
    backup.snapshot_db_vacuum_into(str(source_db), str(snap))
    original_sha = backup.sha256_of_file(str(snap))

    # Upload
    object_key = "flight_account_001_xauusd/2026-09-18.db"
    backup.upload_to_b2(b2_bucket, str(snap), object_key)

    # Restore
    restore_dir = tmp_path / "restored"
    monkeypatch.setattr(backup, "DATA_DIR", str(tmp_path / "real_data"))
    results = backup.restore_from_b2("2026-09-18", str(restore_dir), bucket=b2_bucket)

    assert len(results) == 1
    r = results[0]
    assert r.ok is True
    assert r.integrity_ok is True
    assert r.sha256 == original_sha
    assert r.row_counts["unified_department"] == 1
    assert r.row_counts["tactical_audit"] == 1


# --------------------------------------------------------------------------
# Restore: flight_sessions.json
# --------------------------------------------------------------------------
def test_restore_flight_sessions_json_roundtrip(tmp_path, monkeypatch):
    """Backup flight_sessions.json → restore → verify JSON válido."""
    staging_dir = tmp_path / "staging" / "20260918_090000"
    staging_dir.mkdir(parents=True)

    sessions_data = {
        "000": {"name": "XAUUSDT.P", "db_name": "flight_account_001_xauusd.db"},
        "002": {"name": "BTCUSDT.P", "db_name": "flight_account_002_btcusdtp.db"},
    }
    sessions_copy = staging_dir / "flight_sessions.json"
    sessions_copy.write_text(json.dumps(sessions_data))

    restore_dir = tmp_path / "restored"
    monkeypatch.setattr(backup, "BACKUP_STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setattr(backup, "DATA_DIR", str(tmp_path / "real_data"))
    results = backup.restore_from_local("20260918_090000", str(restore_dir))

    json_results = [r for r in results if r.label == "flight_sessions.json"]
    assert len(json_results) == 1
    assert json_results[0].ok is True
    assert json_results[0].integrity_ok is True

    restored = json.loads((restore_dir / "flight_sessions.json").read_text())
    assert restored["000"]["name"] == "XAUUSDT.P"


# --------------------------------------------------------------------------
# list_local_backups
# --------------------------------------------------------------------------
def test_list_local_backups(tmp_path, monkeypatch):
    staging = tmp_path / "backups"
    run1 = staging / "20260918_080000"
    run2 = staging / "20260917_080000"
    run1.mkdir(parents=True)
    run2.mkdir(parents=True)
    (run1 / "account_001.db").write_bytes(b"fake db content")
    (run2 / "account_001.db").write_bytes(b"older backup")
    (staging / ".lock").write_text("ignore me")  # debe ignorar archivos con punto

    monkeypatch.setattr(backup, "BACKUP_STAGING_ROOT", str(staging))
    entries = backup.list_local_backups()

    assert len(entries) == 2
    assert entries[0]["timestamp"] == "20260917_080000"  # sorted
    assert entries[1]["timestamp"] == "20260918_080000"
    assert len(entries[1]["files"]) == 1
