"""Base des workers OCR.

Un worker est un processus séparé qui traite une page à la fois et communique
avec l'interface via le protocole JSON (lignes sur stdin/stdout).

Règle R2 : l'interface ne fait jamais d'OCR. Règle R5 : le worker Avancé reste
vivant pendant tout le document.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Callable, Any
import subprocess
import json
import os
import sys

from ocr_app.core.protocol import (
    MessageType,
    ProgressMessage,
    PageDoneMessage,
    ErrorMessage,
    CommandMessage,
    parse_line,
)
from ocr_app.core.data_model import Page


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

    def run_subprocess(self, script: str, args: list[str]) -> None:
        """Lance le worker comme processus séparé avec protocole JSON.

        L'interface écrit des CommandMessage sur stdin et lit les
        ProgressMessage/PageDoneMessage/ErrorMessage sur stdout.
        """
        cmd = [sys.executable, script, *args]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._running = True
        self._read_loop()

    def send_command(self, action: str, page: Optional[int] = None) -> None:
        """Envoie une commande au worker (pause, resume, cancel...)."""
        if not self._running:
            return
        msg = CommandMessage(action=action, page=page)
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(msg.to_json() + "\n")
        self._proc.stdin.flush()

    def stop(self) -> None:
        self._running = False
        if getattr(self, "_proc", None) is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            if not self._running:
                break
            try:
                data = parse_line(raw)
            except ValueError:
                continue
            mtype = data.get("type")
            if mtype == MessageType.PROGRESS.value and self._on_progress:
                self._on_progress(
                    ProgressMessage(page=data["page"], total=data["total"], stage=data["stage"])
                )
            elif mtype == MessageType.PAGE_DONE.value and self._on_page_done:
                self._on_page_done(
                    PageDoneMessage(page=data["page"], result_path=data["result_path"])
                )
            elif mtype == MessageType.ERROR.value and self._on_error:
                self._on_error(
                    ErrorMessage(page=data["page"], message=data["message"])
                )


class WorkerProcess(BaseWorker):
    """Worker encapsulé dans un processus séparé (R2, R3).

    Pour le squelette, `run_subprocess` peut être appelé avec un script worker
    factice ; les workers concrets (simple/medium/advanced) héritent de
    BaseWorker et implémentent process_page/initialize/shutdown.
    """