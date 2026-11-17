"""Index persistant du corpus : métadonnées, vecteurs et index lexical BM25.

Stockage canonique sous ``%LOCALAPPDATA%\\AOCRI\\index`` (surchargeable par
``AOCRI_INDEX_DIR``) : JSON pour les documents/pages, ``vectors.npy`` pour les
embeddings et ``bm25.json`` pour le corpus tokenisé. ChromaDB sert de miroir
vectoriel optionnel ; en son absence, la recherche sémantique utilise numpy.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from core.indexing.embedder import EMBED_DIM

# ChromaDB ne doit jamais tenter de télémétrie réseau (application hors ligne).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

logger = logging.getLogger(__name__)

INDEX_VERSION = 1
COLLECTION_NAME = "aocri_pages"
DOCUMENTS_FILE = "documents.json"
PAGES_FILE = "pages.json"
VECTORS_FILE = "vectors.npy"
BM25_FILE = "bm25.json"
CHROMA_DIR = "chroma"

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_PAGE_FILE_RE = re.compile(r"^page_(\d+)\.json$")

FRENCH_STOPWORDS: set[str] = {
    "le", "la", "les", "un", "une", "des", "du", "de", "d", "l",
    "et", "ou", "mais", "donc", "or", "ni", "car",
    "que", "qui", "quoi", "dont", "où",
    "ce", "cet", "cette", "ces",
    "son", "sa", "ses", "mon", "ma", "mes", "ton", "ta", "tes",
    "notre", "nos", "votre", "vos", "leur", "leurs",
    "il", "elle", "ils", "elles", "on", "nous", "vous", "je", "tu",
    "est", "sont", "était", "être", "été",
    "ai", "as", "a", "avons", "avez", "ont", "avait", "avaient",
    "pour", "par", "avec", "sans", "sur", "sous",
    "dans", "chez", "vers", "pendant", "avant", "après", "entre",
    "plus", "moins", "très", "bien", "tout", "tous", "toute", "toutes",
    "comme", "si", "ne", "pas", "n", "m", "s", "t", "qu",
    "lorsque", "lorsqu", "puis", "aussi", "ainsi",
    "ceci", "cela", "y", "en", "the", "of", "and", "to", "in",
}


def tokenize(text: str) -> list[str]:
    """Minuscules + mots Unicode, sans mots vides ni lettres isolées."""
    if not text:
        return []
    return [
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 1 and token not in FRENCH_STOPWORDS
    ]


def default_index_dir() -> Path:
    """Répertoire d'index : ``AOCRI_INDEX_DIR`` sinon ``%LOCALAPPDATA%/AOCRI/index``."""
    env = os.environ.get("AOCRI_INDEX_DIR")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".aocri"
    return base / "AOCRI" / "index"


def validate_doc_id(md5: str) -> str:
    """Normalise et valide un MD5 hexadécimal (protection des chemins/IDs)."""
    if not isinstance(md5, str):
        raise TypeError("md5 doit être une chaîne")
    normalised = md5.strip().lower()
    if not _MD5_RE.fullmatch(normalised):
        raise ValueError(f"md5 invalide : {md5!r} (32 caractères hexadécimaux attendus)")
    return normalised


def _page_id(md5: str, page: int) -> str:
    return f"{md5}:p{page:03d}"


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    """Métadonnées d'un document indexé."""

    md5: str
    source: str
    page_count: int
    indexed_at: str
    work_dir: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "md5": self.md5,
            "source": self.source,
            "page_count": self.page_count,
            "indexed_at": self.indexed_at,
            "work_dir": self.work_dir,
        }


@dataclass(frozen=True, slots=True)
class PageInput:
    """Page candidate à l'indexation (texte déjà extrait, aucun OCR)."""

    page: int
    text: str


@dataclass(frozen=True, slots=True)
class PageVector:
    """Page indexée avec son vecteur normalisé."""

    id: str
    md5: str
    page: int
    source: str
    work_dir: str
    text: str
    vector: np.ndarray


