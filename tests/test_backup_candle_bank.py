"""
T58a (spec 002, N48): el banco de velas en el backup 3-2-1 de `tools/backup.py`. Un artefacto `candle_bank.tar.gz`
con los CSV de temporalidades y el `status.json` de cada símbolo, en los tres medios (local, USB y B2 con su
verificación SHA-256), y un restore que lo descomprime en `<destino>/candle_bank/` sin escribir nunca fuera del destino
ni en `.data/`. B2 con el `RawSimulator` de b2sdk, como tests/test_backup.py; todo en tmp_path.
"""
import io
import json
import os
import tarfile

import pytest

import tools.backup as backup
from tests.test_backup import b2_bucket, fake_usb_dir  # noqa: F401 -- fixtures

FILES = {"XAUUSD": ("1M.csv", "15M.csv", "4H.csv", "status.json"), "BTCUSD": ("1H.csv", "status.json")}


@pytest.fixture
def bank(tmp_path, monkeypatch):
    root = tmp_path / "candle_bank"
    for symbol, names in FILES.items():
        (root / symbol).mkdir(parents=True)
        for name in names:
            (root / symbol / name).write_text(f"{symbol}/{name}\n")
    (root / "XAUUSD" / ".lock").write_text("12345")               # candado de un export en curso
    (root / "XAUUSD" / ".1M.csv.tmp123").write_text("partial")    # temporal de una escritura atómica
    (root / "XAUUSD" / "1M.csv.tmp").write_text("partial")        # otro temporal, con nombre que empieza como un CSV
    (root / "XAUUSD" / "notes.txt").write_text("x")
    os.symlink(root / "XAUUSD" / "1M.csv", root / "BTCUSD" / "5M.csv")  # un enlace no entra
    (root / "README").write_text("x")                             # un archivo suelto en la raíz del banco
    monkeypatch.setattr(backup, "CANDLE_BANK_DIR", str(root))
    monkeypatch.setattr(backup, "DATA_DIR", str(tmp_path / "real_data"))
    return root


def _members(archive):
    with tarfile.open(archive, "r:gz") as tar:
        return sorted(member.name for member in tar.getmembers())


def test_the_archive_holds_only_timeframe_csvs_and_status_json_with_relative_paths(bank, tmp_path):
    archive = tmp_path / "out" / backup.CANDLE_BANK_ARCHIVE
    archive.parent.mkdir()
    assert backup.archive_candle_bank(str(bank), str(archive)) == 6
    assert _members(archive) == sorted(f"candle_bank/{symbol}/{name}" for symbol, names in FILES.items()
                                       for name in names)


def test_backup_candle_bank_goes_to_local_usb_and_b2_with_the_same_sha(bank, tmp_path, fake_usb_dir, b2_bucket,
                                                                       monkeypatch):
    monkeypatch.setattr(backup, "USB_BACKUP_PATH", str(fake_usb_dir))
    staging = tmp_path / "staging"
    staging.mkdir()

    result = backup.backup_candle_bank(str(staging), "2026-10-03", b2_bucket)

    assert result.label == "candle_bank" and result.any_failure is False and result.b2_verified_ok is True
    local = staging / "candle_bank.tar.gz"
    usb = fake_usb_dir / "2026-10-03" / "candle_bank.tar.gz"
    assert backup.sha256_of_file(str(usb)) == backup.sha256_of_file(str(local)) == result.local_sha256
    names = [version.file_name for version, _ in b2_bucket.ls(latest_only=True, recursive=True)]
    assert names == ["candle_bank/2026-10-03.tar.gz"]


def test_without_a_bank_there_is_no_artifact(tmp_path, monkeypatch, b2_bucket):
    monkeypatch.setattr(backup, "CANDLE_BANK_DIR", str(tmp_path / "missing"))
    assert backup.backup_candle_bank(str(tmp_path), "2026-10-03", b2_bucket) is None


def test_main_backup_includes_the_bank_as_one_more_artifact(bank, tmp_path, fake_usb_dir, b2_bucket, monkeypatch):
    sessions = tmp_path / "flight_sessions.json"
    sessions.write_text(json.dumps({}))
    for name, value in (("FLIGHT_SESSIONS_FILE", sessions), ("BACKUP_STAGING_ROOT", tmp_path / "bk"),
                        ("LOG_DIR", tmp_path / "logs"), ("USB_BACKUP_PATH", fake_usb_dir)):
        monkeypatch.setattr(backup, name, str(value))
    # Los valores por defecto de estas funciones se fijan al importar el módulo: se reemplazan las funciones.
    monkeypatch.setattr(backup, "load_active_accounts", lambda: [])
    monkeypatch.setattr(backup, "acquire_backup_lock", lambda: None)
    monkeypatch.setattr(backup, "release_backup_lock", lambda: None)
    monkeypatch.setattr(backup, "get_b2_bucket", lambda: b2_bucket)
    labels = []
    monkeypatch.setattr(backup, "print_summary", lambda results, log_path: labels.extend(r.label for r in results))

    backup.main_backup()

    assert labels == ["flight_sessions.json", "candle_bank"]


