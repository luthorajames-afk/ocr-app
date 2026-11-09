"""Worker principal par mode : protocole JSON stdin/stdout avec OCR réel.

Phase 7 — point d'entrée unique des workers (aussi utilisé par PyInstaller,
scripts/build.py, R3). Phase 8 : ``--page N`` (relance d'une seule page,
même protocole, numéro réel) et ``--lang`` (langues Tesseract du mode
Simple, réglages de l'interface). La boucle commune réutilise le style de
``fake_worker.py`` (démarrage sur commande ``start``, pause/reprise,
annulation, progression par étape, sauvegarde page par page R6) mais
délègue le traitement de chaque page au pipeline réel par mode :

- mode ``simple``    : OCR réel Tesseract via ``core/pipeline.py`` (R8 : le
  texte natif d'un PDF est utilisé sans OCR, segmenté en blocs réels) ;
- mode ``advanced``  : Docling réel (``AdvancedWorker`` + ``DoclingEngine``)
  dans le venv d'évaluation dédié (R1/R13) ;
- mode ``medium``    : pages « placeholder » produites par le worker
  squelette — la boucle protocole reste complète et fonctionnelle.

Exécution (R2 : toujours en sous-processus) :

    python -m ocr_app.workers.worker_main --mode simple --pages N --work-dir ./_work
    python -m ocr_app.workers.worker_main --mode simple --input doc.pdf --work-dir ./_work
    python -m ocr_app.workers.worker_main --mode simple --input doc.pdf --page 3 --work-dir ./_work
    python -m ocr_app.workers.worker_main --mode simple --input doc.pdf --lang fra+eng --dpi 200 --work-dir ./_work

Sans ``--input``, N pages sont simulées (protocole identique, pages
placeholder). Avec ``--input`` (PDF, JPG, PNG), le document réel est traité
page par page à 300 DPI maximum (R7). Avec ``--page N`` (phase 8), une seule
page du document est traitée — même protocole, ``page_done`` avec le numéro
réel de la page (relance ciblée depuis l'interface). ``--lang`` (phase 8)
sélectionne les langues Tesseract du mode Simple (``fra+eng``, ``fra``,
``eng``…).

Commandes lues sur stdin, une ligne JSON par commande :

    {"type": "command", "action": "start", "page": 1}
    {"type": "command", "action": "pause"}
    {"type": "command", "action": "resume"}
    {"type": "command", "action": "cancel"}

Messages écrits sur stdout, une ligne JSON par message (rien d'autre) :

    {"type": "progress", "page": 1, "total": 5, "stage": "render"}
    {"type": "page_done", "page": 1, "result_path": "...\\page_001.json"}
    {"type": "error", "page": 1, "message": "annulé : ..."}

Les diagnostics vont sur stderr ; l'encodage des flux est forcé en UTF-8 pour
que les chemins accentués (R14) et les messages français restent lisibles.
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

from ocr_app.core.data_model import Block, BlockType, Page
from ocr_app.core.pipeline import Pipeline, PipelineConfig, PipelineStage, PipelineError
from ocr_app.core.protocol import (
    ErrorMessage,
    MessageType,
    PageDoneMessage,
    ProgressMessage,
    parse_line,
)
from ocr_app.core.rendering import (
    DocumentRenderer,
    RenderingError,
    UnsupportedFormatError,
)

# Étapes émises pendant le traitement d'une page, dans l'ordre
# (cf. ocr_app.core.pipeline.PipelineStage).
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

MODES = ("simple", "medium", "advanced")

# Confiance indicative des pages placeholder (aucun OCR réel effectué).
_PLACEHOLDER_CONFIDENCE = {"simple": 0.97, "medium": 0.90, "advanced": 0.98}

# Codes de langue Tesseract acceptés par --lang : lettres/underscores séparés
# par « + » (fra+eng, fra, eng, chi_sim…). La disponibilité réelle des données
# est vérifiée par le moteur (repli contrôlé, cf. core/ocr_engine.py).
_LANG_RE = re.compile(r"^[A-Za-z_]{2,32}(\+[A-Za-z_]{2,32})*$")


class _Cancelled(Exception):
    """Signal interne : le traitement doit s'arrêter immédiatement."""


def _log(message: str) -> None:
    """Écrit un diagnostic sur stderr (jamais sur stdout : réservé au protocole)."""
    print(f"[worker_main] {message}", file=sys.stderr, flush=True)


