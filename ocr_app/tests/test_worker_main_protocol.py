"""Tests du protocole du worker principal par mode (phase 7).

Pilote un sous-processus ``python -m ocr_app.workers.worker_main`` (R2), sur le
même modèle que ``test_worker_protocol.py`` (thread lecteur, timeouts, drain
stderr), mais avec l'implémentation réelle :

- start → progress par étape → page_done → fichiers page_XXX.json (R6) ;
- document réel ``fact_001.pdf`` : texte natif (R8) avec tesserocr installé ;
  sans ``--input`` ou en medium/advanced : pages placeholder ;
- pause : gel réel vérifié au chronomètre, puis reprise complète ;
- cancel < 2 s, y compris en pleine étape OCR d'un scan réel ;
- EOF sans start : exit 0, aucun message de protocole ;
- fichier inexistant / PDF corrompu : erreur protocolaire propre ;
- stdout = JSON pur (aucune ligne non-JSON), stderr non vide permis ;
- R14 : dossiers de travail avec espaces et accents.

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
# Sur document réel, le pipeline ajoute son étape finale « done » avant page_done.
REAL_DOC_STAGES = STAGES + ("done",)

_EOF = object()


class WorkerDriver:
    """Pilote de test de worker_main : sous-processus + lecture asynchrone."""

    def __init__(
        self,
        work_dir: Path,
        mode: str = "simple",
        pages: int = 5,
        stage_delay: float = 0.03,
        input_path: Optional[Path] = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.mode = mode
        cmd = [sys.executable, "-u", "-m", WORKER_MODULE, "--mode", mode]
        if input_path is not None:
            cmd += ["--input", str(input_path)]
        else:
            cmd += ["--pages", str(pages), "--stage-delay", str(stage_delay)]
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
            target=self._pump_stdout, name="worker-main-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, name="worker-main-stderr", daemon=True
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
        payload: dict = {"type": "command", "action": action}
        if page is not None:
            payload["page"] = page
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def send_raw(self, raw: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(raw + "\n")
        self.proc.stdin.flush()

    def close_stdin(self) -> None:
        if self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except (OSError, ValueError):
                pass

    # --- Messages ---------------------------------------------------------
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

    def assert_silent(self, duration: float) -> float:
        """Aucun message pendant ``duration`` ; retourne la durée mesurée."""
        started = time.perf_counter()
        message = self.next_message(duration)
        elapsed = time.perf_counter() - started
        assert message is None, f"message inattendu pendant le gel : {message!r}"
        return elapsed

    # --- Arrêt ------------------------------------------------------------
    def wait_exit(self, timeout: float) -> int:
        return self.proc.wait(timeout=timeout)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.close_stdin()
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


def _collect_page_done(driver: WorkerDriver, expected: int, timeout: float = 20.0):
    """Lit les messages jusqu'à ``expected`` page_done (ou fin du worker)."""
    completed: list[dict] = []
    deadline = time.monotonic() + timeout
    while len(completed) < expected and time.monotonic() < deadline:
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
    return completed


@pytest.fixture
def driver_factory(tmp_path: Path):
    drivers: list[WorkerDriver] = []

    def make(
        mode: str = "simple",
        pages: int = 5,
        stage_delay: float = 0.03,
        input_path: Optional[Path] = None,
    ) -> WorkerDriver:
        # R14 : le dossier de travail contient espaces et accents (sous %TEMP%).
        work_dir = tmp_path / "aocri t7 éàç"
        work_dir.mkdir(parents=True, exist_ok=True)
        driver = WorkerDriver(
            work_dir,
            mode=mode,
            pages=pages,
            stage_delay=stage_delay,
            input_path=input_path,
        )
        drivers.append(driver)
        return driver

    yield make

    for driver in drivers:
        driver.close()


# --- Protocole complet en simulation -----------------------------------------

def test_simulated_simple_full_protocol_and_placeholder_files(driver_factory) -> None:
    driver = driver_factory(mode="simple", pages=3, stage_delay=0.01)
    driver.send("start")

    completed = _collect_page_done(driver, expected=3)
    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == [], "stdout doit rester du JSON pur"
    assert [m["page"] for m in completed] == [1, 2, 3]

    progress = [m for m in driver.received if m.get("type") == "progress"]
    for page in range(1, 4):
        page_progress = [m for m in progress if m["page"] == page]
        assert [m["stage"] for m in page_progress] == list(STAGES)
        assert all(m["total"] == 3 for m in page_progress)

    expected_names = {f"page_{page:03d}.json" for page in range(1, 4)}
    actual_names = {path.name for path in driver.work_dir.glob("page_*.json")}
    assert actual_names == expected_names, "R6 : chaque page écrite dès qu'elle est finie"

    resolved_work_dir = driver.work_dir.resolve()
    for message in completed:
        path = Path(message["result_path"])
        assert path.is_file(), f"fichier page manquant : {path}"
        # R14 : résultat rendu dans le dossier accentué demandé.
        assert path.parent == resolved_work_dir

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["number"] == message["page"]
        text = data["blocks"][0]["text"]
        assert "simulation Simple" in text
        assert "éèàç" in text, "les accents survivent au protocole"
        assert data["confidence"] == pytest.approx(0.97)

    # stderr non vide permis (diagnostics) ; stdout, lui, est resté pur.
    assert "worker_main" in driver.stderr_text()


