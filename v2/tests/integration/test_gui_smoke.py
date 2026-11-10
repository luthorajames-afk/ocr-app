"""T4.1/T4.2/T5 — Tests IHM (pytest-qt, offscreen)."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")


from core.gui.main_window import MainWindow
from core.gui.worker_client import WorkerClient

V2_ROOT = Path(__file__).resolve().parents[2]
DATA = V2_ROOT / "tests" / "data"
MANIFEST = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
BY_ID = {entry["id"]: entry for entry in MANIFEST["documents"]}


@pytest.fixture()
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("AOCRI_WORK_DIR", str(tmp_path / "work éàç"))
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    yield win
    win.close()


def test_window_title_and_modes(window) -> None:
    assert "AOCRI" in window.windowTitle()
    assert "v2.0" in window.windowTitle()
    assert set(window._modes) == {"simple", "medium", "advanced", "approfondi"}
    assert window._modes["simple"].isChecked()


def test_mode_selection_updates_state(window) -> None:
    window._modes["advanced"].click()
    assert window._mode == "advanced"
    assert window._modes["advanced"].isChecked()
    assert not window._modes["simple"].isChecked()


def test_add_file_starts_task_and_shows_result(window, qtbot) -> None:
    entry = BY_ID["fact_001"]
    assert window.add_file(str(DATA / entry["document"]))
    task = window._current_task
    assert task is not None
    qtbot.waitUntil(lambda: task.status.value == "done", timeout=120_000)
    assert task.pages_done == 1
    assert (Path(task.work_dir) / "page_001.json").is_file()
    assert window.results.plain.toPlainText().strip()
    assert window.bottom.copy_btn.isEnabled()
    assert window.bottom.export_btn.isEnabled()


def test_worker_client_protocol_messages(qtbot, tmp_path) -> None:
    entry = BY_ID["fact_001"]
    client = WorkerClient()
    progresses: list[tuple[int, int, str]] = []
    done: list[tuple[int, str]] = []
    client.progress.connect(lambda p, t, s: progresses.append((p, t, s)))
    client.page_done.connect(lambda p, r: done.append((p, r)))
    assert client.start(DATA / entry["document"], tmp_path / "wc éàç")
    qtbot.waitUntil(lambda: bool(done), timeout=120_000)
    assert any(stage == "ocr" for _p, _t, stage in progresses)
    client.stop()


def test_export_writes_text(window, qtbot, tmp_path, monkeypatch) -> None:
    entry = BY_ID["fact_001"]
    assert window.add_file(str(DATA / entry["document"]))
    task = window._current_task
    qtbot.waitUntil(lambda: task.status.value == "done", timeout=120_000)

    target = tmp_path / "export éàç.txt"
    monkeypatch.setattr(
        "core.gui.main_window.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), "Texte (*.txt)"),
    )
    window._export()
    assert target.is_file()
    assert "Dupont" in target.read_text(encoding="utf-8")