def _placeholder_text(mode: str, page_number: int) -> str:
    """Texte des pages placeholder (simulation sans ``--input`` uniquement)."""
    if mode == "advanced":
        return (
            f"[page {page_number}] mode Avancé : simulation sans --input "
            f"(Docling s'exécute dans le venv d'évaluation) — accents : éèàç."
        )
    if mode == "medium":
        return (
            f"[page {page_number}] mode Moyen : moteur RapidOCR non encore "
            f"installé — placeholder (accents : éèàç)."
        )
    return (
        f"[page {page_number}] simulation Simple : aucun --input fourni "
        f"(accents : éèàç)."
    )


def _build_placeholder_page(mode: str, page_number: int, source: str) -> Page:
    """Page placeholder pour un mode sans moteur réel ou sans --input."""
    return Page(
        number=page_number,
        source=source,
        blocks=[
            Block(
                type=BlockType.PARAGRAPH,
                text=_placeholder_text(mode, page_number),
                bbox=(72.0, 72.0, 523.0, 120.0),
                confidence=_PLACEHOLDER_CONFIDENCE.get(mode, 0.9),
                block_id=f"p{page_number:03d}-placeholder",
            )
        ],
        confidence=_PLACEHOLDER_CONFIDENCE.get(mode, 0.9),
    )


class _ProtocolOutput:
    """Sérialise les messages du protocole, une ligne JSON par message.

    Le verrou garantit qu'aucune ligne ne peut être entrelacée si le pipeline
    émet de la progression depuis un thread auxiliaire (le protocole exige une
    ligne JSON complète par message).
    """

    def __init__(self) -> None:
        self._stream = sys.stdout
        self._lock = threading.Lock()

    def send(self, message: Any) -> bool:
        try:
            with self._lock:
                self._stream.write(message.to_json() + "\n")
                self._stream.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False


