"""Session d'édition du document courant (phase 5 — édition avant copie, R9).

L'``EditSession`` porte le ``Document`` affiché et applique les modifications
de texte bloc par bloc. Les objets ``Page`` sont partagés avec le
``TaskRunner`` : une édition faite ici est directement visible par la copie et
l'export, sans duplication d'état (R2 : tout reste dans le processus UI, aucun
calcul d'OCR).

Fonctions :
- ``update_block_text`` : édition du texte d'un bloc (``block_id`` conservé) ;
- ``undo`` / ``redo`` : pile bornée (50 pas) ;
- ``is_dirty`` / ``modified_block_count`` : statut et statistiques ;
- ``reset`` : retour au texte d'origine (fichiers ``page_XXX.json`` ou
  instantané pris au chargement — utile en mode ``--demo`` sans dossier).

Le lien bloc ↔ texte s'appuie sur ``block_id`` ; pour les pages dont les blocs
n'ont pas d'identifiant, l'identité positionnelle ``index-N`` est acceptée en
repli (les blocs ne sont jamais réordonnés par l'édition).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from ocr_app.core.data_model import Block, Document, Page


@dataclass(frozen=True)
class _EditStep:
    """Une modification annulable : page, bloc, texte avant / après."""

    page_number: int
    block_id: str
    old_text: str
    new_text: str


def block_identity(block: Block, index: int) -> str:
    """Identifiant stable d'un bloc : ``block_id`` sinon ``index-N``."""
    if block.block_id:
        return block.block_id
    return f"index-{index}"


