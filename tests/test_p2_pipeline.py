"""
P2_systematic — tests del pipeline de ensamblaje, la parte que NO tenía
ninguna cobertura (compute_score_p2_sistematico, resolve_anchor,
assemble_p2_systematic_rows, bias_to_direction/evaluate_match y el
contrato de no-look-ahead).

Usa un OHLCProvider falso en memoria (OHLCProvider es un Protocol, así que
no hace falta CSV ni MT5) y SQLite :memory: para la DB. Sin red, sin
.data/, sin depender de que existan los CSV reales.
"""
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.p2_ground_truth import OhlcBar
from tools.database import AnalysisLayer, Base, TacticalAudit, UnifiedDepartment
from tools.p2_backtest import (
    MIN_BARS_PER_TF,
    TFIndicatorSnapshot,
    TIMEFRAMES,
    assemble_p2_systematic_rows,
    bias_to_direction,
    compute_score_p2_sistematico,
    evaluate_match,
    resolve_anchor,
    summarize_arm,
)

ANCHOR = datetime(2026, 6, 1, 12, 0, 0)


# --------------------------------------------------------------------------
# bias_to_direction / evaluate_match -- el bug del vocabulario
# --------------------------------------------------------------------------

def test_bias_to_direction_traduce_los_tres_valores():
    assert bias_to_direction("Bullish") == "long"
    assert bias_to_direction("Bearish") == "short"
    assert bias_to_direction("Choppy / Neutral") is None
    assert bias_to_direction(None) is None


def test_bullish_contra_long_es_acierto_no_fallo():
    """Regresión del bug real: comparar "Bullish" == "long" daba siempre False."""
    match, abstuvo = evaluate_match("Bullish", "long", False)
    assert match is True and abstuvo is False


def test_bearish_contra_long_es_fallo():
    assert evaluate_match("Bearish", "long", False) == (False, False)


def test_choppy_no_cuenta_como_apuesta():
    """Decisión C aprobada: abstención no es fallo."""
    match, abstuvo = evaluate_match("Choppy / Neutral", "long", False)
    assert match is None, "una abstención NO debe contarse como fallo"
    assert abstuvo is True


def test_ground_truth_incompleto_no_es_abstencion():
    match, abstuvo = evaluate_match("Bullish", None, True)
    assert match is None and abstuvo is False


def test_summarize_arm_separa_abstencion_de_no_medible():
    class Row:
        def __init__(self, m, a):
            self.match_original, self.abstencion_original = m, a
    rows = [Row(True, False), Row(False, False), Row(None, True), Row(None, False)]
    s = summarize_arm(rows, "original")
    assert (s.apostados, s.aciertos, s.abstenciones, s.no_medibles) == (2, 1, 1, 1)
    assert s.punteria == 0.5


# --------------------------------------------------------------------------
# compute_score_p2_sistematico
# --------------------------------------------------------------------------

def _snap(price, ema20, ema200, adx, bars=MIN_BARS_PER_TF):
    return TFIndicatorSnapshot(price=price, ema20=ema20, ema200=ema200,
                               adx14=adx, bars_available=bars)


def test_score_alcista_con_tendencia_fuerte_en_todas_las_tf():
    snaps = {tf: _snap(110, 105, 100, 30) for tf in TIMEFRAMES}
    score, incompleto = compute_score_p2_sistematico(snaps)
    assert incompleto is False
    assert score == pytest.approx(1.0), "pesos suman 1.0 x bias +1 x fuerza 1"


def test_score_bajista_es_simetrico():
    snaps = {tf: _snap(90, 95, 100, 30) for tf in TIMEFRAMES}
    assert compute_score_p2_sistematico(snaps)[0] == pytest.approx(-1.0)


def test_adx_debil_anula_la_fuerza_aunque_el_bias_sea_claro():
    snaps = {tf: _snap(110, 105, 100, 15) for tf in TIMEFRAMES}
    assert compute_score_p2_sistematico(snaps)[0] == pytest.approx(0.0)


