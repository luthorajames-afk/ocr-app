"""Indexation des dossiers work AOCRI, similarité documentaire et recherche RRF.

Aucune ré-extraction ni OCR : seuls les ``page_NNN.json`` déjà produits par les
workers sont lus. La similarité est calculée page à page puis agrégée en Top-3.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.indexing.embedder import PASSAGE_PREFIX, QUERY_PREFIX, Embedder
from core.indexing.store import (
    CorpusIndex,
    DocumentMeta,
    PageHit,
    PageInput,
    PageVector,
)

logger = logging.getLogger(__name__)

_PAGE_FILE_RE = re.compile(r"^page_(\d+)\.json$")
_TOP_PAIRS = 3


@dataclass(frozen=True, slots=True)
class IndexReport:
    """Bilan d'une indexation de dossiers work."""

    indexed: list[DocumentMeta]
    skipped: list[str]
    errors: list[str]
    pages_indexed: int = 0


@dataclass(frozen=True, slots=True)
class SimilarPair:
    """Paire de documents jugés similaires."""

    doc_a: DocumentMeta
    doc_b: DocumentMeta
    score: float
    top_pages: list[tuple[int, int, float]]


@dataclass(frozen=True, slots=True)
class RelatedDocument:
    """Document lié à un document de référence."""

    document: DocumentMeta
    score: float
    top_pages: list[tuple[int, int, float]]


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Page résultat d'une recherche sémantique, lexicale ou hybride."""

    id: str
    md5: str
    page: int
    source: str
    work_dir: str
    text: str
    score: float
    semantic_score: float | None = None
    lexical_score: float | None = None


# ---------------------------------------------------------------------------
# Lecture des dossiers work
# ---------------------------------------------------------------------------


def _page_text(payload: dict[str, object]) -> str:
    """Concatène les textes de blocs, avec repli sur le texte global."""
    blocks = payload.get("blocks")
    parts: list[str] = []
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip("\n"))
    if parts:
        return "\n".join(parts)
    text = payload.get("text")
    return text if isinstance(text, str) else ""


def _hash_file(path: Path) -> str | None:
    """MD5 d'un fichier par blocs ; ``None`` si le fichier est inaccessible."""
    try:
        digest = hashlib.md5(usedforsecurity=False)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        logger.warning("Lecture impossible pour le MD5 de %s : %s", path, exc)
        return None
    return digest.hexdigest()


def _document_md5(source: str, work_dir: Path) -> str:
    """MD5 du PDF source s'il existe, sinon empreinte stable du chemin/work dir."""
    cleaned = source.strip()
    file_hash: str | None = None
    if cleaned and "\x00" not in cleaned:
        with suppress(OSError, ValueError):
            candidate = Path(cleaned)
            if candidate.is_file():
                file_hash = _hash_file(candidate)
    if file_hash is not None:
        return file_hash
    key = cleaned.replace("\\", "/") if cleaned else str(work_dir)
    return hashlib.md5(key.casefold().encode("utf-8"), usedforsecurity=False).hexdigest()


def _read_work_pages(work_dir: Path) -> tuple[list[PageInput], str, list[str]]:
    """Lit les pages d'un dossier work ; retourne (pages, source, erreurs)."""
    errors: list[str] = []
    by_page: dict[int, PageInput] = {}
    source = ""
    for path in sorted(work_dir.glob("page_*.json")):
        match = _PAGE_FILE_RE.fullmatch(path.name)
        if match is None:
            continue
        filename_page = int(match.group(1))
        try:
            payload: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name} illisible : {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path.name} : objet JSON attendu")
            continue
        raw_page = payload.get("page")
        page = filename_page
        if isinstance(raw_page, (int, float, str)) and not isinstance(raw_page, bool):
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                page = filename_page
        if page < 1:
            errors.append(f"{path.name} : numéro de page invalide")
            continue
        if page in by_page:
            errors.append(f"page {page} dupliquée dans {work_dir.name}")
            continue
        raw_source = payload.get("source")
        if not source and isinstance(raw_source, str):
            source = raw_source
        by_page[page] = PageInput(page=page, text=_page_text(payload))
    pages = [by_page[number] for number in sorted(by_page)]
    return pages, source, errors


