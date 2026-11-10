"""Bootstrap v2 : vérifie la structure, la gouvernance et le corpus T0.1-T0.3."""

from __future__ import annotations

import json
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]


def test_metadata_version_is_strict_semver_alpha() -> None:
    from core.metadata import APP_VERSION, PAGE_SCHEMA_VERSION

    assert APP_VERSION == "0.1.0-alpha.0"
    assert isinstance(PAGE_SCHEMA_VERSION, int) and PAGE_SCHEMA_VERSION >= 1


def test_governance_files_exist() -> None:
    for name in ("AGENTS.md", "CHANGELOG.md", "requirements-dev.txt", "pyproject.toml"):
        assert (V2_ROOT / name).is_file(), f"fichier de gouvernance manquant : {name}"


def test_agents_md_encodes_mockup_first_and_roles() -> None:
    text = (V2_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Mockup" in text
    for role in ("Planificateur", "Implémenteur", "Testeur", "Relecteur Sécurité", "Documentaliste"):
        assert role in text, f"rôle manquant : {role}"
    assert "JSON Lines" in text


def test_requirements_dev_contains_tooling() -> None:
    text = (V2_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    for package in ("pytest", "pytest-qt", "pytest-socket", "ruff", "mypy", "bandit", "pydantic", "pip-audit"):
        assert package in text, f"outil manquant : {package}"


def test_structure_matches_agents_md() -> None:
    expected = [
        "src/core/gui",
        "src/core/pipeline",
        "src/core/io",
        "src/workers/medium",
        "src/workers/advanced",
        "tests/unit",
        "tests/integration",
        "tests/data",
    ]
    for rel in expected:
        assert (V2_ROOT / rel).is_dir(), f"répertoire manquant : {rel}"


def test_test_corpus_has_30_documents_with_truth() -> None:
    manifest_path = V2_ROOT / "tests" / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = manifest["documents"]
    assert len(documents) == 30, f"30 documents attendus, reçu {len(documents)}"

    themes = {entry["theme"] for entry in documents}
    assert {"facture", "formulaire", "scan"} <= themes

    for entry in documents:
        doc = V2_ROOT / "tests" / "data" / entry["document"]
        truth = V2_ROOT / "tests" / "data" / entry["truth"]
        assert doc.is_file(), f"document manquant : {doc}"
        assert truth.is_file(), f"vérité terrain manquante : {truth}"
        assert truth.read_text(encoding="utf-8").strip(), f"truth vide : {truth}"
