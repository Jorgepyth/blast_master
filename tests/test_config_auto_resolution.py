"""
config/auto_resolution.py (T8, spec 002): constantes de negocio versionadas,
mapeos de cuentas/símbolos, códigos de motivo, y rutas de máquina resueltas
desde `.env`. No hay lógica de negocio que probar acá -- estos tests fijan los
valores y el contrato del módulo (RF: "siempre como rutas absolutas"), para que
un futuro cambio accidental de una constante no pase desapercibido.
"""
import datetime
import importlib
import os
import sys

import dotenv
import pytest

import config.auto_resolution as auto_resolution_config


def test_business_rule_constants():
    assert auto_resolution_config.MAX_HORIZON == 2160
    assert auto_resolution_config.ANCHOR_FALLBACK_MIN == 20
    assert auto_resolution_config.MARK_PRICE_TOLERANCE == pytest.approx(0.001)
    assert auto_resolution_config.REVERT_H == 24
    assert auto_resolution_config.EXPANSION_H == 24
    assert auto_resolution_config.EXPANSION_R == pytest.approx(0.5)
    assert auto_resolution_config.SWEEP_R == pytest.approx(1.0)
    assert auto_resolution_config.SWEEP_H == 48
    assert auto_resolution_config.OVERLAP_MIN_BARS == 10
    assert auto_resolution_config.S4_WINDOW_H == 48


def test_accounts_and_symbol_maps():
    assert auto_resolution_config.REAL_ACCOUNTS == {
        "000": "flight_account_001_xauusd.db",
        "001": "flight_account_000_us500.db",
        "002": "flight_account_002_btcusdtp.db",
        "003": "flight_account_003_us100.db",
    }
    assert auto_resolution_config.MT5_SYMBOL_MAP == {
        "XAUUSDT.P": "XAUUSD",
        "BTCUSDT.P": "BTCUSD",
        "US100": "USTEC",
        "US500": "US500",
    }


def test_p2_log_models_default_is_model_d_only():
    # N42: la lista arranca con solo D; sumar un modelo (p.ej. H) es agregar
    # una entrada acá, nunca tocar esta.
    assert auto_resolution_config.P2_LOG_MODELS == {"D": "2026-09-27"}


def test_p2_log_models_dates_are_valid_iso_dates():
    # T8: "cada fecha de P2_LOG_MODELS es una fecha ISO válida" -- lo que un
    # modelo nuevo (H) tendría que respetar al sumar su entrada.
    for name, date_str in auto_resolution_config.P2_LOG_MODELS.items():
        parsed = datetime.date.fromisoformat(date_str)  # ValueError si no es "YYYY-MM-DD"
        assert parsed.isoformat() == date_str, f"{name}: {date_str!r} no es ISO canónico"


def test_export_automation_defaults():
    # Apagado hasta que el spike (T22) lo confirme (N31).
    assert auto_resolution_config.AUTO_EXPORT is False
    assert auto_resolution_config.AUTO_EXPORT_WAIT_S == 30
    assert auto_resolution_config.EXPORT_TIMEOUT_S == 180
    assert auto_resolution_config.BROKER_DST_RULE == "us"
    assert auto_resolution_config.BROKER_DST_RULE_CHOICES == ("us", "eu", "none")


def test_reason_codes_match_error_classes_e1_to_e8():
    assert auto_resolution_config.REASON_EXPORT_FAILED == "export_failed"
    assert auto_resolution_config.REASON_CLOCK_MISALIGNED == "clock_misaligned"
    assert auto_resolution_config.REASON_CLOCK_UNVERIFIED == "clock_unverified"
    assert auto_resolution_config.REASON_PENDING_CANDLES == "pending_candles"
    assert auto_resolution_config.REASON_NO_HISTORY == "no_history"
    assert auto_resolution_config.REASON_NO_LEVELS == "no_levels"
    assert auto_resolution_config.REASON_AMBIGUOUS == "ambiguous"
    assert auto_resolution_config.REASON_NO_MT5_SYMBOL == "no_mt5_symbol"


def test_reason_templates_format_as_prefix_colon_value():
    assert auto_resolution_config.reason_insufficient_history("1W") == "insufficient_history:1W"
    assert auto_resolution_config.reason_unknown_model("H") == "unknown_model:H"
    assert auto_resolution_config.reason_timeframe_not_in_bank("2M") == "timeframe_not_in_bank:2M"
    assert auto_resolution_config.reason_model_recipe_changed("D") == "model_recipe_changed:D"


