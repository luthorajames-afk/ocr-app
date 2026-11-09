"""Tests de la boîte de dialogue d'export (phase 6 — R14).

Couverture, hors écran (``QT_QPA_PLATFORM=offscreen`` posé AVANT tout import
PySide6) :

- ``EXPORT_FORMATS`` : six formats exportables, suffixes uniques en
  minuscules, libellés lisibles, méthodes existantes sur ``Exporter`` ;
- ``format_by_key`` / ``format_for_suffix`` : recherche par clé et par
  extension (avec ou sans point, insensible à la casse) ;
- ``ask_export_path`` avec un ``QFileDialog`` simulé (monkeypatch) :
  le format est déduit du suffixe saisi (« rapport v1.2.docx » garde son
  nom ET son .docx, sans double extension ni amputation du « .2 ») ; un
  nom nu reçoit le suffixe du filtre sélectionné ; une extension inconnue
  reçoit le suffixe EN PLUS (pas de substitution) ; annulation → ``None`` ;
  sélection vide → ``None`` ; filtre inconnu → premier format ; stem vide
  → repli « resultat_ocr ».
"""

from __future__ import annotations

import os

# Doit être positionné avant toute création de QApplication / import Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip(
    "PySide6",
    reason="PySide6 n'est pas installé (extra [ui])",
)
pytest.importorskip(
    "ocr_app.ui.export_dialog",
    reason="ocr_app.ui.export_dialog indisponible (phase 6)",
)

from ocr_app.core.exports import Exporter
from ocr_app.ui import export_dialog
from ocr_app.ui.export_dialog import (
    EXPORT_FORMATS,
    ExportFormat,
    ask_export_path,
    format_by_key,
    format_for_suffix,
)

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog


# ---------------------------------------------------------------------------
# EXPORT_FORMATS
# ---------------------------------------------------------------------------
def test_export_formats_table():
    assert len(EXPORT_FORMATS) == 6
    assert [fmt.key for fmt in EXPORT_FORMATS] == [
        "docx",
        "xlsx",
        "pdf",
        "html",
        "md",
        "txt",
    ]
    suffixes = [fmt.suffix for fmt in EXPORT_FORMATS]
    assert len(set(suffixes)) == 6  # tous uniques
    assert set(suffixes) == {".docx", ".xlsx", ".pdf", ".html", ".md", ".txt"}
    for fmt in EXPORT_FORMATS:
        assert isinstance(fmt, ExportFormat)
        assert fmt.suffix.startswith(".") and fmt.suffix == fmt.suffix.lower()
        # Libellé lisible : « Word (*.docx) ».
        assert fmt.label.endswith(f"(*{fmt.suffix})")
        assert fmt.method.startswith("save_")
        assert callable(getattr(Exporter, fmt.method))


def test_format_by_key():
    docx_fmt = format_by_key("docx")
    assert docx_fmt is not None
    assert docx_fmt.suffix == ".docx"
    assert format_by_key("md") is not None
    assert format_by_key("nope") is None
    assert format_by_key("") is None


def test_format_for_suffix():
    docx_fmt = format_by_key("docx")
    assert format_for_suffix(".docx") is docx_fmt
    assert format_for_suffix("docx") is docx_fmt  # sans point
    assert format_for_suffix("DOCX") is docx_fmt  # insensible à la casse
    assert format_for_suffix("") is None
    assert format_for_suffix(".xyz") is None