def _archive_in(directory):
    directory.mkdir(parents=True, exist_ok=True)
    return directory / backup.CANDLE_BANK_ARCHIVE


def test_restore_from_local_extracts_the_bank_into_the_target(bank, tmp_path, monkeypatch):
    run = tmp_path / "staging" / "20261003_080000"
    backup.archive_candle_bank(str(bank), str(_archive_in(run)))
    monkeypatch.setattr(backup, "BACKUP_STAGING_ROOT", str(tmp_path / "staging"))
    target = tmp_path / "restored"

    (result,) = backup.restore_from_local("20261003_080000", str(target))

    assert result.ok is True and result.row_counts == {"files": 6}
    for symbol, names in FILES.items():
        for name in names:
            assert (target / "candle_bank" / symbol / name).read_text() == f"{symbol}/{name}\n"


def test_restore_from_usb_extracts_the_bank(bank, tmp_path, monkeypatch):
    usb = tmp_path / "usb"
    backup.archive_candle_bank(str(bank), str(_archive_in(usb / "2026-10-03")))
    monkeypatch.setattr(backup, "USB_BACKUP_PATH", str(usb))
    (result,) = backup.restore_from_usb("2026-10-03", str(tmp_path / "restored"))
    assert result.ok is True and (tmp_path / "restored" / "candle_bank" / "BTCUSD" / "1H.csv").exists()


def test_restore_from_b2_keeps_the_tar_gz_name_and_extracts_the_bank(bank, tmp_path, b2_bucket):
    archive = _archive_in(tmp_path / "staging")
    backup.archive_candle_bank(str(bank), str(archive))
    backup.upload_to_b2(b2_bucket, str(archive), "candle_bank/2026-10-03.tar.gz")

    (result,) = backup.restore_from_b2("2026-10-03", str(tmp_path / "restored"), bucket=b2_bucket)

    assert result.label == "candle_bank.tar.gz" and result.ok is True
    assert (tmp_path / "restored" / "candle_bank" / "XAUUSD" / "status.json").exists()


def test_b2_listing_groups_the_bank_with_the_databases_of_the_same_date(bank, tmp_path, b2_bucket):
    archive = _archive_in(tmp_path / "staging")
    backup.archive_candle_bank(str(bank), str(archive))
    backup.upload_to_b2(b2_bucket, str(archive), "candle_bank/2026-10-03.tar.gz")
    backup.upload_to_b2(b2_bucket, str(archive), "flight_account_001_xauusd/2026-10-03.db")
    (entry,) = backup.list_b2_backups(bucket=b2_bucket)
    assert entry["date"] == "2026-10-03" and len(entry["files"]) == 2


@pytest.mark.parametrize("evil_name", ["../escaped.csv", "candle_bank/../../escaped.csv", "/tmp/escaped.csv",
                                       "candle_bank/XAUUSD/../../escaped.csv", "other/XAUUSD/1M.csv",
                                       "candle_bank/XAUUSD/evil.sh"])
def test_a_tampered_archive_is_rejected_and_nothing_is_written(tmp_path, monkeypatch, evil_name):
    run = tmp_path / "staging" / "20261003_080000"
    archive = _archive_in(run)
    with tarfile.open(archive, "w:gz") as tar:
        good = b"time,open,high,low,close\n"
        info = tarfile.TarInfo("candle_bank/XAUUSD/1M.csv")
        info.size = len(good)
        tar.addfile(info, io.BytesIO(good))
        info = tarfile.TarInfo(evil_name)
        info.size = 4
        tar.addfile(info, io.BytesIO(b"evil"))
    monkeypatch.setattr(backup, "BACKUP_STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setattr(backup, "DATA_DIR", str(tmp_path / "real_data"))
    target = tmp_path / "deep" / "restored"

    (result,) = backup.restore_from_local("20261003_080000", str(target))

    assert result.ok is False and result.errors
    assert not (target / "candle_bank").exists()  # ni siquiera los archivos buenos: todo o nada
    assert not any(p.name == "escaped.csv" for p in tmp_path.rglob("*"))


def test_a_link_inside_the_archive_is_rejected(tmp_path, monkeypatch):
    run = tmp_path / "staging" / "20261003_080000"
    archive = _archive_in(run)
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("candle_bank/XAUUSD/1M.csv")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    monkeypatch.setattr(backup, "BACKUP_STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setattr(backup, "DATA_DIR", str(tmp_path / "real_data"))
    (result,) = backup.restore_from_local("20261003_080000", str(tmp_path / "restored"))
    assert result.ok is False and not (tmp_path / "restored" / "candle_bank").exists()