def index_work_dirs(
    work_dirs: Iterable[str | Path],
    embedder: Embedder,
    index: CorpusIndex,
    *,
    replace: bool = False,
) -> IndexReport:
    """Indexe des dossiers ``work`` AOCRI sans re-OCR ni ré-extraction.

    Args:
        work_dirs: dossiers contenant ``page_NNN.json``.
        embedder: vecteuriseur (e5 ONNX en production, hachage en test).
        index: index de corpus persistant.
        replace: réindexe les documents dont le MD5 existe déjà.
    """
    indexed: list[DocumentMeta] = []
    skipped: list[str] = []
    errors: list[str] = []
    pages_indexed = 0

    with index.batch():
        for raw in work_dirs:
            work_dir = Path(raw)
            try:
                is_dir = work_dir.is_dir()
            except (OSError, ValueError):
                is_dir = False
            if not is_dir:
                errors.append(f"{work_dir} : dossier introuvable")
                continue
            pages, source, page_errors = _read_work_pages(work_dir)
            errors.extend(f"{work_dir} : {message}" for message in page_errors)
            if source:
                md5 = _document_md5(source, work_dir)
            else:
                source = str(work_dir.resolve())
                md5 = _document_md5("", work_dir)
            if not pages:
                skipped.append(str(work_dir))
                errors.append(f"{work_dir} : aucune page page_NNN.json exploitable")
                continue
            if not replace and index.has_document(md5):
                skipped.append(str(work_dir))
                continue
            texts = [page.text for page in pages]
            vectors = embedder.encode(texts, prefix=PASSAGE_PREFIX)
            matrix = np.asarray(vectors, dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape[0] != len(pages):
                errors.append(
                    f"{work_dir} : vecteurs incohérents {matrix.shape} "
                    f"pour {len(pages)} pages"
                )
                continue
            try:
                added = index.add_document(
                    md5,
                    source,
                    pages,
                    matrix,
                    work_dir=str(work_dir.resolve()),
                    replace=replace,
                )
            except (TypeError, ValueError) as exc:
                errors.append(f"{work_dir} : {exc}")
                continue
            if not added:
                skipped.append(str(work_dir))
                continue
            meta = index.document(md5)
            if meta is not None:
                indexed.append(meta)
            pages_indexed += len(pages)

    return IndexReport(
        indexed=indexed,
        skipped=skipped,
        errors=errors,
        pages_indexed=pages_indexed,
    )


# ---------------------------------------------------------------------------
# Similarité page-à-page (Top-3)
# ---------------------------------------------------------------------------


def _page_matrix(pages: Sequence[PageVector]) -> np.ndarray:
    return np.vstack([page.vector for page in pages]).astype(np.float32)


def _top3_pairs(
    pages_a: Sequence[PageVector], pages_b: Sequence[PageVector]
) -> tuple[float, list[tuple[int, int, float]]]:
    """Moyenne des 3 meilleures similarités cosinus page-à-page."""
    if not pages_a or not pages_b:
        return 0.0, []
    similarities = _page_matrix(pages_a) @ _page_matrix(pages_b).T
    flat = similarities.reshape(-1)
    count = min(_TOP_PAIRS, flat.size)
    top_indices = np.argpartition(flat, -count)[-count:]
    pairs: list[tuple[int, int, float]] = []
    width = similarities.shape[1]
    for index in top_indices:
        row, column = int(index) // width, int(index) % width
        pairs.append((pages_a[row].page, pages_b[column].page, float(flat[int(index)])))
    pairs.sort(key=lambda item: item[2], reverse=True)
    score = sum(item[2] for item in pairs) / len(pairs)
    return score, pairs


def _page_count_compatible(
    doc_a: DocumentMeta,
    doc_b: DocumentMeta,
    same_page_count: bool,
    tolerance: int,
) -> bool:
    if not same_page_count:
        return True
    return abs(doc_a.page_count - doc_b.page_count) <= tolerance


def similar_documents(
    index: CorpusIndex,
    threshold: float = 0.8,
    *,
    mode: str = "exact",
    top_k_per_page: int = 25,
    same_page_count: bool = False,
    page_count_tolerance: int = 0,
) -> list[SimilarPair]:
    """Paires de documents dont la similarité Top-3 pages atteint ``threshold``.

    ``mode="exact"`` compare toutes les paires de pages (O(D²·P²)).
    ``mode="topk"`` interroge les ``top_k_per_page`` voisins de chaque page
    (plus rapide sur un gros corpus, rappel légèrement inférieur).
    """
    if mode not in ("exact", "topk"):
        raise ValueError(f"mode de similarité inconnu : {mode!r}")
    tolerance = max(0, int(page_count_tolerance))
    documents = index.documents()
    if len(documents) < 2:
        return []
    pages_by_doc = {doc.md5: index.pages(doc.md5) for doc in documents}

    if mode == "exact":
        pairs: list[SimilarPair] = []
        for left in range(len(documents)):
            doc_a = documents[left]
            pages_a = pages_by_doc[doc_a.md5]
            if not pages_a:
                continue
            for right in range(left + 1, len(documents)):
                doc_b = documents[right]
                pages_b = pages_by_doc[doc_b.md5]
                if not pages_b:
                    continue
                if not _page_count_compatible(
                    doc_a, doc_b, same_page_count, tolerance
                ):
                    continue
                score, top_pages = _top3_pairs(pages_a, pages_b)
                if score >= threshold:
                    pairs.append(SimilarPair(doc_a, doc_b, score, top_pages))
        pairs.sort(key=lambda pair: pair.score, reverse=True)
        return pairs

    return _similar_documents_topk(
        index, documents, pages_by_doc, threshold, top_k_per_page,
        same_page_count, tolerance,
    )


def _similar_documents_topk(
    index: CorpusIndex,
    documents: list[DocumentMeta],
    pages_by_doc: dict[str, list[PageVector]],
    threshold: float,
    top_k_per_page: int,
    same_page_count: bool,
    tolerance: int,
) -> list[SimilarPair]:
    """Variante approchée : voisins vectoriels par page puis Top-3 par paire."""
    by_md5 = {doc.md5: doc for doc in documents}
    page_scores: dict[tuple[str, str], dict[tuple[int, int], float]] = defaultdict(dict)
    for doc in documents:
        for page in pages_by_doc[doc.md5]:
            neighbours = index.search_vectors(
                page.vector, k=max(1, top_k_per_page), exclude_md5=doc.md5
            )
            for hit in neighbours:
                if hit.md5 == doc.md5:
                    continue
                if doc.md5 <= hit.md5:
                    key = (doc.md5, hit.md5)
                    page_pair = (page.page, hit.page)
                else:
                    key = (hit.md5, doc.md5)
                    page_pair = (hit.page, page.page)
                previous = page_scores[key].get(page_pair)
                if previous is None or hit.score > previous:
                    page_scores[key][page_pair] = hit.score

    pairs: list[SimilarPair] = []
    for (md5_a, md5_b), scores in page_scores.items():
        doc_a, doc_b = by_md5[md5_a], by_md5[md5_b]
        if not _page_count_compatible(doc_a, doc_b, same_page_count, tolerance):
            continue
        top = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:_TOP_PAIRS]
        if not top:
            continue
        score = sum(value for _, value in top) / len(top)
        if score < threshold:
            continue
        pairs.append(
            SimilarPair(
                doc_a,
                doc_b,
                score,
                [(pa, pb, value) for (pa, pb), value in top],
            )
        )
    pairs.sort(key=lambda pair: pair.score, reverse=True)
    return pairs


