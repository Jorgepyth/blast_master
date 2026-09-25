"""
Regresión del look-ahead corregido el 2026-09-23 y del ancla a la hora del análisis.

`time` en los CSV de MT5 es la hora de APERTURA de la vela. Antes el proveedor
filtraba `time < as_of`, así que la vela en formación entraba con su cierre,
máximo y mínimo finales, que ocurren después del ancla (en 1W, un análisis del
miércoles veía el cierre del viernes). Ahora solo cuentan velas cerradas:
`time + duración(TF) <= as_of`.
"""
from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.test_p2_pipeline import _exec, _trade
from tools.database import Base
from tools.p2_backtest import (
    ANALYSIS_ANCHOR_LEAD,
    ANCHOR_MODE_ANALYSIS,
    CsvOHLCProvider,
    assemble_p2_systematic_rows,
)

DAY = datetime(2026, 9, 10)


def _write(tmp_path, tf, start, step, closes):
    times = [start + i * step for i in range(len(closes))]
    pd.DataFrame({"time": times, "open": closes, "high": [c + 1 for c in closes],
                  "low": [c - 1 for c in closes], "close": closes}).to_csv(tmp_path / f"{tf}.csv", index=False)


@pytest.fixture
def provider(tmp_path):
    # 1H: 00:00 .. 10:00, cierre = 100 + hora. 15M: 00:00 .. 09:45.
    _write(tmp_path, "1H", DAY, timedelta(hours=1), [100.0 + h for h in range(11)])
    _write(tmp_path, "15M", DAY, timedelta(minutes=15), [200.0 + i for i in range(40)])
    # 1W: una vela por semana, la última abre el sábado 2026-09-05 y cierra el 12.
    _write(tmp_path, "1W", datetime(2026, 8, 1, 15), timedelta(days=7), [10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    return CsvOHLCProvider(str(tmp_path))


def test_la_vela_en_formacion_no_entra_en_el_snapshot(provider):
    # A las 06:15 la vela de 1H que abrió a las 06:00 sigue abierta: la última cerrada es la de las 05:00.
    snap = provider.get_indicator_snapshot("1H", DAY.replace(hour=6, minute=15))
    assert snap.bars_available == 6
    assert snap.price == pytest.approx(105.0)


def test_una_vela_que_cierra_justo_en_el_ancla_cuenta_como_cerrada(provider):
    snap = provider.get_indicator_snapshot("1H", DAY.replace(hour=6))
    assert snap.price == pytest.approx(105.0)
    assert provider.get_indicator_snapshot("1H", DAY.replace(hour=6, minute=1)).price == pytest.approx(105.0)


def test_cambiar_la_vela_en_formacion_no_cambia_el_resultado(provider, tmp_path):
    as_of = DAY.replace(hour=6, minute=15)
    antes = provider.get_indicator_snapshot("1H", as_of)
    closes = [100.0 + h for h in range(11)]
    closes[6] = 9999.0   # la vela de las 06:00, todavía abierta a las 06:15
    _write(tmp_path, "1H", DAY, timedelta(hours=1), closes)
    despues = CsvOHLCProvider(str(tmp_path)).get_indicator_snapshot("1H", as_of)
    assert despues == antes


def test_la_semana_en_curso_no_entra_en_1w(provider):
    # Miércoles 2026-09-09: la vela semanal del sábado 05 cierra el 12, así que no cuenta.
    snap = provider.get_indicator_snapshot("1W", datetime(2026, 9, 9, 6, 15))
    assert snap.bars_available == 5
    assert snap.price == pytest.approx(14.0)


def test_past_closes_y_ultimo_cierre_usan_el_mismo_candado(provider):
    as_of = DAY.replace(hour=6, minute=15)
    assert provider.get_past_closes("1H", as_of, 3) == [103.0, 104.0, 105.0]
    # 15M: a las 06:15 la vela de las 06:00 ya cerró (índice 24).
    assert provider.last_closed_close("15M", as_of) == pytest.approx(224.0)
    assert provider.last_closed_close("1H", DAY) is None


# --------------------------------------------------------------------------
# Ancla a la hora del análisis
# --------------------------------------------------------------------------

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_ancla_de_analisis_es_created_at_menos_15_con_precio_de_vela_cerrada(session, provider):
    created = DAY.replace(hour=6, minute=30)
    _trade(session, "t1", evp=300.0, si=100.0, created=created)
    # La ejecución llega una hora después: en este modo no debe usarse como ancla.
    _exec(session, "t1", created + timedelta(hours=1), 250.0)
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, provider, anchor_mode=ANCHOR_MODE_ANALYSIS)
    assert not exclusions
    r = rows[0]
    assert r.timestamp_entry == created - ANALYSIS_ANCHOR_LEAD == DAY.replace(hour=6, minute=15)
    assert r.timestamp_source == "analysis_time"
    assert r.entry_price == pytest.approx(224.0)


def test_ancla_de_analisis_incluye_analisis_sin_ejecucion_y_excluye_retroactivos(session, provider):
    _trade(session, "sin_ejec", created=DAY.replace(hour=7))
    _trade(session, "retro", created=DAY.replace(hour=7), is_backdated=True)
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, provider, anchor_mode=ANCHOR_MODE_ANALYSIS)
    assert [r.trade_id for r in rows] == ["sin_ejec"]
    assert {e.trade_id: e.reason for e in exclusions} == {"retro": "backdated_no_analysis_time"}


def test_ancla_de_analisis_sin_velas_para_el_precio_queda_excluida(session, provider):
    _trade(session, "temprano", created=datetime(2000, 1, 1))
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, provider, anchor_mode=ANCHOR_MODE_ANALYSIS)
    assert not rows and exclusions[0].reason == "no_closed_bar_at_analysis_time"


def test_ancla_de_analisis_exige_provider(session):
    _trade(session, "t1")
    session.commit()
    with pytest.raises(ValueError, match="ohlc_provider"):
        assemble_p2_systematic_rows(session, None, anchor_mode=ANCHOR_MODE_ANALYSIS)


def test_modo_de_ancla_desconocido_falla(session, provider):
    _trade(session, "t1")
    session.commit()
    with pytest.raises(ValueError, match="anchor_mode"):
        assemble_p2_systematic_rows(session, provider, anchor_mode="entrada")


def test_analysis_lead_reemplaza_los_15_minutos(session, provider):
    created = DAY.replace(hour=7)
    _trade(session, "t1", evp=300.0, si=100.0, created=created)
    session.commit()
    rows, _ = assemble_p2_systematic_rows(session, provider, anchor_mode=ANCHOR_MODE_ANALYSIS,
                                          analysis_lead=timedelta(minutes=30))
    assert rows[0].timestamp_entry == DAY.replace(hour=6, minute=30)
