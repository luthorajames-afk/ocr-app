"""Tests de la session d'édition (phase 5 — R9), sans interface.

L'``EditSession`` (``ocr_app.ui.edit_session``) porte le document affiché :
édition de texte bloc par bloc, annulation / rétablissement bornés (50 pas),
statistiques de modification et retour au texte d'origine. Ces tests valident
l'API publique du modèle seul, sans fenêtre : les signaux Qt sont captés par de
simples collecteurs Python (connexion directe, donc synchrone).

Couverture demandée (mission phase 5) :
- ``update_block_text`` : texte mis à jour, ``block_id`` inchangé,
  ``is_dirty()`` vrai, signal ``documentChanged`` émis ;
- pile undo/redo exacte (bornée à 50 pas), état restauré à l'identique ;
- undo sur session vide : aucun crash, aucun changement ;
- bloc sans ``block_id`` : identité stable ``index-N`` ;
- page inexistante / document vide : no-op propre, sans exception ;
- ``reset()`` : texte d'origine restauré, ``is_dirty()`` faux (mode démo) ;
- ``adopt_page()`` : nouvelle page adoptée sans casser l'historique ;
- ``modified_block_count()`` / ``total_block_count()`` cohérents.
"""

from __future__ import annotations

import os

# Sans interface : inoffensif pour QtCore, utile si un test touche à QtGui.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 n'est pas installé (extra [ui])",
)
pytest.importorskip(
    "ocr_app.ui.edit_session",
    reason="ocr_app.ui.edit_session indisponible (phase 5)",
)

from ocr_app.core.data_model import Block, BlockType, Cell, Document, Page, Table
from ocr_app.ui.demo_data import generic_page, invoice_page
from ocr_app.ui.edit_session import EditSession, block_identity

ORIGINAL_TITLE_P1 = "Rapport annuel"
ORIGINAL_BODY_P1 = "Premier paragraphe de la page 1."
ORIGINAL_TITLE_P2 = "Contrat de bail"
ORIGINAL_BODY_P2 = "Premier paragraphe de la page 2."


# ---------------------------------------------------------------------------
# Documents d'essai
# ---------------------------------------------------------------------------
def _demo_page(number: int, prefix: str, title: str, body: str) -> Page:
    """Page d'essai : titre, paragraphe et tableau, tous identifiés."""
    return Page(
        number=number,
        source=f"demo://{prefix}.pdf",
        confidence=0.95,
        blocks=[
            Block(
                type=BlockType.TITLE,
                text=title,
                bbox=(72.0, 70.0, 523.0, 106.0),
                confidence=0.98,
                block_id=f"{prefix}-title",
            ),
            Block(
                type=BlockType.PARAGRAPH,
                text=body,
                bbox=(72.0, 118.0, 523.0, 208.0),
                confidence=0.96,
                block_id=f"{prefix}-body",
            ),
            Block(
                type=BlockType.TABLE,
                table=Table(
                    rows=1,
                    cols=2,
                    grid=[
                        [
                            Cell(text="Champ", row_index=0, col_index=0),
                            Cell(text="Valeur", row_index=0, col_index=1),
                        ]
                    ],
                ),
                bbox=(72.0, 236.0, 523.0, 300.0),
                confidence=0.94,
                block_id=f"{prefix}-table",
            ),
        ],
    )


def make_document() -> Document:
    """Document d'essai : 2 pages (blocs title / paragraph / table)."""
    return Document(
        title="Démo AOCRI",
        pages=[
            _demo_page(1, "p1", ORIGINAL_TITLE_P1, ORIGINAL_BODY_P1),
            _demo_page(2, "p2", ORIGINAL_TITLE_P2, ORIGINAL_BODY_P2),
        ],
    )


def make_anonymous_document() -> Document:
    """Document dont les blocs n'ont aucun ``block_id`` (repli ``index-N``)."""
    return Document(
        title="Anonyme",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(
                        type=BlockType.PARAGRAPH,
                        text="Bloc sans identifiant.",
                        confidence=0.9,
                    ),
                    Block(
                        type=BlockType.TITLE,
                        text="Titre sans identifiant.",
                        confidence=0.95,
                    ),
                ],
            )
        ],
    )


class SignalRecorder:
    """Capte les trois signaux de l'``EditSession`` (connexions directes)."""

    def __init__(self, session: EditSession) -> None:
        self.document_changed: list[tuple[int, str]] = []
        self.dirty_events: list[bool] = []
        self.reloaded_count = 0
        session.documentChanged.connect(self._on_document_changed)
        session.dirtyChanged.connect(self._on_dirty_changed)
        session.documentReloaded.connect(self._on_reloaded)

    # Slots Python : appelés de façon synchrone par les signaux Qt.
    def _on_document_changed(self, page_number: int, block_id: str) -> None:
        self.document_changed.append((page_number, block_id))

    def _on_dirty_changed(self, dirty: bool) -> None:
        self.dirty_events.append(dirty)

    def _on_reloaded(self) -> None:
        self.reloaded_count += 1


