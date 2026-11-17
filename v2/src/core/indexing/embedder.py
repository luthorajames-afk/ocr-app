"""Embeddings hors ligne : protocole commun, e5 ONNX et hacheur de test.

Les préfixes ``passage: `` / ``query: `` sont obligatoires pour les modèles
e5. Les imports lourds (onnxruntime, transformers) sont paresseux : importer
ce module ne charge rien de plus que numpy.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

# Mode hors-ligne forcé AVANT tout import de transformers/onnxruntime.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

logger = logging.getLogger(__name__)

EMBED_DIM = 768
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "
DEFAULT_MAX_LENGTH = 512
DEFAULT_BATCH_SIZE = 16

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MODEL_FILE_NAMES = ("model_quantized.onnx", "model.onnx", "model_fp32.onnx")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def default_model_dir() -> Path:
    """Répertoire du modèle e5 : ``AOCRI_E5_MODEL_DIR`` puis emplacements connus."""
    env = os.environ.get("AOCRI_E5_MODEL_DIR")
    if env:
        return Path(env)
    import sys

    candidates = [
        _PROJECT_ROOT / "models" / "multilingual-e5-base",
        _PROJECT_ROOT / "models",
        _PROJECT_ROOT.parent / "app_pdf_pro" / "models",
    ]
    if getattr(sys, "frozen", False):
        # Application empaquetée : modèle livré à côté de OCRApp.exe.
        executable_dir = Path(sys.executable).resolve().parent
        candidates = [
            executable_dir / "models" / "multilingual-e5-base",
            executable_dir / "models",
            executable_dir / "models" / "e5",
            *candidates,
        ]
    for candidate in candidates:
        if any((candidate / name).is_file() for name in _MODEL_FILE_NAMES):
            return candidate
    return candidates[0]


def normalise_prefix(prefix: str) -> str:
    """Ajoute l'espace final attendu par e5 (``"query:"`` → ``"query: "``)."""
    if not prefix or prefix.endswith(" "):
        return prefix
    return f"{prefix} "


def _normalise_rows(matrix: np.ndarray) -> np.ndarray:
    """Normalise chaque ligne en norme L2 (les lignes nulles restent nulles)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True).clip(min=1e-12)
    normalised: np.ndarray = (matrix / norms).astype(np.float32)
    return normalised


@runtime_checkable
class Embedder(Protocol):
    """Contrat minimal d'un vecteuriseur de textes."""

    @property
    def dimension(self) -> int:
        """Dimension des vecteurs produits."""
        ...

    def encode(self, texts: list[str], prefix: str = PASSAGE_PREFIX) -> np.ndarray:
        """Vecteurs normalisés L2 de forme ``(len(texts), dimension)``."""
        ...


def _import_onnxruntime() -> Any:
    """Import paresseux d'onnxruntime (extra optionnel)."""
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover — dépend de l'environnement
        raise ImportError(
            "onnxruntime est requis pour l'embedder ONNX "
            "(pip install onnxruntime)."
        ) from exc
    return ort


