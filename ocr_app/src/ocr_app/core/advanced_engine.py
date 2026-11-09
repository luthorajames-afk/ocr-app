"""Moteur du mode Avancé : Docling (layout, tableaux, lecture structurée).

Règles projet :
- R1/R13 : Docling (et PyTorch) ne vit que dans le venv d'évaluation dédié
  (``.venv-eval-advanced``) et ne tourne QUE dans le sous-processus du
  worker (R2) — jamais dans l'interface. Ce module n'importe Docling que
  lorsque :meth:`DoclingEngine.initialize` est appelée, c'est-à-dire dans le
  worker du mode Avancé ; l'import y est donc paresseux.
- R4 : hors-ligne. Les modèles Docling ont été téléchargés une fois pour
  toutes dans le cache Hugging Face local (``~/.cache/huggingface/hub``,
  dépôts ``docling-project/docling-models`` et ``docling-layout-heron``).
  Quand ce cache existe, les variables ``HF_HUB_OFFLINE``/``TRANSFORMERS_OFFLINE``
  sont forcées AVANT l'import de Docling : aucun accès réseau n'est tenté.
  Un dossier d'artefacts local reste prioritaire via ``DOCLING_ARTIFACTS_PATH``
  (supporté nativement par Docling).

Extraction : ``DocumentConverter().convert(chemin)`` retourne un
``ConversionResult`` dont ``document`` (``DoclingDocument``) est parcouru
avec ``iterate_items()`` : chaque item porte un label (``title``,
``section_header``, ``text``, ``table``, ``picture``…) et une provenance
(``prov`` : numéro de page + ``bbox`` en points). Les bboxes sont converties
en points PDF origine haut-gauche (convention des blocs OCR du projet). Les
tableaux sont reconstruits en ``Table`` (grille de ``Cell``). Si un item
n'a pas de provenance exploitable, il reçoit la bbox pleine page (limite
honnête, documentée) — l'alternative (Markdown exporté sans positions)
n'est pas utilisée car ``prov`` s'est révélée fiable sur le jeu de test.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ocr_app.core.data_model import Block, BlockType, Cell, Table
from ocr_app.core.pipeline import PipelineError
from ocr_app.core.rendering import normalise_native_text

logger = logging.getLogger(__name__)

# Dépôts Hugging Face contenant les modèles Docling (cache local, R4).
_HF_CACHE_SUBSTRINGS = ("models--docling-project--",)

# Labels Docling -> type de bloc du modèle de données.
_LABEL_TO_TYPE: Dict[str, BlockType] = {
    "title": BlockType.TITLE,
    "section_header": BlockType.TITLE,
    "document_index": BlockType.TITLE,
    "table": BlockType.TABLE,
    "picture": BlockType.IMAGE,
    "chart": BlockType.IMAGE,
    "page_header": BlockType.HEADER,
    "page_footer": BlockType.FOOTER,
}


def _hf_cache_dir() -> Path:
    """Cache Hugging Face effectif (variable d'environnement ou défaut)."""
    override = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if override:
        return Path(override)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def docling_models_cached() -> bool:
    """Vrai si les modèles Docling sont déjà présents dans le cache local.

    Sert à décider du mode hors-ligne (R4) sans toucher au réseau : si les
    dépôts ``docling-project/*`` sont dans le cache Hugging Face, le moteur
    peut fonctionner sans aucune connexion.
    """
    cache = _hf_cache_dir()
    try:
        return any(
            substring in entry.name
            for entry in cache.iterdir()
            for substring in _HF_CACHE_SUBSTRINGS
        )
    except OSError:
        return False


def configure_offline_mode() -> bool:
    """Force le hors-ligne (R4) si les modèles sont déjà en cache local.

    À appeler AVANT tout import de Docling/``huggingface_hub`` (ces
    constantes sont lues à l'import). Retourne l'état hors-ligne obtenu.
    """
    offline = docling_models_cached()
    if offline:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    return offline


def import_document_converter() -> type:
    """Import paresseux de Docling ; À FAIRE DANS LE THREAD PRINCIPAL.

    Contournement documenté : importer Docling (qui tire numpy, dont
    l'extension C ``_multiarray_umath`` charge des DLL OpenBLAS) alors
    qu'un thread du processus est bloqué en lecture d'un tube stdin peut
    rester bloqué indéfiniment au chargement de la DLL (verrou du chargeur
    Windows ; reproductible, indépendant du thread importateur ;
    ``tesserocr`` n'est pas affecté). Le worker précharge donc Docling dans
    son thread principal via cette fonction, avant de démarrer son lecteur
    de commandes ; le reste (construction du convertisseur, chargement des
    poids, conversion) s'exécute ensuite sans nouveau chargement de DLL.
    """
    configure_offline_mode()
    from docling.document_converter import DocumentConverter

    return DocumentConverter


class DoclingEngine:
    """Enveloppe paresseuse autour de Docling, page par page.

    Le cycle de vie suit ``BaseWorker`` : ``initialize`` charge le
    convertisseur (une fois), ``convert_document`` traite tout le document
    (Docling travaille à l'échelle du fichier), ``shutdown`` libère.
    """

    def __init__(self) -> None:
        self._converter: Any = None

    # ------------------------------------------------------------ cycle de vie
    def initialize(self) -> None:
        """Importe Docling et construit le convertisseur (hors-ligne, R4).

        L'import passe par :func:`import_document_converter` (offline posé
        avant l'import, voir sa note sur le thread principal). Si le cache
        est absent (première installation), on ne force rien : Docling
        téléchargera ses modèles lors d'une session en ligne, puis
        fonctionnera hors-ligne ensuite.
        """
        if self._converter is not None:
            return
        try:
            converter_class = import_document_converter()
        except ImportError as exc:  # venv Avancé non installé
            raise PipelineError(
                "mode Avancé : Docling est introuvable dans cet "
                "environnement (venv d'évaluation .venv-eval-advanced requis) "
                f"({exc.__class__.__name__})"
            ) from exc
        try:
            self._converter = converter_class()
        except Exception as exc:  # noqa: BLE001 - initialisation contextualisée
            raise PipelineError(
                f"mode Avancé : initialisation de Docling impossible : {exc}"
            ) from exc
        logger.debug(
            "DoclingEngine initialisée (offline=%s)", docling_models_cached()
        )

    def shutdown(self) -> None:
        self._converter = None

    # ------------------------------------------------------------ conversion
    def convert_document(self, source_path: str) -> Dict[int, List[Block]]:
        """Convertit un document et retourne les blocs par numéro de page.

        Retour : ``{numéro de page (1-based): [Block, …]}`` — les ``Block``
        portent type, texte, bbox (points PDF, origine haut-gauche) et
        ``Table`` reconstruite pour les tableaux ; ``block_id`` est posé par
        l'appelant (worker). Docling n'expose pas de score de confiance par
        item : la confiance est fixée à 1.0 (documenté).
        """
        if self._converter is None:
            self.initialize()
        assert self._converter is not None
        path = Path(source_path)
        try:
            result = self._converter.convert(str(path))
        except Exception as exc:  # noqa: BLE001 - erreur Docling contextualisée
            raise PipelineError(
                f"conversion Docling impossible ({path.name}) : "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        status = getattr(result, "status", None)
        status_name = str(status).rsplit(".", maxsplit=1)[-1] if status is not None else "?"
        if status_name not in ("SUCCESS", "PARTIAL_SUCCESS"):
            raise PipelineError(
                f"conversion Docling incomplète ({path.name}) : statut {status_name}"
            )

        document = result.document
        page_sizes = self._page_sizes(document)
        blocks_by_page: Dict[int, List[Block]] = {
            number: [] for number in page_sizes
        }
        for item, _level in document.iterate_items():
            page_number, bbox = self._item_placement(item, page_sizes)
            block = self._item_to_block(item, page_number, bbox)
            if block is None:
                continue
            blocks_by_page.setdefault(page_number, []).append(block)
        return blocks_by_page

    # ------------------------------------------------------------ internes
    @staticmethod
    def _page_sizes(document: Any) -> Dict[int, Tuple[float, float]]:
        """Tailles des pages en points, par numéro (1-based)."""
        sizes: Dict[int, Tuple[float, float]] = {}
        pages = getattr(document, "pages", {}) or {}
        for number, page in pages.items():
            size = getattr(page, "size", None)
            width = float(getattr(size, "width", 0.0) or 0.0)
            height = float(getattr(size, "height", 0.0) or 0.0)
            try:
                key = int(number)
            except (TypeError, ValueError):
                continue
            sizes[key] = (width, height)
        return sizes

    @staticmethod
    def _item_placement(
        item: Any,
        page_sizes: Dict[int, Tuple[float, float]],
    ) -> Tuple[int, Tuple[float, float, float, float]]:
        """(page, bbox origine haut-gauche) d'un item, avec replis honnêtes.

        ``prov[0].bbox`` est en points Docling, origine indiquée par
        ``coord_origin`` (``BOTTOMLEFT`` observé). Sans provenance exploitable
        (rare), l'item reçoit la bbox pleine page : le texte reste complet,
        seule la position est approximative — limite documentée.
        """
        provs = getattr(item, "prov", None) or []
        prov = provs[0] if provs else None
        page_number = int(getattr(prov, "page_no", 0) or 0)
        if page_number not in page_sizes:
            # Page inconnue : première page connue (repli).
            page_number = next(iter(page_sizes), 0)
        width, height = page_sizes.get(page_number, (0.0, 0.0))
        bbox = getattr(prov, "bbox", None) if prov is not None else None
        if bbox is None:
            return page_number, (0.0, 0.0, width, height)
        left = float(getattr(bbox, "l", 0.0) or 0.0)
        right = float(getattr(bbox, "r", 0.0) or 0.0)
        top = float(getattr(bbox, "t", 0.0) or 0.0)
        bottom = float(getattr(bbox, "b", 0.0) or 0.0)
        origin = str(getattr(bbox, "coord_origin", ""))
        if "BOTTOMLEFT" in origin:
            y0, y1 = height - top, height - bottom
        else:  # TOPLEFT (ou origine inconnue : hypothèse haut-gauche)
            y0, y1 = top, bottom
        return page_number, (left, min(y0, y1), right, max(y0, y1))

    def _item_to_block(
        self,
        item: Any,
        page_number: int,
        bbox: Tuple[float, float, float, float],
    ) -> Optional[Block]:
        """Convertit un item Docling en ``Block`` (None si vide/hors page)."""
        if page_number <= 0:
            return None
        label = str(getattr(item, "label", "") or "")
        block_type = _LABEL_TO_TYPE.get(label, BlockType.PARAGRAPH)

        if block_type is BlockType.TABLE:
            table = self._build_table(item)
            if table is None or not table.grid:
                return None
            return Block(
                type=block_type,
                text="",
                bbox=bbox,
                confidence=1.0,
                table=table,
            )

        text = normalise_native_text(
            str(getattr(item, "text", "") or "")
        )
        if not text and block_type is not BlockType.IMAGE:
            return None
        return Block(type=block_type, text=text, bbox=bbox, confidence=1.0)

    @staticmethod
    def _build_table(item: Any) -> Optional[Table]:
        """Reconstruit la grille Docling en ``Table`` (cellules + fusions).

        Docling répète le même objet cellule sur toutes les positions d'une
        cellule fusionnée : la première occurrence garde le texte, les
        suivantes deviennent ``None`` (trous) — la grille reste lisible et
        l'aperçu/export gèrent déjà les cellules vides.
        """
        data = getattr(item, "data", None)
        grid = getattr(data, "grid", None)
        if not grid:
            return None
        seen: set = set()
        rows: List[List[Optional[Cell]]] = []
        for row_index, row in enumerate(grid):
            grid_row: List[Optional[Cell]] = []
            for col_index, cell in enumerate(row):
                cell_id = id(cell)
                if cell is None or cell_id in seen:
                    grid_row.append(None)
                    continue
                seen.add(cell_id)
                text = normalise_native_text(
                    str(getattr(cell, "text", "") or "")
                )
                grid_row.append(
                    Cell(text=text, row_index=row_index, col_index=col_index)
                )
            rows.append(grid_row)
        if not rows:
            return None
        return Table(rows=len(rows), cols=max(len(row) for row in rows), grid=rows)