@pytest.fixture()
def session() -> EditSession:
    """Session chargée avec le document d'essai, nettoyée après le test."""
    edit_session = EditSession()
    edit_session.load_document(make_document())
    try:
        yield edit_session
    finally:
        edit_session.clear()


@pytest.fixture()
def recorder(session: EditSession) -> SignalRecorder:
    return SignalRecorder(session)


# ---------------------------------------------------------------------------
# update_block_text
# ---------------------------------------------------------------------------
def test_update_block_text_updates_text_and_emits_signal(session, recorder):
    block = session.page(1).blocks[0]

    result = session.update_block_text(1, "p1-title", "Titre corrigé")

    assert result is True
    assert block.text == "Titre corrigé"
    # Le lien bloc ↔ texte est conservé (R9) : identifiant inchangé.
    assert block.block_id == "p1-title"
    assert session.is_dirty() is True
    assert recorder.document_changed == [(1, "p1-title")]
    assert recorder.dirty_events == [True]


def test_update_same_text_is_clean_noop(session, recorder):
    current = session.page(1).blocks[0].text

    result = session.update_block_text(1, "p1-title", current)

    assert result is False
    assert session.is_dirty() is False
    assert recorder.document_changed == []
    assert recorder.dirty_events == []
    assert session.can_undo() is False
    assert session.can_redo() is False


def test_edited_page_is_shared_with_the_source_document():
    """Les ``Page`` sont partagées : le ``Document`` d'origine voit l'édition."""
    document = make_document()
    session = EditSession()
    session.load_document(document)

    session.update_block_text(1, "p1-body", "Texte partagé.")

    assert document.pages[0].blocks[1].text == "Texte partagé."


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------
def test_undo_redo_round_trip(session, recorder):
    block = session.page(1).blocks[0]

    session.update_block_text(1, "p1-title", "Version B")

    assert session.undo() is True
    assert block.text == ORIGINAL_TITLE_P1
    assert session.is_dirty() is False
    assert session.can_undo() is False
    assert session.can_redo() is True
    assert recorder.dirty_events == [True, False]
    # L'édition et l'annulation ont signalé le changement du même bloc.
    assert recorder.document_changed == [(1, "p1-title"), (1, "p1-title")]

    assert session.redo() is True
    assert block.text == "Version B"
    assert session.is_dirty() is True
    assert session.can_redo() is False

    assert session.undo() is True
    assert block.text == ORIGINAL_TITLE_P1


def test_undo_restores_original_state_exactly(session):
    """Après annulation, la page retrouve texte, bbox, confidence, table."""
    original = make_document()  # copie de référence, hors de la session
    session.update_block_text(1, "p1-title", "Titre modifié")
    session.update_block_text(1, "p1-body", "Corps modifié")

    while session.undo():
        pass

    assert session.page(1).to_dict() == original.pages[0].to_dict()
    assert session.page(2).to_dict() == original.pages[1].to_dict()
    assert session.is_dirty() is False
    assert session.modified_block_count() == 0


def test_new_edit_clears_redo_stack(session):
    block = session.page(1).blocks[0]

    session.update_block_text(1, "p1-title", "Version B")
    assert session.undo() is True
    assert session.can_redo() is True

    # Une nouvelle édition invalide le rétablissement (comportement standard).
    session.update_block_text(1, "p1-title", "Version C")
    assert session.can_redo() is False

    assert session.undo() is True
    assert block.text == ORIGINAL_TITLE_P1


def test_undo_stack_bounded_at_50_steps(session):
    """51 éditions → 50 pas conservés ; le plus ancien est perdu."""
    body = session.page(1).blocks[1]

    for index in range(51):
        assert session.update_block_text(
            1, "p1-body", f"édition {index:02d}"
        )

    undo_count = 0
    while session.undo():
        undo_count += 1
    assert undo_count == 50  # 51 éditions, pile bornée à UNDO_LIMIT
    assert session.UNDO_LIMIT == 50
    # Le pas de l'édition 00 a été évincé : l'état le plus ancien restaurable
    # est celui produit par l'édition 01 → retour à « édition 00 ».
    assert body.text == "édition 00"
    assert session.can_undo() is False

    redo_count = 0
    while session.redo():
        redo_count += 1
    assert redo_count == 50
    assert body.text == "édition 50"


