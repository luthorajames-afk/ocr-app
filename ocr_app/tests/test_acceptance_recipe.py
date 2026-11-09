"""Tests de la recette d'acceptation ``scripts/acceptance.py`` (phase 8).

La recette est exécutée en sous-processus, depuis le dépôt, comme un
utilisateur le ferait (``python scripts/acceptance.py``) :

- exit 0 et rapport « RECETTE GLOBALE : PASS » dans la sortie ;
- travail exclusivement dans %TEMP% : aucun résidu ``aocri_acceptation*`` /
  ``aocri worker*`` après exécution ;
- le dépôt n'est pas modifié : ``git status --porcelain`` identique avant et
  après (ni data/, ni src/, aucun nouveau fichier — la phase 8 étant en cours
  de revue, on compare l'état, on n'exige pas un arbre propre).

Garde anti-récursion : la recette relance elle-même ``pytest tests/ -q`` ;
pour que cette exécution interne ne ré-exécute pas (à l'infini) le présent
module, ce dernier se saute quand la variable ``AOCRI_ACCEPTANCE_CHILD`` est
posée — variable que ce test passe à la recette et que ``child_env()``
propage à son propre pytest interne.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "acceptance.py"

pytestmark = pytest.mark.skipif(
    os.environ.get("AOCRI_ACCEPTANCE_CHILD") == "1",
    reason="exécution interne de la recette (garde anti-récursion)",
)


def _temp_residues() -> list[Path]:
    """Dossiers/fichiers de recette restés dans %TEMP% (aocri_acceptation*, aocri worker*)."""
    temp = Path(os.environ.get("TEMP", os.environ.get("TMP", str(Path.home()))))
    if not temp.is_dir():
        return []
    residues: list[Path] = []
    for pattern in ("aocri_acceptation*", "aocri worker*", "aocri worker *"):
        residues.extend(temp.glob(pattern))
    return residues


def _git_status() -> set[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, f"git status a échoué : {completed.stderr}"
    return set(completed.stdout.splitlines())


def _run_recipe(extra_args: list[str], timeout: float) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AOCRI_ACCEPTANCE_CHILD"] = "1"  # garde anti-récursion (cf. docstring)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra_args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


@pytest.mark.skipif(not SCRIPT.is_file(), reason="scripts/acceptance.py absent")
def test_acceptance_recipe_full_run_passes_and_leaves_no_trace() -> None:
    """Recette complète : exit 0, « PASS » rapporté, dépôt et %TEMP% intacts."""
    git_before = _git_status()
    residues_before = _temp_residues()

    completed = _run_recipe([], timeout=1500.0)

    output = completed.stdout or ""
    assert completed.returncode == 0, (
        "la recette complète doit terminer en exit 0 (PASS) ; "
        f"sortie tail : {output[-2000:]!r} ; stderr : {completed.stderr[-500:]!r}"
    )
    assert "PASS" in output, "le rapport doit contenir des vérifications PASS"
    assert "RECETTE GLOBALE : PASS" in output, output[-800:]
    assert "RECETTE GLOBALE : FAIL" not in output
    assert "Traceback" not in (completed.stderr or ""), (
        "la recette doit rester blindée (pas d'exception non rapportée)"
    )

    # Les 3 étapes ont été exécutées (rapport nominal, moteur présent).
    assert "Étape 1/3" in output and "Étape 2/3" in output and "Étape 3/3" in output

    # Aucun résidu de travail dans %TEMP% (nettoyage même en cas d'échec).
    residues_after = _temp_residues()
    new_residues = [path for path in residues_after if path not in residues_before]
    assert new_residues == [], (
        f"la recette a laissé des résidus dans %TEMP% : {new_residues!r}"
    )

    # Le dépôt n'est pas modifié par la recette (data/, src/, scripts/…).
    git_after = _git_status()
    assert git_after == git_before, (
        "la recette ne doit rien modifier dans le dépôt ; "
        f"écarts : {sorted(git_after ^ git_before)!r}"
    )


@pytest.mark.skipif(not SCRIPT.is_file(), reason="scripts/acceptance.py absent")
def test_acceptance_recipe_skip_tests_mode_still_verifies_ocr() -> None:
    """Mode rapide (``--skip-tests``) : exit 0, OCR réel vérifié, PASS global."""
    git_before = _git_status()

    completed = _run_recipe(["--skip-tests"], timeout=900.0)

    output = completed.stdout or ""
    assert completed.returncode == 0, (
        f"recette --skip-tests doit PASS ; sortie tail : {output[-1500:]!r}"
    )
    assert "RECETTE GLOBALE : PASS" in output
    # Le pytest interne est sauté, mais les étapes OCR restent vérifiées.
    assert "Étape 2/3" in output and "Étape 3/3" in output
    assert "Étape 1/3" not in output

    assert _git_status() == git_before, "la recette ne doit rien modifier"
