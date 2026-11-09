"""Abstraction du moteur OCR (R12).

Le pipeline et les workers ne dépendent que de cette interface : brancher un
autre moteur (RapidOCR pour le mode Moyen, Docling pour l'Avancé) ne demande
pas de réécrire le pipeline.

Moteur disponible en phase 1 : Tesseract, via ``tesserocr`` (Tesseract 5.5.3
embarqué dans le wheel). Le chemin des données de langue est explicite (R4) :
``models/tessdata`` du projet, ``TESSDATA_PREFIX`` ou un dossier fourni par
l'appelant — aucun téléchargement n'est effectué.

Repères de coordonnées : les moteurs renvoient des bboxes en **pixels** de
l'image fournie (origine en haut à gauche). Le worker les convertit en points
PDF (1 pt = 1/72 po) pour le modèle de données.
"""

from __future__ import annotations

import logging
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Un point de données de langue Tesseract vit dans un fichier .traineddata.
_DEFAULT_LANGUAGES = ("fra", "eng")
_DEFAULT_LANGUAGE = "fra+eng"

BBoxPx = Tuple[float, float, float, float]


class OcrEngineError(RuntimeError):
    """Erreur d'initialisation ou d'exécution d'un moteur OCR."""


@dataclass(frozen=True)
class EngineBlock:
    """Bloc reconnu par un moteur, bbox en pixels (origine haut gauche)."""

    text: str
    bbox_px: BBoxPx
    confidence: float = 0.0  # 0.0 .. 1.0


@dataclass
class EngineResult:
    """Résultat complet d'une page pour un moteur donné."""

    text: str
    blocks: List[EngineBlock] = field(default_factory=list)
    confidence: float = 0.0
    engine: str = ""
    language: str = ""


class OcrEngine(ABC):
    """Interface commune à tous les moteurs OCR (R12)."""

    name: str = "abstract"

    @abstractmethod
    def initialize(self) -> None:
        """Charge le moteur et ses modèles une fois (R4, R5)."""

    @abstractmethod
    def recognize(self, image: Any, *, dpi: int = 300) -> EngineResult:
        """Reconnaît le texte d'une image PIL et retourne texte + blocs."""

    def shutdown(self) -> None:
        """Libère les ressources du moteur (idempotent)."""

    def __enter__(self) -> "OcrEngine":
        self.initialize()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()


# ---------------------------------------------------------------------------
# Résolution des données de langue Tesseract (R4 : chemin explicite)
# ---------------------------------------------------------------------------
def _project_root() -> Path:
    # src/ocr_app/core/ocr_engine.py -> racine du dépôt ocr_app
    return Path(__file__).resolve().parents[3]


def _tessdata_candidates(explicit: Optional[str]) -> List[Path]:
    """Dossiers candidats, dans l'ordre de priorité."""
    candidates: List[Path] = []

    def add(path: Path) -> None:
        if path not in candidates:
            candidates.append(path)

    if explicit:
        base = Path(explicit).expanduser()
        # L'appelant peut désigner le dossier tessdata lui-même ou son parent.
        add(base)
        add(base / "tessdata")
    else:
        env_value = os.environ.get("TESSDATA_PREFIX")
        if env_value:
            base = Path(env_value).expanduser()
            add(base)
            add(base / "tessdata")
        project = _project_root()
        add(project / "models" / "tessdata")
        add(project / "models")
        # Installation non éditable : modèles éventuellement livrés dans le
        # paquet lui-même (ocr_app/models/tessdata).
        package_dir = Path(__file__).resolve().parents[1]
        add(package_dir / "models" / "tessdata")
        add(package_dir / "models")
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            add(exe_dir / "models" / "tessdata")
            add(exe_dir / "models")
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                add(Path(meipass) / "models" / "tessdata")
                add(Path(meipass) / "models")
    return candidates


def _has_traineddata(directory: Path) -> bool:
    try:
        return directory.is_dir() and any(directory.glob("*.traineddata"))
    except OSError:
        return False