def load_pages_from_dir(directory: Path) -> List[Page]:
    """Recharge les ``page_XXX.json`` d'un dossier de travail (triés, tolérant).

    Les fichiers illisibles sont ignorés : la réinitialisation retombe alors
    sur l'instantané mémoire pris au chargement de la page.
    """
    if directory is None:
        return []
    pages: List[Page] = []
    try:
        paths = sorted(Path(directory).glob("page_*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            pages.append(Page.load_json(path))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return pages


class EditSession(QObject):
    """Modèle d'édition du document courant avec annulation / rétablissement."""

    documentChanged = Signal(int, str)  # page_number, block_id
    dirtyChanged = Signal(bool)
    documentReloaded = Signal()

    UNDO_LIMIT = 50
    # Instantanés « origine » conservés par tâche : réafficher un document
    # déjà vu ne doit pas prendre l'état (déjà édité) comme nouvelle origine.
    # Les identifiants de tâche étant uniques, une entrée datée est inoffensive.
    ORIGIN_CACHE_LIMIT = 12

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._document: Optional[Document] = None
        self._task_id: Optional[str] = None
        # Instantanés « origine » par numéro de page (copies indépendantes).
        self._original_pages: Dict[int, Page] = {}
        self._origin_cache: Dict[Optional[str], Dict[int, Page]] = {}
        self._undo_stack: List[_EditStep] = []
        self._redo_stack: List[_EditStep] = []
        self._dirty = False

    # --- Accès ------------------------------------------------------------
    @property
    def document(self) -> Optional[Document]:
        return self._document

    @property
    def task_id(self) -> Optional[str]:
        return self._task_id

    def page(self, page_number: int) -> Optional[Page]:
        if self._document is None:
            return None
        for page in self._document.pages:
            if page.number == page_number:
                return page
        return None

    def block_index(self, page_number: int, block_id: str) -> Optional[int]:
        index, _page, _block = self._resolve(page_number, block_id)
        return index

    # --- Chargement -------------------------------------------------------
    def load_document(
        self,
        document: Optional[Document],
        *,
        task_id: Optional[str] = None,
    ) -> None:
        """Remplace le document courant (les ``Page`` restent partagées).

        Si la même tâche est réaffichée (re-clic dans la liste, rafraîchissement
        après sélection), l'historique d'annulation et l'instantané « origine »
        sont conservés : l'origine reste le texte livré par le worker, et non
        l'état déjà édité au moment du réaffichage. Pour une nouvelle tâche,
        l'origine est reprise du cache par tâche (aller-retour dans la liste)
        ou prise à frais ; l'historique démarre vide.
        """
        if task_id is not None and task_id == self._task_id and document is not None:
            self._document = document
            self._recompute_dirty()
            self.documentReloaded.emit()
            return

        self._document = document
        self._task_id = task_id
        self._original_pages = {}
        if self._document is not None:
            cached = self._origin_cache.get(task_id, {})
            for page in self._document.pages:
                snapshot = cached.get(page.number)
                self._original_pages[page.number] = (
                    snapshot if snapshot is not None else self._snapshot(page)
                )
            self._store_origin_cache()
        self._clear_history()
        self._recompute_dirty()
        self.documentReloaded.emit()

    def adopt_page(self, page: Page) -> None:
        """Intègre une page fraîchement traitée sans casser l'historique.

        Utilisé quand le worker livre une nouvelle page pendant que la session
        est ouverte : les éditions des autres pages restent annulables.
        """
        if page is None:
            return
        if self._document is None:
            self._document = Document(title="Document", pages=[page])
        else:
            existing = self.page(page.number)
            if existing is not None:
                position = self._document.pages.index(existing)
                self._document.pages[position] = page
            else:
                self._document.pages.append(page)
                self._document.pages.sort(key=lambda item: item.number)
        self._original_pages[page.number] = self._snapshot(page)
        if self._task_id is not None and self._task_id in self._origin_cache:
            self._origin_cache[self._task_id][page.number] = self._original_pages[
                page.number
            ]
        self.documentReloaded.emit()

    def clear(self) -> None:
        """Vide la session (aucun document courant)."""
        self._document = None
        self._task_id = None
        self._original_pages = {}
        self._clear_history()
        self.documentReloaded.emit()

    # --- Édition ----------------------------------------------------------
    def update_block_text(
        self, page_number: int, block_id: str, new_text: str
    ) -> bool:
        """Applique l'édition d'un bloc et empile le pas d'annulation.

        Retourne ``False`` si le bloc est introuvable ou si le texte est
        inchangé (rien n'est alors empilé ni signalé).
        """
        index, page, block = self._resolve(page_number, block_id)
        if block is None:
            return False
        text = "" if new_text is None else str(new_text)
        if block.text == text:
            return False
        step = _EditStep(page_number, block_id, block.text, text)
        self._undo_stack.append(step)
        if len(self._undo_stack) > self.UNDO_LIMIT:
            del self._undo_stack[: len(self._undo_stack) - self.UNDO_LIMIT]
        self._redo_stack.clear()
        block.text = text  # les Page sont partagées : le runner voit l'édition
        self._set_dirty(True)
        self.documentChanged.emit(page_number, block_id)
        return True

    def undo(self) -> bool:
        """Annule le dernier pas ; émet ``documentChanged`` si effectif."""
        if not self._undo_stack:
            return False
        step = self._undo_stack.pop()
        _index, _page, block = self._resolve(step.page_number, step.block_id)
        if block is None or block.text != step.new_text:
            # Bloc disparu ou remplacé par une page re-livrée par le worker :
            # le pas ne correspond plus à l'état du modèle, on l'abandonne et
            # on enchaîne (ou on échoue proprement en bout de pile).
            return self.undo()
        block.text = step.old_text
        self._redo_stack.append(step)
        self._set_dirty(bool(self._undo_stack))
        self.documentChanged.emit(step.page_number, step.block_id)
        return True

    def redo(self) -> bool:
        """Rétablit le dernier pas annulé ; émet ``documentChanged``."""
        if not self._redo_stack:
            return False
        step = self._redo_stack.pop()
        _index, _page, block = self._resolve(step.page_number, step.block_id)
        if block is None or block.text != step.old_text:
            return self.redo()
        block.text = step.new_text
        self._undo_stack.append(step)
        self._set_dirty(True)
        self.documentChanged.emit(step.page_number, step.block_id)
        return True

    def reset(self, pages: Optional[List[Page]] = None) -> None:
        """Restaure le texte d'origine de toutes les pages connues.

        ``pages`` : pages rechargées depuis le dossier de travail
        (``page_XXX.json``). Les pages absentes de la liste (ou tous les blocs,
        si ``pages`` est vide — cas du mode ``--demo``) reviennent à
        l'instantané pris au chargement. L'historique est vidé.
        """
        if self._document is None:
            return
        replacements: Dict[int, Page] = {}
        if pages:
            replacements = {page.number: page for page in pages}
        for page in self._document.pages:
            source = replacements.get(page.number)
            if source is None:
                source = self._original_pages.get(page.number)
            if source is None:
                continue
            page.blocks = [
                Block.from_dict(block.to_dict()) for block in source.blocks
            ]
            page.confidence = source.confidence
        # L'état restauré devient la nouvelle origine : les statistiques
        # (« blocs modifiés ») repartent de zéro après réinitialisation.
        self._original_pages = {
            page.number: self._snapshot(page)
            for page in self._document.pages
        }
        self._store_origin_cache()
        self._clear_history()
        self.documentReloaded.emit()

    # --- État -------------------------------------------------------------
    def is_dirty(self) -> bool:
        """Vrai tant qu'au moins une édition est appliquée (non annulée)."""
        return self._dirty

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def modified_block_count(self) -> int:
        """Nombre de blocs dont le texte diffère de l'instantané d'origine."""
        if self._document is None:
            return 0
        count = 0
        for page in self._document.pages:
            original = self._original_pages.get(page.number)
            originals = original.blocks if original is not None else []
            for index, block in enumerate(page.blocks):
                if index >= len(originals):
                    count += 1
                elif originals[index].text != block.text:
                    count += 1
        return count

    def total_block_count(self) -> int:
        if self._document is None:
            return 0
        return sum(len(page.blocks) for page in self._document.pages)

    # --- Interne ----------------------------------------------------------
    def _resolve(
        self, page_number: int, block_id: str
    ) -> Tuple[Optional[int], Optional[Page], Optional[Block]]:
        """Retrouve (index, page, bloc) par ``block_id`` ou repli ``index-N``."""
        page = self.page(page_number)
        if page is None:
            return None, None, None
        for index, block in enumerate(page.blocks):
            if block.block_id is not None and block.block_id == block_id:
                return index, page, block
        if block_id and block_id.startswith("index-"):
            try:
                position = int(block_id[len("index-"):])
            except ValueError:
                return None, None, None
            if 0 <= position < len(page.blocks):
                return position, page, page.blocks[position]
        return None, None, None

    @staticmethod
    def _snapshot(page: Page) -> Page:
        """Copie indépendante d'une page (les Table/Cell sont mutables)."""
        return Page.from_dict(page.to_dict())

    def _store_origin_cache(self) -> None:
        """Mémorise l'origine courante pour la tâche (retour sur la tâche)."""
        if self._task_id is None or self._document is None:
            return
        self._origin_cache[self._task_id] = dict(self._original_pages)
        while len(self._origin_cache) > self.ORIGIN_CACHE_LIMIT:
            oldest = next(iter(self._origin_cache))
            del self._origin_cache[oldest]

    def _recompute_dirty(self) -> None:
        """Recalcule ``dirty`` par comparaison des textes à l'origine.

        Utilisé au (re)chargement d'un document : des éditions antérieures
        subsistent dans les ``Page`` partagées même quand l'historique a été
        vidé, et l'interface doit continuer à les signaler (● modifié,
        Réinitialiser actif).
        """
        self._set_dirty(self.modified_block_count() > 0)

    def _clear_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._set_dirty(False)

    def _set_dirty(self, dirty: bool) -> None:
        if self._dirty != dirty:
            self._dirty = dirty
            self.dirtyChanged.emit(dirty)