def test_undo_on_empty_session_is_safe_noop(recorder):
    fresh = EditSession()

    assert fresh.undo() is False
    assert fresh.redo() is False
    assert fresh.is_dirty() is False
    assert fresh.can_undo() is False
    assert fresh.can_redo() is False

    # Session chargée mais vierge d'édition : même comportement.
    loaded = EditSession()
    loaded.load_document(make_document())
    assert loaded.undo() is False
    assert loaded.redo() is False
    assert loaded.page(1).blocks[0].text == ORIGINAL_TITLE_P1


# ---------------------------------------------------------------------------
# Identité des blocs sans block_id
# ---------------------------------------------------------------------------
def test_block_without_id_gets_stable_index_identity():
    session = EditSession()
    session.load_document(make_anonymous_document())
    try:
        page = session.page(1)
        assert all(block.block_id is None for block in page.blocks)

        first = block_identity(page.blocks[0], 0)
        second = block_identity(page.blocks[0], 0)
        # Identité stable : deux appels ne régénèrent pas (ni dupliquent) l'id.
        assert first == second == "index-0"
        assert page.blocks[0].block_id is None

        assert session.update_block_text(1, first, "Texte édité.") is True
        assert page.blocks[0].text == "Texte édité."

        # Deuxième édition via la même identité : toujours le même bloc.
        assert session.update_block_text(1, first, "Texte réédité.") is True
        assert page.blocks[0].text == "Texte réédité."
        assert page.blocks[0].block_id is None
        assert session.modified_block_count() == 1

        # Undo retrouve le bloc par l'identité positionnelle.
        assert session.undo() is True
        assert page.blocks[0].text == "Texte édité."
        assert session.undo() is True
        assert page.blocks[0].text == "Bloc sans identifiant."
    finally:
        session.clear()


# ---------------------------------------------------------------------------
# Cas limites : page inexistante, document vide
# ---------------------------------------------------------------------------
def test_update_on_missing_page_is_clean_noop(session, recorder):
    assert session.update_block_text(999, "p1-title", "n'importe quoi") is False
    assert session.update_block_text(1, "bloc-inconnu", "texte") is False
    assert session.update_block_text(1, "index-99", "hors limites") is False

    assert session.is_dirty() is False
    assert recorder.document_changed == []
    assert recorder.dirty_events == []
    assert session.can_undo() is False
    assert session.page(999) is None
    assert session.block_index(999, "p1-title") is None


def test_empty_and_never_loaded_documents_are_clean_noop():
    # Document existant mais sans page.
    empty = EditSession()
    empty.load_document(Document(title="Vide", pages=[]))
    try:
        assert empty.update_block_text(1, "p1-title", "texte") is False
        assert empty.total_block_count() == 0
        assert empty.modified_block_count() == 0
        assert empty.page(1) is None
        assert empty.block_index(1, "p1-title") is None
        assert empty.undo() is False
        assert empty.redo() is False
        empty.reset()  # aucun crash
    finally:
        empty.clear()

    # Session jamais chargée : aucun document courant.
    virgin = EditSession()
    assert virgin.document is None
    assert virgin.update_block_text(1, "x", "y") is False
    assert virgin.page(1) is None
    assert virgin.undo() is False
    assert virgin.redo() is False
    assert virgin.reset() is None  # no-op explicite
    assert virgin.adopt_page(None) is None  # garde-fou, sans exception
    assert virgin.is_dirty() is False


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------
def test_reset_restores_original_text_demo_mode(session, recorder):
    session.update_block_text(1, "p1-title", "Titre provisoire")
    session.update_block_text(2, "p2-body", "Corps provisoire")
    assert session.is_dirty() is True

    session.reset()

    # Mode démo : restauration instantanée depuis l'instantané mémoire.
    assert session.page(1).blocks[0].text == ORIGINAL_TITLE_P1
    assert session.page(2).blocks[1].text == ORIGINAL_BODY_P2
    assert session.is_dirty() is False
    assert session.can_undo() is False
    assert session.can_redo() is False
    assert session.modified_block_count() == 0
    assert recorder.reloaded_count >= 1  # load_document + reset


def test_reset_clears_history_for_fresh_edits(session):
    session.update_block_text(1, "p1-title", "Provisoire")
    session.reset()
    assert session.update_block_text(1, "p1-title", "Nouvelle édition")
    assert session.undo() is True
    assert session.page(1).blocks[0].text == ORIGINAL_TITLE_P1


def test_reset_accepts_reloaded_pages(session):
    """Les pages rechargées du dossier de travail priment sur l'instantané."""
    session.update_block_text(1, "p1-title", "Titre provisoire")
    session.update_block_text(2, "p2-title", "Autre provisoire")

    reloaded = _demo_page(2, "p2", "Page 2 rechargée", ORIGINAL_BODY_P2)
    session.reset([reloaded])

    # Page absente de la liste → instantané mémoire (mode démo).
    assert session.page(1).blocks[0].text == ORIGINAL_TITLE_P1
    # Page présente dans la liste → contenu du fichier rechargé.
    assert session.page(2).blocks[0].text == "Page 2 rechargée"
    assert session.is_dirty() is False
    # L'état restauré devient la nouvelle origine : statistiques à zéro.
    assert session.modified_block_count() == 0
    session.update_block_text(2, "p2-title", "Suite")
    assert session.modified_block_count() == 1


