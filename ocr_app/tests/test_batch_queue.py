"""Tests du lot de tâches, de la relance de page et des réglages workers
(phase 8) — interface hors écran.

Le ``TaskRunner`` est exercé avec le worker factice de développement
(``fake_worker``, aucun OCR) : protocole, enchaînement et annulation restent
réels (QProcess, R2). Les tests vérifient :

- lot de 3 tâches : enchaînement séquentiel (jamais 2 tâches actives
  simultanément), compteur ``batch_progress`` (0,3) → (3,3), une seule
  incrémentation par tâche ;
- annulation d'une tâche du lot : le lot continue ; ``cancel_all`` : tout
  CANCELLED, un seul démarrage ;
- ``create_page_task`` : total = 1, page ciblée mémorisée,
  ``is_document_busy`` pendant la relance, titre distinct « Relance page N »
  et branding « — v1.0 » de la fenêtre principale ;
- ``set_worker_settings`` : ``--lang`` / ``--dpi`` ne sont JAMAIS transmis au
  worker factice, mais bien au worker réel (commande inspectée) ;
- « Ajouter un dossier » : collecte récursive optionnelle, tri alphabétique,
  fichiers non pris en charge comptés (jamais silencieux).

Tous les tests sont headless (``QT_QPA_PLATFORM=offscreen``), une seule
QApplication par module, et aucun worker ne survit au test (``shutdown``).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# Doit être positionné avant toute création de QApplication / import Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 n'est pas installé (extra [ui])")
pytest.importorskip("ocr_app.ui.task_runner", reason="ui.task_runner absent")

from ocr_app.core.task_queue import TaskStatus
from ocr_app.ui.task_runner import TaskRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Délai généreux pour l'enchaînement de 3 workers factices (3 × ~1,5 s).
BATCH_TIMEOUT = 90.0


@pytest.fixture(scope="module")
def qapp():
    """QApplication unique pour tout le module (politique Qt)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def work_env(tmp_path: Path, monkeypatch):
    """Isole les dossiers de travail et de réglages dans %TEMP% du test."""
    monkeypatch.setenv("AOCRI_WORK_DIR", str(tmp_path / "ocr_work éàç"))
    monkeypatch.setenv("AOCRI_SETTINGS_DIR", str(tmp_path / "réglages éàç"))
    return tmp_path


@pytest.fixture
def runner(qapp, work_env):
    """TaskRunner sur worker factice, arrêté proprement après le test."""
    instance = TaskRunner()
    try:
        yield instance
    finally:
        instance.shutdown()
        qapp.processEvents()


def _spin(qapp, predicate, timeout: float, description: str):
    """Boucle d'événements Qt jusqu'à ``predicate`` (jamais bloquante)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    qapp.processEvents()
    pytest.fail(f"{description} non vérifié en {timeout:.0f} s")


def _statuses(runner: TaskRunner):
    return {task.id: task.status for task in runner.queue.list()}


class BatchRecorder:
    """Enregistre batch_progress, démarrages et concurrence des tâches."""

    def __init__(self, runner: TaskRunner) -> None:
        self.progress: list[tuple[int, int]] = []
        self.started: list[str] = []
        self.max_concurrent = 0
        self._runner = runner
        runner.batch_progress.connect(self._on_progress)
        runner.task_started.connect(self._on_started)
        runner.task_changed.connect(self._on_task_changed)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.append((int(done), int(total)))

    def _on_started(self, task_id: str) -> None:
        self.started.append(task_id)

    def _on_task_changed(self, _task) -> None:
        running = sum(
            1
            for task in self._runner.queue.list()
            if task.status == TaskStatus.RUNNING
        )
        self.max_concurrent = max(self.max_concurrent, running)


# --- Lot de 3 tâches ----------------------------------------------------------

def test_batch_of_three_runs_sequentially_with_progress(runner, qapp) -> None:
    tasks = [
        runner.create_task(f"document_{index} éàç.pdf", "simple", 1)
        for index in range(1, 4)
    ]
    ids = [task.id for task in tasks]
    recorder = BatchRecorder(runner)

    runner.begin_batch(ids)
    assert recorder.progress == [(0, 3)], "le compteur démarre à (0, N)"

    for task_id in ids:
        runner.enqueue(task_id)

    _spin(
        qapp,
        lambda: all(
            status == TaskStatus.DONE for status in _statuses(runner).values()
        ),
        BATCH_TIMEOUT,
        "les 3 tâches du lot DONE",
    )

    # Enchaînement séquentiel : 3 démarrages, un par tâche, jamais 2 actives.
    assert sorted(recorder.started) == sorted(ids)
    assert len(set(recorder.started)) == 3
    assert recorder.max_concurrent <= 1, (
        "jamais 2 tâches actives simultanément (file séquentielle)"
    )

    # Compteur : une seule incrémentation par tâche, de (0,3) à (3,3).
    assert recorder.progress[0] == (0, 3)
    assert recorder.progress[-1] == (3, 3)
    dones = [done for done, _total in recorder.progress]
    assert dones == sorted(dones), "le compteur ne retombe jamais"
    assert dones == [0, 1, 2, 3], f"une incrémentation par tâche : {dones}"

    # Toutes les tâches sont DONE avec 1/1 page.
    for task in runner.queue.list():
        assert task.status == TaskStatus.DONE
        assert task.pages_done == 1


def test_batch_counter_ignores_outsider_tasks(runner, qapp) -> None:
    """Une tâche hors lot (relance) n'affecte pas le compteur du lot."""
    batch_tasks = [
        runner.create_task(f"lot_{index} éàç.pdf", "simple", 1) for index in (1, 2)
    ]
    batch_ids = [task.id for task in batch_tasks]
    outsider = runner.create_task("hors lot éàç.pdf", "simple", 1)
    recorder = BatchRecorder(runner)

    runner.begin_batch(batch_ids)
    for task_id in batch_ids + [outsider.id]:
        runner.enqueue(task_id)

    _spin(
        qapp,
        lambda: all(
            status in (TaskStatus.DONE, TaskStatus.ERROR, TaskStatus.CANCELLED)
            for status in _statuses(runner).values()
        ),
        BATCH_TIMEOUT,
        "toutes les tâches à un état terminal",
    )
    assert recorder.progress[-1] == (2, 2), "le lot compte ses 2 tâches, pas +"
    for task in runner.queue.list():
        assert task.status == TaskStatus.DONE


