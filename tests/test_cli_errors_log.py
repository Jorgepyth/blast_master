"""`cli.main.log_cli_error`: el detalle de un error que cierra el CLI queda en `.data/cli_errors.log`."""
import cli.main as main


def _fail_in_an_audit():
    trade_id = "c77654ac-7368-4586-b5a5-a90b5f2c8810"  # noqa: F841 -- lo busca el log en las variables del camino
    raise ValueError("algo salió mal al guardar")


def _caught():
    try:
        _fail_in_an_audit()
    except ValueError as exc:
        return exc


def test_the_error_is_appended_with_menu_account_analysis_and_traceback(tmp_path):
    (tmp_path / "datos").mkdir()
    path = tmp_path / "datos" / "cli_errors.log"
    assert main.log_cli_error(_caught(), menu_choice="2", path=str(path)) == str(path)
    main.log_cli_error(_caught(), menu_choice="7", path=str(path))
    text = path.read_text(encoding="utf-8")
    assert text.count("ValueError: algo salió mal al guardar") >= 2
    assert "menú: 2" in text and "menú: 7" in text
    assert "análisis: c77654ac-7368-4586-b5a5-a90b5f2c8810" in text
    assert "Traceback (most recent call last)" in text and "_fail_in_an_audit" in text


def test_without_the_data_folder_nothing_is_created(tmp_path):
    path = tmp_path / "datos" / "cli_errors.log"
    assert main.log_cli_error(_caught(), path=str(path)) is None
    assert not (tmp_path / "datos").exists()


def test_it_never_raises_even_with_a_bad_path(tmp_path):
    (tmp_path / "datos").mkdir()
    bad = tmp_path / "datos"  # una carpeta, no un archivo: abrirla para escribir falla
    assert main.log_cli_error(_caught(), path=str(bad)) is None


def test_money_fields_left_empty_for_an_unverified_symbol_are_shown_not_crashed():
    # Regresión del 2026-10-16: el panel de revisión del Tactical Audit formateaba con :.2f un campo en None (US500,
    # BTC y US100 no tienen contract size verificado) y el CLI se cerraba al guardar.
    assert main.money_text(None) == "N/A (símbolo no verificado)"
    assert main.money_text(1234.5) == "1234.50"


def test_resuming_a_paused_audit_rebuilds_the_enums_of_custom_prompts(tmp_path, monkeypatch):
    # Regresión del 2026-10-16: al retomar b7aa8926, "stop_deviation_reason" volvía como texto y `.value` fallaba.
    import json
    cache = tmp_path / "paused.json"
    cache.write_text(json.dumps({"t1": {"tac:new": {
        "stop_deviation_reason": "discretionary_no_basis",
        "skip_reason": list(main.SkipReason)[0].value,
        "conf_status": list(main.ConfirmationStatus)[0].value}}}), encoding="utf-8")
    monkeypatch.setattr(main, "CACHE_FILE", str(cache))
    session = main.AuditSession("t1", "tac:new")
    assert isinstance(session.prompt("stop_deviation_reason", main.ask_stop_deviation_reason), main.StopDeviationReason)
    assert isinstance(session.prompt("skip_reason", lambda: None), main.SkipReason)
    assert isinstance(session.prompt("conf_status", lambda: None), main.ConfirmationStatus)
