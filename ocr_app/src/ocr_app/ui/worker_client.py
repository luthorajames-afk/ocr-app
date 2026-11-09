"""Client QProcess du worker OCR.

R2 : le worker tourne toujours dans un processus séparé ; l'interface ne fait
aucun OCR. Par défaut, ce client lance ``python -m ocr_app.workers.fake_worker``
(worker de développement) ; une commande réelle peut être fournie par mode via
``worker_command`` / ``start(command=...)`` (ex. ``worker_advanced.exe``).

Le protocole est lu par lignes JSON sur stdout et les commandes sont écrites sur
stdin. Deux précautions importantes pour les vrais workers :

- les octets sont décodés en UTF-8 **incrémentalement** (une séquence accentuée
  peut être coupée entre deux notifications ``readyRead``) ;
- les tampons sont bornés : un worker qui écrirait une ligne sans fin ne peut
  pas faire grossir la mémoire de l'interface indéfiniment.
"""

from __future__ import annotations

import codecs
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from PySide6.QtCore import (
    QByteArray,
    QCoreApplication,
    QObject,
    QProcess,
    QProcessEnvironment,
    QTimer,
    Signal,
)

from ocr_app.core.protocol import CommandMessage, MessageType, parse_line

logger = logging.getLogger(__name__)

WORKER_MODULE = "ocr_app.workers.fake_worker"

# Une ligne de protocole doit rester courte. Une ligne sans fin (worker cassé ou
# binaire étranger qui écrit sur stdout) ne doit pas faire grossir le tampon
# sans limite dans le processus de l'interface.
MAX_PROTOCOL_BUFFER = 1024 * 1024  # 1 Mio de texte en attente
MAX_STDERR_BUFFER = 256 * 1024  # 256 Kio de diagnostics en attente

# Délais d'annulation : cancel doit être effectif en moins de 2 s (R2).
DEFAULT_CANCEL_GRACE_MS = 1000
FORCE_TERMINATE_GRACE_MS = 400
FORCE_KILL_GRACE_MS = 500


def _normalise_command(
    command: Optional[Sequence[str]],
) -> Optional[tuple[str, ...]]:
    """Valide une commande de worker (liste non vide de chaînes)."""
    if command is None:
        return None
    if isinstance(command, (str, bytes)):
        raise TypeError("la commande du worker doit être une séquence, pas une chaîne")
    parts = tuple(str(part) for part in command)
    if not parts:
        raise ValueError("la commande du worker ne peut pas être vide")
    return parts


