"""Tests du drapeau ``--page`` et de ``--lang`` du worker principal (phase 8).

Pilote un sous-processus ``python -m ocr_app.workers.worker_main`` (R2), sur le
même modèle que ``test_worker_main_protocol.py`` (thread lecteur, timeouts,
drain stderr), pour la relance d'une page et les langues Tesseract :

- ``--page 1`` sur ``fact_001.pdf`` : un seul ``page_done`` portant le numéro
  réel 1, ``page_001.json`` avec le texte natif réel (R8), total de
  progression = 1 ;
- ``--page 999`` hors bornes : erreur protocolaire propre, exit 1 ;
- ``--lang fra`` : accepté, journalisé, traitement complet ;
- ``--lang invalide`` : exit 2 d'usage, stdout vide (réservé au protocole) ;
- ``--page`` en simulation (sans ``--input``) : numéro réel conservé ;
- R14 : dossier de travail accentué/espacé ;
- cancel < 2 s pendant une relance en pleine OCR réelle (scan).

Tous les tests sont headless (aucun Qt).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_MODULE = "ocr_app.workers.worker_main"

FACT_001 = PROJECT_ROOT / "data" / "test_suite" / "facts" / "fact_001.pdf"
FACT_002 = PROJECT_ROOT / "data" / "test_suite" / "facts" / "fact_002.pdf"
SCAN_001 = PROJECT_ROOT / "data" / "test_suite" / "scans" / "scan_001.pdf"

# Étapes émises par page en simulation (cf. worker_main.PIPELINE_STAGES).
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


class PageDriver:
    """Pilote minimal de worker_main : sous-processus + lecture asynchrone.

    Version resserrée du ``WorkerDriver`` de ``test_worker_main_protocol.py``,
    étendue aux options phase 8 (``--page``, ``--lang``, ``--dpi``).
    """

    def __init__(
        self,
        work_dir: Path,
        mode: str = "simple",
        input_path: Optional[Path] = None,
        page: Optional[int] = None,
        lang: Optional[str] = None,
        pages: int = 5,
        stage_delay: float = 0.03,
    ) -> None:
        self.work_dir = Path(work_dir)
        cmd = [sys.executable, "-u", "-m", WORKER_MODULE, "--mode", mode]
        if input_path is not None:
            cmd += ["--input", str(input_path)]
        else:
            cmd += ["--pages", str(pages), "--stage-delay", str(stage_delay)]
        if page is not None:
            cmd += ["--page", str(page)]
        if lang is not None:
            cmd += ["--lang", lang]
        cmd += ["--work-dir", str(self.work_dir)]

        env = dict(os.environ)
        env["PYTHONPATH"] = (
            str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        )
        env["PYTHONUTF8"] = "1"

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(PROJECT_ROOT),
            env=env,
        )

        self.received: list[dict] = []
        self.invalid_lines: list[str] = []
        self._stderr_lines: list[str] = []
        self._messages: "queue.Queue[object]" = queue.Queue()
        self._stdout_closed = False

        self._stdout_thread = threading.Thread(
            target=self._pump_stdout, name="page-flag-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, name="page-flag-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

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

    def send(self, action: str) -> None:
        assert self.proc.stdin is not None
        payload = {"type": "command", "action": action}
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def close_stdin(self) -> None:
        if self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except (OSError, ValueError):
                pass

    def next_message(self, timeout: float) -> Optional[dict]:
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
                self._messages.put(_EOF)
                return None
            assert isinstance(item, dict)
            return item

    def wait_for(
        self,
        predicate,
        timeout: float = 15.0,
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
                        f"code={self.proc.poll()} ; messages : {self.received!r} ; "
                        f"stderr : {self.stderr_text()!r}"
                    )
                continue
            if predicate(message):
                return message

    def wait_exit(self, timeout: float) -> int:
        return self.proc.wait(timeout=timeout)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.close_stdin()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=3)
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
    drivers: list[PageDriver] = []

    def make(**kwargs) -> PageDriver:
        # R14 : dossier de travail accentué et espacé (sous %TEMP% via pytest).
        work_dir = tmp_path / "relance éàç ü"
        work_dir.mkdir(parents=True, exist_ok=True)
        driver = PageDriver(work_dir, **kwargs)
        drivers.append(driver)
        return driver

    yield make

    for driver in drivers:
        driver.close()


def _run_usage(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    """Lance worker_main avec stdin fermé : pour les erreurs d'usage (exit 2)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-u", "-m", WORKER_MODULE, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
        env=env,
        timeout=30,
    )


