"""Tests UI du mode Approfondi (headless, offscreen).

Couvre : outils d'aperçu (zone → points PDF, clamp page), persistance du
store, panneau de filtres, transmission des paramètres deep au worker,
indisponibilité du mode sans backend Avancé.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("ocr_app.ui.main_window")

from PySide6.QtWidgets import QApplication

from ocr_app.core.data_model import Block, BlockType, Page
from ocr_app.ui import exclusion_store


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def deep_dir(tmp_path, monkeypatch):
    path = tmp_path / "deep store éàç"
    monkeypatch.setenv("AOCRI_DEEP_DIR", str(path))
    return path


# ---------------------------------------------------------------------------
# Preview : outils et zones
# ---------------------------------------------------------------------------

def test_preview_zone_tool_converts_scene_to_pdf_points(qapp):
    from ocr_app.ui.widgets.preview import PreviewPanel, PreviewTool

    preview = PreviewPanel()
    preview.resize(700, 900)
    preview.show_page(
        Page(number=1, blocks=[Block(type=BlockType.PARAGRAPH, text="x")])
    )

    preview.set_tool(PreviewTool.ZONE)
    preview.set_exclusion_zones([(10.0, 20.0, 110.0, 220.0)])
    zones = preview.get_exclusion_zones()
    assert len(zones) == 1
    assert zones[0] == pytest.approx((10.0, 20.0, 110.0, 220.0), abs=0.5)
    assert preview.zone_count() == 1

    preview.close()


def test_preview_zone_clamped_inside_page(qapp):
    from ocr_app.ui.widgets.preview import PreviewPanel, PreviewTool

    preview = PreviewPanel()
    preview.resize(700, 900)
    preview.show_page(Page(number=1, blocks=[]))

    # Zone débordant très largement la page : ramenée aux bornes de la page.
    preview.set_tool(PreviewTool.ZONE)
    preview.set_exclusion_zones([(-500.0, -500.0, 5000.0, 5000.0)])
    zones = preview.get_exclusion_zones()
    assert len(zones) == 1
    x0, y0, x1, y1 = zones[0]
    assert x0 >= -0.01 and y0 >= -0.01
    assert x1 <= 620.0 and y1 <= 880.0
    assert x1 > x0 and y1 > y0

    preview.close()


def test_preview_set_tool_switches_without_error(qapp):
    from ocr_app.ui.widgets.preview import PreviewPanel, PreviewTool

    preview = PreviewPanel()
    preview.resize(700, 900)
    preview.show_page(Page(number=1, blocks=[]))

    for tool in (PreviewTool.SELECT, PreviewTool.ZONE, PreviewTool.ERASE):
        preview.set_tool(tool)
        assert preview.tool() == tool

    preview.close()


# ---------------------------------------------------------------------------
# Store : persistance et roundtrip
# ---------------------------------------------------------------------------

def test_store_roundtrip_via_env(deep_dir, tmp_path):
    source = str(tmp_path / "acte éàç.pdf")
    zones = [(1.0, 2.0, 3.0, 4.0)]
    filters = {"contrast": 130, "denoise": 3}
    path = exclusion_store.save_state(source, 2, zones, filters)
    assert path.is_file()
    assert path.name == "page_002.json"

    state = exclusion_store.load_state(source, 2)
    assert state.zones == ((1.0, 2.0, 3.0, 4.0),)
    assert state.filters == filters


def test_store_corrupt_file_returns_defaults(deep_dir, tmp_path):
    source = str(tmp_path / "doc.pdf")
    path = exclusion_store.save_state(source, 1, [(1.0, 1.0, 2.0, 2.0)], {})
    path.write_text("{not valid json", encoding="utf-8")
    state = exclusion_store.load_state(source, 1)
    assert state.zones == ()
    assert not state.filters


# ---------------------------------------------------------------------------
# DeepPanel : signaux
# ---------------------------------------------------------------------------

def test_deep_panel_exposes_params_and_apply_signal(qapp):
    from ocr_app.ui.widgets.deep_panel import DeepPanel

    panel = DeepPanel()
    applied: list[bool] = []
    panel.apply_requested.connect(lambda: applied.append(True))

    params = panel.current_params()
    assert isinstance(params, dict)

    panel.apply_requested.emit()
    assert applied == [True]

    panel.set_state({"contrast": 140, "denoise": 3})
    assert isinstance(panel.get_filter_params(), dict)

    panel.close()


# ---------------------------------------------------------------------------
# TaskRunner : paramètres deep → extra_args (jamais vers fake_worker)
# ---------------------------------------------------------------------------

def test_task_runner_deep_params_become_extra_args(tmp_path, qapp):
    from ocr_app.ui.task_runner import TaskRunner
    from ocr_app.ui.worker_commands import WORKER_MAIN_MODULE

    runner = TaskRunner(
        worker_commands={
            "approfondi": (
                "python",
                "-m",
                WORKER_MAIN_MODULE,
                "--mode",
                "approfondi",
            )
        }
    )
    captured: dict = {}

    class SpyWorker:
        def start(self, total, work_dir, command=None, extra_args=()):
            captured["extra_args"] = list(extra_args)
            return True

        def stop(self, *a, **k):
            return None

        def cancel(self, *a, **k):
            return None

        @property
        def is_running(self):
            return False

    runner._worker = SpyWorker()  # type: ignore[assignment]

    source = tmp_path / "facture éàç.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    task = runner.create_page_task(str(source), "approfondi", 3)
    runner.set_task_deep_params(
        task.id, [(10.0, 20.0, 110.0, 220.0)], {"contrast": 140}
    )
    runner.enqueue(task.id)

    args = captured.get("extra_args", [])
    assert "--input" in args
    assert "--page" in args and args[args.index("--page") + 1] == "3"
    assert "--exclude" in args, f"args observés : {args}"
    assert "--filter-json" in args
    assert args[args.index("--exclude") + 1] == "10,20,110,220"
    filters = json.loads(args[args.index("--filter-json") + 1])
    assert filters["contrast"] == 140

    runner.shutdown()


def test_task_runner_never_passes_deep_args_to_fake_worker(tmp_path, qapp):
    from ocr_app.ui.task_runner import TaskRunner

    runner = TaskRunner()  # aucun mode réel → repli fake_worker
    captured: dict = {}

    class SpyWorker:
        def start(self, total, work_dir, command=None, extra_args=()):
            captured["extra_args"] = list(extra_args)
            captured["command"] = command
            return True

        def stop(self, *a, **k):
            return None

        def cancel(self, *a, **k):
            return None

        @property
        def is_running(self):
            return False

    runner._worker = SpyWorker()  # type: ignore[assignment]

    source = tmp_path / "scan éàç.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    task = runner.create_page_task(str(source), "simple", 1)
    runner.set_task_deep_params(task.id, [(1.0, 1.0, 9.0, 9.0)], {"contrast": 130})
    runner.enqueue(task.id)

    assert captured.get("command") is None  # fake_worker
    assert captured.get("extra_args") == []  # jamais les options deep

    runner.shutdown()


# ---------------------------------------------------------------------------
# Disponibilité du mode
# ---------------------------------------------------------------------------

def test_deep_mode_disabled_without_backend(qapp, monkeypatch):
    import ocr_app.ui.main_window as mw

    monkeypatch.setattr(
        mw,
        "resolve_deep_backend",
        lambda *a, **k: (
            None,
            "Le mode Approfondi requiert le backend Avancé (module Docling)",
        ),
        raising=True,
    )
    window = mw.MainWindow()
    assert window._deep_available is False
    window.close()