# --- Annulations ---------------------------------------------------------------

def test_cancelling_one_task_lets_batch_continue(runner, qapp) -> None:
    """« Annuler » sur la tâche en cours : le lot continue (les suivantes démarrent)."""
    tasks = [
        runner.create_task(f"annule_{index} éàç.pdf", "simple", 1)
        for index in range(1, 4)
    ]
    ids = [task.id for task in tasks]
    recorder = BatchRecorder(runner)

    runner.begin_batch(ids)
    for task_id in ids:
        runner.enqueue(task_id)

    # Attente du démarrage effectif de la première tâche, puis annulation.
    _spin(qapp, lambda: runner.current_task_id is not None, 30.0, "1re tâche RUN")
    first_id = runner.current_task_id
    assert runner.cancel_current() is True

    _spin(
        qapp,
        lambda: all(
            status in (TaskStatus.DONE, TaskStatus.CANCELLED)
            for status in _statuses(runner).values()
        ),
        BATCH_TIMEOUT,
        "lot terminal après annulation d'une tâche",
    )

    statuses = _statuses(runner)
    assert statuses[first_id] == TaskStatus.CANCELLED
    others = [status for tid, status in statuses.items() if tid != first_id]
    assert all(status == TaskStatus.DONE for status in others), (
        "le lot continue après l'annulation d'une tâche"
    )
    assert recorder.progress[-1] == (3, 3), "les 3 tâches sont comptées"
    assert len(recorder.started) == 3, "les 2 suivantes ont bien démarré"


def test_cancel_all_stops_the_whole_batch(runner, qapp) -> None:
    """« Tout annuler » : file vidée, tout CANCELLED, aucun nouveau démarrage."""
    tasks = [
        runner.create_task(f"tout_{index} éàç.pdf", "simple", 1)
        for index in range(1, 4)
    ]
    ids = [task.id for task in tasks]
    recorder = BatchRecorder(runner)

    runner.begin_batch(ids)
    for task_id in ids:
        runner.enqueue(task_id)

    _spin(qapp, lambda: runner.current_task_id is not None, 30.0, "1re tâche RUN")
    cancelled = runner.cancel_all()
    assert cancelled == 3, "1 en cours + 2 en attente"

    _spin(
        qapp,
        lambda: all(
            status == TaskStatus.CANCELLED for status in _statuses(runner).values()
        ),
        30.0,
        "les 3 tâches CANCELLED",
    )
    # Le worker de la tâche courante doit avoir fini de mourir (aucun zombie).
    _spin(
        qapp,
        lambda: runner.current_task_id is None and not runner._worker.is_running,
        30.0,
        "worker arrêté après cancel_all",
    )

    assert recorder.started == [recorder.started[0]], (
        "un seul démarrage : la file a été vidée avant le 2e lancement"
    )
    assert recorder.progress[-1] == (3, 3), "les annulations sont comptées"
    assert runner.current_task_id is None


