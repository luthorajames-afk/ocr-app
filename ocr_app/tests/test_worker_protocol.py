"""Tests du protocole worker JSON avec le worker factice (phase 3 partielle).

Le pilote lance ``python -m ocr_app.workers.fake_worker`` dans un sous-processus
(R2), écrit les commandes sur son stdin et lit les messages du protocole sur son
stdout. Les tests vérifient :

- la progression par étape et les ``page_done`` ;
- la sauvegarde page par page (R6), y compris dans un dossier accentué (R14) ;
- la pause/reprise ;
- l'annulation immédiate (moins de 2 secondes).
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import pytest

from ocr_app.core.protocol import CommandMessage

WORKER_MODULE = "ocr_app.workers.fake_worker"

STAGES = (
    "render",
    "preprocess",
    "layout",
    "ocr",
    "tables",
    "reconstruct",
    "save",
)

_EOF = object()


class WorkerDriver:
    """Pilote de test : sous-processus worker + lecture asynchrone du protocole."""

    def __init__(
        self,
        work_dir: Path,
        pages: int = 5,
        stage_delay: float = 0.03,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.pages = pages
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                WORKER_MODULE,
                "--pages",
                str(pages),
                "--work-dir",
                str(self.work_dir),
                "--stage-delay",
                str(stage_delay),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

        self.received: list[dict] = []
        self.invalid_lines: list[str] = []
        self._stderr_lines: list[str] = []
        self._messages: "queue.Queue[object]" = queue.Queue()
        self._stdout_closed = False

        self._stdout_thread = threading.Thread(
            target=self._pump_stdout, name="worker-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, name="worker-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    # --- Lecture ----------------------------------------------------------
    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for raw in self.proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self.invalid_lines.append(line)
                    continue
                if not isinstance(message, dict):
                    self.invalid_lines.append(line)
                    continue
                self.received.append(message)
                self._messages.put(message)
        finally:
            self._messages.put(_EOF)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        try:
            for raw in self.proc.stderr:
                self._stderr_lines.append(raw.rstrip("\r\n"))
        except (OSError, ValueError):
            pass

    def stderr_text(self) -> str:
        return "\n".join(self._stderr_lines)

    # --- Commandes --------------------------------------------------------
    def send(self, action: str, page: Optional[int] = None) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(CommandMessage(action=action, page=page).to_json() + "\n")
        self.proc.stdin.flush()

    # --- Messages ---------------------------------------------------------
    def next_message(self, timeout: float) -> Optional[dict]:
        """Retourne le prochain message ou None si timeout / stdout fermé."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                item = self._messages.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if item is _EOF:
                self._stdout_closed = True
                self._messages.put(_EOF)  # les appels suivants voient aussi la fin
                return None
            assert isinstance(item, dict)
            return item

    def wait_for(
        self,
        predicate: Callable[[dict], bool],
        timeout: float = 10.0,
        description: str = "message",
    ) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"{description} non reçu en {timeout:.1f} s ; "
                    f"messages reçus : {self.received!r} ; "
                    f"stderr : {self.stderr_text()!r}"
                )
            message = self.next_message(min(remaining, 0.5))
            if message is None:
                if self._stdout_closed:
                    raise AssertionError(
                        f"le worker s'est terminé avant {description} ; "
                        f"code={self.proc.poll()} ; messages reçus : {self.received!r} ; "
                        f"stderr : {self.stderr_text()!r}"
                    )
                continue
            if predicate(message):
                return message

    def assert_silent(self, duration: float) -> None:
        message = self.next_message(duration)
        assert message is None, f"message inattendu pendant la pause : {message!r}"

    # --- Arrêt ------------------------------------------------------------
    def wait_exit(self, timeout: float) -> int:
        return self.proc.wait(timeout=timeout)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

        self._stdout_thread.join(timeout=0.5)
        self._stderr_thread.join(timeout=0.5)

        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass


@pytest.fixture
def driver_factory(tmp_path: Path):
    drivers: list[WorkerDriver] = []

    def make(pages: int = 5, stage_delay: float = 0.03) -> WorkerDriver:
        # R14 : le dossier de travail contient espaces et accents.
        work_dir = tmp_path / "travail accentué avec espaces"
        driver = WorkerDriver(work_dir, pages=pages, stage_delay=stage_delay)
        drivers.append(driver)
        return driver

    yield make

    for driver in drivers:
        driver.close()


