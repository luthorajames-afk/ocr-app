"""Fenêtre « Recherche & documents similaires » (maquette validée).

Trois onglets : Rechercher (hybride/sémantique/lexical), Documents
similaires (Top-3 pages) et Doublons (score ≥ 0,95). Aucun traitement
lourd dans le thread d'interface : les indexations/recherches passent par
un QThread dédié. Les tests utilisent ``synchronous=True`` avec un
embedder de hachage.
"""

from __future__ import annotations

import csv
import html
import re
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.gui.pair_comparison import PairComparisonWindow
from core.gui.search_service import SearchService
from core.indexing.search import SearchHit, SimilarPair

T = TypeVar("T")

_SEARCH_MODES = (("hybride", "Hybride"), ("semantique", "Sémantique"), ("lexical", "Lexical"))


class _Job(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, function: Callable[[], object]) -> None:
        super().__init__()
        self._function = function

    def run(self) -> None:  # pragma: no cover — thread réel
        try:
            self.done.emit(self._function())
        except Exception as exc:  # noqa: BLE001 — remonté à l'IHM
            self.failed.emit(str(exc))


def _short(text: str, limit: int = 220) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _as_hits(value: object) -> list[SearchHit]:
    return [item for item in value if isinstance(item, SearchHit)] if isinstance(value, list) else []


def _as_pairs(value: object) -> list[SimilarPair]:
    return [item for item in value if isinstance(item, SimilarPair)] if isinstance(value, list) else []


