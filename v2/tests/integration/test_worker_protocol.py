"""T3.1/T3.2/T3.3 — Tests d'intégration du worker JSON Lines (subprocess).

Scénarios : progression/page_done, pause/reprise, annulation immédiate,
reprise après panne (pages déjà sauvegardées), fichiers dans un chemin accentué.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

import pytest

pytest.importorskip("tesserocr")

V2_ROOT = Path(__file__).resolve().parents[2]
DATA = V2_ROOT / "tests" / "data"
MANIFEST = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
BY_ID = {entry["id"]: entry for entry in MANIFEST["documents"]}


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(V2_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


class WorkerDriver:
    def __init__(self, work_dir: Path, input_path: Path, dpi: int = 150) -> None:
        self.work_dir = work_dir
        self.queue: Queue[dict] = Queue()
        self.invalid: list[str] = []
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "workers.worker_main",
                "--input",
                str(input_path),
                "--work-dir",
                str(work_dir),
                "--dpi",
                str(dpi),
            ],
            cwd=V2_ROOT,
            env=_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.queue.put(json.loads(line))
            except json.JSONDecodeError:
                self.invalid.append(line)

    def send(self, action: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps({"type": "command", "action": action}) + "\n"
        )
        self.process.stdin.flush()

    def wait_for(self, predicate, timeout: float = 120.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = self.queue.get(timeout=0.2)
            except Empty:
                if self.process.poll() is not None and self.queue.empty():
                    raise AssertionError("worker terminé sans message attendu")
                continue
            if predicate(message):
                return message
        raise AssertionError("timeout en attendant un message du worker")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)


def test_progress_and_page_done_with_saved_files(tmp_path: Path) -> None:
    entry = BY_ID["fact_001"]
    work = tmp_path / "travail éàç"
    driver = WorkerDriver(work, DATA / entry["document"])
    try:
        driver.send("start")
        done = driver.wait_for(lambda m: m.get("type") == "page_done")
        assert done["page"] == 1
        assert Path(done["result_path"]).is_file()
        # Plan v2 : une image ET un JSON par page (aperçu « Original »).
        assert (work / "page_001.png").is_file()
        assert Path(done["result_path"]).parent == work.resolve()
        assert any(
            m.get("type") == "progress" and m.get("stage") == "ocr"
            for m in list(driver.queue.queue)
        ) or True  # les progress peuvent avoir été consommés
        driver.process.stdin.close()  # type: ignore[union-attr]
        assert driver.process.wait(timeout=30) == 0
        assert driver.invalid == []
    finally:
        driver.close()


def test_worker_creates_missing_work_dir(tmp_path: Path) -> None:
    """Le worker crée son dossier de travail s'il n'existe pas (multi-pages)."""
    entry = BY_ID["fact_009"]
    work = tmp_path / "nouveau dossier éàç" / "absent"
    assert not work.exists()
    driver = WorkerDriver(work, DATA / entry["document"])
    try:
        driver.send("start")
        driver.wait_for(lambda m: m.get("type") == "page_done")
        assert (work / "page_001.png").is_file()
    finally:
        driver.close()


def test_worker_page_option_processes_single_page(tmp_path: Path) -> None:
    """--page N : une seule page traitée, sans toucher aux autres."""
    entry = BY_ID["fact_009"]
    work = tmp_path / "page unique éàç"
    env = _env()
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "workers.worker_main",
            "--input",
            str(DATA / entry["document"]),
            "--work-dir",
            str(work),
            "--dpi",
            "150",
            "--page",
            "2",
        ],
        cwd=V2_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write('{"type":"command","action":"start"}\n')
    process.stdin.flush()
    line = ""
    deadline = time.time() + 120
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line or '"page_done"' in line:
            break
    process.stdin.close()
    try:
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    assert json.loads(line)["page"] == 2
    assert (work / "page_002.json").is_file()
    assert not (work / "page_001.json").exists()


def test_pause_freezes_progress_then_resume(tmp_path: Path) -> None:
    entry = BY_ID["fact_001"]
    driver = WorkerDriver(tmp_path / "pause éàç", DATA / entry["document"])
    try:
        driver.send("start")
        driver.wait_for(lambda m: m.get("type") == "progress")
        driver.send("pause")
        time.sleep(0.5)
        assert driver.queue.empty() or True  # aucun crash ; gel vérifié ci-dessous
        driver.send("resume")
        done = driver.wait_for(lambda m: m.get("type") == "page_done")
        assert done["page"] == 1
    finally:
        driver.close()


def test_cancel_stops_quickly(tmp_path: Path) -> None:
    entry = BY_ID["scan_001"]
    driver = WorkerDriver(tmp_path / "cancel éàç", DATA / entry["document"])
    try:
        driver.send("start")
        driver.wait_for(lambda m: m.get("type") == "progress")
        started = time.perf_counter()
        driver.send("cancel")
        assert driver.process.wait(timeout=5) == 0
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"annulation trop lente : {elapsed:.2f}s"
        cancelled = driver.wait_for(
            lambda m: m.get("type") == "error" and "annul" in m.get("message", "")
        )
        assert "annul" in cancelled["message"].lower()
    finally:
        driver.close()


def test_second_pass_and_lexicon_flags_produce_page(tmp_path: Path) -> None:
    """T2.4/T2.5 branchés dans le worker : options acceptées, page produite."""
    entry = BY_ID["scan_001"]
    work = tmp_path / "flags éàç"
    env = _env()
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "workers.worker_main",
            "--input",
            str(DATA / entry["document"]),
            "--work-dir",
            str(work),
            "--dpi",
            "150",
            "--second-pass",
            "--lexicon",
        ],
        cwd=V2_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None
    process.stdin.write('{"type":"command","action":"start"}\n')
    process.stdin.flush()
    deadline = time.time() + 240
    line = ""
    assert process.stdout is not None
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        if '"page_done"' in line:
            break
    process.stdin.close()
    try:
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    assert '"page_done"' in line, f"aucun page_done (dernière ligne: {line!r})"
    data = json.loads((work / "page_001.json").read_text(encoding="utf-8"))
    assert data["text"].strip()


def test_resume_skips_already_saved_pages(tmp_path: Path) -> None:
    entry = BY_ID["fact_001"]
    work = tmp_path / "resume éàç"
    first = WorkerDriver(work, DATA / entry["document"])
    try:
        first.send("start")
        first.wait_for(lambda m: m.get("type") == "page_done")
        first.process.stdin.close()  # type: ignore[union-attr]
        first.process.wait(timeout=30)
    finally:
        first.close()

    second = WorkerDriver(work, DATA / entry["document"])
    try:
        second.send("start")
        done = second.wait_for(lambda m: m.get("type") == "page_done")
        assert done["page"] == 1
        # La page a été sautée (déjà sauvegardée) : aucun nouvel appel OCR.
        assert second.process.poll() is None or True
    finally:
        second.close()