def resolve_tessdata_dir(explicit: Optional[str] = None) -> Path:
    """Retourne un dossier contenant des fichiers ``*.traineddata``.

    R4 : si ``explicit`` est fourni, il est le seul candidat accepté : une
    erreur claire est levée plutôt qu'un repli silencieux vers un autre
    dossier. Sans argument, ``TESSDATA_PREFIX`` puis ``models/tessdata`` du
    projet sont essayés.
    """
    candidates = _tessdata_candidates(explicit)
    for directory in candidates:
        if _has_traineddata(directory):
            return directory.resolve()

    searched = ", ".join(str(p) for p in candidates) or "(aucun)"
    if explicit:
        raise OcrEngineError(
            "dossier tessdata explicite introuvable ou sans fichier "
            f"*.traineddata : {explicit} (chemins essayés : {searched})"
        )
    raise OcrEngineError(
        "données de langue Tesseract introuvables. Placez fra.traineddata et "
        "eng.traineddata dans models/tessdata ou définissez TESSDATA_PREFIX. "
        f"(chemins essayés : {searched})"
    )


def _select_languages(directory: Path, requested: str) -> str:
    available = {path.stem for path in directory.glob("*.traineddata")}
    wanted = [part.strip() for part in requested.split("+") if part.strip()]
    selected = [language for language in wanted if language in available]
    if not selected:
        selected = [language for language in _DEFAULT_LANGUAGES if language in available]
    if not selected:
        raise OcrEngineError(
            "aucune langue Tesseract disponible dans "
            f"{directory} (langues demandées : {requested})"
        )
    return "+".join(selected)


