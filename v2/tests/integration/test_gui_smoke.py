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
    from core.gui.worker_client import advanced_available

    # Les modes optionnels sont grisés quand leur environnement manque.
    expected = advanced_available()
    assert window._modes["advanced"].isEnabled() is expected
    assert window._modes["approfondi"].isEnabled() is expected
    if window._modes["medium"].isEnabled():
        window._modes["medium"].click()
        assert window._mode == "medium"
        assert window._modes["medium"].isChecked()
        assert not window._modes["simple"].isChecked()
    else:
        window._modes["simple"].click()
        assert window._mode == "simple"


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


def test_task_card_shows_progress_bar_under_name(window, qtbot) -> None:
    entry = BY_ID["fact_001"]
    assert window.add_file(str(DATA / entry["document"]))
    task = window._current_task
    qtbot.waitUntil(lambda: task.status.value == "done", timeout=120_000)
    widget = window.sidebar._widgets[task.id]
    assert widget.name.text() == Path(task.source_path).name
    assert widget.progress.value() == 100
    assert widget.badge.text() == "Terminé"
    assert widget.progress.y() > widget.name.y()  # barre sous le nom


def test_multi_page_navigation_zoom_and_detach(window, qtbot) -> None:
    entry = BY_ID["mixed_001"]
    assert window.add_file(str(DATA / entry["document"]))
    task = window._current_task
    qtbot.waitUntil(lambda: task.status.value == "done", timeout=180_000)
    assert task.total_pages >= 2
    assert window.preview.page_label.text().startswith("page 2 /")  # dernière page traitée

    window.preview.prev_btn.click()
    assert window.preview.page_label.text().startswith("page 1 /")
    window.preview.next_btn.click()
    assert window.preview.page_label.text().startswith("page 2 /")

    scale_before = window.preview.view.transform().m11()
    window.preview.zoom_in_btn.click()
    assert window.preview.view.transform().m11() > scale_before
    window.preview.fit_btn.click()

    window.preview.detach_btn.click()
    assert window.preview._detached is not None
    assert window.preview._detached.parent() is None  # fenêtre autonome
    assert window.preview.compare.isVisible() is False  # l'original remplit l'espace
    assert window.preview.compare_btn.isEnabled() is False
    assert window.preview._detached.browser.toPlainText().strip()

    # Déplacement libre (autre écran) : la fenêtre est de premier niveau.
    window.preview._detached.move(120, 90)
    assert window.preview._detached.pos().x() == 120

    # Fermeture par la croix : ré-épinglage automatique.
    window.preview._detached.close()
    assert window.preview._detached is None
    assert window.preview.compare.isVisible() is True


def test_rendered_columns_are_side_by_side(qtbot) -> None:
    """Les pages multi-colonnes sont rendues en tableau HTML (colonnes côte à côte)."""
    from core.gui.main_window import ResultsPanel

    blocks = []
    for index in range(6):
        blocks.append(
            {"text": f"gauche {index}", "bbox": [45, 40 + index * 18, 250, 55], "kind": "line"}
        )
    for index in range(6):
        blocks.append(
            {"text": f"droite {index}", "bbox": [305, 40 + index * 18, 520, 55], "kind": "line"}
        )
    panel = ResultsPanel()
    qtbot.addWidget(panel)
    panel.show_record({"page": 1, "text": "gauche droite", "blocks": blocks})
    html = panel.rendered.toHtml()
    assert "vertical-align:top" in html or "vertical-align: top" in html


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


def test_second_file_starts_after_first_completes(window, qtbot) -> None:
    first = BY_ID["fact_001"]
    second = BY_ID["fact_002"]
    assert window.add_file(str(DATA / first["document"]))
    assert window.add_file(str(DATA / second["document"]))  # en file d'attente
    tasks = window._runner.tasks()
    assert len(tasks) == 2
    qtbot.waitUntil(
        lambda: all(task.status.value == "done" for task in tasks), timeout=180_000
    )
    assert tasks[0].pages_done == 1 and tasks[1].pages_done == 1


def test_edit_text_persists_and_marks_dirty(window, qtbot) -> None:
    """R9 — édition avant copie : sauvegarde page + marqueur « ● »."""
    entry = BY_ID["fact_001"]
    assert window.add_file(str(DATA / entry["document"]))
    task = window._current_task
    qtbot.waitUntil(lambda: task.status.value == "done", timeout=120_000)
    window.results.plain.setPlainText("TEXTE ÉDITÉ éàç")
    qtbot.waitUntil(lambda: "●" in window.windowTitle(), timeout=5_000)
    record = json.loads(
        (Path(task.work_dir) / "page_001.json").read_text(encoding="utf-8")
    )
    assert record["text"] == "TEXTE ÉDITÉ éàç"


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