def test_adx_intermedio_pondera_a_la_mitad():
    snaps = {tf: _snap(110, 105, 100, 22) for tf in TIMEFRAMES}
    assert compute_score_p2_sistematico(snaps)[0] == pytest.approx(0.5)


def test_emas_desordenadas_dan_bias_cero():
    # precio arriba pero EMA20 < EMA200 -> no es tendencia limpia
    snaps = {tf: _snap(110, 95, 100, 30) for tf in TIMEFRAMES}
    assert compute_score_p2_sistematico(snaps)[0] == pytest.approx(0.0)


def test_historial_insuficiente_en_una_sola_tf_marca_incompleto():
    snaps = {tf: _snap(110, 105, 100, 30) for tf in TIMEFRAMES}
    snaps["1D"] = _snap(110, 105, 100, 30, bars=MIN_BARS_PER_TF - 1)
    score, incompleto = compute_score_p2_sistematico(snaps)
    assert incompleto is True and score is None


def test_tf_faltante_marca_incompleto():
    snaps = {tf: _snap(110, 105, 100, 30) for tf in TIMEFRAMES}
    snaps["4H"] = None
    assert compute_score_p2_sistematico(snaps) == (None, True)


# --------------------------------------------------------------------------
# resolve_anchor -- regla MIN(entry_time) + fallback
# --------------------------------------------------------------------------

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _trade(s, tid, calc_edge=0.5, evp=110.0, si=90.0, created=ANCHOR, mark_price=None,
           is_backdated=False):
    # Varios campos son NOT NULL en el modelo real (tools/database.py, clase
    # UnifiedDepartment) pero irrelevantes para estos tests: valores de
    # relleno fijos, sin significado -- bias_predicho_original se deriva de
    # calc_edge vía determine_market_bias(), no de market_bias crudo.
    s.add(UnifiedDepartment(
        id=tid, state="READY_FOR_NOTION", asset="XAUUSD",
        market_bias="Bullish", created_at=created, calc_edge=calc_edge,
        p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)", p1_timeframe="15M",
        p1_type="1st_iteration", nodes_l1=1, nodes_l2=1,
        tactical_classification="Continuation_Pressure",
        long_prob=0.5, short_prob=0.5, no_trade_prob=0.0,
        edge_validation_price=evp, structural_invalidation=si,
        mark_price=mark_price, is_backdated=is_backdated,
    ))
    for name, score in (("P0", 1), ("P1", 1), ("P2", 1), ("P3", 1), ("P4", 1)):
        s.add(AnalysisLayer(trade_id=tid, department="Estructural", layer_name=name, score=score))


def _exec(s, tid, entry_time, entry_price, eid=None):
    s.add(TacticalAudit(id=eid or f"{tid}-{entry_time:%H%M}", trade_id=tid,
                        entry_time=entry_time, entry_price=entry_price))


def test_resolve_anchor_toma_la_ejecucion_mas_temprana(session):
    _trade(session, "t1")
    _exec(session, "t1", ANCHOR + timedelta(hours=5), 105.0, eid="tarde")
    _exec(session, "t1", ANCHOR, 100.0, eid="temprana")
    session.commit()
    a = resolve_anchor(session, "t1", ANCHOR)
    assert a.timestamp_entry == ANCHOR
    assert a.entry_price == 100.0
    assert a.timestamp_source == "tactical_audit"


def test_resolve_anchor_cae_a_created_at_sin_ejecuciones(session):
    _trade(session, "t2")
    session.commit()
    a = resolve_anchor(session, "t2", ANCHOR)
    assert a.timestamp_source == "created_at_fallback"
    assert a.entry_price is None


def test_resolve_anchor_ignora_entry_time_nulo(session):
    _trade(session, "t3")
    _exec(session, "t3", None, 999.0, eid="sin-hora")
    _exec(session, "t3", ANCHOR, 100.0, eid="con-hora")
    session.commit()
    assert resolve_anchor(session, "t3", ANCHOR).entry_price == 100.0


