"""Client QProcess du worker OCR.

R2 : le worker tourne toujours dans un processus séparé ; l'interface ne fait
aucun OCR. Ce client lance ``python -m ocr_app.workers.fake_worker``, lit les
lignes JSON du protocole sur stdout et écrit les commandes sur stdin.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QByteArray,
    QObject,
    QProcess,
    QProcessEnvironment,
    QTimer,
    Signal,
)

from ocr_app.core.protocol import CommandMessage, MessageType, parse_line

logger = logging.getLogger(__name__)

WORKER_MODULE = "ocr_app.workers.fake_worker"


class WorkerClient(QObject):
    """Pilote un worker en sous-processus et expose ses messages via signaux."""

    progress = Signal(dict)
    page_done = Signal(dict)
    error = Signal(dict)
    started = Signal()
    finished = Signal(int, bool)  # code de sortie, sortie normale
    failed = Signal(str)
    log_line = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(
            QProcess.ProcessChannelMode.SeparateChannels
        )
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error_occurred)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)

        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._finalized = False

        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_terminate)

    # --- Cycle de vie ----------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    def start(
        self,
        total_pages: int,
        work_dir: Path,
        stage_delay: float = 0.05,
    ) -> bool:
        """Lance le worker factice pour ``total_pages`` pages."""
        if self.is_running:
            return False

        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._finalized = False

        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUNBUFFERED", "1")

        # Rendre le paquet importable même sans installation éditable.
        src_dir = Path(__file__).resolve().parents[2]
        existing = environment.value("PYTHONPATH", "")
        parts = [str(src_dir)]
        if existing:
            parts.append(existing)
        environment.insert("PYTHONPATH", os.pathsep.join(parts))
        self._process.setProcessEnvironment(environment)

        self._process.setWorkingDirectory(str(work_dir))
        arguments = [
            "-u",
            "-m",
            WORKER_MODULE,
            "--pages",
            str(max(1, int(total_pages))),
            "--work-dir",
            str(work_dir),
            "--stage-delay",
            f"{max(0.0, float(stage_delay)):g}",
        ]
        logger.debug("lancement du worker : %s %s", sys.executable, arguments)
        self._process.start(sys.executable, arguments)
        return True

    def send_command(self, action: str, page: Optional[int] = None) -> bool:
        """Écrit une commande JSON (pause, resume, cancel, start) sur stdin."""
        if self._process.state() != QProcess.ProcessState.Running:
            return False
        payload = CommandMessage(action=action, page=page).to_json() + "\n"
        written = self._process.write(QByteArray(payload.encode("utf-8")))
        return written >= 0

    def request_cancel(self, grace_ms: int = 3000) -> None:
        """Demande l'annulation puis force l'arrêt si le worker ne répond pas."""
        if not self.is_running:
            return
        self.send_command("cancel")
        self._kill_timer.start(max(250, int(grace_ms)))

    def stop(self, grace_ms: int = 1500) -> None:
        """Arrêt propre et bloquant, utilisé à la fermeture de l'application."""
        self._kill_timer.stop()
        state = self._process.state()
        if state == QProcess.ProcessState.Running:
            self.send_command("cancel")
            if not self._process.waitForFinished(max(250, int(grace_ms))):
                self._process.kill()
                self._process.waitForFinished(500)
        elif state != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(500)

    # --- Signaux QProcess -------------------------------------------------
    def _on_started(self) -> None:
        self.send_command("start", page=1)
        self.started.emit()

    def _on_finished(
        self, exit_code: int, exit_status: QProcess.ExitStatus
    ) -> None:
        normal = exit_status == QProcess.ExitStatus.NormalExit
        self._finalize(exit_code, normal)

    def _on_error_occurred(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            message = self._process.errorString()
            logger.warning("worker impossible à démarrer : %s", message)
            self.failed.emit(message)
            self._finalize(-1, False)
        elif error == QProcess.ProcessError.Crashed:
            logger.warning("worker interrompu : %s", self._process.errorString())
            self.log_line.emit(self._process.errorString())
        elif error == QProcess.ProcessError.Timedout:
            logger.warning("worker : délai dépassé")
        elif error == QProcess.ProcessError.WriteError:
            logger.warning("worker : erreur d'écriture sur stdin")

    def _finalize(self, exit_code: int, normal: bool) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._kill_timer.stop()
        self._flush_stdout()
        self._flush_stderr()
        self.finished.emit(exit_code, normal)

    # --- Lecture du protocole --------------------------------------------
    def _read_stdout(self) -> None:
        raw = bytes(self._process.readAllStandardOutput())
        if not raw:
            return
        self._stdout_buffer += raw.decode("utf-8", errors="replace")
        self._consume_buffer()

    def _consume_buffer(self) -> None:
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            self._handle_line(line)

    def _flush_stdout(self) -> None:
        self._consume_buffer()
        remainder = self._stdout_buffer.strip()
        self._stdout_buffer = ""
        if remainder:
            self._handle_line(remainder)

    def _handle_line(self, raw: str) -> None:
        line = raw.strip().lstrip("\ufeff")
        if not line:
            return
        try:
            data = parse_line(line)
        except ValueError:
            logger.debug("ligne hors protocole ignorée : %r", line)
            return
        if not isinstance(data, dict):
            return

        message_type = data.get("type")
        if message_type == MessageType.PROGRESS.value:
            try:
                payload = {
                    "page": int(data.get("page", 0)),
                    "total": int(data.get("total", 0)),
                    "stage": str(data.get("stage", "")),
                }
            except (TypeError, ValueError):
                return
            self.progress.emit(payload)
        elif message_type == MessageType.PAGE_DONE.value:
            try:
                payload = {
                    "page": int(data.get("page", 0)),
                    "result_path": str(data.get("result_path", "")),
                }
            except (TypeError, ValueError):
                return
            self.page_done.emit(payload)
        elif message_type == MessageType.ERROR.value:
            try:
                page = int(data.get("page", 0))
            except (TypeError, ValueError):
                page = 0
            self.error.emit(
                {"page": page, "message": str(data.get("message", ""))}
            )

    def _read_stderr(self) -> None:
        raw = bytes(self._process.readAllStandardError())
        if not raw:
            return
        self._stderr_buffer += raw.decode("utf-8", errors="replace")
        while "\n" in self._stderr_buffer:
            line, self._stderr_buffer = self._stderr_buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                logger.debug("[worker] %s", line)
                self.log_line.emit(line)

    def _flush_stderr(self) -> None:
        remainder = self._stderr_buffer.strip()
        self._stderr_buffer = ""
        if remainder:
            logger.debug("[worker] %s", remainder)
            self.log_line.emit(remainder)

    # --- Arrêt forcé ------------------------------------------------------
    def _force_terminate(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            logger.warning("le worker ne répond pas : terminaison forcée")
            self._process.terminate()
            QTimer.singleShot(400, self._force_kill)

    def _force_kill(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