@dataclass(frozen=True, slots=True)
class PageHit:
    """Résultat de recherche au niveau page."""

    id: str
    md5: str
    page: int
    source: str
    work_dir: str
    text: str
    score: float


# ---------------------------------------------------------------------------
# Lecture/écriture robuste
# ---------------------------------------------------------------------------


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _write_json_atomic(path: Path, payload: object) -> None:
    """Écriture JSON atomique (temp + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _write_npy_atomic(path: Path, matrix: np.ndarray) -> None:
    """Écriture numpy atomique."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".npy", dir=str(path.parent)
    )
    os.close(fd)
    try:
        np.save(temp_name, matrix)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"fichier d'index invalide (objet attendu) : {path}")
    return {str(key): value for key, value in raw.items()}


def _normalise_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True).clip(min=1e-12)
    normalised: np.ndarray = (matrix / norms).astype(np.float32)
    return normalised


# ---------------------------------------------------------------------------
# Index lexical
# ---------------------------------------------------------------------------


class _LexicalIndex:
    """BM25 (rank_bm25) avec repli TF-IDF pur numpy."""

    def __init__(
        self, ids: list[str], texts: list[str], tokens: list[list[str]] | None = None
    ) -> None:
        self.ids = list(ids)
        self.tokens = (
            [list(item) for item in tokens]
            if tokens is not None
            else [tokenize(text) for text in texts]
        )
        self.counts = [Counter(item) for item in self.tokens]
        self.df = Counter(term for item in self.tokens for term in set(item))
        self._bm25: Any | None = None
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.info("rank_bm25 absent : repli TF-IDF pour la recherche lexicale.")
        else:
            if self.tokens:
                self._bm25 = BM25Okapi(self.tokens)

    @property
    def backend(self) -> str:
        return "rank_bm25" if self._bm25 is not None else "tfidf"

    def scores(self, query_tokens: list[str]) -> np.ndarray:
        """Scores alignés sur ``ids`` ; le filtrage se fait par présence des termes."""
        if not self.tokens or not query_tokens:
            return np.zeros(len(self.ids), dtype=np.float32)
        if self._bm25 is not None:
            return np.asarray(self._bm25.get_scores(query_tokens), dtype=np.float32)
        total = len(self.tokens)
        scores = np.zeros(total, dtype=np.float32)
        for term in query_tokens:
            df = self.df.get(term, 0)
            if not df:
                continue
            idf = float(np.log((1.0 + total) / (1.0 + df)) + 1.0)
            for row, counts in enumerate(self.counts):
                tf = counts.get(term, 0)
                if tf:
                    scores[row] += tf * idf
        return scores

    def matching_rows(self, query_tokens: list[str]) -> np.ndarray:
        """Indices des pages contenant au moins un terme de la requête.

        BM25Okapi peut renvoyer des scores négatifs quand un terme est présent
        dans tout le corpus : filtrer sur ``score > 0`` perdrait ces pages, on
        filtre donc sur la présence réelle des termes.
        """
        terms = set(query_tokens)
        rows = [
            row for row, tokens in enumerate(self.tokens) if terms.intersection(tokens)
        ]
        return np.asarray(rows, dtype=np.int64)

    def to_payload(self) -> dict[str, object]:
        return {
            "version": INDEX_VERSION,
            "backend": self.backend,
            "ids": self.ids,
            "tokens": self.tokens,
        }


# ---------------------------------------------------------------------------
# Index de corpus
# ---------------------------------------------------------------------------


