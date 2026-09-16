"""
Fase 3 -- cuadro efímero de P&L potencial (`render_pnl_box`) que aparece en el
wizard tras SL/Entry/Size/TP y antes de Entry Time. No persiste nada en journal.db.
"""
import re
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

import cli.main as m
from cli.main import render_pnl_box

_MAIN_SRC = Path(m.__file__).read_text()


def _run(asset, entry, size, sl, tp, pick="accept"):
    """Ejecuta render_pnl_box capturando la salida de consola; devuelve (choice, texto)."""
    with patch.object(m.console, "clear"), \
         patch("cli.main.inquirer.select") as sel, \
         m.console.capture() as cap:
        sel.return_value.execute.return_value = pick
        choice = render_pnl_box(asset, entry, size, sl, tp)
    return choice, cap.get()


# --------------------------------------------------------------------------- #
# cálculo + recálculo en vivo
# --------------------------------------------------------------------------- #
def test_xauusdt_p_shows_correct_usd_amounts():
    _, out = _run("XAUUSDT.P", 2742.92, 0.01, 2734.92, 2759.12)
    assert "XAUUSD" in out
    assert "-$8.00" in out          # 0.01 * 8.00 * 100
    assert "+$16.20" in out         # 0.01 * 16.20 * 100
    assert "2.02" in out            # R:R = 16.20 / 8.00


def test_live_recalc_on_bigger_size():
    _, out = _run("XAUUSDT.P", 2742.92, 0.02, 2734.92, 2759.12)
    assert "-$16.00" in out         # el doble de size -> el doble de $
    assert "+$32.40" in out
    assert "-$8.00" not in out      # NO muestra el número viejo


def test_returns_the_field_key_when_user_picks_modificar():
    for pick in ("sl", "entry_p", "size", "tp"):
        choice, _ = _run("XAUUSDT.P", 2742.92, 0.01, 2734.92, 2759.12, pick=pick)
        assert choice == pick


def test_accept_returns_accept():
    choice, _ = _run("XAUUSDT.P", 2742.92, 0.01, 2734.92, 2759.12, pick="accept")
    assert choice == "accept"


# --------------------------------------------------------------------------- #
# símbolo NO VERIFICADO / desconocido / None -> N/A, sin bloquear
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("asset", ["BTCUSDT.P", "US100", "EURUSD"])
def test_unverified_or_unknown_symbol_shows_na_and_still_returns(asset):
    choice, out = _run(asset, 79000, 0.05, 78000, 81000, pick="accept")
    assert choice == "accept"                       # el wizard puede avanzar igual
    assert "N/A (símbolo no verificado)" in out
    # caso bloqueado: no hay filas de montos, solo la fila "Potential P&L" + "Reason"
    assert "Potential Loss" not in out and "Potential Gain" not in out
    assert "Reason" in out


def test_asset_none_does_not_crash():
    choice, out = _run(None, 100, 0.01, 99, 102)
    assert choice == "accept"
    assert "N/A (símbolo no verificado)" in out


# --------------------------------------------------------------------------- #
# no persiste nada / no toca la red
# --------------------------------------------------------------------------- #
def test_no_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("abrió un socket")))
    choice, out = _run("XAUUSDT.P", 2742.92, 0.01, 2734.92, 2759.12)
    assert choice == "accept" and "-$8.00" in out


def test_uses_preflight_style_not_the_old_magenta_panel():
    """El cuadro replica la 'Pre-Flight Validation' de cli-trading-binance:
    box.HEAVY, columnas cian/gris, add_section(), markup #ff0055 / #00ff00 /
    'bold #ffffff on #ff0000', impreso directo (sin Panel envolvente)."""
    import inspect
    src = inspect.getsource(render_pnl_box)
    for present in ('box=box.HEAVY', '"#00aaff"', '"#aaaaaa"',
                    '[#ff0055]', '[#00ff00]', 'bold #ffffff on #ff0000',
                    'table.add_section()', 'console.print(table)'):
        assert present in src, f"falta el estilo esperado: {present!r}"
    for absent in ('box.ROUNDED', 'border_style="magenta"', '[bold red]', '[bold green]',
                   'Panel(body', 'Panel(table'):
        assert absent not in src, f"quedó estilo viejo: {absent!r}"


def test_render_pnl_box_takes_no_engine_and_touches_no_db():
    import inspect
    params = set(inspect.signature(render_pnl_box).parameters)
    assert params == {"asset", "entry_price", "size_lots", "stop_loss", "take_profit"}
    src = inspect.getsource(render_pnl_box)
    for forbidden in ("engine", "Session(", "create_engine", "update_record_state", "session.commit", "journal.db", ".data/"):
        assert forbidden not in src, f"render_pnl_box referencia {forbidden!r} -- no debe tocar persistencia"


# --------------------------------------------------------------------------- #
# ubicación / guarda (source-level: el flujo completo no tiene test end-to-end)
# --------------------------------------------------------------------------- #
def test_box_is_called_exactly_once_in_the_tac_main_path():
    calls = [i for i, ln in enumerate(_MAIN_SRC.splitlines(), 1) if "render_pnl_box(" in ln and "def render_pnl_box" not in ln]
    assert len(calls) == 1, f"render_pnl_box debe llamarse en un solo lugar, no {calls}"
    call_line = calls[0]
    lines = _MAIN_SRC.splitlines()
    tp_prompt = next(i for i, ln in enumerate(lines, 1) if 'session.prompt("tp", get_mandatory_float' in ln)
    entry_time_def = next(i for i, ln in enumerate(lines, 1) if "def ask_entry_time():" in ln)
    # el cuadro va DESPUÉS de capturar los 4 campos y ANTES de Entry Time
    assert tp_prompt < call_line < entry_time_def


def test_no_trade_and_abort_branches_hardcode_zero_and_never_reach_the_box():
    # las 3 ramas cortas (no_trade, abort_trade, tier gate D/F) fijan los 4
    # campos a 0.0 y salen antes del while True del main path
    assert _MAIN_SRC.count("stop_loss=0.0, entry_price=0.0, size=0.0, take_profit=0.0") == 3


def test_guard_snapshot_is_a_list_not_a_tuple():
    # JSON-safe: session.state puede serializarse (pausa/resume) -> lista en ambos lados
    assert 'session.state["_pnl_box_snapshot"] = list(' in _MAIN_SRC
    assert re.search(r'_pnl_inputs = \[sl, entry_p, size, tp\]', _MAIN_SRC)
    assert 'session.state.get("_pnl_box_snapshot") != _pnl_inputs' in _MAIN_SRC


def test_guard_modificar_clears_field_and_continues():
    # "Modificar X": limpia session.state[<campo>] + el snapshot, y continue el while True
    block = _MAIN_SRC.split("_pnl_choice = render_pnl_box(")[1].split("def ask_entry_time")[0]
    assert 'session.state.pop(_pnl_choice, None)' in block
    assert 'session.state.pop("_pnl_box_snapshot", None)' in block
    assert re.search(r'\n\s+continue\n', block)
