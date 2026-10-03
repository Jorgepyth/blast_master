"""
T33b (spec 002): `python cli/main.py ...` falla con `No module named 'core'` si la raíz del repo no está en `sys.path`.
Solo funcionaba `python -m cli.main` (la función `trading` del usuario). El test corre el archivo en un subproceso,
desde una carpeta temporal y sin PYTHONPATH, como lo haría una terminal cualquiera. Solo pide `--help` del grupo, que
no abre ninguna DB.
"""
import os
import subprocess
import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent / "cli" / "main.py"


def test_running_the_file_directly_finds_the_repo_modules(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run([sys.executable, str(MAIN), "--help"], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "resolution-report" in result.stdout and "candles" in result.stdout
    assert not (tmp_path / ".data").exists()  # el --help del grupo no abre ninguna cuenta