def test_cancel_all_semantics(runner) -> None:
    """« Tout annuler » : 0 si rien à annuler ; une tâche PENDING (jamais
    enfilée) est annulée elle aussi — contrat documenté de cancel_all."""
    assert runner.cancel_all() == 0

    task = runner.create_task("calme éàç.pdf", "simple", 1)
    assert runner.cancel_all() == 1
    assert _statuses(runner)[task.id] == TaskStatus.CANCELLED


# --- Relance d'une page --------------------------------------------------------

def test_create_page_task_targets_single_page(runner, qapp) -> None:
    source = "aperçu éàç.pdf"
    task = runner.create_page_task(source, "simple", 3)

    assert task.total_pages == 1, "une relance = UNE page"
    assert runner.relaunch_page(task.id) == 3
    assert runner.relaunch_page("id-inconnu") is None
    assert runner.is_document_busy(source) is False, "pas encore démarrée"

    runner.enqueue(task.id)
    _spin(qapp, lambda: runner.current_task_id is not None, 30.0, "relance RUN")
    assert runner.is_document_busy(source) is True, "busy pendant la relance"
    assert runner.is_document_busy("autre document.pdf") is False

    _spin(
        qapp,
        lambda: _statuses(runner)[task.id] == TaskStatus.DONE,
        BATCH_TIMEOUT,
        "relance DONE",
    )
    assert runner.is_document_busy(source) is False, "plus busy après la fin"
    updated = runner.task(task.id)
    assert updated.pages_done == 1, "pages_done borné au total de la relance"


def test_relaunch_title_and_v1_branding(qapp, work_env, monkeypatch) -> None:
    """Titre distinct « Relance page N » (barre du bas) et « — v1.0 » (fenêtre)."""
    from ocr_app.ui.main_window import MainWindow

    window = MainWindow()
    try:
        qapp.processEvents()
        assert window.windowTitle().endswith("— v1.0"), (
            f"branding v1.0 attendu, reçu : {window.windowTitle()!r}"
        )

        source = str(PROJECT_ROOT / "data" / "test_suite" / "facts" / "fact_001.pdf")
        relaunch = window._runner.create_page_task(source, "simple", 2)
        assert window._relaunch_title(relaunch) == "Relance page 2"

        normal = window._runner.create_task(source, "simple", 1)
        assert window._relaunch_title(normal) is None

        # La barre du bas affiche bien le titre distinct.
        window._bottom.set_task(relaunch, title_override="Relance page 2")
        qapp.processEvents()
        assert "Relance page 2" in window._bottom._progress_text.text()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


# --- Collecte d'un dossier (« Ajouter un dossier ») ----------------------------

