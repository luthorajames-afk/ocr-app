"""Boîte de dialogue d'export (phase 6) : format + chemin en une étape (R14).

Le ``QFileDialog`` en mode « Enregistrer » combine les six formats
exportables (DOCX / XLSX / PDF / HTML / Markdown / TXT). Le format retenu est
déduit du suffixe saisi s'il est connu (l'utilisateur a le dernier mot), sinon
du filtre sélectionné ; le suffixe par défaut suit le filtre pour qu'un nom
nu (« export éàç ») reçoive la bonne extension. Les noms accentués passent
tels quels : Qt retourne du unicode et les écritures du cœur sont explicites
en UTF-8 (R14).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtWidgets import QFileDialog, QWidget


@dataclass(frozen=True)
class ExportFormat:
    """Un format exportable : clé, filtre, extension, méthode de ``Exporter``."""

    key: str
    label: str  # filtre affiché, ex. « Word (*.docx) »
    suffix: str  # « .docx » (en minuscules)
    method: str  # nom de la méthode correspondante sur ``Exporter``


EXPORT_FORMATS: Tuple[ExportFormat, ...] = (
    ExportFormat("docx", "Word (*.docx)", ".docx", "save_docx"),
    ExportFormat("xlsx", "Excel (*.xlsx)", ".xlsx", "save_xlsx"),
    ExportFormat("pdf", "PDF (*.pdf)", ".pdf", "save_pdf"),
    ExportFormat("html", "HTML (*.html)", ".html", "save_html"),
    ExportFormat("md", "Markdown (*.md)", ".md", "save_markdown"),
    ExportFormat("txt", "Texte brut (*.txt)", ".txt", "save_plain_text"),
)


def format_by_key(key: str) -> Optional[ExportFormat]:
    return next((fmt for fmt in EXPORT_FORMATS if fmt.key == key), None)


def format_for_suffix(suffix: str) -> Optional[ExportFormat]:
    """Format correspondant à une extension (« .docx » ou « docx »)."""
    candidate = f".{(suffix or '').lower().lstrip('.')}"
    return next(
        (fmt for fmt in EXPORT_FORMATS if fmt.suffix == candidate), None
    )


def ask_export_path(
    parent: Optional[QWidget],
    *,
    default_dir: Path,
    default_stem: str,
) -> Optional[Tuple[ExportFormat, Path]]:
    """Ouvre la boîte « Enregistrer » et retourne ``(format, chemin)``.

    ``None`` si l'utilisateur annule. Le suffixe est ajouté ou corrigé selon
    le filtre sélectionné quand le nom saisi n'en porte pas (ou porte une
    extension inconnue) ; un suffixe connu saisi explicitement est conservé.
    """
    stem = (default_stem or "resultat_ocr").strip() or "resultat_ocr"
    dialog = QFileDialog(
        parent, "Exporter le document", str(Path(default_dir) / f"{stem}.docx")
    )
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setNameFilters([fmt.label for fmt in EXPORT_FORMATS])
    dialog.selectNameFilter(EXPORT_FORMATS[0].label)

    def _follow_filter(filter_label: str) -> None:
        fmt = next(
            (item for item in EXPORT_FORMATS if item.label == filter_label),
            None,
        )
        if fmt is not None:
            dialog.setDefaultSuffix(fmt.suffix)

    dialog.filterSelected.connect(_follow_filter)

    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return None
    files = dialog.selectedFiles()
    if not files:
        return None
    output = Path(files[0])

    fmt = format_for_suffix(output.suffix)
    if fmt is None:
        selected = dialog.selectedNameFilter()
        fmt = next(
            (item for item in EXPORT_FORMATS if item.label == selected),
            EXPORT_FORMATS[0],
        )
        if output.suffix:
            # « rapport v1.2 » garde son nom : le suffixe du format est
            # ajouté, pas substitué (``with_suffix`` écraserait « .2 »).
            output = Path(str(output) + fmt.suffix)
        else:
            output = output.with_suffix(fmt.suffix)
    return fmt, output