def test_stdout_stays_pure_json_with_garbage_commands(driver_factory) -> None:
    driver = driver_factory(mode="medium", pages=2, stage_delay=0.01)
    driver.send_raw("ce n'est pas du JSON")
    driver.send_raw('{"type":"command","action":"action_inconnue"}')
    driver.send("start")

    completed = _collect_page_done(driver, expected=2)
    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == [], "aucune ligne non-JSON sur stdout"
    assert [m["page"] for m in completed] == [1, 2]

    # Les pages placeholder medium portent le texte du squelette.
    data = json.loads((driver.work_dir / "page_001.json").read_text(encoding="utf-8"))
    assert "RapidOCR non encore installé" in data["blocks"][0]["text"]
    assert "éèàç" in data["blocks"][0]["text"]
    assert driver.stderr_text() != "", "les diagnostics stderr sont permis"


# --- Document réel ------------------------------------------------------------

@pytest.mark.skipif(not FACT_001.is_file(), reason="fact_001.pdf absente")
def test_real_document_simple_native_text_r8(driver_factory) -> None:
    pytest.importorskip("tesserocr", reason="OCR simple réel (extra [simple])")
    driver = driver_factory(mode="simple", input_path=FACT_001)
    driver.send("start")

    done = driver.wait_for(
        lambda m: m.get("type") == "page_done" and m.get("page") == 1,
        timeout=60.0,
        description="page_done du document réel",
    )
    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == []

    progress = [m for m in driver.received if m.get("type") == "progress"]
    assert [m["stage"] for m in progress] == list(REAL_DOC_STAGES)
    assert all(m["total"] == 1 for m in progress)

    result_path = Path(done["result_path"])
    # R14 : document traité depuis le dossier de travail accentué.
    assert result_path.parent == driver.work_dir.resolve()
    assert result_path.is_file()

    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["number"] == 1
    text = data["blocks"][0]["text"]
    # R8 : texte natif du PDF, aucun OCR inventé.
    assert len(text) > 100, f"texte natif attendu, reçu : {text[:80]!r}"
    assert "Dupont" in text
    assert "é" in text, "les accents du document survivent"
    assert data["confidence"] == pytest.approx(1.0), "R8 : texte natif, confiance 1.0"


@pytest.mark.skipif(not FACT_001.is_file(), reason="fact_001.pdf absente")
def test_real_document_medium_still_placeholder(driver_factory) -> None:
    pytest.importorskip("tesserocr", reason="pipeline réel (noyau)")
    driver = driver_factory(mode="medium", input_path=FACT_001)
    driver.send("start")

    done = driver.wait_for(
        lambda m: m.get("type") == "page_done" and m.get("page") == 1,
        timeout=60.0,
        description="page_done medium sur document réel",
    )
    assert driver.wait_exit(timeout=10.0) == 0
    assert driver.invalid_lines == []

    data = json.loads(Path(done["result_path"]).read_text(encoding="utf-8"))
    text = data["blocks"][0]["text"]
    # Sur document réel, le pipeline passe par le squelette MediumWorker
    # (placeholder explicite en attendant le module) — pas par la simulation.
    assert "squelette MediumWorker" in text, "moteur medium absent : placeholder"
    assert data["confidence"] == pytest.approx(0.90)


# --- Pause / reprise ----------------------------------------------------------

def test_pause_freezes_for_real_then_resume_completes(driver_factory) -> None:
    driver = driver_factory(mode="simple", pages=4, stage_delay=0.05)
    driver.send("start")
    driver.wait_for(
        lambda m: m.get("type") == "progress",
        timeout=10.0,
        description="premier message progress",
    )

    driver.send("pause")
    time.sleep(0.15)  # laisse retomber un éventuel message déjà en vol
    while driver.next_message(timeout=0.05) is not None:
        pass
    count_at_pause = len(driver.received)

    # Chronométrage du gel : au moins 1,0 s sans aucun message ni fin de processus.
    frozen = driver.assert_silent(1.0)
    assert frozen >= 1.0, f"le gel a duré {frozen:.2f} s seulement"
    assert driver.proc.poll() is None, "le worker s'est arrêté pendant la pause"
    assert len(driver.received) == count_at_pause, "des messages ont fui pendant la pause"

    driver.send("resume")
    completed = _collect_page_done(driver, expected=4)
    assert driver.wait_exit(timeout=20.0) == 0
    assert driver.invalid_lines == []
    assert [m["page"] for m in completed] == [1, 2, 3, 4]

    # La pause n'a sauté aucune étape : chaque page a ses 7 étapes.
    progress = [m for m in driver.received if m.get("type") == "progress"]
    for page in range(1, 5):
        page_progress = [m for m in progress if m["page"] == page]
        assert [m["stage"] for m in page_progress] == list(STAGES)


