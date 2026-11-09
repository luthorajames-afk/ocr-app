"""Recette d'acceptation v1.0 d'AOCRI (phase 8).

Recette complète automatisée, exécutable hors interface :

    python scripts/acceptance.py            # recette complète
    python scripts/acceptance.py --skip-tests   # sans relancer pytest

Étapes :

1. suite de tests complète ``python -m pytest tests/ -q`` (181+ tests) ;
2. CLI ``process`` sur 3 documents représentatifs du jeu de test
   (un natif facile, un scan, un mixte) avec vérifications : code de sortie,
   fichiers de sortie attendus, texte non vide, accents présents (R14) ;
3. ``worker_main`` (mode simple) sur UNE page d'un document réel
   (``--page 1``), dans un dossier de travail accentué : protocole complet,
   un unique ``page_done`` portant le numéro réel de la page ;
4. rapport global PASS/FAIL/SKIP par étape et code de sortie 0 (succès) ou 1.

Chaque vérification PASS/FAIL est réellement mesurée (jamais automatique).
Si le moteur Tesseract (tesserocr) n'est pas installé sur la machine, les
vérifications qui exigent un OCR réel (scan, mixte, worker sur document) sont
rapportées **SKIP** — jamais FAIL, jamais de crash — et la recette reste PASS
(le rapport liste les vérifications sautées). Le document natif, qui n'a pas
besoin d'OCR (R8), est toujours vérifié.

Tous les fichiers de travail vont dans ``%TEMP%`` (jamais dans le dépôt) et
sont supprimés à la fin de chaque étape.

R2 (OCR en sous-processus), R7 (DPI ≤ 300) et R14 (chemins accentués, UTF-8)
sont respectés end to end ; le protocole JSON des workers est inchangé.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
TEST_SUITE = PROJECT_ROOT / "data" / "test_suite"

# Documents représentatifs : natif facile, scan, mixte.
# Les deux derniers exigent un OCR réel (tesserocr) ; le premier est du texte
# natif (R8) et reste vérifiable sans moteur.
CLI_DOCUMENTS = (
    ("natif facile", TEST_SUITE / "facts" / "fact_001.pdf", 1, False),
    ("scan", TEST_SUITE / "scans" / "scan_001.pdf", 1, True),
    ("mixte", TEST_SUITE / "mixed" / "mixed_001.pdf", 2, True),
)

WORKER_MODULE = "ocr_app.workers.worker_main"

# Accent vérifié dans chaque sortie (textes français du jeu de test).
ACCENTED_CHARS = "éèêëàâçùûüôîïÉÈÊÀÂÇÔÎ"
ACCENT_MIN_COUNT = 10  # seuil prudent : les textes réels en dépassent 20


def tesserocr_available(env: Optional[dict] = None) -> bool:
    """Vrai si tesserocr est importable **dans l'environnement des enfants**.

    La détection est faite dans un sous-processus lancé comme les enfants de
    la recette (même interpréteur, même ``PYTHONPATH``) : un parent lancé en
    mode isolé (``python -I``) ne doit pas provoquer de faux « moteur absent ».
    En cas de doute (échec du sondage), retourne ``False`` → SKIP explicites,
    jamais de FAIL injustifié.
    """
    probe = (
        "import importlib.util, sys; "
        "sys.exit(0 if importlib.util.find_spec('tesserocr') else 1)"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


class Acceptance:
    """Exécute les étapes de la recette et tient le rapport PASS/FAIL/SKIP."""

    def __init__(self) -> None:
        self.results: List[Tuple[str, bool, str]] = []
        self.skipped: List[Tuple[str, str]] = []

    # --- Journal ----------------------------------------------------------
    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.results.append((label, ok, detail))
        status = "PASS" if ok else "FAIL"
        line = f"  [{status}] {label}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)
        return ok

    def check_skip(self, label: str, detail: str) -> None:
        """Vérification sautée (moteur manquant…) : rapportée, jamais FAIL."""
        self.skipped.append((label, detail))
        print(f"  [SKIP] {label} — {detail}", flush=True)

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===", flush=True)

    def child_env(self) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = (
            str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        )
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    # --- Étape 1 : tests --------------------------------------------------
    def run_test_suite(self) -> None:
        self.section("Étape 1/3 — Suite de tests (pytest)")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
            )
        except subprocess.TimeoutExpired:
            self.check("pytest tests/ -q", False, "délai de 1800 s dépassé")
            return
        except OSError as exc:
            self.check("pytest tests/ -q", False, f"pytest inlancable : {exc}")
            return
        elapsed = time.perf_counter() - started
        tail = (completed.stdout or "").strip().splitlines()[-3:]
        summary = " | ".join(tail) if tail else (completed.stderr or "").strip()[-300:]
        ok = completed.returncode == 0
        self.check(
            "pytest tests/ -q",
            ok,
            f"exit={completed.returncode}, {elapsed:.1f} s — {summary}",
        )

    # --- Étape 2 : CLI process -------------------------------------------
    def run_cli_documents(self) -> None:
        self.section("Étape 2/3 — CLI process sur 3 documents représentatifs")
        engine_ok = tesserocr_available(self.child_env())
        out_root = Path(tempfile.mkdtemp(prefix="aocri_acceptation éàç_"))
        try:
            for label, document, expected_pages, needs_engine in CLI_DOCUMENTS:
                if needs_engine and not engine_ok:
                    # Moteur absent : SKIP explicite (jamais FAIL, jamais crash).
                    self.check_skip(
                        f"CLI {label} — (toutes vérifications)",
                        "tesserocr non installé sur cette machine",
                    )
                    continue
                try:
                    self._check_cli_document(
                        label, document, expected_pages, out_root / label
                    )
                except subprocess.TimeoutExpired:
                    self.check(f"CLI {label} — exit 0", False,
                               "délai de 600 s dépassé")
                except (OSError, ValueError) as exc:
                    self.check(f"CLI {label} — exit 0", False,
                               f"erreur inattendue : {exc}")
        finally:
            # La recette ne pollue pas %TEMP% : nettoyage même en cas d'échec.
            shutil.rmtree(out_root, ignore_errors=True)

    def _check_cli_document(
        self, label: str, document: Path, expected_pages: int, output: Path
    ) -> None:
        """Vérifications d'un document CLI (exit, fichiers, texte, JSON)."""
        if not document.is_file():
            self.check(f"CLI {label}", False, f"document absent : {document}")
            return
        completed = subprocess.run(
            [
                sys.executable, "-m", "ocr_app.cli", "process",
                "--input", str(document),
                "--mode", "simple",
                "--output", str(output),
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=self.child_env(),
        )
        if not self.check(
            f"CLI {label} — exit 0",
            completed.returncode == 0,
            (completed.stderr or "").strip()[-160:] or str(document.name),
        ):
            return

        # Fichiers de sortie attendus (R6 : pages dans work/ + racine).
        expected = ["result.txt", "result.md", "result.html", "document.json"]
        missing = [name for name in expected if not (output / name).is_file()]
        page_files = sorted(output.glob("page_*.json"))
        self.check(
            f"CLI {label} — fichiers de sortie",
            not missing
            and len(page_files) >= expected_pages,
            f"{len(page_files)} page(s) JSON, "
            + ("tous les exports présents" if not missing
               else f"manquants : {missing}"),
        )

        # Non-vide + accents (R14 : lecture UTF-8 explicite).
        try:
            text = (output / "result.txt").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.check(f"CLI {label} — texte lisible", False, str(exc))
            return
        accents = sum(1 for ch in text if ch in ACCENTED_CHARS)
        self.check(
            f"CLI {label} — texte non vide et accentué",
            len(text) > 200 and accents >= ACCENT_MIN_COUNT,
            f"{len(text)} caractères, {accents} accents",
        )

        # Le JSON de page est valide et porte le bon numéro.
        first = page_files[0] if page_files else None
        ok_json = False
        if first is not None:
            try:
                data = json.loads(first.read_text(encoding="utf-8"))
                ok_json = (
                    isinstance(data, dict)
                    and int(data.get("number", 0)) >= 1
                    and bool(data.get("blocks"))
                )
            except (OSError, ValueError, TypeError):
                ok_json = False
        self.check(
            f"CLI {label} — page_001.json valide",
            ok_json,
            first.name if first else "fichier absent",
        )

    # --- Étape 3 : worker_main sur une page -------------------------------
    def _drive_worker(self, command: List[str], timeout: float = 300.0):
        """Pilote le worker par le protocole : start → messages → EOF.

        Retourne ``(exit_code, messages, lignes_hors_json)``. Le flux stdin
        reste ouvert pendant le traitement (un EOF serait interprété comme
        une annulation par le protocole), puis est fermé pour un arrêt propre.
        """
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=self.child_env(),
        )
        messages: List[dict] = []
        invalid = 0
        assert process.stdin is not None and process.stdout is not None
        try:
            start = json.dumps(
                {"type": "command", "action": "start"}, ensure_ascii=False
            )
            process.stdin.write(start + "\n")
            process.stdin.flush()

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                line = process.stdout.readline()
                if not line:  # fin de stdout : le worker s'est arrêté
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    messages.append(json.loads(stripped))
                except json.JSONDecodeError:
                    invalid += 1
                if any(m.get("type") == "page_done" for m in messages):
                    break
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                exit_code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait(timeout=10)
        return exit_code, messages, invalid

    def run_worker_single_page(self) -> None:
        self.section("Étape 3/3 — worker_main simple sur une page (--page 1)")
        if not tesserocr_available(self.child_env()):
            # Le résultat attendu d'un scan réel exige un OCR : sans moteur,
            # la vérification est sautée (SKIP rapporté), pas un échec.
            self.check_skip(
                "worker --page 1 — (toutes vérifications)",
                "tesserocr non installé sur cette machine",
            )
            return
        document = TEST_SUITE / "facts" / "fact_002.pdf"
        if not document.is_file():
            self.check("worker --page 1", False, f"document absent : {document}")
            return

        # R14 : dossier de travail accentué et espacé. Le nettoyage %TEMP%
        # couvre toute l'étape (les vérifications lisent le résultat AVANT).
        temp_root = Path(tempfile.mkdtemp(prefix="aocri worker éàç ü_"))
        work_dir = temp_root / "travail éàç"
        try:
            command = [
                sys.executable, "-u", "-m", WORKER_MODULE,
                "--mode", "simple",
                "--input", str(document),
                "--page", "1",
                "--work-dir", str(work_dir),
            ]
            try:
                exit_code, messages, invalid = self._drive_worker(command)
            except subprocess.TimeoutExpired:
                self.check(
                    "worker --page 1 — exit 0 et JSON pur", False,
                    "délai de 300 s dépassé",
                )
                return

            done = [m for m in messages if m.get("type") == "page_done"]
            progress = [m for m in messages if m.get("type") == "progress"]
            errors = [m for m in messages if m.get("type") == "error"]
            self.check(
                "worker --page 1 — exit 0 et JSON pur",
                exit_code == 0 and invalid == 0 and not errors,
                f"exit={exit_code}, {invalid} ligne(s) hors JSON, "
                f"erreurs={[str(m.get('message'))[:80] for m in errors]}",
            )
            self.check(
                "worker --page 1 — un page_done, page 1 réelle",
                len(done) == 1 and done and int(done[0].get("page", 0)) == 1,
                f"page_done={[m.get('page') for m in done]}",
            )
            self.check(
                "worker --page 1 — progression totale = 1",
                bool(progress) and {m.get("total") for m in progress} == {1},
                f"{len(progress)} messages progress",
            )
            result_path = Path(done[0]["result_path"]) if done else None
            ok_file = False
            detail = "résultat absent"
            if result_path is not None and result_path.is_file():
                try:
                    data = json.loads(result_path.read_text(encoding="utf-8"))
                    text = "".join(
                        str(block.get("text", ""))
                        for block in data.get("blocks", [])
                    )
                    accents = sum(1 for ch in text if ch in ACCENTED_CHARS)
                    ok_file = (
                        int(data.get("number", 0)) == 1
                        and len(text) > 100
                        and accents >= 1
                        and result_path.parent == work_dir.resolve()
                    )
                    detail = (
                        f"page {data.get('number')}, {len(text)} caractères, "
                        f"{accents} accents, dossier accentué respecté"
                    )
                except (OSError, ValueError, TypeError) as exc:
                    detail = str(exc)
            self.check(
                "worker --page 1 — résultat page_001.json valide (R14)",
                ok_file,
                detail,
            )
        finally:
            # La recette ne pollue pas %TEMP% : nettoyage même en cas d'échec.
            shutil.rmtree(temp_root, ignore_errors=True)

    # --- Rapport ----------------------------------------------------------
    def report(self) -> int:
        passed = sum(1 for _, ok, _ in self.results if ok)
        total = len(self.results)
        failed = [label for label, ok, _ in self.results if not ok]
        print("\n" + "=" * 64, flush=True)
        print(f"RAPPORT D'ACCEPTATION v1.0 — {passed}/{total} vérifications PASS")
        if self.skipped:
            print("Vérifications sautées :")
            for label, detail in self.skipped:
                print(f"  - {label} ({detail})")
        if failed:
            print("Étapes en échec :")
            for label in failed:
                print(f"  - {label}")
            print("RECETTE GLOBALE : FAIL")
            return 1
        print("RECETTE GLOBALE : PASS")
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        prog="python scripts/acceptance.py",
        description="Recette d'acceptation v1.0 d'AOCRI (tests + CLI + worker).",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="ne relance pas pytest (déjà exécuté séparément)",
    )
    args = parser.parse_args(argv)

    recipe = Acceptance()
    print("AOCRI v1.0 — recette d'acceptation", flush=True)
    if not args.skip_tests:
        recipe.run_test_suite()
    recipe.run_cli_documents()
    recipe.run_worker_single_page()
    return recipe.report()


def _configure_stdio() -> None:
    """UTF-8 explicite (R14) : rapports et chemins accentués lisibles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - flux non reconfigurable
            pass


if __name__ == "__main__":
    raise SystemExit(main())
