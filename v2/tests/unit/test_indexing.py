"""Tests du noyau d'indexation : HashingEmbedder, persistance, similarité, RRF."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from core.indexing import store as store_module
from core.indexing.embedder import (
    EMBED_DIM,
    HashingEmbedder,
    OnnxE5Embedder,
    default_model_dir,
)
from core.indexing.search import (
    index_work_dirs,
    reciprocal_rank_fusion,
    related_documents,
    search,
    similar_documents,
)
from core.indexing.store import (
    CorpusIndex,
    PageHit,
    PageInput,
    default_index_dir,
    tokenize,
)

SRC = Path(__file__).resolve().parents[2] / "src"


def _write_work(
    root: Path,
    name: str,
    pages: dict[int, list[str]],
    *,
    source: str = "scan.pdf",
) -> Path:
    work = root / name
    work.mkdir(parents=True, exist_ok=True)
    for number, lines in pages.items():
        payload = {
            "schema_version": 1,
            "page": number,
            "source": source,
            "engine": "tesseract",
            "text": "\n".join(lines),
            "blocks": [
                {"text": line, "bbox": [0, 0, 10, 10], "kind": "line", "confidence": 1.0}
                for line in lines
            ],
        }
        (work / f"page_{number:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    return work


def _open_index(root: Path, dimension: int = 64) -> CorpusIndex:
    return CorpusIndex(root, backend="numpy", dimension=dimension)


def _sample_corpus(root: Path) -> tuple[Path, Path, Path]:
    work_a = _write_work(
        root,
        "facture_a",
        {
            1: ["Facture de chauffage collectif", "Résidence Les Lilas"],
            2: ["Montant total TTC", "TVA 20 %"],
            3: ["Conditions de règlement", "Paiement sous 30 jours"],
        },
        source="a.pdf",
    )
    work_b = _write_work(
        root,
        "facture_b",
        {
            1: ["Facture de chauffage collectif", "Résidence Les Lilas"],
            2: ["Montant total TTC", "TVA 20 %"],
            3: ["Conditions de règlement", "Paiement sous 30 jours"],
        },
        source="b.pdf",
    )
    work_c = _write_work(
        root,
        "rapport_c",
        {1: ["Rapport annuel", "Budget informatique"]},
        source="c.pdf",
    )
    return work_a, work_b, work_c


def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbedder(dimension=32)
    vectors = embedder.encode(["facture de chauffage", "facture de chauffage"])
    assert vectors.shape == (2, 32)
    assert vectors.dtype == np.float32
    np.testing.assert_allclose(vectors[0], vectors[1])
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), [1.0, 1.0], atol=1e-6)
    assert embedder.encode([]).shape == (0, 32)
    assert np.all(embedder.encode([""]) == 0)
    assert embedder.dimension == 32


def test_prefixes_are_applied_for_passages_and_queries() -> None:
    embedder = HashingEmbedder(dimension=64)
    passage = embedder.encode(["chauffage"], prefix="passage:")[0]
    query = embedder.encode(["chauffage"], prefix="query: ")[0]
    assert not np.array_equal(passage, query)
    both = embedder.encode(["chauffage", "chauffage"], prefix="query:")
    np.testing.assert_allclose(both[0], both[1])


def test_index_work_dirs_is_idempotent_by_md5(tmp_path: Path) -> None:
    work_a, work_b, work_c = _sample_corpus(tmp_path / "work")
    index = _open_index(tmp_path / "index")
    embedder = HashingEmbedder(dimension=64)

    report = index_work_dirs([work_a, work_b, work_c], embedder, index)
    assert len(report.indexed) == 3
    assert report.pages_indexed == 7
    assert report.skipped == []
    assert report.errors == []
    assert index.page_count() == 7
    assert index.lexical_backend in ("rank_bm25", "tfidf")

    again = index_work_dirs([work_a], embedder, index)
    assert again.indexed == []
    assert len(again.skipped) == 1
    assert index.page_count() == 7

    replaced = index_work_dirs([work_a], embedder, index, replace=True)
    assert len(replaced.indexed) == 1
    assert index.page_count() == 7
    assert len(index.documents()) == 3


def test_same_source_in_two_work_dirs_is_deduplicated(tmp_path: Path) -> None:
    first = _write_work(tmp_path, "one", {1: ["même document"]}, source="identique.pdf")
    second = _write_work(tmp_path, "two", {1: ["même document"]}, source="identique.pdf")
    index = _open_index(tmp_path / "index")
    report = index_work_dirs([first, second], HashingEmbedder(32), index)
    assert len(report.indexed) == 1
    assert len(report.skipped) == 1
    assert len(index.documents()) == 1


def test_similar_and_related_documents(tmp_path: Path) -> None:
    work_a, work_b, work_c = _sample_corpus(tmp_path / "work")
    index = _open_index(tmp_path / "index")
    index_work_dirs([work_a, work_b, work_c], HashingEmbedder(64), index)

    pairs = similar_documents(index, threshold=0.95)
    assert len(pairs) == 1
    assert {pairs[0].doc_a.source, pairs[0].doc_b.source} == {"a.pdf", "b.pdf"}
    assert pairs[0].score == pytest.approx(1.0, abs=1e-5)
    assert pairs[0].top_pages

    approximate = similar_documents(index, threshold=0.95, mode="topk", top_k_per_page=3)
    assert len(approximate) == 1
    assert approximate[0].score == pytest.approx(1.0, abs=1e-5)

    by_source = {document.source: document for document in index.documents()}
    related = related_documents(index, by_source["a.pdf"].md5, k=3)
    assert related[0].document.source == "b.pdf"
    assert related[0].score > related[1].score

    assert related_documents(index, "0" * 32) == []
    with pytest.raises(ValueError):
        similar_documents(index, mode="magique")
    assert similar_documents(_open_index(tmp_path / "empty"), threshold=0.1) == []


def test_hybrid_search_and_rrf(tmp_path: Path) -> None:
    work_a, work_b, work_c = _sample_corpus(tmp_path / "work")
    index = _open_index(tmp_path / "index")
    embedder = HashingEmbedder(dimension=64)
    index_work_dirs([work_a, work_b, work_c], embedder, index)

    hits = search(index, embedder, "chauffage", k=3, mode="hybrid")
    assert hits
    assert hits[0].source == "a.pdf"
    assert hits[0].page == 1
    assert hits[0].semantic_score is not None and hits[0].semantic_score > 0
    assert hits[0].lexical_score is not None

    lexical = search(index, embedder, "budget", k=3, mode="lexical")
    assert lexical[0].source == "c.pdf"
    assert lexical[0].semantic_score is None

    semantic = search(index, embedder, "chauffage", k=3, mode="semantic")
    assert semantic[0].source == "a.pdf"
    assert semantic[0].lexical_score is None

    assert search(index, embedder, "   ", k=3) == []
    with pytest.raises(ValueError):
        search(index, embedder, "chauffage", mode="magique")

    def hit(page_id: str, score: float) -> PageHit:
        return PageHit(page_id, "0" * 32, 1, "", "", "", score)

    fused = reciprocal_rank_fusion([hit("x", 0.9), hit("y", 0.8)], [hit("y", 4.0), hit("z", 3.0)])
    assert [page_id for page_id, _score in fused] == ["y", "x", "z"]
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)


def test_persistence_reload_remove_and_reset(tmp_path: Path) -> None:
    work_a, work_b, work_c = _sample_corpus(tmp_path / "work")
    root = tmp_path / "index"
    index = _open_index(root, dimension=64)
    index_work_dirs([work_a, work_b, work_c], HashingEmbedder(64), index)
    for name in ("documents.json", "pages.json", "vectors.npy", "bm25.json"):
        assert (root / name).is_file(), name

    reloaded = _open_index(root, dimension=64)
    assert reloaded.dimension == 64
    assert len(reloaded.documents()) == 3
    assert reloaded.page_count() == 7
    assert reloaded.lexical_backend in ("rank_bm25", "tfidf")
    hits = search(reloaded, HashingEmbedder(64), "chauffage", k=1, mode="lexical")
    assert hits[0].source == "a.pdf"

    by_source = {document.source: document for document in reloaded.documents()}
    assert reloaded.remove_document(by_source["c.pdf"].md5)
    assert not reloaded.remove_document(by_source["c.pdf"].md5)
    assert reloaded.page_count() == 6
    assert len(_open_index(root, dimension=64).documents()) == 2

    reloaded.reset()
    empty = _open_index(root, dimension=64)
    assert empty.documents() == []
    assert empty.page_count() == 0


def test_chromadb_absent_falls_back_to_numpy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> None:
        raise ImportError("chromadb absent pour le test")

    monkeypatch.setattr(store_module, "_import_chromadb", unavailable)
    index = CorpusIndex(tmp_path / "auto", backend="auto", dimension=32)
    assert index.backend_name == "numpy"
    assert index_work_dirs(
        [_write_work(tmp_path, "w", {1: ["texte"]})], HashingEmbedder(32), index
    ).indexed

    with pytest.raises(RuntimeError):
        CorpusIndex(tmp_path / "forced", backend="chroma", dimension=32)


def test_tfidf_fallback_when_rank_bm25_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    original_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "rank_bm25":
            raise ImportError("rank_bm25 absent pour le test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    work = _write_work(tmp_path, "w", {1: ["Facture de chauffage"]}, source="a.pdf")
    index = _open_index(tmp_path / "index", dimension=32)
    embedder = HashingEmbedder(32)
    index_work_dirs([work], embedder, index)
    assert index.lexical_backend == "tfidf"
    hits = search(index, embedder, "chauffage", k=2, mode="lexical")
    assert hits and hits[0].source == "a.pdf"


def test_weird_paths_are_ignored_or_rejected(tmp_path: Path) -> None:
    work = _write_work(
        tmp_path / "work",
        "..cache_éà",
        {1: ["page valide"]},
        source="../../outside.pdf",
    )
    (work / "page_abc.json").write_text("{}", encoding="utf-8")
    (work / "page_002.json").write_text("{pas du json", encoding="utf-8")
    (work / "page_000.json").write_text('{"page": 0, "text": "zéro"}', encoding="utf-8")

    index = _open_index(tmp_path / "index", dimension=32)
    report = index_work_dirs(
        [tmp_path / "absent", work], HashingEmbedder(32), index
    )
    assert len(report.indexed) == 1
    assert report.pages_indexed == 1
    assert report.skipped == []
    assert any("absent" in error for error in report.errors)
    assert any("page_002.json" in error for error in report.errors)
    assert any("page_000.json" in error for error in report.errors)
    assert not (tmp_path.parent / "outside.pdf").exists()

    with pytest.raises(ValueError):
        index.add_document("../evil", "x.pdf", [PageInput(1, "a")], np.ones((1, 32)))
    with pytest.raises(ValueError):
        index.add_document("a" * 32, "x.pdf", [PageInput(0, "a")], np.ones((1, 32)))
    with pytest.raises(ValueError):
        index.add_document("b" * 32, "x.pdf", [PageInput(1, "a")], np.ones((2, 32)))


def test_doc_id_and_tokenizer_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert tokenize("Le décret N° 2026-17 de la TVA") == ["décret", "2026", "17", "tva"]
    monkeypatch.delenv("AOCRI_INDEX_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_index_dir() == tmp_path / "AOCRI" / "index"
    odd_root = tmp_path / "nested" / ".." / "index"
    index = _open_index(odd_root, dimension=8)
    assert index.root == (tmp_path / "index").resolve()


def test_import_of_package_does_not_load_heavy_dependencies() -> None:
    code = (
        "import sys; import core.indexing; "
        "heavy=[m for m in ('onnxruntime','transformers','chromadb','rank_bm25') "
        "if m in sys.modules]; "
        "print(','.join(heavy)); raise SystemExit(bool(heavy))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_e5_model_presence_is_detected_without_loading() -> None:
    model_dir = default_model_dir()
    candidates = [model_dir / "model_quantized.onnx", model_dir / "model.onnx"]
    if not any(path.is_file() for path in candidates):
        pytest.skip(f"modèle e5 absent de {model_dir}")
    embedder = OnnxE5Embedder(model_dir)
    assert embedder.dimension == EMBED_DIM
    assert embedder._find_model_file().name in ("model_quantized.onnx", "model.onnx")
