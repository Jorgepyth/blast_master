"""
T32 (spec 002): `tools/resolution_report.py`, los datos del reporte de comparación (RF-6, RF-3b, RF-17, RF-21). Una
cuenta de fixture con un banco de 1M que toca 110 a la 01:00 (ver tests/test_auto_resolution.py) y una segunda cuenta
sin reloj verificado. Solo lectura.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert, text

from cli.schemas.audit_efficiency import FailureReason, ResolutionType, StructuralResolution
from config.auto_resolution import REASON_CLOCK_UNVERIFIED, REASON_NO_LEVELS, REASON_PENDING_CANDLES
from core.outcome_metrics import WinRate
from core.p2_ground_truth import LEVEL_VALIDATION
from tests.test_auto_resolution import T0, TOUCH, _write_bank, _write_db
from tools.database import EfficiencyAudit
from tools.resolution_report import ManualAudit, build_report, read_manual_audits

CONFIRMED, INVALIDATED = ResolutionType.CONFIRMED.value, ResolutionType.INVALIDATED.value
OVERLAP = ResolutionType.OVERLAP_INVALIDATION.value

ANALYSES = [
    # a1: toca a la 01:00; a3 empieza a las 00:40, antes del toque: a1 es Overlap con B = a3.
    {"id": "a1", "created_at": T0 + timedelta(minutes=30)},
    # a3: también toca a la 01:00; a6 (retroactivo, 00:45) empieza antes: a3 es Overlap con B = a6.
    {"id": "a3", "created_at": T0 + timedelta(hours=1)},
    # a4: partida 105 (cierre de las 01:59) y objetivo 105: no_levels.
    {"id": "a4", "created_at": T0 + timedelta(hours=2, minutes=20), "evp": 105.0, "si": 108.0, "mark_price": 105.0},
    # a5: nunca toca (sin velas de 1H el horizonte no se cumple: pending) y su Mark Price (120) no está en sus velas.
    {"id": "a5", "created_at": T0 + timedelta(hours=2, minutes=30), "evp": 110.0, "si": 100.0, "mark_price": 120.0},
    # a6: retroactivo, hora tipeada 00:45; toca a la 01:00.
    {"id": "a6", "created_at": T0 + timedelta(minutes=45), "is_backdated": True},
]

MANUAL = {
    # a1 coincide en todo; el MAE tipeado (99.05) difiere de las velas (99.0) por menos de 0.1%.
    "a1": dict(resolution_type=OVERLAP, structural_resolution=StructuralResolution.NA.value,
               failure_reason=FailureReason.OVERLAP.value, specific_bias_compliance="Valid",
               resolution_time=T0 + timedelta(hours=5), structural_mae=99.05, structural_mfe=110.5),
    # a4 (no_levels) tiene audit y Valid: no entra en el win rate manual, porque no entra en el de velas.
    "a4": dict(resolution_type=CONFIRMED, specific_bias_compliance="Valid", resolution_time=T0 + timedelta(hours=3)),
    # a3 difiere en el tipo y en el MAE (95 contra 99).
    "a3": dict(resolution_type=INVALIDATED, structural_resolution=StructuralResolution.NA.value,
               failure_reason=FailureReason.OVERLAP.value, specific_bias_compliance="Invalid",
               resolution_time=T0 + timedelta(hours=3), structural_mae=95.0, structural_mfe=110.5),
}


def _add_manual_audits(db_path, audits, real_bias_b="BOS"):
    """Un `insert` de Core, que nombra solo las columnas que recibe: sirve también con el esquema de antes de T35."""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        for trade_id, fields in audits.items():
            conn.execute(insert(EfficiencyAudit.__table__).values(
                **dict(dict(id=trade_id, bias_a="BOS", real_bias_b=real_bias_b), **fields)))
    engine.dispose()


@pytest.fixture
def report(tmp_path):
    bank_root = tmp_path / "bank"
    bank_root.mkdir()
    _write_bank(bank_root)
    _write_db(tmp_path / "xau.db", ANALYSES)
    _add_manual_audits(tmp_path / "xau.db", MANUAL)
    _write_db(tmp_path / "us100.db", [{"id": "n1", "asset": "US100", "created_at": T0}])
    reports = build_report(str(tmp_path), str(bank_root), real_accounts={"000": "xau.db", "003": "us100.db",
                                                                         "009": "missing.db"})
    return {r.account: r for r in reports}


def test_one_report_per_account_that_exists(report):
    assert sorted(report) == ["000", "003"]  # la cuenta sin archivo no aparece


def test_resolved_count_missing_share_and_reasons(report):
    xau = report["000"]
    assert (xau.total, xau.resolved) == (5, 3)
    assert xau.missing_reasons == {REASON_NO_LEVELS: 1, REASON_PENDING_CANDLES: 1}
    assert xau.missing_pct == pytest.approx(0.4)
    assert report["003"].missing_reasons == {REASON_CLOCK_UNVERIFIED: 1} and report["003"].resolved == 0


def test_agreement_per_field_and_the_list_of_differences(report):
    by_id = {c.trade_id: c for c in report["000"].comparisons}
    assert by_id["a1"].agreement == {"resolution_type": True, "structural_mae": True, "structural_mfe": True,
                                     "structural_resolution": True, "failure_reason": True}
    assert by_id["a3"].agreement["resolution_type"] is False and by_id["a3"].agreement["structural_mae"] is False
    assert by_id["a4"].agreement["resolution_type"] is None  # sin propuesta ni audit: no se compara
    assert [c.trade_id for c in report["000"].differences] == ["a3"]


def test_compliance_is_shown_next_to_the_candles_without_a_percentage(report):
    by_id = {c.trade_id: c for c in report["000"].comparisons}
    assert (by_id["a1"].compliance, by_id["a3"].compliance, by_id["a5"].compliance) == ("Valid", "Invalid", None)


def test_audit_delay_is_registration_time_minus_the_touch(report):
    by_id = {c.trade_id: c for c in report["000"].comparisons}
    assert by_id["a1"].audit_delay_h == pytest.approx(4.0)  # hoy resolution_time guarda la hora del audit (05:00)
    assert by_id["a3"].audit_delay_h == pytest.approx(2.0)
    assert by_id["a5"].audit_delay_h is None


def test_s1_and_s4_for_all_and_directional_with_the_manual_win_rate_on_the_same_analyses(report):
    xau = report["000"]
    assert xau.s1_all.candles == WinRate(wins=2, n=2, outside=0, excluded_backdated=1)
    assert xau.s1_all.manual_wins == 1  # a1 Valid, a3 Invalid
    assert xau.s4_all.candles == WinRate(wins=2, n=2, outside=0, excluded_backdated=1)
    assert xau.s1_directional.candles.n == 2


def test_each_overlap_shows_the_first_level_touched_its_time_and_b(report):
    overlaps = {o.trade_id: o for o in report["000"].overlaps}
    assert set(overlaps) == {"a1", "a3"}
    assert (overlaps["a1"].level, overlaps["a1"].touch_time, overlaps["a1"].b_id) == (LEVEL_VALIDATION, TOUCH, "a3")
    assert overlaps["a1"].b_anchor == T0 + timedelta(minutes=40)
    assert overlaps["a3"].b_id == "a6"  # B puede ser retroactivo (N22)


def test_mark_prices_that_do_not_fit_their_candles(report):
    misfits = report["000"].mark_price_misfits
    assert [m.trade_id for m in misfits] == ["a5"]
    assert misfits[0].check.fits is False and misfits[0].check.distance == pytest.approx(14.5)
    assert misfits[0].with_mark_price_time is False  # sin mark_price_time: el período antes de created_at (RF-3b)


def test_backdated_loading_delay_appears_once_saved_at_exists(report, tmp_path):
    assert report["000"].backdated_delays == []  # hoy la columna saved_at no existe
    engine = create_engine(f"sqlite:///{tmp_path / 'xau.db'}")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE unified_department ADD COLUMN saved_at DATETIME"))
        conn.execute(text("UPDATE unified_department SET saved_at = '2026-06-04 00:45:00.000000' WHERE id = 'a6'"))
        conn.execute(text("UPDATE unified_department SET saved_at = '2026-06-01 00:31:00.000000' WHERE id = 'a1'"))
    engine.dispose()
    (xau,) = [r for r in build_report(str(tmp_path), str(tmp_path / "bank"), real_accounts={"000": "xau.db"})]
    assert xau.backdated_delays == [("a6", pytest.approx(72.0))]


def test_manual_audits_read_with_explicit_columns_and_open_without_real_bias_b_counts_as_empty(tmp_path):
    _write_db(tmp_path / "x.db", [{"id": "a1", "created_at": T0}, {"id": "a2", "created_at": T0}])
    _add_manual_audits(tmp_path / "x.db", {"a1": dict(resolution_type="Open", real_bias_b=None),
                                           "a2": dict(resolution_type=CONFIRMED, structural_mae=2650)})
    audits = read_manual_audits(str(tmp_path / "x.db"))
    assert audits["a1"].resolution_type is None  # N9: "Open" de un audit nunca hecho es vacío
    assert audits["a2"] == ManualAudit("a2", CONFIRMED, None, None, None, None, None, 2650.0, None)


def test_the_report_never_writes(tmp_path):
    bank_root = tmp_path / "bank"
    bank_root.mkdir()
    _write_bank(bank_root)
    _write_db(tmp_path / "xau.db", ANALYSES)
    before = (tmp_path / "xau.db").read_bytes()
    build_report(str(tmp_path), str(bank_root), real_accounts={"000": "xau.db"})
    assert (tmp_path / "xau.db").read_bytes() == before


def test_touch_exceptions_are_listed_with_their_level_time_closest_price_and_note(tmp_path):
    bank_root = tmp_path / "bank"
    bank_root.mkdir()
    _write_bank(bank_root)
    _write_db(tmp_path / "xau.db", [{"id": "e1", "created_at": T0 + timedelta(minutes=30), "evp": 111.0}])
    (xau,) = build_report(str(tmp_path), str(bank_root), real_accounts={"000": "xau.db"},
                          touch_exceptions={"e1": ("validation", "touched 111 on the operator's chart")})
    (entry,) = xau.touch_exceptions
    assert (entry.trade_id, entry.level, entry.touch_time, entry.level_price, entry.closest_price, entry.note) == (
        "e1", "validation", TOUCH, 111.0, 110.5, "touched 111 on the operator's chart")
    from tools.resolution_report import render_markdown
    markdown = render_markdown([xau], generated_at=T0)
    assert "### Touch exceptions" in markdown
    assert "| e1 | validation | 111.00 | 110.50 | 2026-06-01 01:00 | touched 111 on the operator's chart |" in markdown
    assert "No touch exceptions." in render_markdown(
        build_report(str(tmp_path), str(bank_root), real_accounts={"000": "xau.db"}, touch_exceptions={}),
        generated_at=T0)
