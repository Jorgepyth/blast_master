"""
tests/test_export_p2_ohlc.py — P2_systematic: tests unitarios sobre las
funciones puras de windows_export/export_p2_ohlc.py (conversión de zona
horaria, candado anti-repainting, cómputo de rango de fechas). NO prueba
export_timeframe()/main() -- esas SÍ llaman a mt5.copy_rates_range()
directo y solo se pueden validar de verdad con el checklist manual en
Windows (jupyter/p2_systematic_task_plan.md).

MetaTrader5 no está instalado en este entorno Linux/WSL2 a propósito (es
Windows-only, ver CLAUDE.md, sección "Acceso a APIs de broker") -- se
inyecta un stand-in en sys.modules ANTES de importar el módulo bajo
prueba, para que el `import MetaTrader5 as mt5` a nivel de módulo no
falle. Los valores de TIMEFRAME_* son enteros arbitrarios (no las
codificaciones reales de MT5) -- ningún test de este archivo depende de
su valor numérico real, solo de que TIMEFRAME_MAP se construya sin
excepción.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

if "MetaTrader5" not in sys.modules:
    _fake_mt5 = MagicMock()
    _fake_mt5.TIMEFRAME_W1 = 1
    _fake_mt5.TIMEFRAME_D1 = 2
    _fake_mt5.TIMEFRAME_H12 = 3
    _fake_mt5.TIMEFRAME_H4 = 4
    _fake_mt5.TIMEFRAME_H1 = 5
    _fake_mt5.TIMEFRAME_M30 = 6
    _fake_mt5.TIMEFRAME_M15 = 7
    _fake_mt5.TIMEFRAME_M5 = 8
    _fake_mt5.TIMEFRAME_M1 = 9
    sys.modules["MetaTrader5"] = _fake_mt5

from windows_export.export_p2_ohlc import (  # noqa: E402
    ALL_EXPORT_TIMEFRAMES,
    GT_OFFSET_HOURS,
    MIN_BARS_PER_TF,
    BACKWARD_BUFFER_DAYS,
    BACKWARD_MARGIN,
    TIMEFRAME_MAP,
    TIMEFRAME_MINUTES,
    atomic_write_csv,
    base_offset_from_current,
    compute_backward_start,
    dst_masks,
    dst_transition_dates,
    exclude_forming_bar,
    export_timeframe,
    gt_naive_to_utc,
    main,
    MAX_BARS_PER_CALL,
    fetch_rates,
    make_run_dir,
    parse_timeframes_arg,
    split_range,
    server_time_to_utc,
    server_time_to_utc_dst,
    utc_to_gt_naive,
)
import windows_export.export_p2_ohlc as exporter_module  # noqa: E402

UTC = timezone.utc


def test_utc_to_gt_naive_shifts_exactly_six_hours_and_drops_tzinfo():
    series = pd.to_datetime(pd.Series(["2026-06-01 12:00:00", "2026-06-01 18:30:00"]), utc=True)
    result = utc_to_gt_naive(series)

    assert result.dt.tz is None
    assert result.iloc[0] == pd.Timestamp("2026-06-01 06:00:00")
    assert result.iloc[1] == pd.Timestamp("2026-06-01 12:30:00")


def test_gt_naive_to_utc_shifts_forward_six_hours():
    dt_gt = datetime(2026, 6, 1, 6, 0, 0)
    result = gt_naive_to_utc(dt_gt)

    assert result.tzinfo is UTC
    assert result == datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def test_gt_utc_roundtrip_is_exact():
    dt_gt = datetime(2026, 6, 1, 6, 0, 0)
    back_to_utc = gt_naive_to_utc(dt_gt)

    as_series = pd.Series([back_to_utc])
    as_series_utc = pd.to_datetime(as_series, utc=True)
    roundtripped = utc_to_gt_naive(as_series_utc)

    assert roundtripped.iloc[0] == pd.Timestamp(dt_gt)


def test_exclude_forming_bar_excludes_bar_closing_after_now():
    now_utc = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    df = pd.DataFrame({
        "time": pd.to_datetime([
            "2026-06-01 10:30:00",  # cierra 11:00 -- antes de now, incluida
            "2026-06-01 11:30:00",  # cierra 12:00 -- exactamente now, incluida (<=)
            "2026-06-01 11:31:00",  # cierra 12:01 -- después de now, EXCLUIDA
        ], utc=True),
    })

    result = exclude_forming_bar(df, timeframe_minutes=30, now_utc=now_utc)

    assert len(result) == 2
    assert result["time"].iloc[-1] == pd.Timestamp("2026-06-01 11:30:00", tz="UTC")


def test_exclude_forming_bar_empty_input_stays_empty():
    df = pd.DataFrame({"time": pd.to_datetime([], utc=True)})
    result = exclude_forming_bar(df, timeframe_minutes=60, now_utc=datetime.now(UTC))
    assert result.empty


@pytest.mark.parametrize("timeframe,expected_unit_seconds", [
    ("1H", 3600),
    ("4H", 4 * 3600),
    ("12H", 12 * 3600),
    ("1D", 24 * 3600),
    ("1W", 7 * 24 * 3600),
    ("5M", 5 * 60),   # T17 (spec 002, RF-15): banco de velas nuevo
    ("1M", 1 * 60),   # T17 (spec 002, RF-15): banco de velas nuevo
])
def test_compute_backward_start_applies_min_bars_with_backward_margin(timeframe, expected_unit_seconds):
    min_anchor = datetime(2026, 5, 18, 12, 15, 0)
    start = compute_backward_start(min_anchor, timeframe, min_bars=MIN_BARS_PER_TF)

    expected_span = (timedelta(seconds=expected_unit_seconds * MIN_BARS_PER_TF) * BACKWARD_MARGIN
                     + timedelta(days=BACKWARD_BUFFER_DAYS))
    expected_start = min_anchor - expected_span

    assert start == expected_start
    # Sanity check independiente del valor exacto: siempre queda estrictamente
    # antes del anchor, con más margen que el mínimo sin el 25% extra.
    assert start < min_anchor - timedelta(seconds=expected_unit_seconds * MIN_BARS_PER_TF)


def test_compute_backward_start_unknown_timeframe_raises_keyerror():
    with pytest.raises(KeyError):
        compute_backward_start(datetime(2026, 1, 1), "2H")


def test_gt_offset_is_six_hours_no_dst():
    # Constante fija -- Guatemala no observa horario de verano, confirmado
    # en el plan de esta sesión. Si esto cambia alguna vez, es una decisión
    # explícita, no un valor a inferir.
    assert GT_OFFSET_HOURS == 6


# --------------------------------------------------------------------------
# Reloj del servidor (bug del 2026-09-22: el CSV salía corrido +3h)
# --------------------------------------------------------------------------

from windows_export.export_p2_ohlc import (  # noqa: E402
    gt_naive_to_server,
    infer_server_utc_offset,
    server_time_to_utc,
)


def _server_epoch(wall_clock: datetime) -> int:
    """Como MT5 codifica una vela: el reloj de pared del servidor, leído como si fuera UTC."""
    return int(wall_clock.replace(tzinfo=timezone.utc).timestamp())


def test_server_time_to_utc_resta_el_offset_del_servidor():
    epoch = pd.Series([_server_epoch(datetime(2026, 6, 10, 16, 0))])
    out = server_time_to_utc(epoch, 3)
    assert out.iloc[0] == pd.Timestamp("2026-06-10 13:00", tz="UTC")


def test_caso_real_entrada_gt_0700_cae_en_la_vela_servidor_1600():
    """
    Evidencia que destapó el bug: con el servidor en UTC+3, una entrada a las
    07:00 GT (13:00 UTC) pertenece a la vela que el servidor etiqueta 16:00.
    La versión vieja la dejaba en 10:00 GT, 3h tarde.
    """
    epoch = pd.Series([_server_epoch(datetime(2026, 6, 10, 16, 0))])
    gt = utc_to_gt_naive(server_time_to_utc(epoch, 3))
    assert gt.iloc[0] == pd.Timestamp("2026-06-10 07:00")
    viejo = utc_to_gt_naive(pd.to_datetime(epoch, unit="s", utc=True))
    assert viejo.iloc[0] == pd.Timestamp("2026-06-10 10:00"), "así quedaba con el bug"


def test_offset_cero_equivale_al_comportamiento_utc_puro():
    epoch = pd.Series([_server_epoch(datetime(2026, 6, 10, 16, 0))])
    assert server_time_to_utc(epoch, 0).iloc[0] == pd.to_datetime(epoch, unit="s", utc=True).iloc[0]


def test_gt_naive_to_server_es_inverso_de_la_lectura():
    dt_gt = datetime(2026, 6, 10, 7, 0)
    srv = gt_naive_to_server(dt_gt, 3)
    back = utc_to_gt_naive(server_time_to_utc(pd.Series([int(srv.timestamp())]), 3))
    assert back.iloc[0] == pd.Timestamp(dt_gt)


def test_infer_offset_redondea_a_la_hora_con_tick_fresco():
    now = 1_790_000_000.0
    assert infer_server_utc_offset(now + 3 * 3600 + 20, now) == 3
    assert infer_server_utc_offset(now + 2 * 3600 - 30, now) == 2
    assert infer_server_utc_offset(now - 5 * 3600, now) == -5


def test_infer_offset_falla_con_mercado_cerrado():
    """Viernes 17:00 -> domingo: el tick tiene horas, no mide el offset."""
    now = 1_790_000_000.0
    with pytest.raises(RuntimeError, match="mercado está cerrado"):
        infer_server_utc_offset(now + 3 * 3600 - 40 * 60, now)


def test_infer_offset_rechaza_valores_absurdos():
    now = 1_790_000_000.0
    with pytest.raises(RuntimeError, match="fuera de rango"):
        infer_server_utc_offset(now + 20 * 3600, now)


# --- T17 (spec 002, RF-15): 5M/1M en el mapa de TF y --timeframes -----------

def test_timeframe_map_has_5m_and_1m():
    assert "5M" in TIMEFRAME_MAP
    assert "1M" in TIMEFRAME_MAP


def test_timeframe_minutes_durations_for_5m_and_1m():
    assert TIMEFRAME_MINUTES["5M"] == 5
    assert TIMEFRAME_MINUTES["1M"] == 1


def test_all_export_timeframes_has_the_nine_in_order_ending_with_5m_1m():
    assert ALL_EXPORT_TIMEFRAMES == ("1W", "1D", "12H", "4H", "1H", "30M", "15M", "5M", "1M")
    # Todo lo que aparece en el mapa de TF tiene que estar en la lista por defecto, y viceversa.
    assert set(ALL_EXPORT_TIMEFRAMES) == set(TIMEFRAME_MAP)


def test_parse_timeframes_arg_default_returns_all_nine():
    assert parse_timeframes_arg(None) == ALL_EXPORT_TIMEFRAMES
    assert parse_timeframes_arg("") == ALL_EXPORT_TIMEFRAMES


def test_parse_timeframes_arg_parses_comma_separated_list_in_order():
    assert parse_timeframes_arg("1H,30M,15M") == ("1H", "30M", "15M")


def test_parse_timeframes_arg_strips_whitespace_and_drops_empty_entries():
    assert parse_timeframes_arg(" 1H , 5M ,,1M ") == ("1H", "5M", "1M")


def test_parse_timeframes_arg_accepts_5m_and_1m():
    assert parse_timeframes_arg("5M,1M") == ("5M", "1M")


def test_parse_timeframes_arg_rejects_unknown_timeframe_with_the_list_of_valid_ones():
    with pytest.raises(ValueError, match="2H") as exc_info:
        parse_timeframes_arg("1H,2H")
    assert "1M" in str(exc_info.value)  # el mensaje lista las válidas


# --- T18 (spec 002, RF-15b, N34): conversión vela por vela según --dst-rule ---

def _epoch(naive_server_label: str) -> int:
    """Epoch de MT5: el reloj del servidor codificado como si fuera UTC."""
    return int(pd.Timestamp(naive_server_label, tz="UTC").timestamp())


def test_dst_transition_dates_2026():
    assert dst_transition_dates(2026, "us") == (date(2026, 3, 8), date(2026, 11, 1))
    assert dst_transition_dates(2026, "eu") == (date(2026, 3, 29), date(2026, 10, 25))


def test_dst_transition_dates_unknown_rule_raises():
    with pytest.raises(ValueError, match="desconocida"):
        dst_transition_dates(2026, "asia")


@pytest.mark.parametrize("rule", ["us", "eu"])
def test_exporter_dst_rules_agree_with_candle_bank_on_every_day_of_2026_and_2027(rule):
    """El exportador es standalone y duplica las fechas de cambio de
    tools/candle_bank.py -- este test evita que las dos diverjan en silencio."""
    from tools.candle_bank import _dst_active_on

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(730)]
    noon = pd.Series([pd.Timestamp(d) + pd.Timedelta(hours=12) for d in days])
    is_dst, ambiguous = dst_masks(noon, rule)
    assert not ambiguous.any()  # a mediodía nunca cae en la hora del cambio
    assert list(is_dst) == [_dst_active_on(d, rule) for d in days]


def test_dst_masks_none_rule_is_all_false():
    times = pd.Series(pd.to_datetime(["2026-01-15 10:00", "2026-07-15 10:00", "2026-03-08 02:30"]))
    is_dst, ambiguous = dst_masks(times, "none")
    assert not is_dst.any() and not ambiguous.any()


def test_dst_masks_marks_the_transition_hour_as_ambiguous_on_both_transitions():
    times = pd.Series(pd.to_datetime([
        "2026-03-08 01:59", "2026-03-08 02:00", "2026-03-08 02:59", "2026-03-08 03:00",  # inicio (us)
        "2026-11-01 01:59", "2026-11-01 02:00", "2026-11-01 02:59", "2026-11-01 03:00",  # fin (us)
    ]))
    is_dst, ambiguous = dst_masks(times, "us")
    assert list(ambiguous) == [False, True, True, False, False, True, True, False]
    assert list(is_dst) == [False, False, False, True, True, False, False, False]


def test_base_offset_from_current_subtracts_one_hour_when_now_is_in_dst():
    # Septiembre, servidor en UTC+3 (verano) -> base de invierno UTC+2.
    assert base_offset_from_current(3, datetime(2026, 9, 15, 12), "us") == 2
    # Enero, servidor en UTC+2 (invierno) -> ya es la base.
    assert base_offset_from_current(2, datetime(2026, 1, 15, 12), "us") == 2


def test_base_offset_from_current_none_rule_returns_the_offset_untouched():
    assert base_offset_from_current(3, datetime(2026, 9, 15, 12), "none") == 3


def test_base_offset_from_current_fails_inside_the_transition_hour():
    with pytest.raises(ValueError, match="hora del cambio"):
        base_offset_from_current(3, datetime(2026, 3, 8, 2, 30), "us")


def test_january_and_july_bars_exported_in_september_get_the_right_offset_with_us_rule():
    """Servidor en UTC+3 en septiembre (verano) => base UTC+2. Una vela de
    enero (invierno) usa +2; una de julio (verano) usa +3."""
    base = base_offset_from_current(3, datetime(2026, 9, 15, 12), "us")
    epochs = pd.Series([_epoch("2026-01-15 10:00"), _epoch("2026-07-15 10:00")])

    utc, ambiguous = server_time_to_utc_dst(epochs, base, "us")

    assert not ambiguous.any()
    assert utc.iloc[0] == pd.Timestamp("2026-01-15 08:00", tz="UTC")  # 10:00 servidor - 2h
    assert utc.iloc[1] == pd.Timestamp("2026-07-15 07:00", tz="UTC")  # 10:00 servidor - 3h


def test_none_rule_gives_exactly_what_the_single_offset_conversion_gave_before():
    epochs = pd.Series([_epoch("2026-01-15 10:00"), _epoch("2026-07-15 10:00"), _epoch("2026-03-08 02:30")])

    utc, ambiguous = server_time_to_utc_dst(epochs, 3, "none")

    pd.testing.assert_series_equal(utc, server_time_to_utc(epochs, 3))
    assert not ambiguous.any()


def _fake_rates(labels):
    return [{"time": _epoch(label), "open": 1.0 + i, "high": 2.0 + i, "low": 0.5 + i, "close": 1.5 + i}
            for i, label in enumerate(labels)]


def _run_export(monkeypatch, tmp_path, labels, base_offset, dst_rule):
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", lambda *a, **k: _fake_rates(labels))
    now = datetime.now(UTC)
    return export_timeframe("XAUUSD", "1H", now, now, tmp_path, base_offset, dst_rule)


def test_export_timeframe_us_rule_converts_per_bar_and_reports_the_discarded_ones(monkeypatch, tmp_path, capsys):
    labels = ["2026-01-15 10:00", "2026-07-15 10:00", "2026-03-08 02:30"]  # invierno, verano, hora del cambio

    result = _run_export(monkeypatch, tmp_path, labels, base_offset=2, dst_rule="us")

    assert result.rows == 2
    assert result.discarded_dst == 1
    assert "1 vela(s)" in capsys.readouterr().err  # se informa cuántas se descartaron
    written = pd.read_csv(tmp_path / "1H.csv", parse_dates=["time"]).sort_values("time")
    # GT = UTC - 6h: enero 10:00 - 2h = 08:00 UTC = 02:00 GT; julio 10:00 - 3h = 07:00 UTC = 01:00 GT.
    assert list(written["time"]) == [pd.Timestamp("2026-01-15 02:00"), pd.Timestamp("2026-07-15 01:00")]


def test_export_timeframe_none_rule_matches_todays_single_offset_and_discards_nothing(monkeypatch, tmp_path):
    labels = ["2026-01-15 10:00", "2026-07-15 10:00", "2026-03-08 02:30"]

    result = _run_export(monkeypatch, tmp_path, labels, base_offset=3, dst_rule="none")

    assert result.rows == 3
    assert result.discarded_dst == 0
    written = pd.read_csv(tmp_path / "1H.csv", parse_dates=["time"]).sort_values("time")
    # Un solo offset (+3) para todo: enero y julio quedan a la misma hora GT (01:00), enero 1h corrido.
    assert written["time"].iloc[0] == pd.Timestamp("2026-01-15 01:00")
    assert written["time"].iloc[2] == pd.Timestamp("2026-07-15 01:00")


# --- T19 (spec 002, RF-15, plan T2): directorio por corrida y escritura atómica ---

def _frame(closes):
    return pd.DataFrame({"time": pd.date_range("2026-01-15", periods=len(closes), freq="h"),
                         "open": closes, "high": closes, "low": closes, "close": closes})


def _tmp_leftovers(directory):
    return [f for f in os.listdir(directory) if f.startswith(".tmp_export_")]


def test_atomic_write_csv_writes_the_file_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "1H.csv"
    atomic_write_csv(_frame([1.0, 2.0]), target)
    assert list(pd.read_csv(target)["close"]) == [1.0, 2.0]
    assert _tmp_leftovers(tmp_path) == []


def test_atomic_write_csv_creates_missing_parent_directories(tmp_path):
    target = tmp_path / "a" / "b" / "1H.csv"
    atomic_write_csv(_frame([1.0]), target)
    assert target.exists()


def test_atomic_write_csv_failure_mid_write_leaves_the_existing_file_byte_for_byte(tmp_path, monkeypatch):
    target = tmp_path / "1H.csv"
    atomic_write_csv(_frame([1.0, 2.0]), target)
    before = target.read_bytes()

    def _boom(self, *a, **kw):
        raise RuntimeError("disco lleno (simulado)")

    monkeypatch.setattr(pd.DataFrame, "to_csv", _boom)
    with pytest.raises(RuntimeError, match="disco lleno"):
        atomic_write_csv(_frame([9.0, 9.0, 9.0]), target)

    assert target.read_bytes() == before
    assert _tmp_leftovers(tmp_path) == []


def test_atomic_write_csv_failure_on_a_new_file_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(pd.DataFrame, "to_csv", lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        atomic_write_csv(_frame([1.0]), tmp_path / "1H.csv")
    assert os.listdir(tmp_path) == []


def test_make_run_dir_builds_symbol_and_run_id_path(tmp_path):
    now = datetime(2026, 10, 5, 14, 20, 11, tzinfo=UTC)
    run_id, run_dir = make_run_dir(tmp_path, "XAUUSD", now)
    assert run_id == "20261005T142011"
    assert run_dir == tmp_path / "XAUUSD" / "20261005T142011"
    assert run_dir.is_dir()


def test_make_run_dir_two_runs_in_the_same_second_get_different_directories(tmp_path):
    now = datetime(2026, 10, 5, 14, 20, 11, tzinfo=UTC)
    first_id, first_dir = make_run_dir(tmp_path, "XAUUSD", now)
    second_id, second_dir = make_run_dir(tmp_path, "XAUUSD", now)
    third_id, third_dir = make_run_dir(tmp_path, "XAUUSD", now)

    assert (first_id, second_id, third_id) == ("20261005T142011", "20261005T142011-1", "20261005T142011-2")
    assert len({first_dir, second_dir, third_dir}) == 3
    assert all(d.is_dir() for d in (first_dir, second_dir, third_dir))
    assert sorted([third_id, first_id, second_id]) == [first_id, second_id, third_id]  # orden alfabético == cronológico


def test_make_run_dir_symbols_do_not_share_a_directory(tmp_path):
    now = datetime(2026, 10, 5, 14, 20, 11, tzinfo=UTC)
    _, xau = make_run_dir(tmp_path, "XAUUSD", now)
    _, btc = make_run_dir(tmp_path, "BTCUSD", now)
    assert xau != btc


def _main_args(out_dir, *extra):
    return ["--symbol", "XAUUSD", "--out-dir", str(out_dir), "--min-anchor", "2026-05-18 12:15:00",
            "--max-anchor", "2026-05-19 12:15:00", "--server-utc-offset", "2", "--timeframes", "1H", *extra]


@pytest.fixture
def mt5_ready(monkeypatch):
    monkeypatch.setattr(exporter_module.mt5, "initialize", lambda *a, **k: True)


def _run_dir_from(capsys):
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("RUN_DIR: ")]
    assert len(lines) == 1
    return lines[0][len("RUN_DIR: "):]


def test_main_per_run_dir_writes_under_symbol_and_run_id_and_prints_the_path(mt5_ready, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", lambda *a, **k: _fake_rates(["2026-01-15 10:00"]))

    assert main(_main_args(tmp_path, "--per-run-dir")) == 0

    run_dir = _run_dir_from(capsys)
    assert os.path.dirname(os.path.dirname(run_dir)) == str(tmp_path)
    assert os.path.basename(os.path.dirname(run_dir)) == "XAUUSD"
    assert os.path.exists(os.path.join(run_dir, "1H.csv"))
    assert not (tmp_path / "1H.csv").exists()  # nada directo en --out-dir


def test_main_without_per_run_dir_keeps_writing_straight_into_out_dir(mt5_ready, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", lambda *a, **k: _fake_rates(["2026-01-15 10:00"]))

    assert main(_main_args(tmp_path)) == 0

    assert (tmp_path / "1H.csv").exists()
    assert "RUN_DIR" not in capsys.readouterr().out
    assert os.listdir(tmp_path) == ["1H.csv"]


def test_two_runs_do_not_overwrite_each_other(mt5_ready, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", lambda *a, **k: _fake_rates(["2026-01-15 10:00"]))
    assert main(_main_args(tmp_path, "--per-run-dir")) == 0
    first_dir = _run_dir_from(capsys)
    first_bytes = open(os.path.join(first_dir, "1H.csv"), "rb").read()

    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range",
                        lambda *a, **k: _fake_rates(["2026-01-16 10:00", "2026-01-16 11:00"]))
    assert main(_main_args(tmp_path, "--per-run-dir")) == 0
    second_dir = _run_dir_from(capsys)

    assert first_dir != second_dir
    assert open(os.path.join(first_dir, "1H.csv"), "rb").read() == first_bytes  # la primera quedó intacta
    assert len(pd.read_csv(os.path.join(second_dir, "1H.csv"))) == 2
    assert len(os.listdir(tmp_path / "XAUUSD")) == 2


def test_a_timeframe_that_fails_mid_run_is_skipped_and_reported_and_the_rest_is_kept(
        mt5_ready, monkeypatch, tmp_path, capsys):
    # Corrida A completa.
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", lambda *a, **k: _fake_rates(["2026-01-15 10:00"]))
    assert main(_main_args(tmp_path, "--per-run-dir")) == 0
    dir_a = _run_dir_from(capsys)
    a_bytes = open(os.path.join(dir_a, "1H.csv"), "rb").read()

    # Corrida B: 1H sale bien, la segunda TF (30M) es rechazada por MT5 (RF-15: se informa y se sigue).
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return _fake_rates(["2026-01-17 10:00"]) if calls["n"] == 1 else None  # None => MT5 rechazó el rango

    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", flaky)
    args_b = _main_args(tmp_path, "--per-run-dir")
    args_b[args_b.index("--timeframes") + 1] = "1H,30M"

    assert main(args_b) == 0  # 1H se exportó: la corrida sirve

    captured = capsys.readouterr()
    dir_b = [l for l in captured.out.splitlines() if l.startswith("RUN_DIR: ")][0][len("RUN_DIR: "):]
    assert "SKIPPED_TF: 30M" in captured.out
    assert "30M no se pudo exportar, se sigue con las demás" in captured.err
    assert dir_b != dir_a
    assert open(os.path.join(dir_a, "1H.csv"), "rb").read() == a_bytes  # la corrida A ni se tocó
    assert sorted(os.listdir(dir_b)) == ["1H.csv"]  # 1H completo; 30M no existe, ni a medias, ni un temporal
    assert len(pd.read_csv(os.path.join(dir_b, "1H.csv"))) == 1


def test_when_every_timeframe_fails_the_exit_code_is_1(mt5_ready, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", lambda *a, **k: None)
    args = _main_args(tmp_path)
    args[args.index("--timeframes") + 1] = "1H,5M"
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "SKIPPED_TF: 1H" in out and "SKIPPED_TF: 5M" in out


# --- Spike (2026-09-29): MT5 rechaza rangos de ~60 mil barras o más ---------------

def test_split_range_short_range_is_a_single_chunk():
    a, b = datetime(2026, 9, 1), datetime(2026, 9, 2)
    assert split_range(a, b, timeframe_minutes=60) == [(a, b)]


def test_split_range_long_range_is_contiguous_non_overlapping_and_capped():
    a, b = datetime(2026, 5, 10), datetime(2026, 9, 29)  # el rango real del 1M: ~142 días
    chunks = split_range(a, b, timeframe_minutes=1)

    assert len(chunks) > 1
    assert chunks[0][0] == a and chunks[-1][1] == b
    for (start, end), (next_start, _) in zip(chunks, chunks[1:]):
        assert next_start == end + timedelta(seconds=1)  # sin huecos ni solapes
    for start, end in chunks:
        assert (end - start) < timedelta(minutes=MAX_BARS_PER_CALL)  # ningún tramo supera el tope


def test_split_range_a_single_instant_is_one_chunk():
    t = datetime(2026, 9, 29, 12)
    assert split_range(t, t, timeframe_minutes=1) == [(t, t)]


def _limited_mt5(monkeypatch, limit_bars, log):
    """Un MT5 que, como el real, devuelve None si el rango pedido implica más de `limit_bars` velas de 1M."""
    def copy_rates_range(symbol, tf, a, b):
        log.append((a, b))
        minutes = (b - a).total_seconds() / 60
        if minutes > limit_bars:
            return None  # (-2, 'Terminal: Invalid params')
        first = int(pd.Timestamp(a).timestamp() // 60 * 60)
        last = int(pd.Timestamp(b).timestamp() // 60 * 60)
        return [{"time": t, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5} for t in range(first, last + 1, 60)]
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", copy_rates_range)


def test_fetch_rates_survives_the_mt5_range_limit_by_chunking_and_returns_every_candle_once(monkeypatch):
    calls = []
    _limited_mt5(monkeypatch, limit_bars=60_000, log=calls)
    a = datetime(2026, 8, 1, tzinfo=UTC)
    b = a + timedelta(days=150)  # 216 000 velas de 1M: un solo pedido fallaría

    df = fetch_rates("XAUUSD", "1M", a, b)

    assert len(calls) > 1
    assert len(df) == 150 * 24 * 60 + 1  # todas las velas, ni una menos ni repetida en los bordes de tramo
    assert df["time"].is_monotonic_increasing and df["time"].is_unique


def test_the_same_range_without_chunking_is_rejected_by_the_emulated_mt5(monkeypatch):
    """Sanity del emulador: sin partir el rango, este MT5 falso rechaza el pedido, como el real."""
    _limited_mt5(monkeypatch, limit_bars=60_000, log=[])
    a = datetime(2026, 8, 1, tzinfo=UTC)
    assert exporter_module.mt5.copy_rates_range("XAUUSD", 9, a, a + timedelta(days=150)) is None


def test_fetch_rates_a_chunk_rejected_by_mt5_raises(monkeypatch):
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="copy_rates_range devolvió None"):
        fetch_rates("XAUUSD", "1M", datetime(2026, 9, 1), datetime(2026, 9, 2))


def test_fetch_rates_without_any_candle_returns_an_empty_frame(monkeypatch):
    monkeypatch.setattr(exporter_module.mt5, "copy_rates_range", lambda *a, **k: [])
    assert fetch_rates("XAUUSD", "1M", datetime(2026, 9, 1), datetime(2026, 9, 2)).empty