def test_start_progress_page_done_and_json_files(driver_factory) -> None:
    driver = driver_factory(pages=5, stage_delay=0.01)
    driver.send("start")

    completed: list[dict] = []
    deadline = time.monotonic() + 20.0
    while len(completed) < 5 and time.monotonic() < deadline:
        message = driver.next_message(timeout=5.0)
        if message is None:
            break
        if message.get("type") == "error":
            pytest.fail(
                f"le worker a signalé une erreur inattendue : {message!r} ; "
                f"stderr : {driver.stderr_text()!r}"
            )
        if message.get("type") == "page_done":
            completed.append(message)

    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == []
    assert [m["page"] for m in completed] == [1, 2, 3, 4, 5]

    progress = [m for m in driver.received if m.get("type") == "progress"]
    for page in range(1, 6):
        page_progress = [m for m in progress if m["page"] == page]
        assert [m["stage"] for m in page_progress] == list(STAGES)
        assert all(m["total"] == 5 for m in page_progress)

    expected_names = {f"page_{page:03d}.json" for page in range(1, 6)}
    actual_names = {path.name for path in driver.work_dir.glob("page_*.json")}
    assert actual_names == expected_names

    resolved_work_dir = driver.work_dir.resolve()
    for message in completed:
        path = Path(message["result_path"])
        assert path.is_file(), f"fichier page manquant : {path}"
        assert path.parent == resolved_work_dir

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["number"] == message["page"]
        assert len(data["blocks"]) == 3
        assert data["blocks"][2]["type"] == "table"
        assert data["blocks"][2]["table"]["rows"] == 2
        assert "éèàç" in data["blocks"][1]["text"]


def test_pause_suspends_and_resume_continues(driver_factory) -> None:
    driver = driver_factory(pages=5, stage_delay=0.04)
    driver.send("start")
    driver.wait_for(
        lambda m: m.get("type") == "progress",
        timeout=10.0,
        description="premier message progress",
    )

    driver.send("pause")
    time.sleep(0.15)  # laisse retomber l'éventuel message déjà en vol
    while driver.next_message(timeout=0.05) is not None:
        pass
    count_at_pause = len(driver.received)

    driver.assert_silent(0.45)
    assert driver.proc.poll() is None, "le worker s'est arrêté pendant la pause"
    assert len(driver.received) == count_at_pause

    driver.send("resume")

    completed: list[dict] = []
    deadline = time.monotonic() + 20.0
    while len(completed) < 5 and time.monotonic() < deadline:
        message = driver.next_message(timeout=5.0)
        if message is None:
            break
        if message.get("type") == "error":
            pytest.fail(
                f"le worker a signalé une erreur inattendue : {message!r} ; "
                f"stderr : {driver.stderr_text()!r}"
            )
        if message.get("type") == "page_done":
            completed.append(message)

    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == []
    assert [m["page"] for m in completed] == [1, 2, 3, 4, 5]

    # La pause ne doit pas avoir sauté d'étape : chaque page a ses 7 stages.
    progress = [m for m in driver.received if m.get("type") == "progress"]
    for page in range(1, 6):
        page_progress = [m for m in progress if m["page"] == page]
        assert [m["stage"] for m in page_progress] == list(STAGES)


def test_cancel_stops_within_two_seconds(driver_factory) -> None:
    driver = driver_factory(pages=5, stage_delay=0.05)
    driver.send("start")
    first = driver.wait_for(
        lambda m: m.get("type") == "progress",
        timeout=10.0,
        description="premier message progress",
    )
    assert first["page"] == 1

    started = time.perf_counter()
    driver.send("cancel")
    try:
        exit_code = driver.wait_exit(timeout=2.0)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "le worker n'a pas répondu à cancel en moins de 2 s ; "
            f"stderr : {driver.stderr_text()!r}"
        )
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0
    assert exit_code == 0

    error = driver.wait_for(
        lambda m: m.get("type") == "error",
        timeout=1.0,
        description="événement error d'annulation",
    )
    assert "annul" in error["message"].lower()
    assert error["page"] >= 1

    done = [m for m in driver.received if m.get("type") == "page_done"]
    assert len(done) < 5, "l'annulation aurait dû interrompre le document"
    assert driver.invalid_lines == []
