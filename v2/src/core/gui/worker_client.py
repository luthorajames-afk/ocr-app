"""T4.2 — Client asynchrone du worker (QProcess, JSON Lines).

L'IHM ne fait aucun calcul d'OCR : elle pilote ``workers.worker_main`` et lit
les événements de protocole (T3.1) sur stdout.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from core.pipeline.protocol import (
    ErrorMessage,
    PageDoneMessage,
    ProgressMessage,
    ProtocolError,
    parse_line,
)


def medium_python() -> str:
    """Interpréteur du module Moyen : venv dédié (R13), sinon noyau."""
    override = os.environ.get("AOCRI_MEDIUM_VENV")
    candidates: list[Path] = []
    if override:
        base = Path(override)
        candidates.append(base / "Scripts" / "python.exe" if base.is_dir() else base)
    root = Path(__file__).resolve().parents[3]
    candidates.append(root.parent / "ocr_app" / ".venv-eval-medium" / "Scripts" / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def advanced_python() -> str:
    """Interpréteur du module Avancé (Docling) : venv dédié (R13)."""
    override = os.environ.get("AOCRI_ADVANCED_VENV")
    candidates: list[Path] = []
    if override:
        base = Path(override)
        candidates.append(base / "Scripts" / "python.exe" if base.is_dir() else base)
    root = Path(__file__).resolve().parents[3]
    candidates.append(
        root.parent / "ocr_app" / ".venv-eval-advanced" / "Scripts" / "python.exe"
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def medium_available() -> bool:
    return Path(medium_python()).resolve() != Path(sys.executable).resolve()


def advanced_available() -> bool:
    return Path(advanced_python()).resolve() != Path(sys.executable).resolve()


class WorkerClient(QObject):
    """Un sous-processus worker par document (R5 : vivant tout le long)."""

    progress = Signal(int, int, str)   # page, total, stage
    page_done = Signal(int, str)       # page, result_path
    error = Signal(str)                # message
    finished = Signal(int)             # code de sortie
    log_line = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._on_finished)
        self._buffer = ""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(
        self,
        input_path: str | Path,
        work_dir: str | Path,
        dpi: int = 300,
        mode: str = "simple",
        languages: str = "fra+eng",
        page: int | None = None,
        second_pass: bool = False,
        best_variant: bool = False,
        lexicon: bool = False,
    ) -> bool:
        if self._running:
            return False
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            self._process.waitForFinished(2000)
        self._buffer = ""
        if mode == "medium":
            program = medium_python()
        elif mode in ("advanced", "approfondi"):
            program = advanced_python()
        else:
            program = sys.executable
        arguments = [
            "-u",
            "-m",
            "workers.worker_main",
            "--input",
            str(input_path),
            "--work-dir",
            str(work_dir),
            "--dpi",
            str(dpi),
            "--mode",
            mode,
            "--lang",
            languages,
        ]
        if page is not None:
            arguments += ["--page", str(page)]
        if second_pass:
            arguments.append("--second-pass")
        if best_variant:
            arguments.append("--best-variant")
        if lexicon:
            arguments.append("--lexicon")
        env = QProcessEnvironment.systemEnvironment()
        src = str(Path(__file__).resolve().parents[3] / "src")
        existing = env.value("PYTHONPATH")
        env.insert("PYTHONPATH", src + (os.pathsep + existing if existing else ""))
        self._process.setProcessEnvironment(env)
        self._process.setWorkingDirectory(str(Path(__file__).resolve().parents[3]))
        self._process.start(program, arguments)
        if not self._process.waitForStarted(5000):
            self.error.emit("impossible de démarrer le worker")
            return False
        self._running = True
        self.send_command("start")
        return True

    def send_command(self, action: str) -> None:
        if not self._running:
            return
        payload = json.dumps({"type": "command", "action": action})
        self._process.write((payload + "\n").encode("utf-8"))

    def pause(self) -> None:
        self.send_command("pause")

    def resume(self) -> None:
        self.send_command("resume")

    def cancel(self) -> None:
        self.send_command("cancel")

    def stop(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(3000):
                self._process.kill()
        self._running = False

    # ------------------------------------------------------------- interne
    def _read_stdout(self) -> None:
        data = bytes(self._process.readAllStandardOutput().data()).decode("utf-8", "replace")
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                message = parse_line(line)
            except ProtocolError as exc:
                self.log_line.emit(f"protocole: {exc}")
                continue
            if isinstance(message, ProgressMessage):
                self.progress.emit(message.page, message.total, message.stage)
            elif isinstance(message, PageDoneMessage):
                self.page_done.emit(message.page, message.result_path)
            elif isinstance(message, ErrorMessage):
                self.error.emit(message.message)

    def _read_stderr(self) -> None:
        raw = self._process.readAllStandardError().data()
        data = bytes(raw).decode("utf-8", "replace")
        for line in data.splitlines():
            if line.strip():
                self.log_line.emit(line.strip())

    def _on_finished(self, code: int, _status: int) -> None:
        self._running = False
        self.finished.emit(int(code))
