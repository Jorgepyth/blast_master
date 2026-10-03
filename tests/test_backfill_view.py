"""
T48 (spec 002): `cli/backfill_view.py`, la vista del backfill al estilo `git log --decorate --oneline --graph`
(RF-19, N27): una línea por corrida con sus decoraciones (la cuenta, `HEAD` en la más reciente y `dry-run` en las
vistas previas) y debajo una línea por cambio: id, campo, valor anterior → valor nuevo y origen. Verde lo que se
llena, amarillo y `!` los conflictos, gris lo que no cambia, y `—` en los vacíos.
"""
from datetime import datetime

from cli.backfill_view import ViewLine, ViewRun, render_runs
from tools.auto_backfill import KIND_CONFLICT, KIND_FILL, KIND_LEGACY_MOVE, KIND_UNCHANGED, PlannedChange

RUN_AT = datetime(2026, 10, 3, 10, 15)


def _change(kind, field, old, new, source="candles", record_id="00e2e31b-4c1a-4f0e-9d2b-1a2b3c4d5e6f",
            table="efficiency_audit"):
    return PlannedChange("000", table, record_id, record_id, field, kind, old, new, source)


CHANGES = [
    _change(KIND_FILL, "resolution_type", "Open", "Confirmed (A equal to B)"),
    _change(KIND_CONFLICT, "structural_mae", 95.0, 99.0),
    _change(KIND_UNCHANGED, "failure_reason", "N/A", "N/A"),
    _change(KIND_LEGACY_MOVE, "audit_registration_time", None, datetime(2026, 6, 2, 18, 0), source="legacy"),
    _change(KIND_FILL, "resolution_time", datetime(2026, 6, 2, 18, 0), None, source="clock_unverified"),
]


def test_a_preview_is_one_decorated_line_and_one_line_per_change():
    lines = render_runs([ViewRun("7f3a2c1e-9b8a-4c3d", RUN_AT, "000", CHANGES, dry_run=True)])
    assert lines[0] == ViewLine("* 7f3a2c1 (000, HEAD, dry-run) 2026-10-03 10:15 · 2 fill · 1 conflict · "
                                "1 unchanged · 1 legacy_move", "bold")
    assert lines[1] == ViewLine("| + 00e2e31b efficiency_audit.resolution_type: Open → Confirmed (A equal to B) "
                                "[candles]", "green")
    assert lines[2] == ViewLine("| ! 00e2e31b efficiency_audit.structural_mae: 95 → 99 [candles]", "yellow")
    assert lines[3] == ViewLine("| = 00e2e31b efficiency_audit.failure_reason: N/A → N/A [candles]", "dim")
    assert lines[4] == ViewLine("| > 00e2e31b efficiency_audit.audit_registration_time: — → 2026-06-02 18:00 "
                                "[legacy]", "green")
    assert lines[5] == ViewLine("| + 00e2e31b efficiency_audit.resolution_time: 2026-06-02 18:00 → — "
                                "[clock_unverified]", "green")
    assert len(lines) == 6


def test_head_goes_only_on_the_newest_run_and_dry_run_only_on_previews():
    older = ViewRun("1111111aaaa", datetime(2026, 10, 1, 9, 0), "000", CHANGES[:1], dry_run=False)
    newer = ViewRun("2222222bbbb", datetime(2026, 10, 2, 9, 0), "000", CHANGES[:1], dry_run=False)
    headers = [line.text for line in render_runs([older, newer]) if line.text.startswith("*")]
    assert headers == ["* 2222222 (000, HEAD) 2026-10-02 09:00 · 1 fill",
                       "* 1111111 (000) 2026-10-01 09:00 · 1 fill"]


def test_unchanged_lines_can_be_hidden_but_still_count():
    lines = render_runs([ViewRun("7f3a2c1e", RUN_AT, "000", CHANGES, dry_run=True)], show_unchanged=False)
    assert "1 unchanged" in lines[0].text
    assert not any(line.text.startswith("| = ") for line in lines)


def test_numbers_dates_and_empty_values_are_shown_plainly():
    changes = [_change(KIND_FILL, "mae_adverse", None, 0.2, table="tactical_audit", record_id="t1"),
               _change(KIND_CONFLICT, "structural_mfe", 4369.62, 4382.5)]
    lines = render_runs([ViewRun("abcdef12", RUN_AT, "002", changes, dry_run=True)])
    assert lines[1].text == "| + t1 tactical_audit.mae_adverse: — → 0.2 [candles]"
    assert lines[2].text == "| ! 00e2e31b efficiency_audit.structural_mfe: 4369.62 → 4382.5 [candles]"


def test_a_run_without_changes_says_so():
    lines = render_runs([ViewRun("abcdef12", RUN_AT, "001", [], dry_run=True)])
    assert lines == [ViewLine("* abcdef1 (001, HEAD, dry-run) 2026-10-03 10:15 · no changes", "bold")]


def test_an_accepted_conflict_in_the_history_keeps_the_conflict_mark():
    from tools.auto_backfill import KIND_ACCEPTED_CONFLICT
    lines = render_runs([ViewRun("abcdef12", RUN_AT, "000", [_change(KIND_ACCEPTED_CONFLICT, "structural_mae", 95.0,
                                                                         99.0)], dry_run=False)])
    assert lines[0].text.endswith("· 1 accepted_conflict")
    assert lines[1] == ViewLine("| ! 00e2e31b efficiency_audit.structural_mae: 95 → 99 [candles]", "yellow")
