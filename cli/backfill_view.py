"""
cli/backfill_view.py — la vista del backfill al estilo `git log --decorate --oneline --graph` (spec 002, T48; RF-19,
N27). Solo presentación: el plan lo arma `tools/auto_backfill.py` y el historial sale de `backfill_history`.

Una línea por corrida, la más nueva primero, con sus decoraciones: la cuenta, `HEAD` en la más reciente y `dry-run`
en las vistas previas. Debajo, una línea por cambio: id del registro, tabla y campo, valor anterior → valor nuevo y
origen. Verde lo que se llena (`+`, y `>` el traslado de la hora vieja), amarillo con `!` los conflictos y gris (`=`)
lo que no cambia. Un valor vacío se muestra como `—`. Todo en inglés (N30).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, NamedTuple, Sequence

from tools.auto_backfill import KIND_CONFLICT, KIND_FILL, KIND_LEGACY_MOVE, KIND_UNCHANGED

EMPTY = "—"
_MARK = {KIND_FILL: "+", KIND_CONFLICT: "!", KIND_UNCHANGED: "=", KIND_LEGACY_MOVE: ">"}
_STYLE = {KIND_FILL: "green", KIND_CONFLICT: "yellow", KIND_UNCHANGED: "dim", KIND_LEGACY_MOVE: "green"}
_KIND_ORDER = (KIND_FILL, KIND_CONFLICT, KIND_UNCHANGED, KIND_LEGACY_MOVE)


class ViewLine(NamedTuple):
    text: str
    style: str


@dataclass(frozen=True)
class ViewRun:
    """Una corrida del backfill: una vista previa (`dry_run`) o una aplicada, tomada de `backfill_history`. Los cambios
    tienen los atributos de `PlannedChange` (`record_id`, `table_name`, `field`, `kind`, `old_value`, `new_value`,
    `source`)."""
    run_id: str
    run_at: datetime
    account: str
    changes: Sequence[Any]
    dry_run: bool


def format_value(value: Any) -> str:
    if value is None:
        return EMPTY
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, float):
        return format(value, ".8f").rstrip("0").rstrip(".")
    return str(value)


def _summary(changes: Sequence[Any]) -> str:
    counts = Counter(change.kind for change in changes)
    parts = [f"{counts[kind]} {kind}" for kind in _KIND_ORDER if counts[kind]]
    return " · ".join(parts) if parts else "no changes"


def render_runs(runs: Sequence[ViewRun], show_unchanged: bool = True) -> List[ViewLine]:
    """Las líneas de la vista, la corrida más nueva primero."""
    lines: List[ViewLine] = []
    ordered = sorted(runs, key=lambda run: run.run_at, reverse=True)
    for index, run in enumerate(ordered):
        decorations = [run.account] + (["HEAD"] if index == 0 else []) + (["dry-run"] if run.dry_run else [])
        lines.append(ViewLine(f"* {run.run_id[:7]} ({', '.join(decorations)}) {run.run_at:%Y-%m-%d %H:%M} · "
                              f"{_summary(run.changes)}", "bold"))
        for change in run.changes:
            if change.kind == KIND_UNCHANGED and not show_unchanged:
                continue
            lines.append(ViewLine(
                f"| {_MARK[change.kind]} {change.record_id[:8]} {change.table_name}.{change.field}: "
                f"{format_value(change.old_value)} → {format_value(change.new_value)} [{change.source}]",
                _STYLE[change.kind]))
    return lines


def print_runs(console, runs: Sequence[ViewRun], show_unchanged: bool = True) -> None:
    """Imprime la vista con Rich, sin interpretar markup en los valores."""
    for line in render_runs(runs, show_unchanged):
        console.print(line.text, style=line.style, markup=False, highlight=False, soft_wrap=True)