class WorkerMain:
    """Worker principal par mode, piloté par commandes JSON sur stdin.

    Deux sources de pages, selon les arguments CLI :

    - ``--input <pdf/jpg/png>`` : document réel traité par le pipeline du mode
      (OCR vrai en Simple, Docling en Avancé, placeholder en Moyen) ;
    - sans ``--input`` : simulation de ``--pages N`` pages (protocole complet,
      pages placeholder), pour les tests de protocole sans document.
    """

    def __init__(
        self,
        mode: str,
        work_dir: Path | str,
        input_path: Optional[str] = None,
        total_pages: int = 5,
        stage_delay: float = 0.05,
        dpi: int = 300,
        models_dir: Optional[str] = None,
        page: Optional[int] = None,
        language: Optional[str] = None,
    ) -> None:
        self.mode = str(mode).strip().lower()
        if self.mode not in MODES:
            raise ValueError(
                f"mode inconnu : {mode!r} (modes acceptés : {', '.join(MODES)})"
            )
        self.work_dir = Path(work_dir)
        self.input_path = str(input_path) if input_path else None
        self.total_pages = max(1, int(total_pages))
        self.stage_delay = max(0.0, float(stage_delay))
        self.dpi = int(dpi) if int(dpi) > 0 else 300
        self.models_dir = models_dir

        # Phase 8 : relance d'une seule page (--page N) et langues Tesseract
        # (--lang fra+eng). Page invalide ou langue mal formée : erreur d'usage
        # propre (ValueError → code 2, comme un mode inconnu).
        if page is None:
            self.single_page: Optional[int] = None
        else:
            try:
                requested = int(page)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"--page invalide : {page!r}") from None
            if requested < 1:
                raise ValueError("--page doit être un entier >= 1")
            self.single_page = requested

        if language is None:
            self.language: Optional[str] = None
        else:
            wanted = str(language).strip().lower()
            if not wanted or not _LANG_RE.match(wanted):
                raise ValueError(
                    f"--lang invalide : {language!r} "
                    "(attendu : codes séparés par « + », ex. fra+eng)"
                )
            self.language = wanted

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

        self._reader = threading.Thread(
            target=self._read_commands,
            name="worker-main-commands",
            daemon=True,
        )

    # --- Cycle de vie ----------------------------------------------------
    def run(self) -> int:
        try:
            self.work_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _log(f"dossier de travail inaccessible : {exc}")
            self._emit_error(0, f"dossier de travail inaccessible : {exc}")
            return 1

        _log(
            f"prêt : mode {self.mode}, "
            f"{'document ' + str(self.input_path) if self.input_path else f'{self.total_pages} page(s) simulée(s)'}, "
            + (f"page {self.single_page} seule, " if self.single_page else "")
            + (f"langues {self.language}, " if self.language else "")
            + f"dossier de travail {self.work_dir}"
        )
        self._prewarm_engine()
        self._reader.start()

        # Attente de la commande start, d'une annulation ou de la fin de stdin.
        while not self._started.wait(POLL_INTERVAL):
            if self._stop.is_set():
                if self._cancelled.is_set():
                    self._emit_cancelled(self._start_page)
                else:
                    _log("fin de stdin avant start : arrêt sans erreur")
                return 0

        if self._stop.is_set():
            if self._cancelled.is_set():
                self._emit_cancelled(self._start_page)
            return 0

        with self._state_lock:
            first_page = self._start_page

        try:
            if self.input_path:
                self._process_document(first_page)
            else:
                self._process_simulated(first_page)
        except _Cancelled:
            self._emit_cancelled(self._current_page)
            return 0
        except (
            FileNotFoundError,
            RenderingError,
            UnsupportedFormatError,
            PipelineError,
        ) as exc:
            _log(f"erreur de document : {exc}")
            self._emit_error(
                max(1, self._current_page), f"erreur de document : {exc}"
            )
            return 1
        except Exception as exc:  # jamais de trace non protocole sur stdout
            _log(
                f"erreur interne à la page {self._current_page} : "
                f"{type(exc).__name__}: {exc}"
            )
            traceback.print_exc(file=sys.stderr)
            self._emit_error(
                max(1, self._current_page),
                f"erreur interne : {type(exc).__name__}: {exc}",
            )
            return 1

        _log("terminé")
        return 0

    # --- Lecture des commandes -------------------------------------------
    def _prewarm_engine(self) -> None:
        """Précharge les dépendances lourdes du mode dans le thread principal.

        Mode Avancé uniquement : l'import de Docling (qui tire numpy et ses
        DLL natives) doit avoir lieu AVANT le démarrage du lecteur de
        commandes. Une fois qu'un thread est bloqué en lecture du tube
        stdin, le chargement de l'extension C de numpy peut rester bloqué
        indéfiniment (verrou du chargeur DLL Windows — voir la note de
        ``core.advanced_engine.import_document_converter`` ; ``tesserocr``
        n'est pas concerné). L'import reste paresseux (R1) : il n'a lieu que
        pour le mode Avancé avec un document réel, et un échec n'est pas
        fatal ici — le moteur produira l'erreur de protocole appropriée.
        """
        if self.mode != "advanced" or not self.input_path:
            return
        try:
            from ocr_app.core.advanced_engine import import_document_converter

            import_document_converter()
            _log("Docling préchargé (thread principal)")
        except ImportError as exc:
            _log(f"mode Avancé : Docling indisponible au préchargement ({exc})")

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
                    self._start_page = page
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

    # --- Points de synchronisation ---------------------------------------
    def _checkpoint(self) -> None:
        if self._stop.is_set():
            raise _Cancelled

    def _wait_while_paused(self) -> None:
        """Bloque tant que la pause est active (sans dépasser la fin de stdin)."""
        while not self._proceed.is_set():
            if self._stop.is_set():
                raise _Cancelled
            self._proceed.wait(POLL_INTERVAL)

    # --- Document réel (pipeline par mode) --------------------------------
    def _count_document_pages(self) -> int:
        """Compte les pages du document d'entrée et valide sa lisibilité."""
        renderer = DocumentRenderer(self.input_path or "", dpi=self.dpi)
        try:
            return max(0, int(renderer.page_count))
        finally:
            renderer.close()

    def _build_pipeline(self, displayed_total: int) -> Pipeline:
        """Pipeline du mode courant + callback d'étapes + langues (--lang)."""
        pipeline = Pipeline(
            PipelineConfig(
                mode=self.mode,
                dpi=self.dpi,
                work_dir=str(self.work_dir),
                models_dir=self.models_dir,
            )
        )
        self._inject_language(pipeline)
        pipeline.on_stage(self._make_stage_callback(pipeline, displayed_total))
        return pipeline

    def _inject_language(self, pipeline: Pipeline) -> None:
        """Applique ``--lang`` au moteur Tesseract du mode Simple.

        Phase 8 : les réglages de l'interface arrivent jusqu'au moteur. Le
        worker Simple accepte un moteur injecté : on le construit avec les
        langues demandées et on le place comme worker du pipeline (R2 inchangé :
        tout ceci tourne dans le sous-processus du worker). Les modes
        medium/advanced n'ont pas encore de moteur réel : ``--lang`` y est
        accepté et journalisé, sans autre effet.
        """
        if not self.language or self.mode != "simple":
            return
        from ocr_app.core.ocr_engine import create_engine
        from ocr_app.workers.simple import SimpleWorker

        engine = create_engine(
            "tesseract", tessdata_dir=self.models_dir, language=self.language
        )
        # Point d'injection du Pipeline : ``_ensure_worker`` réutilise un
        # worker déjà présent au lieu d'en construire un ; ``close()`` le
        # libère comme n'importe quel worker créé par le pipeline lui-même.
        pipeline._worker = SimpleWorker(  # noqa: SLF001 - injection volontaire
            models_dir=self.models_dir, engine=engine
        )
        _log(f"langues OCR demandées : {self.language}")

    def _run_pipeline_monitored(self, pipeline: Pipeline, runner) -> None:
        """Exécute ``runner(pipeline)`` dans un thread daemon surveillé.

        La pause/reprise et l'annulation sont vérifiées à chaque étape du
        pipeline via le callback ``on_stage``. Le pipeline tourne dans un
        thread d'assistance daemon : une annulation (ou un EOF) en pleine
        étape OCR — qui peut durer plusieurs secondes par page — est vue par
        la boucle principale en moins de ``POLL_INTERVAL`` et abandonne le
        traitement immédiatement (cancel < 2 s garanti côté worker, R2).
        """
        error: list[BaseException] = []
        done = threading.Event()

        def run_pipeline() -> None:
            try:
                runner(pipeline)
            except BaseException as exc:  # remonté au thread principal
                error.append(exc)
            finally:
                done.set()

        thread = threading.Thread(
            target=run_pipeline, name="worker-main-pipeline", daemon=True
        )
        thread.start()
        while not done.wait(POLL_INTERVAL):
            if self._stop.is_set():
                # Annulation/EOF en pleine étape : abandon immédiat. Le thread
                # daemon meurt avec le processus (pas de close() ici : le
                # moteur est potentiellement en pleine reconnaissance).
                raise _Cancelled
        # Le pipeline est terminé : les ressources peuvent être libérées,
        # y compris quand la page a échoué (erreur remontée au thread principal).
        try:
            if error:
                raise error[0]
        finally:
            pipeline.close()

    def _process_document(self, first_page: int) -> None:
        """Traite le document réel via le pipeline du mode (R6, R7, R8).

        Phase 8 : avec ``--page N``, une seule page est traitée (même
        protocole, ``page_done`` avec le numéro réel de la page).
        """
        assert self.input_path is not None
        total = self._count_document_pages()
        if self.single_page is not None:
            self._process_single_page(self.single_page, total)
            return
        if first_page > max(1, total):
            _log(f"page de départ {first_page} hors document ({total} page(s))")
            return

        # La pipeline ouvre le worker du mode une fois pour tout le document
        # (R5) et sauvegarde chaque page dès qu'elle est terminée (R6) dans
        # work_dir/page_NNN.json.
        pipeline = self._build_pipeline(total)
        self._run_pipeline_monitored(
            pipeline,
            lambda pl: pl.process_document(self.input_path or ""),
        )

    def _process_single_page(self, page_number: int, total: int) -> None:
        """Traite UNE seule page du document (``--page N``, phase 8).

        Même protocole que le document complet : progression par étape avec
        ``total = 1`` (une page demandée), puis un unique ``page_done`` portant
        le numéro réel de la page et le chemin ``page_NNN.json`` écrit dans le
        dossier de travail de la tâche (jamais celui du document d'origine).
        """
        assert self.input_path is not None
        if page_number < 1 or page_number > max(1, total):
            message = f"page {page_number} hors document ({total} page(s))"
            _log(message)
            raise PipelineError(message)

        pipeline = self._build_pipeline(displayed_total=1)
        self._run_pipeline_monitored(
            pipeline,
            lambda pl: pl.process_page(self.input_path or "", page_number),
        )

    def _make_stage_callback(self, pipeline: Pipeline, total: int):
        def on_stage(page_number: int, stage: str) -> None:
            self._current_page = page_number
            self._checkpoint()
            self._wait_while_paused()
            self._checkpoint()
            self._emit(
                ProgressMessage(page=page_number, total=max(1, total), stage=stage)
            )
            if stage == PipelineStage.DONE.value:
                # R6 : la page vient d'être écrite par le pipeline.
                paths = pipeline.saved_page_paths
                if paths:
                    result_path = str(Path(paths[-1]).resolve())
                else:  # sans work_dir, la page n'a pas été persistée
                    result_path = str(self._page_path(page_number).resolve())
                self._emit(
                    PageDoneMessage(page=page_number, result_path=result_path)
                )

        return on_stage

    # --- Simulation (sans --input) ---------------------------------------
    def _process_simulated(self, first_page: int) -> None:
        """Simule ``--pages N`` pages placeholder avec le protocole complet.

        Phase 8 : avec ``--page N``, une seule page simulée est produite (même
        convention que le document réel : total affiché = 1, numéro réel).
        """
        if self.single_page is not None:
            pages: range = range(self.single_page, self.single_page + 1)
            total = 1
        else:
            total = self.total_pages
            if first_page > total:
                _log(f"page de départ {first_page} hors borne ({total} page(s))")
                return
            pages = range(first_page, total + 1)
        for page_number in pages:
            self._current_page = page_number
            for stage in PIPELINE_STAGES:
                self._checkpoint()
                self._emit(
                    ProgressMessage(page=page_number, total=total, stage=stage)
                )
                self._simulate_work(self.stage_delay)
                self._checkpoint()

                if stage == "save":
                    # R6 : la page est écrite dès qu'elle est terminée.
                    path = self._page_path(page_number)
                    page = _build_placeholder_page(
                        self.mode,
                        page_number,
                        source=f"simulated://page/{page_number}",
                    )
                    page.save_json(path)
                    self._emit(
                        PageDoneMessage(
                            page=page_number, result_path=str(path.resolve())
                        )
                    )

    def _simulate_work(self, seconds: float) -> None:
        """Attend ``seconds`` en restant réactif à pause, reprise et annulation."""
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

    # --- Divers ------------------------------------------------------------
    def _page_path(self, page_number: int) -> Path:
        width = max(3, len(str(max(1, self.total_pages))))
        return self.work_dir / f"page_{page_number:0{width}d}.json"

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

    def _emit_error(self, page_number: int, message: str) -> None:
        """Émet une erreur de protocole sans jamais laisser fuiter de trace."""
        try:
            self._emit(
                ErrorMessage(page=max(0, int(page_number)), message=str(message))
            )
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
        prog="python -m ocr_app.workers.worker_main",
    description=(
        "Worker OCR par mode (protocole JSON stdin/stdout). "
        "Mode simple : OCR réel Tesseract ; mode advanced : Docling (venv "
        "d'évaluation) ; mode medium : placeholder (module non installé)."
    ),
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="simple",
        help="mode OCR du worker (défaut : simple)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=5,
        help="nombre de pages simulées sans --input (défaut : 5)",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="document d'entrée PDF/JPG/PNG (optionnel : sans lui, pages simulées)",
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
        help="durée simulée d'une étape sans --input, en secondes (défaut : 0.05)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="DPI de rendu PDF, 300 maximum (R7, défaut : 300)",
    )
    parser.add_argument(
        "--models-dir",
        default=None,
        help="dossier tessdata explicite (R4 ; défaut : models/tessdata du projet)",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=None,
        help=(
            "traite UNE seule page du document (numéro réel, 1-based) ; "
            "sans --input, simule cette page seule (phase 8)"
        ),
    )
    parser.add_argument(
        "--lang",
        default=None,
        help=(
            "langues Tesseract du mode Simple, séparées par « + » "
            "(ex. fra+eng ; défaut : fra+eng)"
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        worker = WorkerMain(
            mode=args.mode,
            work_dir=args.work_dir,
            input_path=args.input,
            total_pages=args.pages,
            stage_delay=args.stage_delay,
            dpi=args.dpi,
            models_dir=args.models_dir,
            page=args.page,
            language=args.lang,
        )
    except ValueError as exc:
        _log(str(exc))
        return 2
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