def related_documents(
    index: CorpusIndex,
    doc_id: str,
    k: int = 5,
    *,
    threshold: float = 0.0,
    mode: str = "exact",
) -> list[RelatedDocument]:
    """Documents les plus proches d'un document donné (Top-3 pages)."""
    if k <= 0:
        return []
    if mode not in ("exact", "topk"):
        raise ValueError(f"mode de similarité inconnu : {mode!r}")
    reference = index.document(doc_id)
    if reference is None:
        return []
    reference_pages = index.pages(reference.md5)
    if not reference_pages:
        return []
    if mode == "topk":
        candidates = {
            pair.doc_b.md5 if pair.doc_a.md5 == reference.md5 else pair.doc_a.md5
            for pair in similar_documents(index, threshold, mode="topk")
        }
        targets = [doc for doc in index.documents() if doc.md5 in candidates]
    else:
        targets = [doc for doc in index.documents() if doc.md5 != reference.md5]

    related: list[RelatedDocument] = []
    for candidate in targets:
        pages = index.pages(candidate.md5)
        if not pages:
            continue
        score, top_pages = _top3_pairs(reference_pages, pages)
        if score >= threshold:
            related.append(RelatedDocument(candidate, score, top_pages))
    related.sort(key=lambda item: item.score, reverse=True)
    return related[:k]


