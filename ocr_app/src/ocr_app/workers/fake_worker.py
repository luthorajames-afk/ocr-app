"""Worker factice de validation du protocole JSON (phase 3 partielle).

Ce worker ne fait aucun OCR : il simule le pipeline d'une page, étape par étape,
et sauvegarde un JSON par page afin de valider le protocole, la pause/reprise,
l'annulation et la sauvegarde page par page (R6) avant de brancher un vrai moteur.

Exécution (R2 : toujours en sous-processus) :

    python -m ocr_app.workers.fake_worker --pages 5 --work-dir ./_work

Commandes lues sur stdin, une ligne JSON par commande :

    {"type": "command", "action": "start", "page": 1}
    {"type": "command", "action": "pause"}
    {"type": "command", "action": "resume"}
    {"type": "command", "action": "cancel"}

Messages écrits sur stdout, une ligne JSON par message (rien d'autre) :

    {"type": "progress", "page": 1, "total": 5, "stage": "render"}
    {"type": "page_done", "page": 1, "result_path": "...\\page_001.json"}
    {"type": "error", "page": 1, "message": "annulé : ..."}

Les diagnostics sont écrits sur stderr. L'encodage des flux est forcé en UTF-8
pour que les chemins accentués (R14) et les messages français restent lisibles.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from ocr_app.core.data_model import Block, BlockType, Cell, Page, Table
from ocr_app.core.protocol import (
    ErrorMessage,
    MessageType,
    PageDoneMessage,
    ProgressMessage,
    parse_line,
)

# Étapes du pipeline, dans l'ordre (cf. ocr_app.core.pipeline.PipelineStage).
PIPELINE_STAGES = (
    "render",
    "preprocess",
    "layout",
    "ocr",
    "tables",
    "reconstruct",
    "save",
)

# Période de vérification des événements : borne la latence de pause/cancel.
POLL_INTERVAL = 0.02

CANCELLED_MESSAGE = "annulé : traitement interrompu par l'interface"


class _Cancelled(Exception):
    """Signal interne : le traitement doit s'arrêter immédiatement."""


def _log(message: str) -> None:
    """Écrit un diagnostic sur stderr (jamais sur stdout : réservé au protocole)."""
    print(f"[fake_worker] {message}", file=sys.stderr, flush=True)


class _ProtocolOutput:
    """Sérialise les messages du protocole, une ligne JSON par message."""

    def __init__(self) -> None:
        self._stream = sys.stdout

    def send(self, message: Any) -> bool:
        try:
            self._stream.write(message.to_json() + "\n")
            self._stream.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False


