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
import sys
from datetime import datetime, timedelta, timezone
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
    sys.modules["MetaTrader5"] = _fake_mt5

from windows_export.export_p2_ohlc import (  # noqa: E402
    GT_OFFSET_HOURS,
    MIN_BARS_PER_TF,
    BACKWARD_BUFFER_DAYS,
    BACKWARD_MARGIN,
    compute_backward_start,
    exclude_forming_bar,
    gt_naive_to_utc,
    utc_to_gt_naive,
)

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