# --- Annulation ---------------------------------------------------------------

def test_cancel_during_simulation_within_two_seconds(driver_factory) -> None:
    driver = driver_factory(mode="simple", pages=200, stage_delay=0.05)
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

    assert elapsed < 2.0, f"cancel traité en {elapsed:.2f} s"
    assert exit_code == 0, "une annulation n'est pas une erreur de processus"

    error = driver.wait_for(
        lambda m: m.get("type") == "error",
        timeout=1.0,
        description="événement error d'annulation",
    )
    assert "annul" in error["message"].lower()
    assert error["page"] >= 1
    assert driver.invalid_lines == []


@pytest.mark.skipif(not SCAN_001.is_file(), reason="scan_001.pdf absente")
def test_cancel_during_real_ocr_within_two_seconds(driver_factory) -> None:
    """Cancel en pleine étape native (rendu 300 DPI + OCR tesserocr).

    Le durcissement phase 7 (thread pipeline daemon surveillé toutes les
    20 ms) doit tenir la borne de 2 s même au milieu d'une étape OCR réelle.
    NB : le code de sortie peut être 0 ou un code d'abort Windows selon que le
    thread daemon natif a eu le temps de mourir proprement — ce n'est pas une
    exigence du protocole (constat rapporté, non corrigé) ; seul le délai et
    le message « annulé » sont contractuels ici.
    """
    pytest.importorskip("tesserocr", reason="OCR réel nécessaire (scan)")
    driver = driver_factory(mode="simple", input_path=SCAN_001)
    driver.send("start")
    first = driver.wait_for(
        lambda m: m.get("type") == "progress",
        timeout=30.0,
        description="premier progress du scan réel",
    )
    assert first["page"] == 1

    started = time.perf_counter()
    driver.send("cancel")
    try:
        driver.wait_exit(timeout=2.0)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "cancel en pleine OCR : le worker n'est pas terminé en moins de 2 s ; "
            f"stderr : {driver.stderr_text()!r}"
        )
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"cancel en pleine OCR traité en {elapsed:.2f} s"

    error = driver.wait_for(
        lambda m: m.get("type") == "error",
        timeout=1.0,
        description="error « annulé » en pleine OCR",
    )
    assert "annul" in error["message"].lower()
    done = [m for m in driver.received if m.get("type") == "page_done"]
    assert len(done) < 1, "l'annulation a interrompu le document avant la 1re page"


# --- Arrêts propres et erreurs de document ------------------------------------

def test_eof_without_start_exits_zero(driver_factory) -> None:
    driver = driver_factory(mode="simple", pages=3)
    driver.close_stdin()

    assert driver.wait_exit(timeout=5.0) == 0
    assert driver.received == [], "aucun message de protocole sans start"
    assert driver.invalid_lines == []


def test_missing_document_clean_protocol_error(tmp_path: Path) -> None:
    # R14 : le nom du fichier inexistant porte accents et espaces.
    missing = tmp_path / "aocri t7 éàç" / "document introuvable éàç.pdf"
    missing.parent.mkdir(parents=True)
    driver = WorkerDriver(
        missing.parent, mode="simple", input_path=missing, pages=1
    )
    try:
        driver.send("start")
        error = driver.wait_for(
            lambda m: m.get("type") == "error",
            timeout=10.0,
            description="erreur de document",
        )
        assert "erreur de document" in error["message"]
        assert "introuvable" in error["message"]
        assert driver.wait_exit(timeout=5.0) == 1
        assert driver.invalid_lines == []
    finally:
        driver.close()


def test_corrupt_pdf_clean_protocol_error(tmp_path: Path) -> None:
    corrupt = tmp_path / "aocri t7 éàç" / "corrompu éàç.pdf"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"%PDF-1.7\n contenu delibere non parseable, pas de xref\n")
    driver = WorkerDriver(corrupt.parent, mode="simple", input_path=corrupt, pages=1)
    try:
        driver.send("start")
        error = driver.wait_for(
            lambda m: m.get("type") == "error",
            timeout=15.0,
            description="erreur PDF corrompu",
        )
        assert "erreur de document" in error["message"]
        assert driver.wait_exit(timeout=5.0) == 1
        assert driver.invalid_lines == []
    finally:
        driver.close()


def test_unknown_mode_rejected_cleanly(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, "-u", "-m", WORKER_MODULE,
         "--mode", "bogus", "--work-dir", str(tmp_path / "aocri t7 éàç")],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(PROJECT_ROOT),
        env=env,
        timeout=30,
    )
    assert completed.returncode == 2, "erreur d'usage argparse, pas un crash"
    assert "Traceback" not in completed.stderr, "aucune trace non protocolaire"
    assert "invalid choice" in completed.stderr
    for mode in ("simple", "medium", "advanced"):
        assert mode in completed.stderr
    # stdout reste réservé au protocole : rien du tout dans ce cas d'erreur.
    assert completed.stdout.strip() == "", "aucune ligne de protocole sur stdout"