class CorpusIndex:
    """Index persistant des pages déjà traitées par les workers AOCRI.

    Args:
        root: répertoire de persistance (défaut : ``default_index_dir()``).
        backend: ``"auto"`` (Chroma si disponible, sinon numpy), ``"numpy"``
            ou ``"chroma"`` (échec explicite si indisponible).
        dimension: dimension attendue avant le premier ajout (768 pour e5).
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        backend: str = "auto",
        dimension: int = EMBED_DIM,
    ) -> None:
        if backend not in ("auto", "numpy", "chroma"):
            raise ValueError(f"backend inconnu : {backend!r}")
        self.root = Path(root) if root is not None else default_index_dir()
        self.root = self.root.expanduser().resolve()
        if self.root.exists() and not self.root.is_dir():
            raise NotADirectoryError(f"index impossible : {self.root} n'est pas un dossier")
        self.root.mkdir(parents=True, exist_ok=True)
        self._dimension = max(1, int(dimension))
        self._documents: dict[str, DocumentMeta] = {}
        self._pages: dict[str, PageVector] = {}
        self._matrix = np.zeros((0, self._dimension), dtype=np.float32)
        self._ids: list[str] = []
        self._lexical = _LexicalIndex([], [])
        self._defer_depth = 0
        self._collection: Any | None = None
        self._chroma_client: Any | None = None
        self._load()
        self._init_backend(backend)

    # -- propriétés --------------------------------------------------------
    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def backend_name(self) -> str:
        """``"chroma"`` si le miroir vectoriel est actif, sinon ``"numpy"``."""
        return "chroma" if self._collection is not None else "numpy"

    @property
    def lexical_backend(self) -> str:
        return self._lexical.backend

    # -- chargement --------------------------------------------------------
    def _load(self) -> None:
        docs_payload = _read_json(self.root / DOCUMENTS_FILE)
        pages_payload = _read_json(self.root / PAGES_FILE)
        if docs_payload:
            stored_dim = _as_int(docs_payload.get("dimension"), self._dimension)
            if stored_dim > 0:
                self._dimension = stored_dim
            raw_docs = docs_payload.get("documents")
            if isinstance(raw_docs, list):
                for item in raw_docs:
                    if not isinstance(item, dict):
                        continue
                    md5 = _as_str(item.get("md5"))
                    if not md5:
                        continue
                    self._documents[md5] = DocumentMeta(
                        md5=md5,
                        source=_as_str(item.get("source")),
                        page_count=_as_int(item.get("page_count")),
                        indexed_at=_as_str(item.get("indexed_at")),
                        work_dir=_as_str(item.get("work_dir")),
                    )

        records: list[PageVector] = []
        if pages_payload:
            raw_pages = pages_payload.get("pages")
            if isinstance(raw_pages, list):
                for item in raw_pages:
                    if not isinstance(item, dict):
                        continue
                    md5 = _as_str(item.get("md5"))
                    page = _as_int(item.get("page"))
                    if md5 not in self._documents or page < 1:
                        continue
                    page_id = _as_str(item.get("id")) or _page_id(md5, page)
                    records.append(
                        PageVector(
                            id=page_id,
                            md5=md5,
                            page=page,
                            source=_as_str(item.get("source")),
                            work_dir=_as_str(item.get("work_dir")),
                            text=_as_str(item.get("text")),
                            vector=np.zeros(0, dtype=np.float32),
                        )
                    )

        vectors_path = self.root / VECTORS_FILE
        if records:
            if not vectors_path.is_file():
                raise ValueError(f"vecteurs manquants pour l'index : {vectors_path}")
            matrix = np.load(vectors_path)
            if matrix.ndim != 2 or matrix.shape[0] != len(records):
                raise ValueError(
                    f"incohérence pages/vecteurs : {len(records)} pages, "
                    f"matrice {matrix.shape}"
                )
            matrix = matrix.astype(np.float32)
            self._dimension = int(matrix.shape[1])
            records = [
                PageVector(
                    id=record.id,
                    md5=record.md5,
                    page=record.page,
                    source=record.source,
                    work_dir=record.work_dir,
                    text=record.text,
                    vector=matrix[row].copy(),
                )
                for row, record in enumerate(records)
            ]
        self._pages = {record.id: record for record in records}
        self._rebuild_matrix()
        self._load_lexical()

    def _load_lexical(self) -> None:
        payload = _read_json(self.root / BM25_FILE)
        ids = list(self._pages)
        if payload:
            stored_ids = payload.get("ids")
            stored_tokens = payload.get("tokens")
            if (
                isinstance(stored_ids, list)
                and isinstance(stored_tokens, list)
                and [str(item) for item in stored_ids] == ids
                and len(stored_tokens) == len(ids)
                and all(isinstance(item, list) for item in stored_tokens)
            ):
                tokens = [
                    [str(token) for token in item]
                    for item in stored_tokens
                    if isinstance(item, list)
                ]
                self._lexical = _LexicalIndex(ids, [], tokens=tokens)
                return
        self._lexical = _LexicalIndex(ids, [page.text for page in self._pages.values()])

    def _rebuild_matrix(self) -> None:
        self._ids = list(self._pages)
        if not self._ids:
            self._matrix = np.zeros((0, self._dimension), dtype=np.float32)
            return
        self._matrix = np.vstack(
            [self._pages[page_id].vector for page_id in self._ids]
        ).astype(np.float32)

    # -- backend vectoriel -------------------------------------------------
    def _init_backend(self, backend: str) -> None:
        if backend == "numpy":
            return
        try:
            chromadb = _import_chromadb()
            client = chromadb.PersistentClient(path=str(self.root / CHROMA_DIR))
            collection = client.get_or_create_collection(
                name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
            )
            if int(collection.count()) != len(self._pages):
                collection = self._reset_chroma(client)
                self._sync_chroma_full(collection)
            self._chroma_client = client
            self._collection = collection
        except Exception as exc:
            if backend == "chroma":
                raise RuntimeError(
                    f"ChromaDB indisponible pour {self.root} : {exc}"
                ) from exc
            logger.warning("ChromaDB indisponible, repli numpy : %s", exc)
            self._collection = None

    def _reset_chroma(self, client: Any) -> Any:
        with suppress(Exception):  # collection absente au premier lancement
            client.delete_collection(COLLECTION_NAME)
        return client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )

    def _sync_chroma_full(self, collection: Any) -> None:
        records = list(self._pages.values())
        batch = 500
        for start in range(0, len(records), batch):
            self._sync_chroma_full_records(collection, records[start : start + batch])

    def _chroma_upsert(self, records: list[PageVector]) -> None:
        if self._collection is None or not records:
            return
        try:
            self._sync_chroma_full_records(self._collection, records)
        except Exception as exc:  # noqa: BLE001 — le stockage numpy reste valide
            logger.warning("Écriture ChromaDB impossible, repli numpy : %s", exc)
            self._collection = None

    @staticmethod
    def _sync_chroma_full_records(collection: Any, records: list[PageVector]) -> None:
        collection.upsert(
            ids=[record.id for record in records],
            embeddings=[record.vector.tolist() for record in records],
            documents=[record.text for record in records],
            metadatas=[
                {
                    "md5": record.md5,
                    "page": record.page,
                    "source": record.source,
                    "work_dir": record.work_dir,
                }
                for record in records
            ],
        )

    def _chroma_delete(self, doc_id: str) -> None:
        if self._collection is None:
            return
        try:
            self._collection.delete(where={"md5": doc_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Suppression ChromaDB impossible : %s", exc)
            self._collection = None

    # -- persistance -------------------------------------------------------
    @contextmanager
    def batch(self) -> Iterator[None]:
        """Regroupe les écritures disque d'une série d'ajouts/suppressions."""
        self._defer_depth += 1
        try:
            yield
        finally:
            self._defer_depth -= 1
            if self._defer_depth == 0:
                self.save()

    def save(self) -> None:
        """Persiste documents, pages, vecteurs et index lexical."""
        if self._defer_depth:
            return
        _write_json_atomic(
            self.root / DOCUMENTS_FILE,
            {
                "version": INDEX_VERSION,
                "dimension": self._dimension,
                "documents": [doc.to_dict() for doc in self._documents.values()],
            },
        )
        _write_json_atomic(
            self.root / PAGES_FILE,
            {
                "version": INDEX_VERSION,
                "pages": [
                    {
                        "id": record.id,
                        "md5": record.md5,
                        "page": record.page,
                        "source": record.source,
                        "work_dir": record.work_dir,
                        "text": record.text,
                    }
                    for record in self._pages.values()
                ],
            },
        )
        _write_npy_atomic(self.root / VECTORS_FILE, self._matrix)
        _write_json_atomic(self.root / BM25_FILE, self._lexical.to_payload())

    # -- consultation ------------------------------------------------------
    def documents(self) -> list[DocumentMeta]:
        return list(self._documents.values())

    def document(self, doc_id: str) -> DocumentMeta | None:
        return self._documents.get(validate_doc_id(doc_id))

    def has_document(self, doc_id: str) -> bool:
        return validate_doc_id(doc_id) in self._documents

    def pages(self, doc_id: str | None = None) -> list[PageVector]:
        if doc_id is not None:
            normalised = validate_doc_id(doc_id)
            return [
                record
                for record in self._pages.values()
                if record.md5 == normalised
            ]
        return list(self._pages.values())

    def page_count(self, doc_id: str | None = None) -> int:
        return len(self.pages(doc_id))

    # -- mutations ---------------------------------------------------------
    def add_document(
        self,
        md5: str,
        source: str,
        pages: list[PageInput],
        vectors: np.ndarray,
        *,
        work_dir: str = "",
        replace: bool = False,
    ) -> bool:
        """Ajoute un document ; ``False`` si le MD5 est déjà indexé.

        L'ajout est idempotent par MD5 sauf ``replace=True``, qui supprime
        d'abord l'ancienne version du document.
        """
        doc_id = validate_doc_id(md5)
        if not isinstance(source, str):
            raise TypeError("source doit être une chaîne")
        if doc_id in self._documents and not replace:
            return False
        if not pages:
            raise ValueError("un document indexé doit contenir au moins une page")

        page_numbers = [item.page for item in pages]
        if any(isinstance(number, bool) or int(number) < 1 for number in page_numbers):
            raise ValueError("les numéros de page doivent être des entiers >= 1")
        if len(set(page_numbers)) != len(page_numbers):
            raise ValueError("numéros de page dupliqués dans le document")
        if any(not isinstance(item.text, str) for item in pages):
            raise TypeError("le texte des pages doit être une chaîne")

        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(pages):
            raise ValueError(
                f"vecteurs incohérents : {matrix.shape} pour {len(pages)} pages"
            )
        if matrix.shape[1] < 1:
            raise ValueError("les vecteurs doivent avoir au moins une dimension")
        if self._pages and matrix.shape[1] != self._dimension:
            raise ValueError(
                f"dimension {matrix.shape[1]} != index {self._dimension}"
            )
        if not self._pages:
            self._dimension = int(matrix.shape[1])
        matrix = _normalise_rows(matrix)

        if replace and doc_id in self._documents:
            self.remove_document(doc_id)

        meta = DocumentMeta(
            md5=doc_id,
            source=source,
            page_count=len(pages),
            indexed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            work_dir=work_dir,
        )
        new_records: list[PageVector] = []
        for item, vector in zip(pages, matrix):
            page = int(item.page)
            record = PageVector(
                id=_page_id(doc_id, page),
                md5=doc_id,
                page=page,
                source=source,
                work_dir=work_dir,
                text=item.text,
                vector=np.asarray(vector, dtype=np.float32),
            )
            self._pages[record.id] = record
            new_records.append(record)
        self._documents[doc_id] = meta
        self._rebuild_matrix()
        self._rebuild_lexical()
        self._chroma_upsert(new_records)
        self.save()
        return True

    def _rebuild_lexical(self) -> None:
        ids = list(self._pages)
        self._lexical = _LexicalIndex(ids, [self._pages[page_id].text for page_id in ids])

    def remove_document(self, doc_id: str) -> bool:
        """Supprime un document et ses pages ; ``False`` s'il est absent."""
        normalised = validate_doc_id(doc_id)
        if normalised not in self._documents:
            return False
        self._documents.pop(normalised, None)
        for page_id in [pid for pid, record in self._pages.items() if record.md5 == normalised]:
            self._pages.pop(page_id, None)
        self._rebuild_matrix()
        self._rebuild_lexical()
        self._chroma_delete(normalised)
        self.save()
        return True

    def reset(self) -> None:
        """Vide entièrement l'index (mémoire, disque et miroir Chroma)."""
        self._documents.clear()
        self._pages.clear()
        self._rebuild_matrix()
        self._lexical = _LexicalIndex([], [])
        if self._collection is not None and self._chroma_client is not None:
            self._collection = self._reset_chroma(self._chroma_client)
        self.save()

    # -- recherche ---------------------------------------------------------
    def search_vectors(
        self, vector: np.ndarray, k: int = 20, *, exclude_md5: str | None = None
    ) -> list[PageHit]:
        """Recherche cosinus des ``k`` pages les plus proches."""
        if k <= 0 or not self._pages:
            return []
        query = np.asarray(vector, dtype=np.float32).reshape(-1)
        if query.shape[0] != self._dimension:
            raise ValueError(
                f"vecteur de dimension {query.shape[0]} != index {self._dimension}"
            )
        norm = float(np.linalg.norm(query))
        if norm <= 1e-12:
            return []
        query = (query / norm).astype(np.float32)

        if self._collection is not None:
            hits = self._chroma_search(query, k, exclude_md5)
            if hits is not None:
                return hits
        return self._numpy_search(query, k, exclude_md5)

    def _numpy_search(
        self, query: np.ndarray, k: int, exclude_md5: str | None
    ) -> list[PageHit]:
        scores = self._matrix @ query
        order = np.argsort(-scores, kind="stable")
        hits: list[PageHit] = []
        for row in order:
            record = self._pages[self._ids[int(row)]]
            if exclude_md5 and record.md5 == exclude_md5:
                continue
            hits.append(
                PageHit(
                    id=record.id,
                    md5=record.md5,
                    page=record.page,
                    source=record.source,
                    work_dir=record.work_dir,
                    text=record.text,
                    score=float(scores[int(row)]),
                )
            )
            if len(hits) >= k:
                break
        return hits

    def _chroma_search(
        self, query: np.ndarray, k: int, exclude_md5: str | None
    ) -> list[PageHit] | None:
        if self._collection is None:
            return None
        buffer = len(self.pages(exclude_md5)) if exclude_md5 else 0
        wanted = min(k + buffer, len(self._pages))
        try:
            result = self._collection.query(
                query_embeddings=[query.tolist()],
                n_results=wanted,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:  # noqa: BLE001 — repli numpy immédiat
            logger.warning("Requête ChromaDB impossible, repli numpy : %s", exc)
            self._collection = None
            return None
        ids = result.get("ids") or [[]]
        distances = result.get("distances") or [[]]
        hits: list[PageHit] = []
        for page_id, distance in zip(ids[0], distances[0]):
            record = self._pages.get(str(page_id))
            if record is None or (exclude_md5 and record.md5 == exclude_md5):
                continue
            similarity = max(-1.0, min(1.0, 1.0 - float(distance)))
            hits.append(
                PageHit(
                    id=record.id,
                    md5=record.md5,
                    page=record.page,
                    source=record.source,
                    work_dir=record.work_dir,
                    text=record.text,
                    score=similarity,
                )
            )
            if len(hits) >= k:
                break
        return hits

    def search_lexical(self, query: str, k: int = 20) -> list[PageHit]:
        """Recherche BM25 (ou TF-IDF de repli) sur les pages contenant les termes."""
        if k <= 0 or not self._pages:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._lexical.scores(tokens)
        rows = self._lexical.matching_rows(tokens)
        if rows.size == 0:
            return []
        order = rows[np.argsort(-scores[rows], kind="stable")]
        hits: list[PageHit] = []
        for row in order:
            record = self._pages[self._ids[int(row)]]
            hits.append(
                PageHit(
                    id=record.id,
                    md5=record.md5,
                    page=record.page,
                    source=record.source,
                    work_dir=record.work_dir,
                    text=record.text,
                    score=float(scores[int(row)]),
                )
            )
            if len(hits) >= k:
                break
        return hits


def _import_chromadb() -> Any:
    """Import paresseux de ChromaDB (miroir vectoriel optionnel)."""
    import chromadb

    return chromadb