class WorkerClient(QObject):
    """Pilote un worker en sous-processus et expose ses messages via signaux."""

    progress = Signal(dict)
    page_done = Signal(dict)
    error = Signal(dict)
    started = Signal()
    finished = Signal(int, bool)  # code de sortie, sortie normale
    failed = Signal(str)
    log_line = Signal(str)

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        worker_command: Optional[Sequence[str]] = None,
    ) -> None:
        """``worker_command`` : commande complète du worker (préfixe).

        Exemples : ``[sys.executable, "-u", "-m", "ocr_app.workers.simple"]``
        ou ``[r"C:\\...\\worker_advanced.exe"]``. Sans commande, le worker
        factice de développement est utilisé.
        """
        super().__init__(parent)
        self._worker_command = _normalise_command(worker_command)

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
        self._stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._finalized = False

        # Fin du délai après « cancel » : le worker n'a pas répondu.
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_terminate)

        # Fin du délai après « terminate » : terminaison forcée.
        self._terminate_timer = QTimer(self)
        self._terminate_timer.setSingleShot(True)
        self._terminate_timer.setInterval(FORCE_TERMINATE_GRACE_MS)
        self._terminate_timer.timeout.connect(self._force_kill)

        # Filet de sécurité hors TaskRunner : à la fermeture de l'application,
        # aucun worker QProcess ne doit rester vivant.
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    # --- Cycle de vie ----------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    @property
    def process_id(self) -> int:
        """PID du worker (0 si aucun processus n'est lancé)."""
        return int(self._process.processId())

    def start(
        self,
        total_pages: int,
        work_dir: Path,
        stage_delay: float = 0.05,
        *,
        command: Optional[Sequence[str]] = None,
    ) -> bool:
        """Lance le worker pour ``total_pages`` pages.

        ``command`` remplace (pour cet appel) la commande configurée sur le
        client. Les arguments de protocole (``--pages``, ``--work-dir``,
        ``--stage-delay``) sont ajoutés à la suite : un worker réel doit
        accepter ces options, comme le worker factice.
        """
        if self.is_running:
            return False

        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._finalized = False
        self._kill_timer.stop()
        self._terminate_timer.stop()

        prefix = _normalise_command(command) or self._worker_command
        if prefix is None:
            prefix = (sys.executable, "-u", "-m", WORKER_MODULE)

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
            *prefix[1:],
            "--pages",
            str(max(1, int(total_pages))),
            "--work-dir",
            str(work_dir),
            "--stage-delay",
            f"{max(0.0, float(stage_delay)):g}",
        ]
        logger.debug("lancement du worker : %s %s", prefix[0], arguments)
        self._process.start(prefix[0], arguments)
        return True

    def send_command(self, action: str, page: Optional[int] = None) -> bool:
        """Écrit une commande JSON (pause, resume, cancel, start) sur stdin."""
        if self._process.state() != QProcess.ProcessState.Running:
            return False
        payload = CommandMessage(action=action, page=page).to_json() + "\n"
        written = self._process.write(QByteArray(payload.encode("utf-8")))
        if written < 0:
            logger.warning("commande « %s » non écrite sur stdin du worker", action)
            return False
        return True

    def request_cancel(self, grace_ms: int = DEFAULT_CANCEL_GRACE_MS) -> bool:
        """Demande l'annulation puis force l'arrêt si le worker ne répond pas.

        Le délai de grâce (1 s par défaut) laisse au worker le temps d'écrire
        son message ``error`` d'annulation ; au-delà, ``terminate`` puis
        ``kill`` garantissent la fin du processus en moins de 2 s.
        """
        if not self.is_running:
            return False
        sent = self.send_command("cancel")
        # Même si l'écriture a échoué, le processus doit être arrêté.
        self._kill_timer.start(max(250, int(grace_ms)))
        return sent

    def stop(self, grace_ms: int = DEFAULT_CANCEL_GRACE_MS) -> None:
        """Arrêt propre et bloquant, utilisé à la fermeture de l'application.

        Envoie ``cancel``, attend ``grace_ms``, puis ``terminate`` et enfin
        ``kill``. Ne retourne jamais en laissant le processus vivant (sauf
        échec du système d'exploitation, journalisé).
        """
        self._kill_timer.stop()
        self._terminate_timer.stop()
        state = self._process.state()
        if state == QProcess.ProcessState.Running:
            self.send_command("cancel")
            if not self._process.waitForFinished(max(250, int(grace_ms))):
                self._process.terminate()
                if not self._process.waitForFinished(FORCE_TERMINATE_GRACE_MS):
                    logger.warning("le worker ne répond pas : terminaison forcée")
                    self._kill_process()
        elif state != QProcess.ProcessState.NotRunning:
            # Démarrage en cours : attendre que le processus existe réellement
            # avant de le tuer (kill() est sans effet sur un état Starting).
            self._kill_process()

    def _kill_process(self) -> None:
        if self._process.state() == QProcess.ProcessState.NotRunning:
            return
        if self._process.state() == QProcess.ProcessState.Starting:
            self._process.waitForStarted(FORCE_KILL_GRACE_MS)
        self._process.kill()
        if not self._process.waitForFinished(FORCE_KILL_GRACE_MS):
            logger.error("worker pid %s toujours vivant après kill", self.process_id)

    def close(self) -> None:
        """Alias explicite de :meth:`stop` pour les fermetures d'application."""
        self.stop()

    # --- Signaux QProcess -------------------------------------------------
    def _on_started(self) -> None:
        if self._kill_timer.isActive():
            # Annulation demandée avant la fin du démarrage : ne pas lancer le
            # traitement, demander l'arrêt propre au worker.
            logger.debug("start ignoré : annulation déjà demandée")
            self.send_command("cancel")
            return
        if not self.send_command("start", page=1):
            message = "impossible d'envoyer la commande start au worker"
            logger.warning(message)
            self.failed.emit(message)
            self._process.kill()
            return
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
        elif error == QProcess.ProcessError.ReadError:
            logger.warning("worker : erreur de lecture sur stdout/stderr")

    def _finalize(self, exit_code: int, normal: bool) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._kill_timer.stop()
        self._terminate_timer.stop()
        self._flush_stdout()
        self._flush_stderr()
        # Ne pas fermer le QProcess ici : un récepteur de « finished » peut
        # démarrer immédiatement le document suivant sur le même objet.
        self.finished.emit(exit_code, normal)

    # --- Lecture du protocole --------------------------------------------
    def _read_stdout(self) -> None:
        raw = bytes(self._process.readAllStandardOutput())
        if raw:
            self._feed_stdout(raw)

    def _feed_stdout(self, raw: bytes) -> None:
        """Décode et découpe un morceau de stdout (testable sans processus)."""
        text = self._stdout_decoder.decode(raw)
        if not text:
            return
        self._stdout_buffer += text
        self._consume_buffer()
        if len(self._stdout_buffer) > MAX_PROTOCOL_BUFFER:
            logger.warning(
                "ligne de protocole trop longue (%d caractères) : tampon vidé",
                len(self._stdout_buffer),
            )
            self._stdout_buffer = ""

    def _consume_buffer(self) -> None:
        if "\n" not in self._stdout_buffer:
            return
        # split() en une passe (O(n)) au lieu d'un split/recopie par ligne.
        *lines, self._stdout_buffer = self._stdout_buffer.split("\n")
        for line in lines:
            self._handle_line(line)

    def _flush_stdout(self) -> None:
        tail = self._stdout_decoder.decode(b"", final=True)
        if tail:
            self._stdout_buffer += tail
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
            page = data.get("page")
            total = data.get("total")
            stage = data.get("stage")
            if (
                not _is_int(page)
                or not _is_int(total)
                or page < 1
                or total < 1
                or not isinstance(stage, str)
            ):
                logger.debug("message progress invalide ignoré : %r", data)
                return
            self.progress.emit({"page": page, "total": total, "stage": stage})
        elif message_type == MessageType.PAGE_DONE.value:
            page = data.get("page")
            result_path = data.get("result_path")
            if (
                not _is_int(page)
                or page < 1
                or not isinstance(result_path, str)
                or not result_path.strip()
            ):
                logger.debug("message page_done invalide ignoré : %r", data)
                return
            self.page_done.emit({"page": page, "result_path": result_path})
        elif message_type == MessageType.ERROR.value:
            page = data.get("page", 0)
            message = data.get("message", "")
            if not _is_int(page) or page < 0:
                page = 0
            if not isinstance(message, str):
                message = ""
            self.error.emit({"page": page, "message": message})

    def _read_stderr(self) -> None:
        raw = bytes(self._process.readAllStandardError())
        if raw:
            self._feed_stderr(raw)

    def _feed_stderr(self, raw: bytes) -> None:
        """Décode et découpe un morceau de stderr (borné, jamais bloquant)."""
        text = self._stderr_decoder.decode(raw)
        if not text:
            return
        self._stderr_buffer += text
        if "\n" in self._stderr_buffer:
            *lines, self._stderr_buffer = self._stderr_buffer.split("\n")
            for line in lines:
                line = line.rstrip("\r")
                if line:
                    logger.debug("[worker] %s", line)
                    self.log_line.emit(line)
        if len(self._stderr_buffer) > MAX_STDERR_BUFFER:
            logger.warning(
                "ligne stderr trop longue (%d caractères) : tronquée",
                len(self._stderr_buffer),
            )
            self.log_line.emit(self._stderr_buffer[:4096])
            self._stderr_buffer = ""

    def _flush_stderr(self) -> None:
        tail = self._stderr_decoder.decode(b"", final=True)
        if tail:
            self._stderr_buffer += tail
        remainder = self._stderr_buffer.strip()
        self._stderr_buffer = ""
        if remainder:
            logger.debug("[worker] %s", remainder)
            self.log_line.emit(remainder)

    # --- Arrêt forcé ------------------------------------------------------
    def _force_terminate(self) -> None:
        state = self._process.state()
        if state == QProcess.ProcessState.Starting:
            # terminate() n'a pas de cible tant que le processus n'existe pas.
            self._kill_process()
            return
        if state != QProcess.ProcessState.NotRunning:
            logger.warning("le worker ne répond pas : terminaison forcée")
            self._process.terminate()
            self._terminate_timer.start(FORCE_TERMINATE_GRACE_MS)

    def _force_kill(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            logger.warning("le worker ne répond pas : kill")
            self._kill_process()


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
