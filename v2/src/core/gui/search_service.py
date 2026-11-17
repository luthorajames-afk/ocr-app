"""Service de recherche et similarité pour l'interface (noyau sans Qt).

Enveloppe ``core.indexing`` : indexation incrémentale des dossiers work
AOCRI, recherche hybride, documents similaires et doublons. Le modèle e5
n'est chargé qu'au premier besoin (lazy) et peut être remplacé par un
embedder de test.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from core.indexing.embedder import Embedder, OnnxE5Embedder
from core.indexing.search import (
    IndexReport,
    SearchHit,
    SimilarPair,
    index_work_dirs,
    search,
    similar_documents,
)
from core.indexing.store import CorpusIndex, DocumentMeta, default_index_dir


def default_work_root() -> Path:
    """Dossier des traitements AOCRI (``%LOCALAPPDATA%/AOCRI/work``)."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".aocri"
    return base / "AOCRI" / "work"


class SearchService:
    """Façade index/recherche utilisée par la fenêtre de recherche."""

    def __init__(
        self,
        *,
        index_dir: str | Path | None = None,
        work_root: str | Path | None = None,
        embedder: Embedder | None = None,
        backend: str | None = None,
    ) -> None:
        """``backend`` : numpy (défaut, sans code natif) ou chroma.

        Chroma est explicitement opt-in via ``backend="chroma"`` ou la
        variable ``AOCRI_INDEX_BACKEND`` : son client natif ne doit jamais
        s'initialiser implicitement au démarrage de l'application.
        """
        self._index_dir = Path(index_dir) if index_dir else default_index_dir()
        self._backend = backend or os.environ.get("AOCRI_INDEX_BACKEND", "numpy")
        self.index: CorpusIndex | None = None
        self.work_root = Path(work_root) if work_root else default_work_root()
        self._embedder = embedder
        self._model_error: str | None = None
        self._index_lock = threading.Lock()
        self._index: CorpusIndex | None = None
        self._index_init_error: str | None = None

    # ------------------------------------------------------------------ base
    def _build_index(self) -> CorpusIndex:
        """Construit l'index ; un échec de Chroma retombe sur numpy."""
        try:
            return CorpusIndex(self._index_dir, backend=self._backend)
        except Exception as exc:  # noqa: BLE001 — remonté via model_error
            self._index_init_error = str(exc)
            return CorpusIndex(self._index_dir, backend="numpy")

    def ensure_index(self) -> CorpusIndex:
        """Index garanti construit (lève si vraiment indisponible)."""
        if self.index is None:
            self.index = self._build_index()
        return self.index

    @property
    def index_init_error(self) -> str | None:
        return self._index_init_error

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            # Préchargement des DLL lourdes dans le thread appelant (main) :
            # les charger depuis un thread gèle le chargeur Windows.
            import onnxruntime  # noqa: F401 — préchargement DLL
            import transformers  # noqa: F401

            self._embedder = OnnxE5Embedder()
        return self._embedder

    @property
    def model_error(self) -> str | None:
        return self._model_error

    def documents(self) -> list[DocumentMeta]:
        if self.index is None:
            return []
        return self.index.documents()

    def status(self) -> tuple[int, int]:
        """(nombre de documents, nombre de pages) indexés."""
        if self.index is None:
            return 0, 0
        return len(self.index.documents()), self.index.page_count()

    def work_dirs(self) -> list[Path]:
        """Dossiers work contenant au moins une page JSON."""
        if not self.work_root.is_dir():
            return []
        return sorted(
            directory
            for directory in self.work_root.iterdir()
            if directory.is_dir() and any(directory.glob("page_*.json"))
        )

    # --------------------------------------------------------------- actions
    def index_new(self, *, replace: bool = False) -> IndexReport:
        self._model_error = None
        try:
            return index_work_dirs(
                self.work_dirs(), self.embedder, self.ensure_index(), replace=replace
            )
        except Exception as exc:
            self._model_error = str(exc)
            raise

    def search(self, query: str, k: int = 10, mode: str = "hybrid") -> list[SearchHit]:
        return search(self.ensure_index(), self.embedder, query, k=k, mode=mode)

    def similar(
        self, threshold: float = 0.75, *, source: str | None = None
    ) -> list[SimilarPair]:
        index = self.ensure_index()
        mode = "topk" if len(index.documents()) > 200 else "exact"
        pairs = similar_documents(index, threshold, mode=mode)
        if source:
            wanted = str(Path(source).resolve())
            pairs = [
                pair
                for pair in pairs
                if str(Path(pair.doc_a.source).resolve()) == wanted
                or str(Path(pair.doc_b.source).resolve()) == wanted
            ]
        return pairs

    def duplicates(self, threshold: float = 0.95) -> list[SimilarPair]:
        index = self.ensure_index()
        mode = "topk" if len(index.documents()) > 200 else "exact"
        return similar_documents(index, threshold, mode=mode)