# ---------------------------------------------------------------------------
# adopt_page()
# ---------------------------------------------------------------------------
def test_adopt_page_keeps_history_and_signals(session, recorder):
    session.update_block_text(1, "p1-title", "Titre modifié")

    new_page = Page(
        number=3,
        blocks=[
            Block(
                type=BlockType.PARAGRAPH,
                text="Résultat du worker.",
                confidence=0.92,
                block_id="p3-body",
            )
        ],
    )
    before = recorder.reloaded_count
    session.adopt_page(new_page)

    # La page adoptée est celle du runner (partagée, pas copiée) et triée.
    assert session.page(3) is new_page
    assert [page.number for page in session.document.pages] == [1, 2, 3]
    assert recorder.reloaded_count == before + 1

    # L'historique n'est pas cassé : l'édition de la page 1 reste annulable.
    assert session.can_undo() is True
    assert session.is_dirty() is True
    assert session.undo() is True
    assert session.page(1).blocks[0].text == ORIGINAL_TITLE_P1
    assert session.is_dirty() is False

    # La page adoptée est éditable et son instantané « origine » est pris.
    assert session.update_block_text(3, "p3-body", "Édité.") is True
    assert session.modified_block_count() == 1
    assert session.total_block_count() == 7


def test_adopt_page_replaces_existing_page_number(session):
    replacement = _demo_page(2, "p2", "Version corrigée", "Nouveau corps")
    session.adopt_page(replacement)

    assert session.page(2) is replacement
    assert [page.number for page in session.document.pages] == [1, 2]
    assert session.total_block_count() == 6
    # L'ancienne page 2 a été remplacée, l'instantané suit : aucune
    # « modification fantôme » ne subsiste après le remplacement.
    assert session.modified_block_count() == 0


# ---------------------------------------------------------------------------
# Statistiques
# ---------------------------------------------------------------------------
def test_modified_and_total_block_counts(session):
    assert session.total_block_count() == 6  # 2 pages × 3 blocs
    assert session.modified_block_count() == 0

    session.update_block_text(1, "p1-title", "A")
    assert session.modified_block_count() == 1

    # Rééditer le même bloc ne compte qu'une fois (différence vs origine).
    session.update_block_text(1, "p1-title", "B")
    assert session.modified_block_count() == 1

    session.update_block_text(2, "p2-body", "C")
    assert session.modified_block_count() == 2

    # Annulations en ordre inverse : la statistique suit.
    session.undo()  # annule « C »
    assert session.modified_block_count() == 1
    session.undo()  # annule « B » (le bloc reste différent de l'origine)
    assert session.modified_block_count() == 1
    session.undo()  # annule « A »
    assert session.modified_block_count() == 0
    assert session.is_dirty() is False


def test_counts_on_adopted_and_empty_documents():
    empty = EditSession()
    empty.load_document(Document(title="Vide", pages=[]))
    try:
        assert empty.total_block_count() == 0
        assert empty.modified_block_count() == 0
    finally:
        empty.clear()

    session = EditSession()
    session.load_document(
        Document(
            title="Démo",
            pages=[invoice_page(1), generic_page("Contrat de bail", 2)],
        )
    )
    try:
        assert session.total_block_count() == 6
        session.update_block_text(1, "demo-title", "Facture corrigée")
        assert session.modified_block_count() == 1
        assert session.page(1).blocks[0].block_id == "demo-title"
    finally:
        session.clear()


# ---------------------------------------------------------------------------
# Accès aux blocs
# ---------------------------------------------------------------------------
def test_block_index_resolution(session):
    assert session.block_index(1, "p1-title") == 0
    assert session.block_index(1, "p1-body") == 1
    assert session.block_index(2, "p2-table") == 2
    assert session.block_index(1, "inconnu") is None
    assert session.block_index(42, "p1-title") is None


def test_load_document_clears_previous_history(session, recorder):
    session.update_block_text(1, "p1-title", "Provisoire")
    assert session.is_dirty() is True

    before = recorder.reloaded_count
    session.load_document(make_document(), task_id="task-2")

    assert session.is_dirty() is False
    assert session.can_undo() is False
    assert session.task_id == "task-2"
    assert recorder.reloaded_count == before + 1
    assert session.page(1).blocks[0].text == ORIGINAL_TITLE_P1
