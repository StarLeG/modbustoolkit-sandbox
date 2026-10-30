"""TASK-809: нагрузочный скрипт работает end-to-end через CLI (--embedded).

Короткий прогон (5 клиентов, 2 с) против сервера в том же процессе: скрипт
завершается без ошибок и печатает итоги (ops/s и перцентили задержки).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "load_test.py"


def test_load_script_embedded_smoke() -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--embedded", "--clients", "5", "--duration", "2"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert "ops/s" in run.stdout
    assert "ошибок: 0" in run.stdout
    assert "задержка, мс:" in run.stdout
