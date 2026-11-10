"""Mode Moyen — worker réel avec RapidOCR dans son venv dédié (R13).

Sauté si l'environnement `.venv-eval-medium` n'est pas présent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

import pytest

V2_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V2_ROOT / "src"))
from core.gui.worker_client import medium_python

MEDIUM = Path(medium_python())
if not MEDIUM.is_file() or "ocr_app" not in str(MEDIUM) and not os.environ.get("AOCRI_MEDIUM_VENV"):
    pytest.skip("venv Moyen absent", allow_module_level=True)

DATA = V2_ROOT / "tests" / "data"
MANIFEST = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
FACT = DATA / next(e["document"] for e in MANIFEST["documents"] if e["id"] == "fact_001")


def test_medium_worker_produces_rapidocr_blocks(tmp_path: Path) -> None:
    work = tmp_path / "medium éàç"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(V2_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [
            str(MEDIUM),
            "-u",
            "-m",
            "workers.worker_main",
            "--input",
            str(FACT),
            "--work-dir",
            str(work),
            "--dpi",
            "200",
            "--mode",
            "medium",
        ],
        cwd=V2_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    messages: Queue[dict] = Queue()

    def read() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if line:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue

    threading.Thread(target=read, daemon=True).start()
    assert process.stdin is not None
    process.stdin.write('{"type":"command","action":"start"}\n')
    process.stdin.flush()

    done = None
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            message = messages.get(timeout=0.5)
        except Empty:
            if process.poll() is not None:
                break
            continue
        if message.get("type") == "page_done":
            done = message
            break

    process.stdin.close()
    try:
        process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
    assert done is not None, "aucun page_done du worker Moyen"
    data = json.loads(Path(done["result_path"]).read_text(encoding="utf-8"))
    assert data["engine"] == "rapidocr"
    assert data["text"].strip()
    assert "Dupont" in data["text"] or "FACTURE" in data["text"]


def test_gui_medium_mode_enabled_when_venv_present(qtbot) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from core.gui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._modes["medium"].isEnabled()
    assert not window._modes["advanced"].isEnabled()
    assert not window._modes["approfondi"].isEnabled()
    window.close()
