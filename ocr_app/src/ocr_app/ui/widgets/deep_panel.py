"""Panneau « Approfondi » : presets, filtres, binarisation, relance.

Repliable sous l'en-tête de l'aperçu, visible uniquement en mode Approfondi.
Reprenu le vocabulaire visuel de la maquette validée : presets en boutons
``SubModeButton``, quatre curseurs (luminosité, contraste, suppression
d'ombres/taches, débruitage), interrupteur de binarisation adaptative,
bascule Original/Filtré et bouton primaire « Appliquer et relancer l'OCR
de la page ». Aucun traitement d'image ici : le panneau ne produit que des
dicts de paramètres (``core/preprocessing.py``) et des signaux.

Signaux :

- ``filters_changed(dict)``  : paramètres normalisés, après un debounce de
  250 ms (chaque mouvement de curseur ne déclenche pas de recalcul) ;
- ``view_mode_changed(bool)`` : bascule aperçu (False = original, True =
  filtré — l'image filtrée est produite hors du thread GUI par la fenêtre) ;
- ``apply_requested()``      : bouton primaire (relance OCR de la page).
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ocr_app.core.preprocessing import (
    DEFAULT_FILTER_PARAMS,
    PreprocessingError,
    list_presets,
    normalize_params,
    params_differ_from_defaults,
    preset_params,
)

#: Débounce des changements de filtres (mission : 250 ms).
FILTERS_DEBOUNCE_MS = 250

#: Valeurs du débruitage (taille du filtre médian), index du curseur.
_DENOISE_VALUES: Tuple[int, int, int] = (0, 3, 5)
_DENOISE_LABELS: Dict[int, str] = {0: "Désactivé", 3: "Médian 3", 5: "Médian 5"}


class DeepPanel(QWidget):
    """Filtres interactifs + zones d'exclusion du mode Approfondi."""

    filters_changed = Signal(dict)
    view_mode_changed = Signal(bool)
    apply_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DeepPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(10)

        # --- Ligne d'en-tête : repli + titre + bascule Original/Filtré ----
        head = QWidget(self)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(8)

        self._collapse = QToolButton(head)
        self._collapse.setArrowType(Qt.ArrowType.DownArrow)
        self._collapse.setAutoRaise(True)
        self._collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse.setToolTip("Replier/déplier les réglages approfondis")
        self._collapse.setAccessibleName("Replier les réglages approfondis")
        self._collapse.setCheckable(True)
        self._collapse.toggled.connect(self._on_collapsed)
        head_layout.addWidget(self._collapse)

        title = QLabel("Analyse approfondie", head)
        title.setObjectName("DeepTitle")
        head_layout.addWidget(title)
        head_layout.addStretch(1)

        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._original_button = QPushButton("Original", head)
        self._filtered_button = QPushButton("Filtré", head)
        for index, button in enumerate(
            (self._original_button, self._filtered_button)
        ):
            button.setObjectName("SubModeButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._view_group.addButton(button, index)
            head_layout.addWidget(button)
        self._original_button.setChecked(True)
        self._original_button.setToolTip("Aperçu de la page originale")
        self._filtered_button.setToolTip("Aperçu de la page avec les filtres")
        self._view_group.idClicked.connect(self._on_view_toggled)
        root.addWidget(head)

        # --- Corps repliable ----------------------------------------------
        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        root.addWidget(self._body)

        # Presets.
        presets_row = QWidget(self._body)
        presets_layout = QHBoxLayout(presets_row)
        presets_layout.setContentsMargins(0, 0, 0, 0)
        presets_layout.setSpacing(6)
        presets_label = QLabel("Presets", presets_row)
        presets_label.setObjectName("DeepSection")
        presets_layout.addWidget(presets_label)
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        self._preset_buttons: Dict[str, QPushButton] = {}
        for index, (name, label) in enumerate(list_presets()):
            button = QPushButton(label, presets_row)
            button.setObjectName("SubModeButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(
                f"Preset « {label} » — règle les curseurs pour ce défaut"
            )
            self._preset_group.addButton(button, index)
            self._preset_buttons[name] = button
            presets_layout.addWidget(button)
        self._preset_group.idClicked.connect(self._on_preset_clicked)
        body.addWidget(presets_row)

        # Quatre curseurs (grille 4 colonnes, comme la maquette).
        sliders_row = QWidget(self._body)
        sliders_grid = QGridLayout(sliders_row)
        sliders_grid.setContentsMargins(0, 0, 0, 0)
        sliders_grid.setHorizontalSpacing(12)
        sliders_grid.setVerticalSpacing(2)
        self._sliders: Dict[str, QSlider] = {}
        self._value_labels: Dict[str, QLabel] = {}
        specs = (
            ("brightness", "Luminosité", 50, 200, 1),
            ("contrast", "Contraste", 50, 200, 2),
            ("shadow_removal", "Suppr. ombres", 0, 100, 3),
            ("denoise", "Débruitage", 0, 2, 4),
        )
        for key, label_text, low, high, column in specs:
            caption = QLabel(label_text, sliders_row)
            caption.setObjectName("DeepValue")
            value = QLabel("", sliders_row)
            value.setObjectName("DeepValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            head_row = QHBoxLayout()
            head_row.setContentsMargins(0, 0, 0, 0)
            head_row.addWidget(caption)
            head_row.addStretch(1)
            head_row.addWidget(value)
            slider = QSlider(Qt.Orientation.Horizontal, sliders_row)
            slider.setRange(low, high)
            slider.setPageStep(1)
            slider.setCursor(Qt.CursorShape.PointingHandCursor)
            slider.valueChanged.connect(self._on_filter_touched)
            self._sliders[key] = slider
            self._value_labels[key] = value
            sliders_grid.addLayout(head_row, 0, column - 1)
            sliders_grid.addWidget(slider, 1, column - 1)
        body.addWidget(sliders_row)

        # Ligne basse : binarisation + hint zones + bouton primaire.
        bottom = QWidget(self._body)
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)
        self._binarize = QCheckBox("Binarisation adaptative", bottom)
        self._binarize.setCursor(Qt.CursorShape.PointingHandCursor)
        self._binarize.setToolTip(
            "Seuil local adaptatif (style Sauvola, fenêtre ~25 px) — "
            "texte noir sur papier blanc"
        )
        self._binarize.toggled.connect(self._on_filter_touched)
        bottom_layout.addWidget(self._binarize)

        self._hint = QLabel(bottom)
        self._hint.setObjectName("DeepHint")
        self._hint.setVisible(False)
        bottom_layout.addWidget(self._hint, 0, Qt.AlignmentFlag.AlignVCenter)
        bottom_layout.addStretch(1)

        apply_button = QPushButton(
            "Appliquer et relancer l'OCR de la page", bottom
        )
        apply_button.setObjectName("StatusPrimary")
        apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_button.setToolTip(
            "Relance l'OCR de cette page (backend Avancé) avec les filtres "
            "et les zones d'exclusion actuels"
        )
        apply_button.setAccessibleName("Appliquer et relancer l'OCR de la page")
        apply_button.clicked.connect(self.apply_requested)
        bottom_layout.addWidget(apply_button)
        body.addWidget(bottom)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(FILTERS_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_filters_changed)

        self.set_state(None)
        self.set_zone_count(0)

    # --- État --------------------------------------------------------------
    def current_params(self) -> dict:
        """Paramètres actuels, normalisés (dict JSON-sérialisable)."""
        return normalize_params(
            {
                "brightness": self._sliders["brightness"].value(),
                "contrast": self._sliders["contrast"].value(),
                "shadow_removal": self._sliders["shadow_removal"].value(),
                "denoise": _DENOISE_VALUES[self._sliders["denoise"].value()],
                "binarize": self._binarize.isChecked(),
            }
        )

    def get_filter_params(self) -> Optional[dict]:
        """Filtres actifs (``None`` si tout est au défaut = image inchangée)."""
        params = self.current_params()
        return params if params_differ_from_defaults(params) else None

    def set_state(self, filters: Optional[dict]) -> None:
        """Applique un état persisté (sans émettre ``filters_changed``)."""
        params = normalize_params(filters) if filters else dict(
            DEFAULT_FILTER_PARAMS
        )
        self._mute(True)
        try:
            self._sliders["brightness"].setValue(int(params["brightness"]))
            self._sliders["contrast"].setValue(int(params["contrast"]))
            self._sliders["shadow_removal"].setValue(
                int(params["shadow_removal"])
            )
            self._sliders["denoise"].setValue(
                _DENOISE_VALUES.index(int(params["denoise"]))
            )
            self._binarize.setChecked(bool(params["binarize"]))
            self._refresh_value_labels()
            self._sync_preset_buttons(params)
        finally:
            self._mute(False)

    def set_zone_count(self, count: int) -> None:
        """Hint ambre du nombre de zones d'exclusion (masqué si 0)."""
        count = max(0, int(count))
        if count == 0:
            self._hint.setVisible(False)
            self._hint.setText("")
            return
        noun = "zone" if count == 1 else "zones"
        self._hint.setText(
            f"{count} {noun} d'exclusion — l'OCR ignorera ces surfaces"
        )
        self._hint.setVisible(True)

    # --- Bascule de vue ------------------------------------------------------
    def is_filtered_view(self) -> bool:
        return self._filtered_button.isChecked()

    def set_view_filtered(self, filtered: bool) -> None:
        """Positionne la bascule (sans réémettre ``view_mode_changed``)."""
        target = self._filtered_button if filtered else self._original_button
        if not target.isChecked():
            self._mute(True)
            try:
                target.setChecked(True)
            finally:
                self._mute(False)

    # --- Repli ---------------------------------------------------------------
    def set_expanded(self, expanded: bool) -> None:
        """Déplie (``True``) ou replie (``False``) le corps du panneau."""
        self._collapse.setChecked(not bool(expanded))

    def is_expanded(self) -> bool:
        return not self._collapse.isChecked()

    def _on_collapsed(self, collapsed: bool) -> None:
        self._apply_expanded(not collapsed)

    def _apply_expanded(self, expanded: bool) -> None:
        self._body.setVisible(bool(expanded))
        self._collapse.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    # --- Internes --------------------------------------------------------------
    def _mute(self, muted: bool) -> None:
        """Coupe les signaux des contrôles (mise à jour programmatique)."""
        widgets = list(self._sliders.values()) + [self._binarize]
        for widget in widgets:
            widget.blockSignals(muted)

    def _on_filter_touched(self, _value=None) -> None:
        self._refresh_value_labels()
        self._sync_preset_buttons(self.current_params())
        self._debounce.start()

    def _on_preset_clicked(self, button_id: int) -> None:
        for name, index in self._preset_order().items():
            if index == button_id:
                try:
                    params = preset_params(name)
                except PreprocessingError:  # pragma: no cover - défensif
                    return
                self._mute(True)
                try:
                    self._sliders["brightness"].setValue(int(params["brightness"]))
                    self._sliders["contrast"].setValue(int(params["contrast"]))
                    self._sliders["shadow_removal"].setValue(
                        int(params["shadow_removal"])
                    )
                    self._sliders["denoise"].setValue(
                        _DENOISE_VALUES.index(int(params["denoise"]))
                    )
                    self._binarize.setChecked(bool(params["binarize"]))
                    self._refresh_value_labels()
                finally:
                    self._mute(False)
                self._debounce.start()
                return

    def _preset_order(self) -> Dict[str, int]:
        return {
            name: index
            for index, (name, _label) in enumerate(list_presets())
        }

    def _sync_preset_buttons(self, params: dict) -> None:
        """Coche le preset correspondant exactement, sinon décoche tout."""
        self._mute(True)
        try:
            matched = False
            for name, index in self._preset_order().items():
                try:
                    preset = preset_params(name)
                except PreprocessingError:  # pragma: no cover - défensif
                    continue
                # Comparaison sur les clés du preset (binarize_window n'est
                # pas exposée par l'interface et ne doit pas fausser le match).
                matches = all(
                    params.get(key) == value for key, value in preset.items()
                )
                self._preset_buttons[name].setChecked(matches)
                matched = matched or matches
            if not matched:
                for button in self._preset_buttons.values():
                    button.setChecked(False)
        finally:
            self._mute(False)

    def _refresh_value_labels(self) -> None:
        self._value_labels["brightness"].setText(
            f"{self._sliders['brightness'].value()} %"
        )
        self._value_labels["contrast"].setText(
            f"{self._sliders['contrast'].value()} %"
        )
        self._value_labels["shadow_removal"].setText(
            f"{self._sliders['shadow_removal'].value()} %"
        )
        denoise = _DENOISE_VALUES[self._sliders["denoise"].value()]
        self._value_labels["denoise"].setText(_DENOISE_LABELS[denoise])

    def _on_view_toggled(self, button_id: int) -> None:
        self.view_mode_changed.emit(button_id == 1)

    def _emit_filters_changed(self) -> None:
        self.filters_changed.emit(self.current_params())