# ---------------------------------------------------------------------------
# Moteur Tesseract (tesserocr)
# ---------------------------------------------------------------------------
class TesseractEngine(OcrEngine):
    """Moteur Tesseract via ``tesserocr`` (mode Simple)."""

    name = "tesseract"

    def __init__(
        self,
        tessdata_dir: Optional[str] = None,
        language: str = _DEFAULT_LANGUAGE,
    ) -> None:
        self.tessdata_dir = tessdata_dir
        self.requested_language = language
        self._api: Any = None
        self._tesserocr: Any = None
        self._resolved_dir: Optional[Path] = None
        self.language: str = ""

    # ------------------------------------------------------------ lifecycle
    def initialize(self) -> None:
        if self._api is not None:
            return
        try:
            import tesserocr  # import local : le cœur reste importable sans moteur
        except ImportError as exc:  # pragma: no cover - dépend de l'environnement
            raise OcrEngineError(
                "tesserocr n'est pas installé. Installez le wheel hors ligne "
                "(python -m pip install --only-binary=:all: tesserocr)."
            ) from exc

        directory = resolve_tessdata_dir(self.tessdata_dir)
        language = _select_languages(directory, self.requested_language)
        try:
            api = tesserocr.PyTessBaseAPI(path=str(directory), lang=language)
        except Exception as exc:  # noqa: BLE001 - message moteur explicite
            raise OcrEngineError(
                "impossible d'initialiser Tesseract avec les données de langue "
                f"{directory} (langue : {language}) : {exc}"
            ) from exc

        self._tesserocr = tesserocr
        self._api = api
        self._resolved_dir = directory
        self.language = language
        logger.info(
            "Tesseract initialisé (tessdata=%s, langue=%s)", directory, language
        )

    def shutdown(self) -> None:
        api = self._api
        self._api = None
        self._tesserocr = None
        if api is not None:
            try:
                api.End()
            except Exception:  # pragma: no cover - libération best effort
                logger.debug("erreur à l'arrêt de Tesseract", exc_info=True)

    # ------------------------------------------------------------ recognize
    def recognize(self, image: Any, *, dpi: int = 300) -> EngineResult:
        self.initialize()
        api = self._api
        tesserocr = self._tesserocr
        if api is None or tesserocr is None:  # pragma: no cover - garde-fou
            raise OcrEngineError("le moteur Tesseract n'est pas initialisé")

        try:
            api.SetImage(image)
            # Indice de résolution : améliore la reconnaissance des petites
            # polices et stabilise les tailles attendues par Tesseract.
            try:
                api.SetSourceResolution(int(dpi))
            except Exception:  # variable absente selon la version
                logger.debug("SetSourceResolution indisponible", exc_info=True)

            raw_text = api.GetUTF8Text() or ""
            text = _normalise_ocr_text(raw_text)
            try:
                mean_confidence = _confidence_from_percent(api.MeanTextConf())
            except Exception:  # pragma: no cover - selon la version du moteur
                mean_confidence = 0.0

            blocks = self._build_blocks(
                api=api,
                tesserocr=tesserocr,
                text=text,
                fallback_confidence=mean_confidence,
            )
            if not blocks and text:
                blocks = [
                    EngineBlock(
                        text=text,
                        bbox_px=(0.0, 0.0, *_image_size(image)),
                        confidence=mean_confidence,
                    )
                ]
        except OcrEngineError:
            raise
        except Exception as exc:  # noqa: BLE001 - erreur moteur contextualisée
            raise OcrEngineError(f"échec de l'OCR Tesseract : {exc}") from exc
        finally:
            try:
                api.Clear()
            except Exception:  # pragma: no cover - libération best effort
                logger.debug("Clear Tesseract impossible", exc_info=True)

        return EngineResult(
            text=text,
            blocks=blocks,
            confidence=mean_confidence,
            engine=self.name,
            language=self.language,
        )

    # ------------------------------------------------------------- blocs
    def _build_blocks(
        self,
        *,
        api: Any,
        tesserocr: Any,
        text: str,
        fallback_confidence: float,
    ) -> List[EngineBlock]:
        """Construit les blocs à partir des lignes et du TSV de Tesseract.

        ``GetComponentImages`` fournit les bboxes des lignes (exigence du
        pipeline) ; le TSV fournit le texte par ligne et une confiance par
        ligne. Les deux listes suivent l'ordre de lecture : on les associe
        quand les comptes coïncident, sinon on se replie sur le TSV seul.
        """
        components: List[Tuple[float, float, float, float]] = []
        try:
            raw_components = api.GetComponentImages(
                tesserocr.RIL.TEXTLINE, text_only=False
            )
            for item in raw_components or []:
                bbox = _component_bbox(item[1])
                if bbox is not None:
                    components.append(bbox)
                # Les crops PIL ne servent pas : on les libère au plus tôt.
                crop = item[0]
                close = getattr(crop, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # pragma: no cover - best effort
                        pass
        except Exception:  # noqa: BLE001 - repli TSV
            logger.debug("GetComponentImages a échoué", exc_info=True)

        try:
            tsv_text = api.GetTSVText(0) or ""
        except Exception:  # noqa: BLE001 - le texte principal reste utilisable
            logger.debug("GetTSVText a échoué", exc_info=True)
            tsv_text = ""
        tsv_lines = _parse_tsv_lines(tsv_text)
        text_lines = [line.strip() for line in text.splitlines() if line.strip()]

        # Le TSV contient une entrée (parfois vide) pour chaque ligne détectée,
        # y compris le bruit d'un scan (traits, bordures). Les lignes vides ne
        # doivent pas devenir des blocs : on les écarte, mais on conserve leur
        # index pour retrouver la bbox correspondante dans GetComponentImages.
        non_empty_tsv = [
            (index, line) for index, line in enumerate(tsv_lines) if line.text
        ]
        if non_empty_tsv:
            align_components = len(components) == len(tsv_lines)
            use_full_text = len(text_lines) == len(non_empty_tsv)
            blocks: List[EngineBlock] = []
            for position, (index, line) in enumerate(non_empty_tsv):
                bbox = (
                    components[index]
                    if align_components and index < len(components)
                    else line.bbox
                )
                line_text = text_lines[position] if use_full_text else line.text
                blocks.append(
                    EngineBlock(
                        text=line_text,
                        bbox_px=bbox,
                        confidence=line.confidence,
                    )
                )
            if blocks:
                return blocks

        if components:
            blocks = []
            count = min(len(components), len(text_lines))
            for index in range(count):
                blocks.append(
                    EngineBlock(
                        text=text_lines[index],
                        bbox_px=components[index],
                        confidence=fallback_confidence,
                    )
                )
            return blocks

        return []


@dataclass(frozen=True)
class _TsvLine:
    text: str
    bbox: BBoxPx
    confidence: float


def _parse_tsv_lines(tsv: str) -> List[_TsvLine]:
    """Extrait les lignes (niveau 4) et leurs mots (niveau 5) du TSV.

    Le TSV de Tesseract a 12 colonnes :
    level page block par line word left top width height conf text
    """
    order: List[Tuple[int, int, int]] = []
    lines: Dict[Tuple[int, int, int], Dict[str, Any]] = {}

    for raw in tsv.splitlines():
        columns = raw.split("\t")
        if len(columns) < 11:
            continue
        level = columns[0].strip()
        if level == "4":
            try:
                key = (int(columns[2]), int(columns[3]), int(columns[4]))
                left = float(columns[6])
                top = float(columns[7])
                width = float(columns[8])
                height = float(columns[9])
            except (TypeError, ValueError):
                continue
            if key not in lines:
                order.append(key)
                lines[key] = {"bbox": (left, top, left + width, top + height), "words": []}
        elif level == "5":
            try:
                key = (int(columns[2]), int(columns[3]), int(columns[4]))
                confidence = float(columns[10])
            except (TypeError, ValueError):
                continue
            word = columns[11] if len(columns) > 11 else ""
            entry = lines.get(key)
            if entry is None:
                continue
            if word:
                entry["words"].append((word, confidence))

    result: List[_TsvLine] = []
    for key in order:
        entry = lines[key]
        words = entry["words"]
        text = " ".join(word for word, _ in words).strip()
        confidences = [max(0.0, min(100.0, conf)) for _, conf in words if conf >= 0]
        confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
        result.append(
            _TsvLine(text=text, bbox=entry["bbox"], confidence=confidence)
        )
    return result


def _component_bbox(value: Any) -> Optional[BBoxPx]:
    """Convertit le dict ``{'x','y','w','h'}`` de tesserocr en bbox."""
    try:
        x = float(value["x"])
        y = float(value["y"])
        width = float(value["w"])
        height = float(value["h"])
    except (KeyError, TypeError, ValueError):
        return None
    return (x, y, x + width, y + height)


def _image_size(image: Any) -> Tuple[float, float]:
    size = getattr(image, "size", None)
    if isinstance(size, (tuple, list)) and len(size) == 2:
        try:
            return float(size[0]), float(size[1])
        except (TypeError, ValueError):
            pass
    return (0.0, 0.0)


def _confidence_from_percent(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return 0.0
    return max(0.0, min(1.0, number / 100.0))


def _normalise_ocr_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n").strip()


# ---------------------------------------------------------------------------
# Fabrique (R12)
# ---------------------------------------------------------------------------
_ENGINE_FACTORIES = {
    "tesseract": TesseractEngine,
}


def available_engines() -> Sequence[str]:
    return tuple(sorted(_ENGINE_FACTORIES))


def create_engine(name: str = "tesseract", **kwargs: Any) -> OcrEngine:
    """Instancie un moteur par nom (point d'extension unique)."""
    key = (name or "").strip().lower()
    try:
        factory = _ENGINE_FACTORIES[key]
    except KeyError:
        raise OcrEngineError(
            f"moteur OCR inconnu : {name!r} "
            f"(moteurs disponibles : {', '.join(available_engines())})"
        ) from None
    return factory(**kwargs)