def test_collect_folder_documents_recursive_and_sorted(tmp_path: Path) -> None:
    from ocr_app.ui.main_window import MainWindow

    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4 x")
    (tmp_path / "A.jpg").write_bytes(b"\xff\xd8 fake")
    (tmp_path / "c.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "note.txt").write_text("ignoré", encoding="utf-8")
    sub = tmp_path / "sous-dossier éàç"
    sub.mkdir()
    (sub / "d.pdf").write_bytes(b"%PDF-1.4 x")

    flat, skipped = MainWindow._collect_folder_documents(str(tmp_path), False)
    assert [path.name for path in flat] == ["A.jpg", "b.pdf", "c.png"], (
        "tri alphabétique insensible à la casse, racine seule par défaut"
    )
    assert skipped == 1, "note.txt compté comme ignoré (jamais silencieux)"

    recursive, skipped = MainWindow._collect_folder_documents(str(tmp_path), True)
    assert [path.name for path in recursive] == ["A.jpg", "b.pdf", "c.png", "d.pdf"]
    assert skipped == 1


def test_collect_folder_empty_or_missing_directory(tmp_path: Path) -> None:
    from ocr_app.ui.main_window import MainWindow

    empty = tmp_path / "vide éàç"
    empty.mkdir()
    assert MainWindow._collect_folder_documents(str(empty), True) == ([], 0)
    assert MainWindow._collect_folder_documents(
        str(tmp_path / "introuvable éàç"), True
    ) == ([], 0)


# --- Réglages workers : --lang/--dpi jamais transmis au worker factice ---------

class _StartSpy:
    """Capture les commandes passées à WorkerClient.start sans les altérer."""

    def __init__(self, runner: TaskRunner) -> None:
        self.calls: list[dict] = []
        self._client = runner._worker
        self._original = runner._worker.start
        runner._worker.start = self._start  # type: ignore[method-assign]

    def _start(self, total_pages, work_dir, stage_delay=0.05, *, command=None,
               extra_args=None) -> bool:
        self.calls.append(
            {
                "command": tuple(str(part) for part in (command or ())),
                "extra_args": tuple(str(arg) for arg in (extra_args or ())),
            }
        )
        return self._original(
            total_pages, work_dir, stage_delay, command=command,
            extra_args=extra_args,
        )


def test_settings_never_passed_to_fake_worker(runner, qapp) -> None:
    """fake_worker ne doit jamais recevoir --lang ni --dpi (ni --page/--input)."""
    runner.set_worker_settings(["fra", "eng"], 300)
    spy = _StartSpy(runner)

    task = runner.create_task("factice éàç.pdf", "simple", 1)
    runner.enqueue(task.id)

    _spin(
        qapp,
        lambda: _statuses(runner)[task.id]
        in (TaskStatus.DONE, TaskStatus.ERROR, TaskStatus.CANCELLED),
        BATCH_TIMEOUT,
        "tâche factice terminée",
    )

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert "fake_worker" in " ".join(call["command"]) or call["command"] == (), (
        "le worker factice de développement est utilisé"
    )
    assert call["extra_args"] == (), (
        f"aucun argument extra pour fake_worker, reçu : {call['extra_args']!r}"
    )
    joined = " ".join(call["extra_args"])
    assert "--lang" not in joined and "--dpi" not in joined
    assert "--page" not in joined and "--input" not in joined


def test_settings_passed_to_real_worker_only(qapp, work_env, tmp_path) -> None:
    """worker_main reçoit --lang/--dpi (et --page pour une relance) ; pas le
    worker factice. La commande est inspectée via un espion sur start()."""
    import sys

    real_command = (
        sys.executable, "-u", "-m", "ocr_app.workers.worker_main",
        "--mode", "simple",
    )
    instance = TaskRunner(worker_commands={"simple": real_command})
    try:
        instance.set_worker_settings(["eng"], 200)
        spy = _StartSpy(instance)

        # Document existant mais illisible : le worker réel échoue vite et
        # proprement (erreur protocolaire) — seuls les arguments nous intéressent.
        bogus = tmp_path / "corrompu éàç.pdf"
        bogus.write_bytes(b"%PDF-1.7 pas de xref\n")
        task = instance.create_task(str(bogus), "simple", 1)
        instance.enqueue(task.id)

        _spin(
            qapp,
            lambda: _statuses(instance)[task.id]
            in (TaskStatus.DONE, TaskStatus.ERROR, TaskStatus.CANCELLED),
            60.0,
            "tâche worker réel terminée",
        )

        assert len(spy.calls) == 1
        call = spy.calls[0]
        assert "ocr_app.workers.worker_main" in call["command"]
        joined = " ".join(call["extra_args"])
        assert "--lang" in call["extra_args"], "réglages langues transmis"
        assert "--dpi" in call["extra_args"], "réglages DPI transmis"
        lang_index = call["extra_args"].index("--lang")
        assert call["extra_args"][lang_index + 1] == "eng"
        dpi_index = call["extra_args"].index("--dpi")
        assert call["extra_args"][dpi_index + 1] == "200"
        assert "--page" not in joined, "pas de relance sur une tâche normale"

        # Une relance ajoute --page N aux arguments du worker réel.
        relaunch = instance.create_page_task(str(bogus), "simple", 2)
        instance.enqueue(relaunch.id)
        _spin(
            qapp,
            lambda: _statuses(instance)[relaunch.id]
            in (TaskStatus.DONE, TaskStatus.ERROR, TaskStatus.CANCELLED),
            60.0,
            "relance worker réel terminée",
        )
        relaunch_call = spy.calls[-1]
        page_index = relaunch_call["extra_args"].index("--page")
        assert relaunch_call["extra_args"][page_index + 1] == "2"
    finally:
        instance.shutdown()
        qapp.processEvents()


def test_settings_args_canonical_order_and_unknown_language(runner) -> None:
    """Ordre canonique (fra, eng) ; langue inconnue conservée en fin, pas perdue."""
    runner.set_worker_settings(["eng", "fra"], 300)
    assert runner._settings_args == ("--lang", "fra+eng", "--dpi", "300")

    runner.set_worker_settings(["fra"], 150)
    assert runner._settings_args == ("--lang", "fra", "--dpi", "150")

    # DPI borné à 300 quoi qu'il arrive (R7).
    runner.set_worker_settings(["fra", "eng"], 999)
    assert runner._settings_args == ("--lang", "fra+eng", "--dpi", "300")