# ---------------------------------------------------------------------------
# ask_export_path : QFileDialog simulé
# ---------------------------------------------------------------------------
class _FakeSaveDialog(QObject):
    """Remplaçant minimal du QFileDialog « Enregistrer ».

    Reproduit la liaison ``filterSelected`` → ``setDefaultSuffix`` du vrai
    dialogue (connexion directe, donc synchrone).
    """

    filterSelected = Signal(str)

    # Références des énumérations utilisées par ``ask_export_path``.
    AcceptMode = QFileDialog.AcceptMode
    DialogCode = QFileDialog.DialogCode

    def __init__(self) -> None:
        super().__init__()
        self.constructor_args: tuple = ()
        self.accept_mode = None
        self.name_filters: list = []
        self.initial_filter = None
        self.default_suffix = ""
        self.result = self.DialogCode.Rejected
        self.files: list = []
        self.final_filter = ""
        # Changement de filtre simulé PENDANT exec() (comme un clic de
        # l'utilisateur : le vrai dialogue n'émet pas filterSelected pour la
        # sélection programmatique initiale).
        self.pending_filter: str | None = None

    # --- API utilisée par ask_export_path ---------------------------------
    def setAcceptMode(self, mode) -> None:
        self.accept_mode = mode

    def setNameFilters(self, filters) -> None:
        self.name_filters = list(filters)

    def selectNameFilter(self, label) -> None:
        self.initial_filter = label
        self.filterSelected.emit(label)  # comme le vrai dialogue

    def setDefaultSuffix(self, suffix) -> None:
        self.default_suffix = suffix

    def exec(self):  # noqa: A002 - nom de l'API Qt
        if self.pending_filter is not None:
            self.selectNameFilter(self.pending_filter)
            self.final_filter = self.pending_filter
        return self.result

    def selectedFiles(self) -> list:
        return self.files

    def selectedNameFilter(self) -> str:
        return self.final_filter  # choix final de l'utilisateur


@pytest.fixture()
def fake_dialog(monkeypatch):
    """Installe un dialogue simulé : (result, files, selected_filter)."""

    def _install(result, files, selected_filter, filter_change=None):
        dialog = _FakeSaveDialog()
        dialog.result = result
        dialog.files = list(files)
        dialog.final_filter = selected_filter
        dialog.pending_filter = filter_change

        class _FakeQFileDialog:
            AcceptMode = QFileDialog.AcceptMode
            DialogCode = QFileDialog.DialogCode

            def __new__(cls, *_args, **_kwargs):
                dialog.constructor_args = _args
                return dialog

        monkeypatch.setattr(export_dialog, "QFileDialog", _FakeQFileDialog)
        return dialog

    return _install


def test_dialog_configuration(tmp_path, fake_dialog):
    dialog = fake_dialog(QFileDialog.DialogCode.Rejected, [], "Word (*.docx)")

    ask_export_path(None, default_dir=tmp_path, default_stem="rapport")

    assert dialog.accept_mode == QFileDialog.AcceptMode.AcceptSave
    assert dialog.constructor_args == (
        None,
        "Exporter le document",
        str(tmp_path / "rapport.docx"),
    )
    assert dialog.name_filters == [fmt.label for fmt in EXPORT_FORMATS]
    assert dialog.initial_filter == EXPORT_FORMATS[0].label


def test_filter_change_updates_default_suffix(tmp_path, fake_dialog):
    """Changement de filtre pendant exec() → le suffixe par défaut suit le
    filtre (liaison filterSelected → setDefaultSuffix)."""
    dialog = fake_dialog(
        QFileDialog.DialogCode.Accepted,
        [str(tmp_path / "sortie")],  # nom nu
        "Excel (*.xlsx)",
        filter_change="Excel (*.xlsx)",
    )

    fmt, output = ask_export_path(None, default_dir=tmp_path, default_stem="sortie")

    assert dialog.default_suffix == ".xlsx"
    assert fmt.key == "xlsx"
    assert output.name == "sortie.xlsx"


