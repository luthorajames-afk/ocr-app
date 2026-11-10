"""Mise en forme des états de tâche pour l'interface."""

from __future__ import annotations

from typing import Tuple

from ocr_app.core.task_queue import Task, TaskStatus

# Même ordre que le pipeline et le worker factice.
PIPELINE_STAGES: Tuple[str, ...] = (
    "render",
    "preprocess",
    "layout",
    "ocr",
    "tables",
    "reconstruct",
    "save",
)

STAGE_LABELS = {
    "render": "rendu",
    "preprocess": "prétraitement",
    "layout": "mise en page",
    "ocr": "ocr",
    "tables": "tableaux",
    "reconstruct": "reconstruction",
    "save": "sauvegarde",
    "done": "terminé",
}

MODE_LABELS = {
    "simple": "Simple",
    "medium": "Moyen",
    "advanced": "Avancé",
    "approfondi": "Approfondi",
}


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode.capitalize())


def stage_label(stage: str) -> str:
    if not stage:
        return "—"
    return STAGE_LABELS.get(stage, stage)


def format_pages(count: int) -> str:
    return "1 page" if int(count) == 1 else f"{int(count)} pages"


def status_badge(task: Task) -> Tuple[str, str]:
    """Retourne (type de badge QSS, libellé) pour une tâche."""
    status = task.status
    if status == TaskStatus.PENDING:
        return "wait", "En attente"
    if status == TaskStatus.RUNNING:
        if task.current_page > 0 and task.total_pages > 0:
            return "run", f"En cours {task.current_page}/{task.total_pages}"
        return "run", "En cours"
    if status == TaskStatus.PAUSED:
        return "wait", "En pause"
    if status == TaskStatus.DONE:
        return "done", "Terminé"
    if status == TaskStatus.CANCELLED:
        return "cancelled", "Annulé"
    if status == TaskStatus.ERROR:
        return "error", "Erreur"
    return "wait", status.value.capitalize()


def task_percent(task: Task) -> int:
    """Pourcentage global, étapes intermédiaires incluses (0–100)."""
    total = max(0, int(task.total_pages))
    if total == 0:
        return 0
    if task.status == TaskStatus.DONE:
        return 100

    done = min(max(0, int(task.pages_done)), total)
    ratio = done / total

    if task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED):
        if task.current_page > done and task.stage in PIPELINE_STAGES:
            index = PIPELINE_STAGES.index(task.stage)
            stage_fraction = (index + 1) / len(PIPELINE_STAGES)
            ratio = min(1.0, (done + stage_fraction) / total)

    percent = int(round(ratio * 100))
    return max(0, min(99, percent)) if task.status != TaskStatus.DONE else 100
