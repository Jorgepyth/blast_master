"""
P2_systematic — chequeo de reloj de las velas (calibrate_clock_offset).

Regresión del bug del 2026-09-22: el exportador trataba la hora del servidor
de MT5 (UTC+3) como UTC y el CSV salía corrido +3h. Una orden llenada tiene
que caer dentro de su vela; si solo cuadra desplazando, el CSV está mal.
"""
from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tools.database import Base, TacticalAudit, UnifiedDepartment
from tools.p2_backtest import (
    CLOCK_MIN_ENTRIES,
    CsvOHLCProvider,
    calibrate_clock_offset,
)

T0 = datetime(2026, 6, 1, 0, 0)


def _write_15m_csv(tmp_path, lag_hours=0, n_bars=24 * 4 * 5):
    """
    Serie 15M con precio que sube 1 punto por vela: cada vela es única, así que
    un precio solo cae dentro de UNA vela. lag_hours>0 etiqueta las velas más
    tarde de lo real, como hacía el exportador con el bug.
    """
    rows = []
    for i in range(n_bars):
        real = T0 + timedelta(minutes=15 * i)
        rows.append({"time": (real + timedelta(hours=lag_hours)).strftime("%Y-%m-%d %H:%M:%S"),
                     "open": 1000 + i, "high": 1000 + i + 0.9, "low": 1000 + i, "close": 1000 + i + 0.5})
    pd.DataFrame(rows).to_csv(tmp_path / "15M.csv", index=False)
    return CsvOHLCProvider(str(tmp_path))


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _analysis(session, tid, **kw):
    """UnifiedDepartment mínimo: los NOT NULL con relleno, el resto por kwargs."""
    campos = dict(
        id=tid, state="READY_FOR_NOTION", asset="XAUUSD", market_bias="Bullish", calc_edge=0.5,
        p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1,
        tactical_classification="x", long_prob=0.5, short_prob=0.5, no_trade_prob=0.0,
    )
    campos.update(kw)
    session.add(UnifiedDepartment(**campos))


def _fills(session, n=12, filled=True):
    """Fills cuyo precio es el de la vela real que contiene su entry_time."""
    for i in range(n):
        bar = 10 + i * 7
        t = T0 + timedelta(minutes=15 * bar + 5)
        _analysis(session, f"t{i}-{filled}")
        session.add(TacticalAudit(trade_id=f"t{i}-{filled}", entry_time=t,
                                  entry_price=1000 + bar + 0.4, order_filled=filled))
    session.commit()


def test_csv_bien_alineado_da_offset_cero(tmp_path, session):
    _fills(session)
    c = calibrate_clock_offset(session, _write_15m_csv(tmp_path, lag_hours=0))
    assert c.best_offset == 0 and c.aligned is True
    assert c.rate_at_zero == pytest.approx(1.0)


def test_csv_corrido_3h_se_detecta_como_desalineado(tmp_path, session):
    """El caso real: velas etiquetadas 3h tarde."""
    _fills(session)
    c = calibrate_clock_offset(session, _write_15m_csv(tmp_path, lag_hours=3))
    assert c.best_offset == 3
    assert c.aligned is False
    assert "DESALINEADO" in c.describe() and "+3h" in c.describe()


def test_ordenes_no_llenadas_no_cuentan(tmp_path, session):
    _fills(session, n=CLOCK_MIN_ENTRIES - 1)
    _fills(session, n=5, filled=False)
    c = calibrate_clock_offset(session, _write_15m_csv(tmp_path), include_mark_price=False)
    assert c.aligned is None, "con menos del mínimo de fills no se decide"
    assert c.n_entries == CLOCK_MIN_ENTRIES - 1


def test_mark_price_completa_la_muestra(tmp_path, session):
    """Cuentas con pocos fills (BTC: 3, US100: 1) se calibran con mark_price."""
    _fills(session, n=3)
    for i in range(10):
        bar = 200 + i * 5
        _analysis(session, f"a{i}", asset="BTCUSDT.P",
                  created_at=T0 + timedelta(minutes=15 * bar + 3),
                  mark_price=1000 + bar + 0.2, is_backdated=False)
    session.commit()
    c = calibrate_clock_offset(session, _write_15m_csv(tmp_path, lag_hours=3))
    assert c.n_entries == 13
    assert c.best_offset == 3 and c.aligned is False


def test_mark_price_de_analisis_retroactivos_se_ignora(tmp_path, session):
    _fills(session, n=3)
    _analysis(session, "bd", created_at=T0, mark_price=1000.0, is_backdated=True)
    session.commit()
    c = calibrate_clock_offset(session, _write_15m_csv(tmp_path))
    assert c.n_entries == 3


def test_bar_containing_respeta_el_intervalo(tmp_path):
    prov = _write_15m_csv(tmp_path)
    assert prov.bar_containing("15M", T0 + timedelta(minutes=14)) == (1000.9, 1000.0)
    assert prov.bar_containing("15M", T0 + timedelta(minutes=15)) == (1001.9, 1001.0)
    assert prov.bar_containing("15M", T0 - timedelta(minutes=1)) is None
