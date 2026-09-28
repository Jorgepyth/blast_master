"""
T20b (spec 002): pruebas de `tools/candle_sync.py` contra Windows REAL, opt-in.

Por defecto se saltean: la suite normal no toca Windows ni `/mnt/c` (NFR-2).
Para correrlas, en WSL con `powershell.exe` disponible:

    WINDOWS_INTEROP_TESTS=1 pytest -q tests/test_candle_sync_windows_interop.py

Solo lanzan comandos inofensivos de Windows (`cmd.exe /c echo`, `cmd.exe /c exit
7`, `ping` a localhost). NO usan MT5 ni el exportador real: eso es del spike (T22).
Verifican lo que los tests con exportador falso no pueden: cómo se comporta
PowerShell de verdad (código de salida, comillas, y que matar `powershell.exe`
desde WSL no mata al programa que lanzó).
"""
import os
import shutil
import subprocess
import time

import pytest

from tools.candle_sync import WINDOWS_CWD, powershell_argv, run_exporter

pytestmark = pytest.mark.skipif(
    os.environ.get("WINDOWS_INTEROP_TESTS") != "1" or shutil.which("powershell.exe") is None,
    reason="opt-in: WINDOWS_INTEROP_TESTS=1 en WSL con powershell.exe (lanza comandos inofensivos de Windows)",
)

CMD = "C:\\Windows\\System32\\cmd.exe"
PING = "C:\\Windows\\System32\\PING.EXE"


def _ping_processes() -> int:
    out = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq PING.EXE", "/NH"],
                         capture_output=True, cwd=WINDOWS_CWD).stdout.decode("cp850", "replace")
    return sum(1 for line in out.splitlines() if line.strip().upper().startswith("PING.EXE"))


def test_powershell_wrapper_preserves_the_exit_code_and_delivers_quoted_arguments():
    exit_seven = run_exporter(powershell_argv([CMD, "/c", "exit 7"]), 30, cwd=WINDOWS_CWD)
    assert exit_seven.returncode == 7  # sin `exit $LASTEXITCODE` PowerShell lo aplastaría a 1
    assert exit_seven.windows_pid is not None  # la línea WINPID llegó

    echo = run_exporter(powershell_argv([CMD, "/c", "echo", "x y", "O'Brien"]), 30, cwd=WINDOWS_CWD)
    assert echo.returncode == 0
    assert '"x y" O\'Brien' in echo.stdout  # espacios y apóstrofe llegaron intactos al .exe


def test_timeout_kills_the_windows_child_not_just_powershell():
    before = _ping_processes()

    run = run_exporter(powershell_argv([PING, "-n", "40", "127.0.0.1"]), 3, cwd=WINDOWS_CWD)

    assert run.timed_out
    assert run.windows_pid is not None
    assert run.windows_kill_error is None
    time.sleep(1)
    assert _ping_processes() <= before  # el PING.EXE de Windows también murió