# --------------------------------------------------------------------------
# assemble_p2_systematic_rows + no-look-ahead
# --------------------------------------------------------------------------

class FakeProvider:
    """OHLCProvider en memoria. Registra qué se le pidió, para el no-look-ahead."""

    def __init__(self, snapshot=None, forward=()):
        self._snapshot = snapshot or _snap(110, 105, 100, 30)
        self._forward = list(forward)
        self.snapshot_calls = []

    def get_indicator_snapshot(self, timeframe, as_of):
        self.snapshot_calls.append((timeframe, as_of))
        return self._snapshot

    def get_forward_path(self, as_of, max_bars=2160):
        return [b for b in self._forward if b.time > as_of][:max_bars]


def test_sin_provider_todo_queda_incompleto(session):
    _trade(session, "t1")
    _exec(session, "t1", ANCHOR, 100.0)
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, None)
    assert len(rows) == 1 and not exclusions
    r = rows[0]
    assert r.p2_sistematico is None and r.snapshot_incompleto
    assert r.ground_truth_incompleto and r.match_original is None


def test_scope_filter_enumera_cada_exclusion_con_motivo(session):
    _trade(session, "ok")
    _exec(session, "ok", ANCHOR, 100.0)
    _trade(session, "sin_niveles", evp=None, si=None)
    _exec(session, "sin_niveles", ANCHOR, 100.0)
    _trade(session, "sin_ejecucion")
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, None)
    assert [r.trade_id for r in rows] == ["ok"]
    motivos = {e.trade_id: e.reason for e in exclusions}
    assert motivos["sin_niveles"] == "missing_edge_validation_price_or_structural_invalidation"
    assert motivos["sin_ejecucion"] == "zero_tactical_audit_rows"


def test_ningun_trade_se_omite_en_silencio(session):
    for i in range(4):
        _trade(session, f"t{i}", evp=None if i % 2 else 110.0)
        if i < 3:
            _exec(session, f"t{i}", ANCHOR, 100.0)
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, None)
    assert len(rows) + len(exclusions) == 4


def test_pipeline_completo_marca_acierto(session):
    """Tesis long (validación arriba); el precio toca la validación -> acierto."""
    _trade(session, "t1", calc_edge=0.9, evp=110.0, si=90.0)
    _exec(session, "t1", ANCHOR, 100.0)
    session.commit()
    forward = [OhlcBar(ANCHOR + timedelta(hours=1), 111.0, 99.0)]
    rows, _ = assemble_p2_systematic_rows(session, FakeProvider(forward=forward))
    r = rows[0]
    assert r.ground_truth_direction == "long"
    assert r.bias_predicho_original == "Bullish"
    assert r.match_original is True, "Bullish contra ground truth long debe ser acierto"
    assert r.abstencion_original is False


def test_vela_que_toca_ambos_niveles_queda_incompleta(session):
    """Decisión B aprobada: no se adivina el orden intrabar."""
    _trade(session, "t1", evp=110.0, si=90.0)
    _exec(session, "t1", ANCHOR, 100.0)
    session.commit()
    forward = [OhlcBar(ANCHOR + timedelta(hours=1), 111.0, 89.0)]
    rows, _ = assemble_p2_systematic_rows(session, FakeProvider(forward=forward))
    assert rows[0].ground_truth_incompleto is True
    assert rows[0].match_original is None


def test_no_look_ahead_snapshot_pedido_exactamente_en_el_anchor(session):
    _trade(session, "t1")
    _exec(session, "t1", ANCHOR + timedelta(hours=3), 100.0, eid="tarde")
    _exec(session, "t1", ANCHOR, 100.0, eid="temprana")
    session.commit()
    provider = FakeProvider()
    assemble_p2_systematic_rows(session, provider)
    assert provider.snapshot_calls, "debió pedirse al menos un snapshot"
    for tf, as_of in provider.snapshot_calls:
        assert as_of == ANCHOR, (
            f"snapshot de {tf} pedido en {as_of}, no en el anchor {ANCHOR} — "
            "usar una ejecución posterior filtraría información del futuro"
        )