# --- --page sur document réel -------------------------------------------------

@pytest.mark.skipif(not FACT_001.is_file(), reason="fact_001.pdf absente")
def test_page_1_single_page_done_with_real_text(driver_factory) -> None:
    """``--page 1`` sur fact_001 : une seule page, numéro réel, texte natif."""
    pytest.importorskip("tesserocr", reason="OCR simple réel (extra [simple])")
    driver = driver_factory(input_path=FACT_001, page=1)
    driver.send("start")

    done = driver.wait_for(
        lambda m: m.get("type") == "page_done",
        timeout=60.0,
        description="page_done de la relance page 1",
    )
    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == [], "stdout doit rester du JSON pur"

    # Un unique page_done, portant le numéro réel de la page (1, jamais 0).
    done_messages = [m for m in driver.received if m.get("type") == "page_done"]
    assert len(done_messages) == 1
    assert done["page"] == 1

    # Total de progression = 1 (une page demandée), pas le total du document.
    progress = [m for m in driver.received if m.get("type") == "progress"]
    assert progress, "aucun message de progression"
    assert {m["total"] for m in progress} == {1}
    assert {m["page"] for m in progress} == {1}
    assert [m["stage"] for m in progress] == list(STAGES) + ["done"]

    # Résultat : page_001.json dans le dossier accentué demandé (R14),
    # texte réel (natif R8), accents conservés.
    result_path = Path(done["result_path"])
    assert result_path.parent == driver.work_dir.resolve(), (
        "R14 : le résultat est écrit dans le dossier accentué demandé"
    )
    assert result_path.is_file()
    assert result_path.name == "page_001.json"

    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["number"] == 1
    text = data["blocks"][0]["text"]
    assert len(text) > 100, f"texte réel attendu, reçu : {text[:80]!r}"
    assert "Dupont" in text, "texte natif du document (R8)"
    assert "é" in text, "les accents du document survivent"


@pytest.mark.skipif(not FACT_001.is_file(), reason="fact_001.pdf absente")
def test_page_999_out_of_bounds_protocol_error_exit_1(driver_factory) -> None:
    """``--page 999`` hors document : erreur protocolaire propre, exit 1."""
    driver = driver_factory(input_path=FACT_001, page=999)
    driver.send("start")

    error = driver.wait_for(
        lambda m: m.get("type") == "error",
        timeout=15.0,
        description="erreur protocolaire hors bornes",
    )
    assert "hors document" in error["message"]
    assert "999" in error["message"]

    # Aucune page produite : le document n'est pas traité.
    assert [m for m in driver.received if m.get("type") == "page_done"] == []
    assert driver.wait_exit(timeout=5.0) == 1, "erreur protocolaire → exit 1"
    assert driver.invalid_lines == []


# --- --lang -------------------------------------------------------------------

@pytest.mark.skipif(not FACT_001.is_file(), reason="fact_001.pdf absente")
def test_lang_fra_accepted_on_real_document(driver_factory) -> None:
    """``--lang fra`` : accepté, langues journalisées, traitement complet."""
    pytest.importorskip("tesserocr", reason="OCR simple réel (extra [simple])")
    driver = driver_factory(input_path=FACT_001, page=1, lang="fra")
    driver.send("start")

    driver.wait_for(
        lambda m: m.get("type") == "page_done",
        timeout=60.0,
        description="page_done avec --lang fra",
    )
    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == []
    # Les langues demandées atteignent le moteur (diagnostic documenté).
    assert "langues OCR demandées : fra" in driver.stderr_text()


def test_lang_fra_accepted_in_simulation(driver_factory) -> None:
    """``--lang fra`` est validé et accepté même sans document (simulation)."""
    driver = driver_factory(pages=2, stage_delay=0.01, lang="fra")
    driver.send("start")

    done = driver.wait_for(
        lambda m: m.get("type") == "page_done" and m.get("page") == 2,
        timeout=15.0,
        description="fin des 2 pages simulées avec --lang fra",
    )
    assert done["page"] == 2
    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == []


