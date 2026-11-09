"""Barre inférieure : progression page X/Y + Pause / Annuler / Réinitialiser /
Copier / Exporter, statistiques d'édition et temps de traitement."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ocr_app.core.task_queue import Task, TaskStatus
from ocr_app.ui import theme
from ocr_app.ui.icons import make_icon
from ocr_app.ui.task_format import stage_label, task_percent


def format_duration(seconds: Optional[float]) -> Optional[str]:
    """Durée lisible : « 7 s », « 1 min 24 s », « 1 h 03 min » (sinon None)."""
    if seconds is None or seconds < 0:
        return None
    total = int(round(seconds))
    if total < 60:
        return f"{total} s"
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


class BottomBar(QWidget):
    """Barre d'état inférieure de l'application."""

    pause_clicked = Signal()
    cancel_clicked = Signal()
    reset_clicked = Signal()
    copy_clicked = Signal()
    export_clicked = Signal()
    process_all_clicked = Signal()  # phase 8 : « Tout traiter »
    cancel_all_clicked = Signal()  # phase 8 : « Tout annuler » (lot entier)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BottomBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(12)

        progress_box = QWidget(self)
        progress_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        progress_box.setMinimumWidth(180)
        progress_layout = QVBoxLayout(progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(5)

        label_row = QHBoxLayout()
        label_row.setContentsMargins(0, 0, 0, 0)
        label_row.setSpacing(8)
        self._progress_text = QLabel(progress_box)
        self._progress_text.setObjectName("ProgressText")
        self._progress_text.setTextFormat(Qt.TextFormat.RichText)
        self._progress_text.setText("Aucun document")
        label_row.addWidget(self._progress_text)
        label_row.addStretch(1)
        # Phase 8 : compteur global du lot (« Lot : 2/5 terminés »).
        self._batch_label = QLabel("", progress_box)
        self._batch_label.setObjectName("ProgressText")
        self._batch_label.setTextFormat(Qt.TextFormat.RichText)
        self._batch_label.hide()
        label_row.addWidget(self._batch_label)
        self._edit_stats = QLabel("", progress_box)
        self._edit_stats.setObjectName("ProgressText")
        self._edit_stats.setTextFormat(Qt.TextFormat.RichText)
        self._edit_stats.hide()
        label_row.addWidget(self._edit_stats)
        self._percent = QLabel("—", progress_box)
        self._percent.setObjectName("ProgressPercent")
        label_row.addWidget(self._percent)
        progress_layout.addLayout(label_row)

        self._bar = QProgressBar(progress_box)
        self._bar.setObjectName("Progress")
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        progress_layout.addWidget(self._bar)
        layout.addWidget(progress_box, 1)

        self._toast = QLabel("", self)
        self._toast.setObjectName("Toast")
        self._toast.setMaximumWidth(320)
        self._toast.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._toast.hide()
        layout.addWidget(self._toast, 0)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)

        self._pause = self._make_icon_button(
            "pause", "Mettre en pause", progress_box
        )
        self._pause.setAccessibleName("Pause")
        self._cancel = self._make_icon_button(
            "close", "Annuler le traitement", progress_box
        )
        self._cancel.setAccessibleName("Annuler")
        self._reset = self._make_text_button(
            "reset", "Réinitialiser", "StatusButton", progress_box
        )
        self._reset.setAccessibleName("Réinitialiser")
        self._reset.setToolTip(
            "Recharger le texte d'origine (annule les éditions avant copie)"
        )
        self._copy = self._make_text_button(
            "copy", "Copier", "StatusButton", progress_box
        )
        self._copy.setToolTip(
            "Copier la page affichée ou tout le document "
            "(texte, HTML et Markdown)"
        )
        self._export = self._make_text_button(
            "download", "Exporter", "StatusPrimary", progress_box, white_icon=True
        )
        # Phase 8 : relance des tâches restées en attente (ex. après une
        # annulation ou un worker occupé), visible seulement si nécessaire.
        self._process_all = self._make_text_button(
            "play", "Tout traiter", "StatusButton", progress_box
        )
        self._process_all.setToolTip(
            "Lancer (ou relancer) toutes les tâches en attente de la file"
        )
        self._process_all.setAccessibleName("Tout traiter")
        # Phase 8 : contrepartie explicite de « Tout traiter » — annuler la
        # tâche en cours ET vider la file (annuler une seule tâche, bouton
        # « Annuler », laisse le lot continuer).
        self._cancel_all = self._make_text_button(
            "close", "Tout annuler", "StatusButton", progress_box
        )
        self._cancel_all.setToolTip(
            "Annuler la tâche en cours et toutes les tâches en attente"
        )
        self._cancel_all.setAccessibleName("Tout annuler")

        self._pause.clicked.connect(self.pause_clicked)
        self._cancel.clicked.connect(self.cancel_clicked)
        self._reset.clicked.connect(self.reset_clicked)
        self._copy.clicked.connect(self.copy_clicked)
        self._export.clicked.connect(self.export_clicked)
        self._process_all.clicked.connect(self.process_all_clicked)
        self._cancel_all.clicked.connect(self.cancel_all_clicked)

        layout.addWidget(self._pause)
        layout.addWidget(self._cancel)
        layout.addWidget(self._reset)
        layout.addWidget(self._copy)
        layout.addWidget(self._export)
        layout.addWidget(self._process_all)
        layout.addWidget(self._cancel_all)

        self.set_task(None)
        self.set_content_available(False)
        self.set_batch_progress(None)
        self.set_pending_count(0)

    @property
    def copy_button(self) -> QPushButton:
        return self._copy

    @property
    def export_button(self) -> QPushButton:
        return self._export

    # --- Construction -----------------------------------------------------
    def _make_icon_button(
        self, icon_name: str, tooltip: str, parent: QWidget
    ) -> QPushButton:
        button = QPushButton(parent)
        button.setObjectName("StatusButton")
        button.setProperty("iconOnly", "true")
        button.setIcon(make_icon(icon_name, theme.TEXT, 15))
        button.setIconSize(QSize(15, 15))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setFixedSize(34, 30)
        return button

    def _make_text_button(
        self,
        icon_name: str,
        text: str,
        object_name: str,
        parent: QWidget,
        white_icon: bool = False,
    ) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName(object_name)
        color = "#ffffff" if white_icon else theme.TEXT
        button.setIcon(make_icon(icon_name, color, 15))
        button.setIconSize(QSize(15, 15))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    # --- Mise à jour ------------------------------------------------------
    def set_task(
        self, task: Optional[Task], *, title_override: Optional[str] = None
    ) -> None:
        if task is None:
            self._progress_text.setText("Aucun document")
            self._percent.setText("—")
            self._bar.setValue(0)
            self._pause.setEnabled(False)
            self._cancel.setEnabled(False)
            self._pause.setIcon(make_icon("pause", "#a4aab4", 15))
            self._cancel.setIcon(make_icon("close", "#a4aab4", 15))
            self._pause.setToolTip("Mettre en pause")
            return

        percent = task_percent(task)
        self._bar.setValue(percent)
        self._percent.setText(f"{percent} %")

        if title_override:
            # Relance de page (phase 8) : « Relance page 3 » au lieu de
            # « Page 3 / 1 » qui n'aurait pas de sens pour une tâche ciblée.
            page_part = (
                f'<span style="color:#1a1d24; font-weight:600;">'
                f"{title_override}</span>"
            )
        elif task.current_page > 0 and task.total_pages > 0:
            page_part = (
                f'<span style="color:#1a1d24; font-weight:600;">'
                f"Page {task.current_page} / {task.total_pages}</span>"
            )
        elif task.total_pages > 0:
            page_part = (
                f'<span style="color:#1a1d24; font-weight:600;">'
                f"{task.total_pages} pages</span>"
            )
        else:
            page_part = ""

        if task.status == TaskStatus.DONE:
            detail = "terminé"
        elif task.status == TaskStatus.CANCELLED:
            detail = "annulé"
        elif task.status == TaskStatus.ERROR:
            detail = "erreur"
        elif task.status == TaskStatus.PENDING:
            detail = "en attente"
        else:
            detail = f"étape : {stage_label(task.stage)}"

        if page_part:
            self._progress_text.setText(
                f'{page_part}<span style="color:#68707d;"> — {detail}</span>'
            )
        else:
            self._progress_text.setText(
                f'<span style="color:#68707d;">{detail.capitalize()}</span>'
            )

        active = task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED)
        self._pause.setEnabled(active)
        self._cancel.setEnabled(active)
        icon_color = theme.TEXT if active else "#a4aab4"
        self._cancel.setIcon(make_icon("close", icon_color, 15))
        if task.status == TaskStatus.PAUSED:
            self._pause.setIcon(make_icon("play", icon_color, 15))
            self._pause.setToolTip("Reprendre le traitement")
        else:
            self._pause.setIcon(make_icon("pause", icon_color, 15))
            self._pause.setToolTip("Mettre en pause")

    def set_content_available(self, available: bool) -> None:
        self._copy.setEnabled(available)
        self._export.setEnabled(available)
        self._reset.setEnabled(available)
        copy_color = theme.TEXT if available else "#a4aab4"
        export_color = "#ffffff" if available else "#a4aab4"
        reset_color = theme.TEXT if available else "#a4aab4"
        self._copy.setIcon(make_icon("copy", copy_color, 15))
        self._export.setIcon(make_icon("download", export_color, 15))
        self._reset.setIcon(make_icon("reset", reset_color, 15))

    # --- Lot et file d'attente (phase 8) ----------------------------------
    def set_batch_progress(self, progress: Optional[tuple]) -> None:
        """Compteur global du lot : « Lot : 2/5 terminés » (sinon masqué).

        ``progress`` est un couple ``(terminées, total)`` ou ``None``.
        """
        if progress is None:
            self._batch_label.setText("")
            self._batch_label.hide()
            return
        done, total = progress
        done = max(0, int(done))
        total = max(0, int(total))
        if total < 2:
            self._batch_label.setText("")
            self._batch_label.hide()
            return
        self._batch_label.setText(
            f'<span style="color:#68707d;">Lot : <b>{done}/{total}</b> '
            "terminés</span>"
        )
        self._batch_label.show()

    def set_pending_count(self, count: int) -> None:
        """Boutons « Tout traiter » / « Tout annuler » : visibles seulement si
        des tâches attendent."""
        pending = int(count) > 0
        self._process_all.setEnabled(pending)
        self._process_all.setVisible(pending)
        self._cancel_all.setEnabled(pending)
        self._cancel_all.setVisible(pending)

    def set_edit_stats(
        self,
        modified: int = 0,
        total: int = 0,
        elapsed_seconds: Optional[float] = None,
    ) -> None:
        """Statistiques d'édition : « 2 / 3 blocs modifiés · 12 s »."""
        parts = []
        if total > 0:
            parts.append(f"<b>{modified} / {total}</b> blocs modifiés")
        duration = format_duration(elapsed_seconds)
        if duration is not None:
            parts.append(duration)
        if parts:
            self._edit_stats.setText(" · ".join(parts))
            self._edit_stats.show()
        else:
            self._edit_stats.setText("")
            self._edit_stats.hide()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause.setIcon(make_icon("play", theme.TEXT, 15))
            self._pause.setToolTip("Reprendre le traitement")
        else:
            self._pause.setIcon(make_icon("pause", theme.TEXT, 15))
            self._pause.setToolTip("Mettre en pause")

    # --- Message transitoire ---------------------------------------------
    def show_toast(
        self, message: str, error: bool = False, timeout: int = 3500
    ) -> None:
        metrics = QFontMetrics(self._toast.font())
        elided = metrics.elidedText(message, Qt.TextElideMode.ElideRight, 300)
        self._toast.setText(elided)
        self._toast.setToolTip(message)
        self._toast.setProperty("error", "true" if error else "false")
        theme.repolish(self._toast)
        self._toast.show()
        self._toast_timer.start(max(800, int(timeout)))

    def clear_toast(self) -> None:
        self._toast_timer.stop()
        self._toast.hide()
        self._toast.setText("")