class SearchWindow(QDialog):
    """Recherche locale, documents liés et doublons dans l'index AOCRI."""

    open_requested = Signal(str, int)

    def __init__(
        self,
        service: SearchService | None = None,
        *,
        current_source: str | None = None,
        synchronous: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or SearchService()
        self.current_source = current_source
        self.synchronous = synchronous
        self._thread: _Job | None = None
        self._busy = False
        self._last_query = ""
        self._search_hits: list[SearchHit] = []
        self._similar_pairs: list[SimilarPair] = []
        self._duplicate_pairs: list[SimilarPair] = []
        self._pair_windows: list[PairComparisonWindow] = []

        self.setWindowTitle("AOCRI — Recherche & documents similaires")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.resize(1160, 780)

        header = QHBoxLayout()
        title = QLabel("Recherche & documents similaires")
        title.setObjectName("Title")
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.index_btn = QPushButton("Indexer les nouveaux documents")
        self.index_btn.setToolTip("Indexe les pages déjà traitées (aucun ré-OCR)")
        self.index_btn.clicked.connect(self.index_documents)
        header.addWidget(title)
        header.addWidget(self.status_label)
        header.addStretch(1)
        header.addWidget(self.index_btn)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_search_tab(), "Rechercher")
        self.tabs.addTab(self._build_similar_tab(), "Documents similaires")
        self.tabs.addTab(self._build_duplicates_tab(), "Doublons")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)

        self.refresh_status()
        self._populate_documents()

    # ------------------------------------------------------------------ onglets
    def _build_search_tab(self) -> QWidget:
        page = QWidget(self)
        self.query_input = QLineEdit(page)
        self.query_input.setPlaceholderText("Rechercher dans les documents traités…")
        self.query_input.returnPressed.connect(self.run_search)
        self.mode_group = QButtonGroup(self)
        mode_layout = QHBoxLayout()
        for index, (key, label) in enumerate(_SEARCH_MODES):
            button = QPushButton(label, page)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setProperty("mode", key)
            self.mode_group.addButton(button)
            mode_layout.addWidget(button)
        self.search_btn = QPushButton("Rechercher", page)
        self.search_btn.setObjectName("Primary")
        self.search_btn.clicked.connect(self.run_search)
        self.query_input.setMinimumHeight(34)
        row = QHBoxLayout()
        row.addWidget(self.query_input, 1)
        row.addLayout(mode_layout)
        row.addWidget(self.search_btn)

        self.search_meta = QLabel("", page)
        self.search_meta.setObjectName("Muted")

        self.search_table = QTableWidget(0, 4, page)
        self.search_table.setHorizontalHeaderLabels(["Score", "Document", "Page", "Extrait"])
        self.search_table.setColumnWidth(0, 70)
        self.search_table.setColumnWidth(1, 290)
        self.search_table.setColumnWidth(2, 60)
        self.search_table.horizontalHeader().setStretchLastSection(True)
        self.search_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.search_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.search_table.setWordWrap(True)
        self.search_table.itemSelectionChanged.connect(self._show_selected_hit)
        self.search_table.itemDoubleClicked.connect(lambda _item: self.open_selected_hit())

        self.preview = QTextBrowser(page)
        self.preview.setOpenExternalLinks(False)
        self.open_btn = QPushButton("Ouvrir dans l'application", page)
        self.open_btn.setObjectName("Primary")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_selected_hit)
        preview_box = QWidget(page)
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(QLabel("Aperçu de la page", preview_box))
        preview_layout.addWidget(self.preview, 1)
        preview_layout.addWidget(self.open_btn)

        split = QSplitter(Qt.Orientation.Horizontal, page)
        split.addWidget(self.search_table)
        split.addWidget(preview_box)
        split.setSizes([740, 360])

        layout = QVBoxLayout(page)
        layout.addLayout(row)
        layout.addWidget(self.search_meta)
        layout.addWidget(split, 1)
        return page

    def _build_similar_tab(self) -> QWidget:
        page = QWidget(self)
        self.doc_combo = QComboBox(page)
        self.doc_combo.addItem("Tous les documents", "")
        self.threshold = QDoubleSpinBox(page)
        self.threshold.setRange(0.5, 0.99)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.threshold.setValue(0.75)
        recalc = QPushButton("Recalculer", page)
        recalc.clicked.connect(self.load_similar)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Document :", page))
        toolbar.addWidget(self.doc_combo, 1)
        toolbar.addWidget(QLabel("Seuil :", page))
        toolbar.addWidget(self.threshold)
        toolbar.addWidget(recalc)

        self.similar_table = QTableWidget(0, 4, page)
        self.similar_table.setHorizontalHeaderLabels(
            ["Score", "Document A", "Document B", "Pages proches"]
        )
        self.similar_table.setColumnWidth(0, 70)
        self.similar_table.setColumnWidth(1, 330)
        self.similar_table.setColumnWidth(2, 330)
        self.similar_table.horizontalHeader().setStretchLastSection(True)
        self.similar_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.similar_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.similar_table.itemSelectionChanged.connect(self._update_similar_buttons)

        open_a = QPushButton("Ouvrir A", page)
        open_a.clicked.connect(lambda: self._open_pair(side="a"))
        open_b = QPushButton("Ouvrir B", page)
        open_b.clicked.connect(lambda: self._open_pair(side="b"))
        self.compare_pair_btn = QPushButton("Comparer côte à côte", page)
        self.compare_pair_btn.setObjectName("Primary")
        self.compare_pair_btn.setEnabled(False)
        self.compare_pair_btn.clicked.connect(self.compare_selected_pair)
        export = QPushButton("Exporter les paires (CSV)", page)
        export.clicked.connect(self.export_pairs)
        self.similar_open_a = open_a
        self.similar_open_b = open_b
        actions = QHBoxLayout()
        actions.addWidget(open_a)
        actions.addWidget(open_b)
        actions.addWidget(self.compare_pair_btn)
        actions.addStretch(1)
        actions.addWidget(export)

        layout = QVBoxLayout(page)
        layout.addLayout(toolbar)
        layout.addWidget(self.similar_table, 1)
        layout.addLayout(actions)
        return page

    def _build_duplicates_tab(self) -> QWidget:
        page = QWidget(self)
        note = QLabel(
            "Doublons stricts (score ≥ 0,95). Aucune suppression automatique : "
            "ouvre les deux documents avant de décider.",
            page,
        )
        note.setObjectName("Muted")
        self.duplicates_table = QTableWidget(0, 4, page)
        self.duplicates_table.setHorizontalHeaderLabels(
            ["Score", "Document A", "Document B", "Pages proches"]
        )
        self.duplicates_table.setColumnWidth(0, 70)
        self.duplicates_table.setColumnWidth(1, 330)
        self.duplicates_table.setColumnWidth(2, 330)
        self.duplicates_table.horizontalHeader().setStretchLastSection(True)
        self.duplicates_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.duplicates_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.duplicates_table.itemSelectionChanged.connect(
            self._update_duplicate_buttons
        )
        refresh = QPushButton("Recalculer", page)
        refresh.clicked.connect(self.load_duplicates)
        open_a = QPushButton("Ouvrir A", page)
        open_a.clicked.connect(lambda: self._open_pair(side="a", duplicates=True))
        open_b = QPushButton("Ouvrir B", page)
        open_b.clicked.connect(lambda: self._open_pair(side="b", duplicates=True))
        self.duplicates_open_a = open_a
        self.duplicates_open_b = open_b
        self.duplicates_compare_btn = QPushButton("Comparer côte à côte", page)
        self.duplicates_compare_btn.setObjectName("Primary")
        self.duplicates_compare_btn.setEnabled(False)
        self.duplicates_compare_btn.clicked.connect(
            lambda: self.compare_selected_pair(duplicates=True)
        )
        actions = QHBoxLayout()
        actions.addWidget(refresh)
        actions.addWidget(open_a)
        actions.addWidget(open_b)
        actions.addWidget(self.duplicates_compare_btn)
        actions.addStretch(1)
        layout = QVBoxLayout(page)
        layout.addWidget(note)
        layout.addWidget(self.duplicates_table, 1)
        layout.addLayout(actions)
        return page

    # ------------------------------------------------------------------ état
    def refresh_status(self) -> None:
        documents, pages = self.service.status()
        suffix = ""
        if self.service.model_error:
            suffix = f" · modèle : {self.service.model_error}"
        self.status_label.setText(
            f"index local : {documents} document(s) · {pages} page(s){suffix}"
        )
        self._populate_documents()

    def _populate_documents(self) -> None:
        chosen = self.doc_combo.currentData()
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        self.doc_combo.addItem("Tous les documents", "")
        for meta in self.service.documents():
            label = Path(meta.source).name if meta.source else meta.md5
            self.doc_combo.addItem(label, meta.source)
        if self.current_source:
            wanted = str(Path(self.current_source).resolve())
            for index in range(self.doc_combo.count()):
                data = str(self.doc_combo.itemData(index) or "")
                if data and str(Path(data).resolve()) == wanted:
                    self.doc_combo.setCurrentIndex(index)
                    break
        elif chosen:
            for index in range(self.doc_combo.count()):
                if self.doc_combo.itemData(index) == chosen:
                    self.doc_combo.setCurrentIndex(index)
                    break
        self.doc_combo.blockSignals(False)

    # ------------------------------------------------------------ exécution
    def _run(
        self,
        function: Callable[[], object],
        on_done: Callable[[object], None],
        label: str,
        needs_embedder: bool = False,
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self.status_label.setText(label)
        self.index_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        if needs_embedder:
            # Charger le modèle dans le thread principal : le faire depuis un
            # QThread fait échouer le chargeur DLL de Windows (build gelé).
            try:
                _ = self.service.embedder
            except Exception as exc:  # noqa: BLE001 — remonté au statut
                self._finished()
                self.status_label.setText(
                    f"Modèle de recherche indisponible : {exc}"
                )
                return
        if self.synchronous:
            try:
                result = function()
            except Exception as exc:  # noqa: BLE001
                self._finished()
                self.status_label.setText(f"Erreur : {exc}")
                return
            self._finished()
            on_done(result)
            return
        job = _Job(function)
        job.done.connect(lambda result: self._job_done(result, on_done))
        job.failed.connect(self._job_failed)
        job.finished.connect(job.deleteLater)
        self._thread = job
        job.start()

    def _job_done(self, result: object, on_done: Callable[[object], None]) -> None:
        self._finished()
        on_done(result)

    def _job_failed(self, message: str) -> None:
        self._finished()
        self.status_label.setText(f"Erreur : {message}")

    def _finished(self) -> None:
        self._busy = False
        self._thread = None
        self.index_btn.setEnabled(True)
        self.search_btn.setEnabled(True)
        self.refresh_status()

    # --------------------------------------------------------------- actions
    def index_documents(self) -> None:
        self._run(
            self.service.index_new,
            self._on_indexed,
            "Indexation des documents traités…",
            needs_embedder=True,
        )

    def _on_indexed(self, report: object) -> None:
        from core.indexing.search import IndexReport

        if isinstance(report, IndexReport):
            self.status_label.setText(
                f"{len(report.indexed)} document(s), {report.pages_indexed} page(s) "
                f"indexée(s) · {len(report.skipped)} ignoré(s)"
            )
        self.refresh_status()

    def _selected_mode(self) -> str:
        button = self.mode_group.checkedButton()
        if button is None:
            return "hybride"
        mode = str(button.property("mode") or "hybride")
        return "semantic" if mode == "semantique" else mode

    def run_search(self) -> None:
        query = self.query_input.text().strip()
        if not query:
            self.status_label.setText("Saisis une requête")
            return
        self._last_query = query
        mode = self._selected_mode()
        self._run(
            lambda: self.service.search(query, k=20, mode=mode),
            self._on_search,
            "Recherche…",
            needs_embedder=mode in ("hybrid", "semantic"),
        )

    def _on_search(self, hits: object) -> None:
        self._search_hits = _as_hits(hits)
        self._fill_search_table()
        self.search_meta.setText(
            f"{len(self._search_hits)} résultat(s) · {self._search_hits and 'top 20 pages' or ''}".strip()
        )

    def _fill_search_table(self) -> None:
        self.search_table.setRowCount(len(self._search_hits))
        for row, hit in enumerate(self._search_hits):
            score = QTableWidgetItem(f"{hit.score:.3f}")
            score.setData(Qt.ItemDataRole.UserRole, hit)
            self.search_table.setItem(row, 0, score)
            self.search_table.setItem(row, 1, QTableWidgetItem(Path(hit.source).name))
            self.search_table.setItem(row, 2, QTableWidgetItem(str(hit.page)))
            excerpt = QTableWidgetItem(_short(hit.text, 280))
            excerpt.setToolTip(hit.text[:2000])
            self.search_table.setItem(row, 3, excerpt)
        if self._search_hits:
            self.search_table.selectRow(0)

    def _selected_hit(self) -> SearchHit | None:
        rows = self.search_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.search_table.item(rows[0].row(), 0)
        hit = item.data(Qt.ItemDataRole.UserRole) if item else None
        return hit if isinstance(hit, SearchHit) else None

    def _show_selected_hit(self) -> None:
        hit = self._selected_hit()
        self.open_btn.setEnabled(hit is not None)
        if hit is None:
            self.preview.setHtml("")
            return
        self.preview.setHtml(self._highlighted_html(hit.text, self._last_query))

    @staticmethod
    def _highlighted_html(text: str, query: str) -> str:
        escaped = html.escape(text)
        terms = [
            re.escape(term)
            for term in re.split(r"\W+", query)
            if len(term) >= 3
        ]
        if terms:
            pattern = re.compile("(" + "|".join(terms) + ")", re.IGNORECASE)
            escaped = pattern.sub(r"<mark>\1</mark>", escaped)
        return f"<div style='white-space:pre-wrap'>{escaped}</div>"

    def open_selected_hit(self) -> None:
        hit = self._selected_hit()
        if hit is None:
            return
        self.open_requested.emit(hit.source, hit.page)

    # ------------------------------------------------------------- similaires
    def load_similar(self) -> None:
        source = str(self.doc_combo.currentData() or "")
        threshold = float(self.threshold.value())
        self._run(
            lambda: self.service.similar(threshold, source=source or None),
            self._on_similar,
            "Calcul des documents similaires…",
        )

    def _on_similar(self, pairs: object) -> None:
        self._similar_pairs = _as_pairs(pairs)
        self._fill_pair_table(self.similar_table, self._similar_pairs)
        self._update_similar_buttons()

    def _fill_pair_table(
        self, table: QTableWidget, pairs: list[SimilarPair]
    ) -> None:
        table.setRowCount(len(pairs))
        for row, pair in enumerate(pairs):
            item = QTableWidgetItem(f"{pair.score:.3f}")
            item.setData(Qt.ItemDataRole.UserRole, pair)
            table.setItem(row, 0, item)
            table.setItem(row, 1, QTableWidgetItem(Path(pair.doc_a.source).name))
            table.setItem(row, 2, QTableWidgetItem(Path(pair.doc_b.source).name))
            pages = " · ".join(
                f"{a}↔{b} ({score:.3f})" for a, b, score in pair.top_pages
            )
            table.setItem(row, 3, QTableWidgetItem(pages))

    def _selected_pair(self, duplicates: bool = False) -> SimilarPair | None:
        table = self.duplicates_table if duplicates else self.similar_table
        rows = table.selectionModel().selectedRows()
        if not rows:
            return None
        item = table.item(rows[0].row(), 0)
        pair = item.data(Qt.ItemDataRole.UserRole) if item else None
        return pair if isinstance(pair, SimilarPair) else None

    def _update_similar_buttons(self) -> None:
        has = self._selected_pair() is not None
        self.similar_open_a.setEnabled(has)
        self.similar_open_b.setEnabled(has)
        self.compare_pair_btn.setEnabled(has)

    def _update_duplicate_buttons(self) -> None:
        has = self._selected_pair(duplicates=True) is not None
        self.duplicates_open_a.setEnabled(has)
        self.duplicates_open_b.setEnabled(has)
        self.duplicates_compare_btn.setEnabled(has)

    def _open_pair(self, side: str, duplicates: bool = False) -> None:
        pair = self._selected_pair(duplicates=duplicates)
        if pair is None or not pair.top_pages:
            return
        page_a, page_b, _score = pair.top_pages[0]
        if side == "a":
            self.open_requested.emit(pair.doc_a.source, page_a)
        else:
            self.open_requested.emit(pair.doc_b.source, page_b)

    def compare_selected_pair(self, duplicates: bool = False) -> None:
        """Ouvre les deux pages de la paire côte à côte (images + textes)."""
        pair = self._selected_pair(duplicates=duplicates)
        if pair is None or not pair.top_pages:
            return
        page_a, page_b, score = pair.top_pages[0]
        window = PairComparisonWindow(
            pair.doc_a.source,
            page_a,
            pair.doc_a.work_dir,
            pair.doc_b.source,
            page_b,
            pair.doc_b.work_dir,
            score,
        )
        window.show()
        self._pair_windows.append(window)

    # -------------------------------------------------------------- doublons
    def load_duplicates(self) -> None:
        self._run(
            self.service.duplicates,
            self._on_duplicates,
            "Recherche des doublons…",
        )

    def _on_duplicates(self, pairs: object) -> None:
        self._duplicate_pairs = _as_pairs(pairs)
        self._fill_pair_table(self.duplicates_table, self._duplicate_pairs)
        self._update_duplicate_buttons()

    def export_pairs(self) -> None:
        """Exporte les paires de l'onglet courant en CSV."""
        pairs = self._similar_pairs or self._duplicate_pairs
        if not pairs:
            self.status_label.setText("Aucune paire à exporter")
            return
        target, _filter = QFileDialog.getSaveFileName(
            self, "Exporter les paires", "paires_similaires.csv", "CSV (*.csv)"
        )
        if not target:
            return
        with Path(target).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["score", "document_a", "document_b", "pages"])
            for pair in pairs:
                pages = ";".join(f"{a}-{b}:{score:.3f}" for a, b, score in pair.top_pages)
                writer.writerow([f"{pair.score:.3f}", pair.doc_a.source, pair.doc_b.source, pages])
        self.status_label.setText(f"Exporté : {target}")