def test_resolution_time_source_values_match_plan_order():
    # plan.md:68-69, orden exacto documentado.
    assert auto_resolution_config.RESOLUTION_TIME_SOURCE_VALUES == (
        "candles", "corrected", "pending_candles", "no_history", "clock_unverified",
        "clock_misaligned", "ambiguous", "no_levels", "no_mt5_symbol", "open",
    )


def test_legacy_exports_dir_defaults_to_the_folder_that_contains_the_incoming_dir():
    assert os.path.isabs(auto_resolution_config.LEGACY_EXPORTS_DIR)
    assert auto_resolution_config.LEGACY_EXPORTS_DIR == os.path.dirname(auto_resolution_config.MT5_INCOMING_DIR)


def test_windows_paths_have_no_default(monkeypatch):
    # Sin default en el código: el usuario las carga en su `.env` (T22). Se reimporta el módulo sin las
    # variables y sin leer ningún `.env`, para no depender de la máquina: desde T22 el `.env` del checkout
    # principal sí las tiene, y con él cargado este test fallaba.
    for var in ("WINDOWS_PYTHON", "EXPORTER_WIN_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    sys.modules.pop("config.auto_resolution", None)
    try:
        reloaded = importlib.import_module("config.auto_resolution")
        assert reloaded.WINDOWS_PYTHON is None
        assert reloaded.EXPORTER_WIN_PATH is None
    finally:
        sys.modules["config.auto_resolution"] = auto_resolution_config


def test_paths_are_absolute_even_when_first_imported_from_tmp_path(tmp_path, monkeypatch):
    """T8: 'las rutas sean absolutas aunque se corra desde tmp_path'. Estas rutas
    no dependen de cwd (van ancladas a __file__, mismo patrón que
    tools/backup.py:38), así que siguen siendo absolutas -- y siguen apuntando
    al repo real, no a tmp_path -- incluso si el módulo se (re)importa estando
    chdir'ado a un tmp_path vacío y sin las variables de entorno seteadas."""
    monkeypatch.chdir(tmp_path)
    for var in ("ACCOUNTS_DATA_DIR", "CANDLE_BANK_DIR", "MT5_INCOMING_DIR", "LEGACY_EXPORTS_DIR"):
        monkeypatch.delenv(var, raising=False)

    sys.modules.pop("config.auto_resolution", None)
    try:
        reloaded = importlib.import_module("config.auto_resolution")
        for value in (reloaded.ROOT_DIR, reloaded.ACCOUNTS_DATA_DIR,
                      reloaded.CANDLE_BANK_DIR, reloaded.MT5_INCOMING_DIR, reloaded.LEGACY_EXPORTS_DIR):
            assert os.path.isabs(value), value
        # No se coló la ruta de tmp_path: siguen ancladas al repo real, no al cwd.
        assert not reloaded.CANDLE_BANK_DIR.startswith(str(tmp_path))
        assert reloaded.CANDLE_BANK_DIR == os.path.join(reloaded.ACCOUNTS_DATA_DIR, "candle_bank")
    finally:
        # Restaura el módulo ya importado a nivel de archivo (import normal,
        # sin el cwd/env temporales de este test) en vez de reimportarlo de
        # nuevo -- monkeypatch recién revierte cwd/env DESPUÉS de este finally.
        sys.modules["config.auto_resolution"] = auto_resolution_config


def test_broker_dst_rule_env_override(monkeypatch):
    monkeypatch.setenv("BROKER_DST_RULE", "eu")
    sys.modules.pop("config.auto_resolution", None)
    try:
        reloaded = importlib.import_module("config.auto_resolution")
        assert reloaded.BROKER_DST_RULE == "eu"
    finally:
        sys.modules["config.auto_resolution"] = auto_resolution_config


def test_broker_dst_rule_invalid_env_value_raises(monkeypatch):
    monkeypatch.setenv("BROKER_DST_RULE", "not-a-real-rule")
    sys.modules.pop("config.auto_resolution", None)
    try:
        with pytest.raises(ValueError):
            importlib.import_module("config.auto_resolution")
    finally:
        sys.modules["config.auto_resolution"] = auto_resolution_config