def test_no_look_ahead_forward_path_excluye_la_vela_de_entrada(session):
    _trade(session, "t1", evp=110.0, si=90.0)
    _exec(session, "t1", ANCHOR, 100.0)
    session.commit()
    # La vela EN el anchor toca la validación; solo debe contar la posterior,
    # que toca la invalidación.
    forward = [
        OhlcBar(ANCHOR, 111.0, 99.0),
        OhlcBar(ANCHOR + timedelta(hours=1), 101.0, 89.0),
    ]
    rows, _ = assemble_p2_systematic_rows(session, FakeProvider(forward=forward))
    assert rows[0].ground_truth_direction == "short", (
        "la vela del propio anchor no debe consumirse: hacerlo adelanta el resultado"
    )


# --------------------------------------------------------------------------
# Etiquetas de exclusión y scope con mark_price (edge_evaluation, fase 0a)
# --------------------------------------------------------------------------

def test_etiqueta_distingue_sin_filas_de_filas_sin_entry_time(session):
    """Regresión: antes ambos casos salían como zero_tactical_audit_rows (25 de 26 mal)."""
    _trade(session, "sin_filas")
    _trade(session, "filas_sin_hora")
    _exec(session, "filas_sin_hora", None, None, eid="x1")
    _trade(session, "hora_sin_precio")
    _exec(session, "hora_sin_precio", ANCHOR, None, eid="x2")
    session.commit()
    _, exclusions = assemble_p2_systematic_rows(session, None)
    motivos = {e.trade_id: e.reason for e in exclusions}
    assert motivos["sin_filas"] == "zero_tactical_audit_rows"
    assert motivos["filas_sin_hora"] == "tactical_audit_rows_exist_but_entry_time_all_null"
    assert motivos["hora_sin_precio"] == "anchor_execution_missing_entry_price"


def test_por_defecto_los_analisis_sin_ejecucion_siguen_excluidos(session):
    _trade(session, "con_mark", mark_price=100.0)
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, None)
    assert rows == [] and exclusions[0].reason == "zero_tactical_audit_rows"


def test_include_no_execution_usa_mark_price_y_created_at(session):
    creado = ANCHOR - timedelta(hours=2)
    _trade(session, "con_mark", mark_price=100.0, created=creado)
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, None, include_no_execution=True)
    assert not exclusions
    r = rows[0]
    assert r.timestamp_source == "mark_price"
    assert r.timestamp_entry == creado
    assert r.entry_price == 100.0
    assert r.thesis_direction == "long", "evp 110 arriba y si 90 abajo de 100"


def test_include_no_execution_excluye_retroactivos_con_motivo(session):
    _trade(session, "retro", mark_price=100.0, is_backdated=True)
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, None, include_no_execution=True)
    assert rows == []
    assert exclusions[0].reason == "no_execution_backdated"


def test_include_no_execution_sin_mark_price_sigue_excluido(session):
    _trade(session, "sin_mark")
    session.commit()
    rows, exclusions = assemble_p2_systematic_rows(session, None, include_no_execution=True)
    assert rows == [] and exclusions[0].reason == "zero_tactical_audit_rows"


def test_una_ejecucion_real_tiene_prioridad_sobre_mark_price(session):
    _trade(session, "ambos", mark_price=999.0)
    _exec(session, "ambos", ANCHOR, 100.0)
    session.commit()
    rows, _ = assemble_p2_systematic_rows(session, None, include_no_execution=True)
    assert rows[0].timestamp_source == "tactical_audit"
    assert rows[0].entry_price == 100.0


def test_la_fila_expone_scores_y_activo(session):
    _trade(session, "t1")
    _exec(session, "t1", ANCHOR, 100.0)
    session.commit()
    rows, _ = assemble_p2_systematic_rows(session, None)
    assert rows[0].layer_scores == {"P0": 1, "P1": 1, "P2": 1, "P3": 1, "P4": 1}
    assert rows[0].asset == "XAUUSD"
