"""Noyau d'indexation AOCRI : embeddings, persistance, similarité, recherche.

API principale :
    Embedder / OnnxE5Embedder / HashingEmbedder
    CorpusIndex
    index_work_dirs / similar_documents / related_documents / search
"""

from core.indexing.embedder import (
    EMBED_DIM,
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    Embedder,
    HashingEmbedder,
    OnnxE5Embedder,
)
from core.indexing.search import (
    IndexReport,
    RelatedDocument,
    SearchHit,
    SimilarPair,
    index_work_dirs,
    reciprocal_rank_fusion,
    related_documents,
    search,
    similar_documents,
)
from core.indexing.store import (
    CorpusIndex,
    DocumentMeta,
    PageHit,
    PageInput,
    PageVector,
    default_index_dir,
)

__all__ = [
    "EMBED_DIM",
    "PASSAGE_PREFIX",
    "QUERY_PREFIX",
    "CorpusIndex",
    "DocumentMeta",
    "Embedder",
    "HashingEmbedder",
    "IndexReport",
    "OnnxE5Embedder",
    "PageHit",
    "PageInput",
    "PageVector",
    "RelatedDocument",
    "SearchHit",
    "SimilarPair",
    "default_index_dir",
    "index_work_dirs",
    "reciprocal_rank_fusion",
    "related_documents",
    "search",
    "similar_documents",
]