# ---------------------------------------------------------------------------
# Recherche hybride
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(
    semantic_hits: Sequence[PageHit],
    lexical_hits: Sequence[PageHit],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fusion RRF : ``Σ 1 / (k + rang + 1)`` sur les deux listes classées."""
    scores: dict[str, float] = {}
    for rank, hit in enumerate(semantic_hits):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
    for rank, hit in enumerate(lexical_hits):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _to_search_hits(
    hits: Sequence[PageHit],
    *,
    semantic: bool = False,
    lexical: bool = False,
) -> list[SearchHit]:
    return [
        SearchHit(
            id=hit.id,
            md5=hit.md5,
            page=hit.page,
            source=hit.source,
            work_dir=hit.work_dir,
            text=hit.text,
            score=hit.score,
            semantic_score=hit.score if semantic else None,
            lexical_score=hit.score if lexical else None,
        )
        for hit in hits
    ]


def search(
    index: CorpusIndex,
    embedder: Embedder,
    query: str,
    k: int = 10,
    mode: str = "hybrid",
) -> list[SearchHit]:
    """Recherche ``semantic``, ``lexical`` ou ``hybrid`` (RRF k=60)."""
    if mode not in ("hybrid", "semantic", "lexical"):
        raise ValueError(f"mode de recherche inconnu : {mode!r}")
    if k <= 0 or not query.strip():
        return []
    pool = max(k * 2, 20)
    if mode == "lexical":
        return _to_search_hits(index.search_lexical(query, pool)[:k], lexical=True)

    query_vector = embedder.encode([query], prefix=QUERY_PREFIX)
    if np.asarray(query_vector).shape[0] != 1:
        raise ValueError("l'embedder doit retourner un vecteur pour la requête")
    semantic_hits = index.search_vectors(np.asarray(query_vector)[0], k=pool)
    if mode == "semantic":
        return _to_search_hits(semantic_hits[:k], semantic=True)

    lexical_hits = index.search_lexical(query, pool)
    fused = reciprocal_rank_fusion(semantic_hits, lexical_hits, k=60)[:k]
    semantic_by_id = {hit.id: hit.score for hit in semantic_hits}
    lexical_by_id = {hit.id: hit.score for hit in lexical_hits}
    pages = {page.id: page for page in index.pages()}
    results: list[SearchHit] = []
    for page_id, score in fused:
        page = pages.get(page_id)
        if page is None:
            continue
        results.append(
            SearchHit(
                id=page.id,
                md5=page.md5,
                page=page.page,
                source=page.source,
                work_dir=page.work_dir,
                text=page.text,
                score=score,
                semantic_score=semantic_by_id.get(page_id),
                lexical_score=lexical_by_id.get(page_id),
            )
        )
    return results


__all__ = [
    "IndexReport",
    "RelatedDocument",
    "SearchHit",
    "SimilarPair",
    "index_work_dirs",
    "reciprocal_rank_fusion",
    "related_documents",
    "search",
    "similar_documents",
]