class OnnxE5Embedder:
    """Embedder e5-base quantifié au format ONNX.

    Mean pooling sur ``last_hidden_state`` puis normalisation L2. Le chargement
    est paresseux ; CPU par défaut, CUDA si le provider est disponible.
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        max_length: int = DEFAULT_MAX_LENGTH,
        batch_size: int = DEFAULT_BATCH_SIZE,
        force_cpu: bool | None = None,
    ) -> None:
        self.model_dir = Path(model_dir) if model_dir is not None else default_model_dir()
        self.max_length = int(max_length)
        self.batch_size = max(1, int(batch_size))
        self.force_cpu = force_cpu
        self.session: Any | None = None
        self.tokenizer: Any | None = None
        self.device = "cpu"
        self._dimension = EMBED_DIM

    @property
    def dimension(self) -> int:
        return self._dimension

    def _find_model_file(self) -> Path:
        """Priorité au modèle quantifié, puis aux variantes fp32."""
        for name in _MODEL_FILE_NAMES:
            candidate = self.model_dir / name
            if candidate.is_file():
                return candidate
        onnx_files = sorted(self.model_dir.glob("*.onnx"))
        if not onnx_files:
            raise FileNotFoundError(
                f"Aucun modèle ONNX dans {self.model_dir} ; définir "
                "AOCRI_E5_MODEL_DIR ou passer model_dir."
            )
        return onnx_files[0]

    def _select_providers(self) -> tuple[list[str], str]:
        """Providers ONNX Runtime : CPU par défaut, CUDA si disponible."""
        force = self.force_cpu
        if force is None:
            raw = os.environ.get(
                "AOCRI_FORCE_CPU", os.environ.get("APP_PDF_FORCE_CPU", "")
            )
            force = raw.lower() in ("1", "true", "yes")
        if force:
            return ["CPUExecutionProvider"], "cpu"

        ort = _import_onnxruntime()
        available = ort.get_available_providers()
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"], "cuda"
        return ["CPUExecutionProvider"], "cpu"

    def load(self) -> None:
        """Charge le tokenizer et la session ONNX (idempotent)."""
        if self.session is not None:
            return
        ort = _import_onnxruntime()
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover — dépend de l'environnement
            raise ImportError(
                "transformers est requis pour l'embedder ONNX "
                "(pip install transformers)."
            ) from exc

        model_path = self._find_model_file()
        logger.info("Chargement du modèle ONNX : %s", model_path)
        try:
            # Corrige le motif regex du tokenizer e5 (warning transformers).
            self.tokenizer = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
                str(self.model_dir), fix_mistral_regex=True
            )
        except TypeError:  # pragma: no cover — ancienne version de transformers
            self.tokenizer = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
                str(self.model_dir)
            )

        providers, self.device = self._select_providers()
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self.session = ort.InferenceSession(
                str(model_path), sess_options=options, providers=providers
            )
        except Exception as exc:
            if self.device != "cuda":
                raise
            logger.warning("Session CUDA impossible (%s) ; repli CPU.", exc)
            self.device = "cpu"
            self.session = ort.InferenceSession(
                str(model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        active = list(self.session.get_providers())
        if self.device == "cuda" and "CUDAExecutionProvider" not in active:
            logger.warning("CUDA demandé mais inactif ; repli CPU (%s).", active)
            self.device = "cpu"
        output_shape = self.session.get_outputs()[0].shape
        if output_shape and isinstance(output_shape[-1], int):
            self._dimension = int(output_shape[-1])
        logger.info("Modèle chargé sur %s (providers=%s).", self.device, active)

    def encode(self, texts: list[str], prefix: str = PASSAGE_PREFIX) -> np.ndarray:
        """Vecteurs e5 normalisés ; ``prefix`` vaut ``passage: `` ou ``query: ``."""
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        self.load()
        assert self.tokenizer is not None and self.session is not None
        full_prefix = normalise_prefix(prefix)
        prefixed = [f"{full_prefix}{text}" for text in texts]

        chunks: list[np.ndarray] = []
        for start in range(0, len(prefixed), self.batch_size):
            batch = prefixed[start : start + self.batch_size]
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="np",
            )
            inputs: dict[str, np.ndarray] = {}
            for key in ("input_ids", "attention_mask", "token_type_ids"):
                if key in encoded:
                    inputs[key] = np.asarray(encoded[key], dtype=np.int64)

            outputs = self.session.run(None, inputs)
            hidden = np.asarray(outputs[0], dtype=np.float32)
            mask = np.asarray(encoded["attention_mask"], dtype=np.float32)
            summed = (hidden * mask[:, :, None]).sum(axis=1)
            counts = mask.sum(axis=1, keepdims=True).clip(min=1e-9)
            chunks.append(_normalise_rows(summed / counts))

        matrix: np.ndarray = np.vstack(chunks).astype(np.float32)
        self._dimension = int(matrix.shape[1])
        return matrix

    def encode_one(self, text: str, prefix: str = PASSAGE_PREFIX) -> np.ndarray:
        """Vecteur d'un texte unique, forme ``(dimension,)``."""
        vector: np.ndarray = self.encode([text], prefix=prefix)[0]
        return vector


class HashingEmbedder:
    """Embedder déterministe sans modèle, réservé aux tests.

    Chaque mot est projeté dans un vecteur par double hachage BLAKE2b (indice
    et signe), puis le vecteur est normalisé L2. Deux textes proches partagent
    des mots et obtiennent donc un cosinus élevé.
    """

    def __init__(self, dimension: int = EMBED_DIM) -> None:
        if dimension <= 0:
            raise ValueError("dimension doit être strictement positive")
        self._dimension = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    def _hash_token(self, token: str) -> tuple[int, float]:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        index = value % self._dimension
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return index, sign

    def encode(self, texts: list[str], prefix: str = PASSAGE_PREFIX) -> np.ndarray:
        """Vecteurs creux hachés et normalisés, forme ``(len(texts), dimension)``."""
        matrix = np.zeros((len(texts), self._dimension), dtype=np.float32)
        full_prefix = normalise_prefix(prefix)
        for row, text in enumerate(texts):
            if not text.strip():
                continue
            tokens = _TOKEN_RE.findall(f"{full_prefix}{text}".lower())
            for token in tokens:
                index, sign = self._hash_token(token)
                matrix[row, index] += sign
        if not matrix.size:
            return matrix
        return _normalise_rows(matrix)
