"""T3.1/T3.2/T3.3 — Worker v2 : protocole JSON Lines, pause/reprise/annulation.

Usage :
    python -m workers.worker_main --input <pdf> --work-dir <dossier> [--dpi 300]

Commandes stdin : {"type":"command","action":"start|pause|resume|cancel"}
Événements stdout : progress / page_done / error (une ligne JSON chacun).
Diagnostics : stderr uniquement.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from core.pipeline.documents import iter_document_pages, page_count
from core.pipeline.interfaces import OcrEngine
from core.pipeline.job_store import PageRecord, save_page, saved_pages
from core.pipeline.protocol import (
    CommandMessage,
    ErrorMessage,
    PageDoneMessage,
    ProgressMessage,
    ProtocolError,
    dumps,
    parse_line,
)

STAGES = ("render", "preprocess", "layout", "ocr", "reconstruct", "save")


def _lexicon_language(languages: list[str] | None) -> str:
    """fr / en / bilingual selon les langues OCR demandées."""
    codes = set(languages or [])
    if "fra" in codes and "eng" in codes:
        return "bilingual"
    if codes == {"fra"}:
        return "fr"
    if codes == {"eng"}:
        return "en"
    return "bilingual"


def _correct_blocks(blocks: list[dict[str, object]], language: str) -> None:
    """Corrige le texte des blocs (références protégées).

    Les tableaux sont traités cellule par cellule, et seulement si la cellule
    ne contient aucun chiffre (codes, montants et références intacts).
    """
    from core.pipeline.lexicon import correct_text

    for block in blocks:
        if block.get("kind") == "table":
            grid = block.get("grid")
            if not isinstance(grid, list):
                continue
            new_grid: list[list[str]] = []
            for row in grid:
                new_row: list[str] = []
                for cell in row if isinstance(row, list) else []:
                    value = str(cell)
                    if value.strip() and not any(ch.isdigit() for ch in value) and len(value) >= 4:
                        value = correct_text(value, language=language).text
                    new_row.append(value)
                new_grid.append(new_row)
            block["grid"] = new_grid
            block["text"] = chr(10).join(chr(9).join(row) for row in new_grid)
            continue
        text = str(block.get("text", ""))
        if not text.strip():
            continue
        block["text"] = correct_text(text, language=language).text


def _mean_confidence(result: object) -> float:
    """Confiance moyenne des blocs (0 si aucun bloc)."""
    blocks = getattr(result, "blocks", ()) or ()
    values = [float(getattr(block, "confidence", 0.0)) for block in blocks]
    return sum(values) / len(values) if values else 0.0
_OUT_LOCK = threading.Lock()


def emit(message: ProgressMessage | PageDoneMessage | ErrorMessage | CommandMessage) -> None:
    """Écrit une ligne de protocole (stdout uniquement, UTF-8, thread-safe)."""
    with _OUT_LOCK:
        sys.stdout.write(dumps(message) + "\n")
        sys.stdout.flush()


def build_engine(mode: str) -> OcrEngine:
    """Fabrique de moteur : imports paresseux (R1/R13)."""
    if mode == "medium":
        from workers.medium.engine import RapidOcrEngine

        return RapidOcrEngine()
    if mode in ("advanced", "approfondi"):
        from workers.advanced.engine import DoclingEngine

        return DoclingEngine()
    from core.pipeline.tesseract_engine import TesseractEngine

    return TesseractEngine()


class Worker:
    def __init__(
        self,
        input_path: Path,
        work_dir: Path,
        dpi: int = 300,
        resume_existing: bool = True,
        mode: str = "simple",
        second_pass: bool = False,
        lexicon: bool = True,
        languages: str = "fra+eng",
        page: int | None = None,
        best_variant: bool = False,
        engine: OcrEngine | None = None,
    ) -> None:
        self.input_path = input_path
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.dpi = max(72, min(int(dpi), 300))
        self._resume_existing = resume_existing
        self.mode = mode
        self.second_pass = second_pass
        self.lexicon = lexicon
        self.languages = [part for part in languages.split("+") if part] or None
        self.lexicon_language = _lexicon_language(self.languages)
        self.page = page
        self.best_variant = best_variant
        self._engine = engine
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ run
    def start(self) -> None:
        self._thread = threading.Thread(target=self._process, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()

    def wait_done(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    # -------------------------------------------------------------- process
    def _checkpoint(self, page: int, total: int, stage: str) -> bool:
        """Pause/annulation coopératives entre étapes. Retourne False si annulé."""
        while self._pause.is_set() and not self._cancel.is_set():
            time.sleep(0.02)
        if self._cancel.is_set():
            return False
        emit(ProgressMessage(page=page, total=total, stage=stage))
        return True

    def _debug(self, message: str) -> None:
        if os.environ.get("AOCRI_DEBUG") == "1":
            print(f"[worker:{self.mode}] {message}", file=sys.stderr, flush=True)

    def _process(self) -> None:
        try:
            self._debug("thread start")
            # R7 : rendu page par page, une seule image en mémoire.
            self._debug("counting pages")
            total = page_count(self.input_path)
            self._debug(f"pages={total}")
            if self.page is not None:
                self.page = max(1, min(int(self.page), total))
                total = 1
            pages = iter_document_pages(self.input_path, dpi=self.dpi)
            already = saved_pages(self.work_dir) if self._resume_existing else set()
            engine = self._engine if self._engine is not None else build_engine(self.mode)
            engine.initialize()
            try:
                for page in pages:
                    if self.page is not None and page.number != self.page:
                        continue
                    if self._cancel.is_set():
                        break
                    self._debug(f"page {page.number} start")
                    if page.number in already:
                        emit(PageDoneMessage(page=page.number, result_path=str(
                            self.work_dir / f"page_{page.number:03d}.json")))
                        continue
                    if not self._checkpoint(page.number, total, "render"):
                        break
                    if not self._checkpoint(page.number, total, "preprocess"):
                        break
                    text = page.native_text
                    duration = 0.0
                    blocks: list[dict[str, object]] = []
                    if page.has_native_text:
                        # T1.3/T2.3 : blocs natifs segmentés (colonnes, titres).
                        blocks = [
                            {
                                "text": block.text,
                                "bbox": list(block.bbox),
                                "confidence": block.confidence,
                                "kind": block.kind,
                                "grid": [list(row) for row in block.grid],
                            }
                            for block in page.blocks
                        ]
                        if self.lexicon:
                            _correct_blocks(blocks, self.lexicon_language)
                        # Texte dans l'ordre de lecture des colonnes (pas l'ordre
                        # brut de la couche PDF, souvent ligne par ligne).
                        text = chr(10).join(
                            str(block["text"])
                            for block in blocks
                            if str(block.get("text", "")).strip()
                        )
                        if not self._checkpoint(page.number, total, "ocr"):
                            break
                    else:
                        if not self._checkpoint(page.number, total, "ocr"):
                            break
                        from core.pipeline.preprocess import preprocess

                        prepared = preprocess(page.image)
                        self._debug("ocr start")
                        result = engine.process(prepared.image, languages=self.languages)
                        self._debug("ocr done")
                        if self.best_variant:
                            # Compare brut vs prétraité : garde la meilleure confiance.
                            raw_result = engine.process(page.image, languages=self.languages)
                            prepared_conf = _mean_confidence(result)
                            raw_conf = _mean_confidence(raw_result)
                            if raw_conf > prepared_conf + 0.02:
                                result = raw_result
                        if self.second_pass and hasattr(engine, "process_region"):
                            from dataclasses import replace as dc_replace

                            from core.pipeline.second_pass import apply_second_pass

                            report = apply_second_pass(page.image, result, engine)
                            result = report.result

                        from dataclasses import replace as dc_replace

                        from core.pipeline.layout import order_blocks_in_columns

                        ordered = order_blocks_in_columns(result.blocks)
                        result = dc_replace(
                            result,
                            blocks=tuple(ordered),
                            text="\n".join(block.text for block in ordered if block.text.strip()),
                        )
                        text = result.text
                        duration = result.duration_ms
                        blocks = [
                            {
                                "text": block.text,
                                "bbox": list(block.bbox),
                                "confidence": block.confidence,
                                "kind": block.kind,
                                "grid": [list(row) for row in block.grid],
                            }
                            for block in result.blocks
                        ]
                        if self.lexicon:
                            _correct_blocks(blocks, self.lexicon_language)
                            text = chr(10).join(
                                str(block["text"])
                                for block in blocks
                                if str(block.get("text", "")).strip()
                            )
                    if not self._checkpoint(page.number, total, "reconstruct"):
                        break
                    record = PageRecord(
                        schema_version=1,
                        page=page.number,
                        source=str(self.input_path),
                        engine=engine.name,
                        engine_version=engine.version,
                        duration_ms=duration,
                        text=text,
                        blocks=blocks,
                    )
                    # Image de la page (plan v2 : une image ET un JSON par page),
                    # utilisée par l'aperçu « Original » de l'IHM.
                    png_path = self.work_dir / f"page_{page.number:03d}.png"
                    page.image.save(png_path, format="PNG", dpi=(self.dpi, self.dpi))
                    path = save_page(record, self.work_dir)
                    if not self._checkpoint(page.number, total, "save"):
                        break
                    emit(PageDoneMessage(page=page.number, result_path=str(path)))
            finally:
                engine.shutdown()
            if self._cancel.is_set():
                emit(ErrorMessage(page=0, message="annulé : traitement interrompu"))
        except Exception as exc:  # noqa: BLE001 — erreur protocolaire explicite
            emit(ErrorMessage(page=0, message=f"erreur de traitement : {exc}"))
        finally:
            self._done.set()


def _preload(mode: str) -> None:
    """Précharge les DLL avant la lecture stdin (verrou du loader Windows).

    Même classe de bug que Docling en v1 : importer pypdfium2/onnxruntime
    depuis un thread pendant qu'un autre thread lit stdin peut bloquer le
    chargeur DLL. Le préchargement dans le thread principal l'évite.
    """
    import numpy  # noqa: F401
    import PIL.Image  # noqa: F401
    import pypdfium2  # noqa: F401

    if mode == "medium":
        # Précharger TOUTES les DLL lourdes dans le thread principal avant la
        # lecture stdin (chargeur Windows) : onnxruntime/cv2/rapidocr.
        import cv2  # noqa: F401
        import onnxruntime  # noqa: F401
        import rapidocr  # noqa: F401
    if mode in ("advanced", "approfondi"):
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            bundled_hf = Path(bundle) / "hf"
            if bundled_hf.is_dir():
                os.environ.setdefault("HF_HOME", str(bundled_hf))
                os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(bundled_hf / "hub"))
                os.environ.setdefault("HF_HUB_CACHE", str(bundled_hf / "hub"))
        # Précharger le convertisseur (sous-modules + DLL torch/docling) dans
        # le thread principal : sinon blocage du chargeur pendant que le
        # thread principal lit stdin (même classe de bug que v1).
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        import torch  # noqa: F401
        from docling.document_converter import DocumentConverter  # noqa: F401


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worker_main", description="AOCRI v2 worker")
    parser.add_argument("--input", required=True, help="PDF/JPG/PNG")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--mode", choices=("simple", "medium", "advanced", "approfondi"), default="simple"
    )
    parser.add_argument("--lang", default="fra+eng", help="langues OCR (ex. fra+eng)")
    parser.add_argument("--page", type=int, default=None, help="traiter une seule page (1-based)")
    parser.add_argument(
        "--best-variant", action="store_true", help="choisir le meilleur pretraitement par page"
    )
    parser.add_argument("--second-pass", action="store_true", help="re-OCR des lignes sous confiance")
    parser.add_argument(
        "--lexicon", dest="lexicon", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--no-lexicon",
        dest="lexicon",
        action="store_false",
        help="désactiver le correcteur lexical juridique FR/EN",
    )
    parser.set_defaults(lexicon=True)
    parser.add_argument("--no-resume", action="store_true", help="ignorer les pages déjà sauvegardées")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_file():
        emit(ErrorMessage(page=0, message=f"document introuvable : {input_path}"))
        return 1
    _preload(args.mode)
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Initialisation du moteur dans le thread principal : les DLL lourdes
    # (onnxruntime/torch) sont chargées avant toute lecture stdin.
    engine = build_engine(args.mode)
    engine.initialize()
    worker = Worker(
        input_path,
        work_dir,
        dpi=args.dpi,
        resume_existing=not args.no_resume,
        mode=args.mode,
        second_pass=args.second_pass,
        lexicon=args.lexicon,
        languages=args.lang,
        page=args.page,
        best_variant=args.best_variant,
        engine=engine,
    )

    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = parse_line(line)
            except ProtocolError as exc:
                emit(ErrorMessage(page=0, message=f"commande invalide : {exc}"))
                continue
            if not isinstance(message, CommandMessage):
                continue
            if message.action == "start":
                worker.start()
            elif message.action == "pause":
                worker.pause()
            elif message.action == "resume":
                worker.resume()
            elif message.action == "cancel":
                worker.cancel()
                emit(ErrorMessage(page=0, message="annulé : traitement interrompu"))
                sys.stdout.flush()
                os._exit(0)
    except KeyboardInterrupt:  # pragma: no cover
        worker.cancel()
    worker.wait_done(timeout=1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
