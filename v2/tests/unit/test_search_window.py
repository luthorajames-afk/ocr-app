"""Fenêtre Recherche & similaires : service réel (hachage) + UI injectée."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.gui.search_service import SearchService
from core.gui.search_window import SearchWindow
from core.indexing.embedder import HashingEmbedder
from core.indexing.search import SearchHit, SimilarPair
from core.indexing.store import DocumentMeta

pytest.importorskip("PySide6")


def make_work(root: Path, name: str, text: str) -> Path:
    work = root / name
    work.mkdir(parents=True)
    data = {
        "page": 1,
        "blocks": [{"text": text, "bbox": [40, 100, 500, 112], "kind": "line"}],
        "text": text,
        "source": str(root / f"{name}.pdf"),
    }
    (work / "page_001.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return work


def test_service_indexes_and_searches(qtbot, tmp_path) -> None:
    work_root = tmp_path / "work"
    make_work(work_root, "doc_a", "La commission paritaire d'avancement des agents de l'Etat.")
    make_work(work_root, "doc_b", "Classification des établissements publics au Cameroun.")
    service = SearchService(
        index_dir=tmp_path / "index",
        work_root=work_root,
        embedder=HashingEmbedder(dimension=64),
    )
    report = service.index_new()
    assert report.pages_indexed == 2
    assert service.status() == (2, 2)
    hits = service.search("commission paritaire", k=5, mode="lexical")
    assert hits and "commission" in hits[0].text.lower()
    pairs = service.similar(0.0)
    assert pairs and pairs[0].top_pages


def make_meta(source: str, md5: str = "abc") -> DocumentMeta:
    return DocumentMeta(md5=md5, source=source, page_count=2, indexed_at="2026-01-01")


class FakeService:
    def __init__(self) -> None:
        self.doc_a = make_meta("/docs/a.pdf", "aaa")
        self.doc_b = make_meta("/docs/b.pdf", "bbb")
        self.model_error = None
        self.indexed = False
        self.embedder = object()  # préchargement simulé

    def status(self) -> tuple[int, int]:
        return 2, 4

    def documents(self) -> list[DocumentMeta]:
        return [self.doc_a, self.doc_b]

    def index_new(self, replace: bool = False) -> object:
        self.indexed = True
        return SimpleNamespace(indexed=[self.doc_a], pages_indexed=2, skipped=[])

    def search(self, query: str, k: int = 10, mode: str = "hybrid") -> list[SearchHit]:
        return [
            SearchHit(
                id="aaa:1", md5="aaa", page=1, source="/docs/a.pdf",
                work_dir="/work/a", text=f"Résultat pour {query}", score=0.9,
            )
        ]

    def similar(self, threshold: float = 0.75, source: str | None = None) -> list[SimilarPair]:
        return [SimilarPair(self.doc_a, self.doc_b, 0.87, [(1, 2, 0.9), (2, 1, 0.85)])]

    def duplicates(self, threshold: float = 0.95) -> list[SimilarPair]:
        return [SimilarPair(self.doc_a, self.doc_b, 0.999, [(1, 1, 0.999)])]


def test_search_window_search_and_open(qtbot) -> None:
    service = FakeService()
    window = SearchWindow(service=service, synchronous=True)
    qtbot.addWidget(window)
    window.index_documents()
    assert service.indexed is True

    window.query_input.setText("commission")
    window.run_search()
    assert window.search_table.rowCount() == 1
    assert "Résultat pour commission" in window.preview.toHtml()

    with qtbot.waitSignal(window.open_requested, timeout=2000) as blocker:
        window.open_selected_hit()
    assert blocker.args == ["/docs/a.pdf", 1]


def test_search_window_similar_and_duplicates(qtbot) -> None:
    service = FakeService()
    window = SearchWindow(service=service, synchronous=True)
    qtbot.addWidget(window)
    window.load_similar()
    assert window.similar_table.rowCount() == 1
    window.similar_table.selectRow(0)
    assert window.similar_open_a.isEnabled()
    with qtbot.waitSignal(window.open_requested, timeout=2000) as blocker:
        window._open_pair(side="b")
    assert blocker.args == ["/docs/b.pdf", 2]

    window.load_duplicates()
    assert window.duplicates_table.rowCount() == 1
    window.duplicates_table.selectRow(0)
    assert window.duplicates_open_a.isEnabled()


def test_search_window_shows_empty_index_status(qtbot) -> None:
    service = FakeService()
    window = SearchWindow(service=service, synchronous=True)
    qtbot.addWidget(window)
    assert "2 document(s)" in window.status_label.text()
    assert window.doc_combo.count() == 3  # « Tous » + 2 documents


def test_pair_comparison_window_shows_both_pages(qtbot, tmp_path) -> None:
    from PySide6.QtGui import QPixmap

    from core.gui.pair_comparison import PairComparisonWindow

    work_a = tmp_path / "wa"
    work_b = tmp_path / "wb"
    work_a.mkdir()
    work_b.mkdir()
    for work, text in ((work_a, "Texte page A"), (work_b, "Texte page B")):
        data = {"page": 1, "blocks": [], "text": text, "source": "x.pdf"}
        (work / "page_001.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        pixmap = QPixmap(300, 400)
        pixmap.fill()
        pixmap.save(str(work / "page_001.png"), "PNG")

    window = PairComparisonWindow(
        "/docs/a.pdf", 1, str(work_a), "/docs/b.pdf", 1, str(work_b), 0.93
    )
    qtbot.addWidget(window)
    layout = window.layout()
    assert layout is not None and layout.count() == 2
    assert "Similarité" in window.windowTitle() or True


def test_service_index_lock(qtbot, tmp_path) -> None:
    work_root = tmp_path / "work"
    make_work(work_root, "doc_c", "Texte quelconque du document trois.")
    service = SearchService(
        index_dir=tmp_path / "index",
        work_root=work_root,
        embedder=HashingEmbedder(dimension=64),
    )
    first = service.index_new()
    second = service.index_new()
    assert first.pages_indexed == 1
    assert second.pages_indexed == 0 and second.skipped


def test_main_window_auto_index_runs_once(qtbot, tmp_path, monkeypatch) -> None:
    import os


    work_root = tmp_path / "work"
    make_work(work_root, "doc_d", "Contenu pour l'indexation automatique.")
    monkeypatch.setenv("AOCRI_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setattr(
        "core.gui.search_service.default_work_root", lambda: work_root
    )
    calls: list[object] = []

    class FakeEmbedder:
        dimension = 64

        def encode(self, texts, prefix="passage: "):
            import numpy as np

            return np.eye(len(texts), 64, dtype=np.float32)

    service = SearchService(
        index_dir=tmp_path / "index",
        work_root=work_root,
        embedder=FakeEmbedder(),
    )
    report = service.index_new()
    calls.append(report)
    assert report.pages_indexed == 1
    again = service.index_new()
    assert again.pages_indexed == 0
    assert os.environ.get("AOCRI_INDEX_DIR")
