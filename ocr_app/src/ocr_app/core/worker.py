"""Base des workers OCR.

Un worker est un processus séparé qui traite une page à la fois et communique
avec l'interface via le protocole JSON (lignes sur stdin/stdout).

Règle R2 : l'interface ne fait jamais d'OCR. Règle R5 : le worker Avancé reste
vivant pendant tout le document.

La lecture de stdout se fait dans un thread dédié (``start_subprocess``) : la
boucle de lecture ne bloque donc ni l'interface ni les callbacks. ``stop()``
ferme stdin (arrêt propre), puis termine et finalement tue le processus si
nécessaire, en récupérant toujours son code de sortie pour ne pas laisser de
processus orphelin.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ocr_app.core.data_model import Page
from ocr_app.core.protocol import (
    CommandMessage,
    ErrorMessage,
    MessageType,
    PageDoneMessage,
    ProgressMessage,
    parse_line,
)

logger = logging.getLogger(__name__)

# Nombre de lignes de stderr conservées pour le diagnostic.
_STDERR_MAX_LINES = 200
# Durée maximale d'attente d'un thread de lecture à l'arrêt.
_THREAD_JOIN_TIMEOUT = 5.0


@dataclass
class WorkerResult:
    page: int
    page_obj: Optional[Page]
    stage: str
    error: Optional[str] = None


class BaseWorker(ABC):
    """Classe de base d'un worker OCR. À spécialiser par mode."""

    def __init__(self, mode: str, models_dir: Optional[str] = None) -> None:
        self.mode = mode
        self.models_dir = models_dir
        self._running = False
        self._on_progress: Optional[Callable[[ProgressMessage], None]] = None
        self._on_page_done: Optional[Callable[[PageDoneMessage], None]] = None
        self._on_error: Optional[Callable[[ErrorMessage], None]] = None

        self._proc: Optional[subprocess.Popen[str]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_lines: deque[str] = deque(maxlen=_STDERR_MAX_LINES)
        self._lock = threading.RLock()
        # Sérialise démarrage / arrêt : deux appels concurrents à stop() (UI et
        # atexit) ne doivent pas fermer deux fois les mêmes pipes.
        self._lifecycle = threading.RLock()
        self._stopped = threading.Event()

    # --- Callbacks -------------------------------------------------------
    def on_progress(self, cb: Callable[[ProgressMessage], None]) -> None:
        self._on_progress = cb

    def on_page_done(self, cb: Callable[[PageDoneMessage], None]) -> None:
        self._on_page_done = cb

    def on_error(self, cb: Callable[[ErrorMessage], None]) -> None:
        self._on_error = cb

    # --- API -------------------------------------------------------------
    @abstractmethod
    def process_page(self, page_input: Any, page_number: int) -> Page:
        """Traite une page et retourne un objet Page. À implémenter."""
        raise NotImplementedError

    @abstractmethod
    def initialize(self) -> None:
        """Charge les modèles une fois (worker Avancé garde les modèles en vie)."""
        raise NotImplementedError

    @abstractmethod
    def shutdown(self) -> None:
        """Libère les ressources du worker."""
        raise NotImplementedError

    # --- Sous-processus ---------------------------------------------------
    def start_subprocess(
        self,
        script: str,
        args: Optional[list[str]] = None,
    ) -> None:
        """Lance le worker et démarre la lecture du protocole en arrière-plan.

        Ne bloque pas : les messages sont délivrés aux callbacks depuis un
        thread dédié. Pour attendre la fin, utiliser :meth:`wait`.

        Les callbacks sont donc appelés depuis un thread de fond : une
        interface graphique doit les remettre dans son thread principal
        (par exemple via un signal Qt).

        ``script`` peut être un fichier ``.py`` (exécuté par l'interpréteur
        courant, avec ``-u`` pour un stdout non bufferisé) ou un exécutable
        autonome (worker packagé PyInstaller, R3).
        """
        with self._lifecycle:
            with self._lock:
                old = self._proc
                old_reader = self._reader_thread
                old_stderr = self._stderr_thread
            if old is not None and old.poll() is None:
                raise RuntimeError("un worker est déjà en cours d'exécution")
            if old is not None:
                # Redémarrage après fin/crash : libérer les tubes de l'ancien
                # processus avant de réaffecter _proc.
                self._close_streams(old)
                self._join_thread(old_reader, timeout=1.0)
                self._join_thread(old_stderr, timeout=1.0)

            cmd = self._build_command(script, args or [])
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            reader = threading.Thread(
                target=self._read_loop,
                args=(proc,),
                name=f"{self.mode}-worker-stdout",
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(proc,),
                name=f"{self.mode}-worker-stderr",
                daemon=True,
            )

            with self._lock:
                self._proc = proc
                self._reader_thread = reader
                self._stderr_thread = stderr_thread
                self._running = True
                self._stopped.clear()
                self._stderr_lines.clear()

            stderr_thread.start()
            reader.start()

    @staticmethod
    def _build_command(script: str, args: list[str]) -> list[str]:
        script_path = Path(script)
        # Un exécutable packagé se lance directement ; un script Python passe
        # par l'interpréteur courant (et -u pour un protocole réactif).
        is_python_script = script_path.suffix.lower() == ".py" or not script_path.exists()
        prefix = [sys.executable, "-u"] if is_python_script else []
        return [*prefix, str(script), *(str(arg) for arg in args)]

    def run_subprocess(
        self,
        script: str,
        args: Optional[list[str]] = None,
        *,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Optional[int]:
        """Lance le worker comme processus séparé avec protocole JSON.

        Par défaut, retourne immédiatement après le démarrage (lecture en
        arrière-plan). ``wait=True`` attend la fin du processus et retourne son
        code de sortie.
        """
        self.start_subprocess(script, args)
        if not wait:
            return None
        return self.wait(timeout=timeout)

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """Attend la fin du worker et retourne son code de sortie."""
        with self._lock:
            proc = self._proc
            reader = self._reader_thread
            stderr_thread = self._stderr_thread
        if proc is None:
            return None
        code = proc.wait(timeout=timeout)
        self._join_thread(reader, timeout=timeout)
        self._join_thread(stderr_thread, timeout=timeout)
        return code

    def send_command(self, action: str, page: Optional[int] = None) -> bool:
        """Envoie une commande au worker. Retourne False si le canal est fermé."""
        with self._lock:
            proc = self._proc
            if not self._running or proc is None or proc.stdin is None:
                return False
            message = CommandMessage(action=action, page=page)
            try:
                proc.stdin.write(message.to_json() + "\n")
                proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError, ValueError):
                self._running = False
                return False

    def cancel(self, timeout: float = 1.0) -> Optional[int]:
        """Annule le document en cours sans laisser le worker tourner.

        Écrit ``cancel`` sur stdin (le worker peut ainsi émettre son message
        ``error`` d'annulation et libérer ses ressources), attend ``timeout``
        puis retombe sur :meth:`stop` (terminate/kill) si nécessaire.

        Contrairement à :meth:`stop`, les messages finaux émis par le worker
        sont délivrés aux callbacks avant la fermeture des flux.
        """
        with self._lock:
            proc = self._proc
            reader = self._reader_thread
        if proc is None:
            return None
        if not self.send_command("cancel"):
            return self.stop(timeout=timeout)
        code = self._wait_quietly(proc, max(0.0, float(timeout)))
        if code is None:
            # Le worker ignore l'annulation : escalade immédiate.
            return self.stop(timeout=0.0)
        # Processus terminé : laisser le thread de lecture consommer les
        # derniers messages (error d'annulation) avant de fermer les flux.
        self._join_thread(reader, timeout=1.0)
        return self.stop(timeout=0.0)

    def stop(self, timeout: float = 3.0) -> Optional[int]:
        """Arrête le worker sans laisser de processus orphelin.

        Séquence : fermeture de stdin (EOF = arrêt propre), attente, puis
        ``terminate``, puis ``kill``. ``timeout`` est le budget total accordé
        au worker pour sortir proprement ; la terminaison forcée ajoute au
        plus ~1 s. Le processus est toujours récupéré (``wait``) et les threads
        de lecture sont joints.
        """
        with self._lifecycle:
            with self._lock:
                proc = self._proc
                reader = self._reader_thread
                stderr_thread = self._stderr_thread
                self._running = False
                self._stopped.set()

            if proc is None:
                self._join_thread(reader, timeout=timeout)
                self._join_thread(stderr_thread, timeout=timeout)
                return None

            deadline = time.monotonic() + max(0.0, float(timeout))

            # 1) Arrêt propre : EOF sur stdin pour un worker qui lit ses commandes.
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass

            code = self._wait_quietly(proc, self._remaining(deadline))
            if code is None:
                self._signal(proc, "terminate")
                code = self._wait_quietly(proc, self._remaining(deadline))
            if code is None:
                self._signal(proc, "kill")
                # Après kill, le système doit rendre la main rapidement ;
                # plafonner évite de bloquer l'interface indéfiniment.
                code = self._wait_quietly(
                    proc, max(0.5, min(1.0, self._remaining(deadline)))
                )
            if code is None:
                logger.warning(
                    "le worker %s (pid %s) ne répond pas après kill",
                    self.mode,
                    proc.pid,
                )

            self._join_thread(reader, timeout=timeout)
            self._join_thread(stderr_thread, timeout=timeout)
            self._close_streams(proc)

            with self._lock:
                if self._proc is proc:
                    self._proc = None
                    self._reader_thread = None
                    self._stderr_thread = None
            return code

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.0, deadline - time.monotonic())

    @staticmethod
    def _close_streams(proc: subprocess.Popen[str]) -> None:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    @staticmethod
    def _wait_quietly(proc: subprocess.Popen[str], timeout: float) -> Optional[int]:
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    @staticmethod
    def _signal(proc: subprocess.Popen[str], name: str) -> None:
        try:
            getattr(proc, name)()
        except (OSError, ProcessLookupError):
            pass

    @staticmethod
    def _join_thread(
        thread: Optional[threading.Thread], timeout: Optional[float] = None
    ) -> None:
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_THREAD_JOIN_TIMEOUT if timeout is None else timeout)

    # --- Lecture des messages ---------------------------------------------
    def _drain_stderr(self, proc: subprocess.Popen[str]) -> None:
        """Vide stderr en continu : évite le blocage du worker sur un pipe plein."""
        if proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                with self._lock:
                    # Un worker précédent peut encore se vider : ne pas polluer
                    # le diagnostic du worker courant.
                    if self._proc is proc:
                        self._stderr_lines.append(raw.rstrip("\r\n"))
        except (OSError, ValueError):
            pass

    def _read_loop(self, proc: subprocess.Popen[str]) -> None:
        if proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                # Après stop(), on continue à vider le pipe (pour ne pas bloquer
                # le worker) mais on ne délivre plus de callbacks.
                if self._stopped.is_set() or self._proc is not proc:
                    continue
                try:
                    data = parse_line(raw)
                except ValueError:
                    logger.debug("ligne de protocole ignorée : %r", raw)
                    continue
                self._dispatch(data)
        except (OSError, ValueError):
            logger.debug("lecture du protocole interrompue", exc_info=True)
        finally:
            # Récupérer le code de sortie (pas de zombie POSIX) et ne pas
            # écraser l'état d'un worker plus récent.
            proc.poll()
            with self._lock:
                if self._proc is proc:
                    self._running = False

    def _dispatch(self, data: dict) -> None:
        mtype = data.get("type")
        try:
            if mtype == MessageType.PROGRESS.value:
                callback = self._on_progress
                if callback is None:
                    return
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
                callback(ProgressMessage(page=page, total=total, stage=stage))
            elif mtype == MessageType.PAGE_DONE.value:
                callback = self._on_page_done
                if callback is None:
                    return
                page = data.get("page")
                result_path = data.get("result_path")
                if (
                    not _is_int(page)
                    or page < 1
                    or not isinstance(result_path, str)
                    or not result_path
                ):
                    logger.debug("message page_done invalide ignoré : %r", data)
                    return
                callback(PageDoneMessage(page=page, result_path=result_path))
            elif mtype == MessageType.ERROR.value:
                callback = self._on_error
                if callback is None:
                    return
                page = data.get("page", 0)
                message = data.get("message", "")
                if not _is_int(page) or page < 0 or not isinstance(message, str):
                    logger.debug("message error invalide ignoré : %r", data)
                    return
                callback(ErrorMessage(page=page, message=message))
        except Exception:  # un callback défaillant ne doit pas tuer la lecture
            logger.exception("erreur dans un callback du worker %s", self.mode)

    # --- Diagnostic -------------------------------------------------------
    @property
    def is_running(self) -> bool:
        proc = self._proc
        return self._running and proc is not None and proc.poll() is None

    def stderr_text(self) -> str:
        with self._lock:
            return "\n".join(self._stderr_lines)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class WorkerProcess(BaseWorker):
    """Superviseur générique d'un worker externe (R2, R3).

    Ce mode ne réalise aucun OCR dans le processus appelant : il lance le
    script/exécutable fourni, lit son protocole et l'arrête proprement. Les
    workers concrets (simple/medium/advanced) héritent de :class:`BaseWorker`
    et implémentent ``process_page``/``initialize``/``shutdown`` ; ce
    superviseur sert aux appelants qui veulent seulement piloter un worker
    déjà packagé.
    """

    def __init__(
        self,
        mode: str,
        script: str,
        args: Optional[list[str]] = None,
        models_dir: Optional[str] = None,
    ) -> None:
        super().__init__(mode=mode, models_dir=models_dir)
        self._script = script
        self._args = list(args or [])

    def initialize(self) -> None:
        """Démarre le processus worker (une fois par document, R5)."""
        self.start_subprocess(self._script, self._args)

    def process_page(self, page_input: Any, page_number: int) -> Page:
        raise RuntimeError(
            "WorkerProcess délègue le traitement des pages au sous-processus"
        )

    def shutdown(self) -> None:
        """Arrête le processus worker sans laisser d'orphelin."""
        self.stop()