class FakeWorker:
    """Worker factice piloté par commandes JSON sur stdin."""

    def __init__(
        self,
        total_pages: int,
        work_dir: Path | str,
        stage_delay: float = 0.05,
    ) -> None:
        self.total_pages = max(1, int(total_pages))
        self.work_dir = Path(work_dir)
        self.stage_delay = max(0.0, float(stage_delay))

        self._out = _ProtocolOutput()

        # _stop : annulation ou fin de stdin. _proceed : non pause (set = actif).
        self._stop = threading.Event()
        self._proceed = threading.Event()
        self._proceed.set()
        self._started = threading.Event()
        self._cancelled = threading.Event()
        self._state_lock = threading.Lock()

        self._start_page = 1
        self._current_page = 0
        self._eof = False

        self._reader = threading.Thread(
            target=self._read_commands,
            name="fake-worker-commands",
            daemon=True,
        )

    # --- Cycle de vie ----------------------------------------------------
    def run(self) -> int:
        try:
            self.work_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _log(f"dossier de travail inaccessible : {exc}")
            self._emit(ErrorMessage(page=0, message=f"dossier de travail inaccessible : {exc}"))
            return 1

        _log(f"prêt : {self.total_pages} page(s), dossier de travail {self.work_dir}")
        self._reader.start()

        # Attente de la commande start, d'une annulation ou de la fin de stdin.
        while not self._started.wait(POLL_INTERVAL):
            if self._stop.is_set():
                if self._cancelled.is_set():
                    self._emit_cancelled(self._start_page)
                return 0

        if self._stop.is_set():
            self._emit_cancelled(self._start_page)
            return 0

        with self._state_lock:
            first_page = self._start_page

        try:
            for page_number in range(first_page, self.total_pages + 1):
                self._process_page(page_number)
        except _Cancelled:
            self._emit_cancelled(self._current_page)
            return 0

        _log(f"terminé : {self.total_pages} page(s)")
        return 0

    # --- Lecture des commandes -------------------------------------------
    def _read_commands(self) -> None:
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if not line:
                    continue
                try:
                    data = parse_line(line)
                except ValueError as exc:
                    _log(f"ligne de commande invalide ignorée : {exc}")
                    continue
                if data.get("type") != MessageType.COMMAND.value:
                    _log(f"message ignoré (type={data.get('type')!r})")
                    continue
                self._handle_command(data)
                if self._stop.is_set():
                    return
        except (OSError, ValueError) as exc:
            _log(f"lecture de stdin interrompue : {exc}")
        finally:
            # EOF ou fin du lecteur : réveille le traitement pour qu'il s'arrête.
            self._eof = True
            self._stop.set()
            self._proceed.set()

    def _handle_command(self, data: dict) -> None:
        action = str(data.get("action", "")).strip().lower()
        page = data.get("page")

        if action == "start":
            if self._started.is_set():
                _log("commande start ignorée : traitement déjà démarré")
                return
            if isinstance(page, int) and not isinstance(page, bool) and page > 0:
                with self._state_lock:
                    self._start_page = min(page, self.total_pages)
            self._started.set()
            _log("commande start reçue")
        elif action == "pause":
            self._proceed.clear()
            _log("commande pause reçue")
        elif action == "resume":
            self._proceed.set()
            _log("commande resume reçue")
        elif action == "cancel":
            self._cancelled.set()
            self._stop.set()
            self._proceed.set()
            _log("commande cancel reçue")
        else:
            _log(f"action inconnue ignorée : {action!r}")

    # --- Traitement simulé ------------------------------------------------
    def _process_page(self, page_number: int) -> None:
        self._current_page = page_number
        for stage in PIPELINE_STAGES:
            self._checkpoint()
            self._emit(
                ProgressMessage(page=page_number, total=self.total_pages, stage=stage)
            )
            self._simulate_work(self.stage_delay)
            self._checkpoint()

            if stage == "save":
                # R6 : la page est écrite dès qu'elle est terminée.
                path = self._page_path(page_number)
                self._build_page(page_number).save_json(str(path))
                self._emit(
                    PageDoneMessage(page=page_number, result_path=str(path.resolve()))
                )

    def _page_path(self, page_number: int) -> Path:
        width = max(3, len(str(self.total_pages)))
        return self.work_dir / f"page_{page_number:0{width}d}.json"

    @staticmethod
    def _build_page(page_number: int) -> Page:
        table = Table(
            rows=2,
            cols=2,
            grid=[
                [
                    Cell(text=f"p{page_number}-A1", row_index=0, col_index=0),
                    Cell(text=f"p{page_number}-B1", row_index=0, col_index=1),
                ],
                [
                    Cell(text=f"p{page_number}-A2", row_index=1, col_index=0),
                    Cell(text=f"p{page_number}-B2", row_index=1, col_index=1),
                ],
            ],
        )
        return Page(
            number=page_number,
            source=f"fake://page/{page_number}",
            blocks=[
                Block(
                    type=BlockType.TITLE,
                    text=f"Page {page_number} — document factice",
                    bbox=(72.0, 72.0, 523.0, 100.0),
                    confidence=1.0,
                    block_id=f"p{page_number}-title",
                ),
                Block(
                    type=BlockType.PARAGRAPH,
                    text=f"Contenu OCR simulé de la page {page_number} (accents : éèàç).",
                    bbox=(72.0, 112.0, 523.0, 160.0),
                    confidence=0.98,
                    block_id=f"p{page_number}-body",
                ),
                Block(
                    type=BlockType.TABLE,
                    table=table,
                    bbox=(72.0, 172.0, 523.0, 300.0),
                    confidence=0.95,
                    block_id=f"p{page_number}-table",
                ),
            ],
            confidence=0.97,
        )

    def _simulate_work(self, seconds: float) -> None:
        """Attend `seconds` en restant réactif à pause, reprise et annulation."""
        remaining = seconds
        while remaining > 0:
            self._checkpoint()
            if not self._proceed.is_set():
                # Pause : le temps restant ne s'écoule pas.
                self._proceed.wait(POLL_INTERVAL)
                continue
            tick = min(POLL_INTERVAL, remaining)
            self._stop.wait(tick)
            remaining -= tick

    def _checkpoint(self) -> None:
        if self._stop.is_set():
            raise _Cancelled

    # --- Sortie protocole -------------------------------------------------
    def _emit(self, message: Any) -> None:
        if not self._out.send(message):
            # Plus personne n'écoute : on s'arrête sans tenter d'écrire.
            raise _Cancelled

    def _emit_cancelled(self, page_number: int) -> None:
        _log(f"annulation à la page {page_number}")
        try:
            self._emit(ErrorMessage(page=page_number, message=CANCELLED_MESSAGE))
        except _Cancelled:
            pass


def _reconfigure(stream: Any, **kwargs: Any) -> None:
    try:
        stream.reconfigure(**kwargs)
    except (AttributeError, OSError, ValueError):
        # Flux non standard (redirection, tests) : comportement par défaut conservé.
        pass


def _configure_stdio() -> None:
    """Force l'UTF-8 : chemins accentués (R14) et messages français."""
    _reconfigure(sys.stdin, encoding="utf-8", errors="replace")
    _reconfigure(
        sys.stdout,
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
        newline="\n",
    )
    _reconfigure(sys.stderr, encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ocr_app.workers.fake_worker",
        description="Worker factice de validation du protocole JSON (aucun OCR réel).",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=5,
        help="nombre de pages simulées (défaut : 5)",
    )
    parser.add_argument(
        "--work-dir",
        default="ocr_work",
        help="dossier où écrire page_XXX.json (défaut : ./ocr_work)",
    )
    parser.add_argument(
        "--stage-delay",
        type=float,
        default=0.05,
        help="durée simulée d'une étape, en secondes (défaut : 0.05)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    worker = FakeWorker(
        total_pages=args.pages,
        work_dir=args.work_dir,
        stage_delay=args.stage_delay,
    )
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