def test_ask_export_path_keeps_typed_known_suffix(tmp_path, fake_dialog):
    """« rapport v1.2.docx » : le suffixe saisi gagne, le nom n'est ni
    amputé (« rapport v1.2 ») ni doublé (« .docx.docx »)."""
    target = tmp_path / "rapport v1.2.docx"
    fake_dialog(
        QFileDialog.DialogCode.Accepted, [str(target)], "Word (*.docx)"
    )

    choice = ask_export_path(None, default_dir=tmp_path, default_stem="rapport")

    assert choice is not None
    fmt, output = choice
    assert fmt.key == "docx"
    assert output == target
    assert output.name == "rapport v1.2.docx"
    assert output.stem == "rapport v1.2"
    assert not output.name.endswith(".docx.docx")


def test_ask_export_path_typed_suffix_beats_filter(tmp_path, fake_dialog):
    """« choix.pdf » saisi avec le filtre Word : le suffixe a le dernier mot."""
    target = tmp_path / "choix.pdf"
    fake_dialog(
        QFileDialog.DialogCode.Accepted, [str(target)], "Word (*.docx)"
    )

    fmt, output = ask_export_path(None, default_dir=tmp_path, default_stem="choix")

    assert fmt.key == "pdf"
    assert output.name == "choix.pdf"


def test_ask_export_path_adds_suffix_from_selected_filter(
    tmp_path, fake_dialog
):
    """Nom nu + filtre Markdown → « export éàç Été.md » (R14 : accents ok)."""
    target = tmp_path / "export éàç Été"
    fake_dialog(
        QFileDialog.DialogCode.Accepted, [str(target)], "Markdown (*.md)"
    )

    fmt, output = ask_export_path(
        None, default_dir=tmp_path, default_stem="export éàç Été"
    )

    assert fmt.key == "md"
    assert output.name == "export éàç Été.md"


def test_ask_export_path_appends_suffix_for_unknown_extension(
    tmp_path, fake_dialog
):
    """Extension inconnue « scan.old » : le suffixe est ajouté EN PLUS
    (``with_suffix`` écraserait le « .2 » de « rapport v1.2 »)."""
    target = tmp_path / "scan.old"
    fake_dialog(
        QFileDialog.DialogCode.Accepted, [str(target)], "Word (*.docx)"
    )

    fmt, output = ask_export_path(None, default_dir=tmp_path, default_stem="scan")

    assert fmt.key == "docx"
    assert output.name == "scan.old.docx"


def test_ask_export_path_falls_back_to_first_format(tmp_path, fake_dialog):
    """Nom nu + filtre inconnu → premier format (DOCX) par défaut."""
    target = tmp_path / "sanssuffixe"
    fake_dialog(QFileDialog.DialogCode.Accepted, [str(target)], "")

    fmt, output = ask_export_path(
        None, default_dir=tmp_path, default_stem="sanssuffixe"
    )

    assert fmt is EXPORT_FORMATS[0]
    assert fmt.key == "docx"
    assert output.name == "sanssuffixe.docx"


def test_ask_export_path_cancel_returns_none(tmp_path, fake_dialog):
    fake_dialog(QFileDialog.DialogCode.Rejected, [], "")

    assert (
        ask_export_path(None, default_dir=tmp_path, default_stem="rapport")
        is None
    )


def test_ask_export_path_empty_selection_returns_none(tmp_path, fake_dialog):
    fake_dialog(QFileDialog.DialogCode.Accepted, [], "Word (*.docx)")

    assert (
        ask_export_path(None, default_dir=tmp_path, default_stem="rapport")
        is None
    )


def test_ask_export_path_blank_stem_falls_back_to_default(
    tmp_path, fake_dialog
):
    """Stem vide ou blanc → repli « resultat_ocr » dans le répertoire transmis."""
    dialog = fake_dialog(
        QFileDialog.DialogCode.Accepted,
        [str(tmp_path / "resultat_ocr.docx")],
        "Word (*.docx)",
    )

    fmt, output = ask_export_path(None, default_dir=tmp_path, default_stem="   ")

    assert fmt.key == "docx"
    assert output.name == "resultat_ocr.docx"
    assert dialog.constructor_args[2] == str(tmp_path / "resultat_ocr.docx")