@pytest.mark.parametrize("bad_lang", ["fra eng", "fr@", "fra+", "+fra", "++"])
def test_lang_invalid_usage_error_exit_2_stdout_empty(bad_lang, tmp_path) -> None:
    """``--lang invalide`` : exit 2 d'usage, stdout vide (protocole intact)."""
    completed = _run_usage(
        ["--lang", bad_lang, "--work-dir", str(tmp_path / "aocri éàç")],
        tmp_path,
    )
    assert completed.returncode == 2, (
        f"--lang {bad_lang!r} doit être refusé en exit 2 ; "
        f"stderr : {completed.stderr[-200:]!r}"
    )
    assert completed.stdout.strip() == "", "stdout réservé au protocole"
    assert "Traceback" not in completed.stderr, "aucune trace non protocolaire"
    assert "invalide" in completed.stderr


# --- --page erreurs d'usage ---------------------------------------------------

@pytest.mark.skipif(not FACT_001.is_file(), reason="fact_001.pdf absente")
def test_page_zero_usage_error_exit_2(tmp_path) -> None:
    """``--page 0`` : erreur d'usage (entier >= 1), exit 2, stdout vide."""
    completed = _run_usage(
        ["--input", str(FACT_001), "--page", "0",
         "--work-dir", str(tmp_path / "aocri éàç")],
        tmp_path,
    )
    assert completed.returncode == 2
    assert completed.stdout.strip() == ""
    assert "Traceback" not in completed.stderr
    assert "--page" in completed.stderr


# --- --page en simulation (sans --input) --------------------------------------

def test_page_flag_simulation_keeps_real_number(driver_factory) -> None:
    """``--page 42`` sans --input : la page 42 seule, numéro réel conservé."""
    driver = driver_factory(pages=5, stage_delay=0.01, page=42)
    driver.send("start")

    done = driver.wait_for(
        lambda m: m.get("type") == "page_done",
        timeout=15.0,
        description="page_done de la page simulée 42",
    )
    assert driver.wait_exit(timeout=10.0) == 0
    assert done["page"] == 42, "le numéro réel de la page est conservé"

    done_messages = [m for m in driver.received if m.get("type") == "page_done"]
    assert len(done_messages) == 1

    progress = [m for m in driver.received if m.get("type") == "progress"]
    assert {m["total"] for m in progress} == {1}, "total affiché = 1"

    # page_042.json (numérotation réelle), contenu placeholder avec accents.
    data = json.loads((driver.work_dir / "page_042.json").read_text(encoding="utf-8"))
    assert data["number"] == 42
    assert "éèàç" in data["blocks"][0]["text"]


# --- Cancel pendant une relance ------------------------------------------------

@pytest.mark.skipif(not SCAN_001.is_file(), reason="scan_001.pdf absente")
def test_cancel_during_relaunch_within_two_seconds(driver_factory) -> None:
    """Cancel < 2 s pendant une relance en pleine OCR réelle (scan, R2)."""
    pytest.importorskip("tesserocr", reason="OCR réel nécessaire (scan)")
    driver = driver_factory(input_path=SCAN_001, page=1)
    driver.send("start")
    first = driver.wait_for(
        lambda m: m.get("type") == "progress",
        timeout=30.0,
        description="premier progress de la relance sur scan",
    )
    assert first["page"] == 1

    started = time.perf_counter()
    driver.send("cancel")
    try:
        driver.wait_exit(timeout=2.0)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "cancel pendant une relance : worker pas terminé en moins de 2 s ; "
            f"stderr : {driver.stderr_text()!r}"
        )
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"cancel traité en {elapsed:.2f} s"

    error = driver.wait_for(
        lambda m: m.get("type") == "error",
        timeout=1.0,
        description="error « annulé » pendant la relance",
    )
    assert "annul" in error["message"].lower()
    assert [m for m in driver.received if m.get("type") == "page_done"] == []
    assert driver.invalid_lines == []
