"""Tests de lecture croisée des scripts Inno Setup (phase 7).

Aucune exécution d'ISCC : les trois fichiers ``installers/*.iss`` sont lus et
contrôlés par sections :

- AppId identique (chaîne exacte) dans core/medium/advanced — une seule entrée
  « Applications et fonctionnalités » (R15) ;
- ``PrivilegesRequired=lowest`` partout (R14 : aucune élévation) ;
- ``UsePreviousAppDir=no`` sur les installateurs de module (R15 : jamais
  réécrire le dossier du noyau) ;
- aucun chemin absolu codé en dur (lettres de lecteur) ;
- dossiers des modules ``modules\\medium`` / ``modules\\advanced`` corrects ;
- la section ``[Run]`` ne lance jamais de worker (le worker lit stdin).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = PROJECT_ROOT / "installers"

SHARED_APP_ID = "{{8A0C4D5E-1F2B-4C3D-9E4F-A0C21E5F6001}}"


def _read_text(name: str) -> str:
    path = INSTALLERS / name
    assert path.is_file(), f"installateur manquant : {path}"
    # Les .iss sont encodés UTF-8 avec BOM (accents de l'assistant visibles).
    return path.read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def specs():
    return {
        name: _read_text(f"{name}.iss")
        for name in ("core", "medium", "advanced")
    }


def _sections(text: str) -> dict:
    """Découpe un .iss en {section: [lignes]}, directives #… exclues."""
    result: dict = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(";") or not line:
            continue
        if line.startswith("#"):
            continue
        match = re.match(r"^\[(.+)\]$", line)
        if match:
            current = match.group(1)
            result.setdefault(current, [])
            continue
        if current is not None:
            result[current].append(line)
    return result


def _setup_value(sections: dict, key: str) -> str:
    for line in sections.get("Setup", []):
        match = re.match(rf"^{re.escape(key)}=(.*)$", line)
        if match:
            return match.group(1).strip()
    return ""


def _define(text: str, name: str) -> str:
    match = re.search(rf'^#define\s+{re.escape(name)}\s+"(.*)"\s*$', text, re.M)
    assert match is not None, f"directive #define {name} absente"
    return match.group(1)


# --- AppId partagé ------------------------------------------------------------

def test_app_id_identical_exact_string_in_all_three(specs) -> None:
    app_ids = {name: _define(text, "MyAppId") for name, text in specs.items()}
    assert app_ids == {
        "core": SHARED_APP_ID,
        "medium": SHARED_APP_ID,
        "advanced": SHARED_APP_ID,
    }, "AppId constant partagé ×3 (une seule entrée Applications et fonctionnalités)"

    for name, text in specs.items():
        sections = _sections(text)
        assert _setup_value(sections, "AppId") == "{#MyAppId}", (
            f"{name}.iss : [Setup] AppId doit référencer la constante partagée"
        )


def test_app_version_and_publisher_defined_everywhere(specs) -> None:
    for name, text in specs.items():
        assert _define(text, "MyAppVersion")
        assert _define(text, "MyAppPublisher") == "Kriesty Solutions"


# --- Droits et dossiers -------------------------------------------------------

def test_privileges_required_lowest_everywhere(specs) -> None:
    for name, text in specs.items():
        sections = _sections(text)
        assert _setup_value(sections, "PrivilegesRequired") == "lowest", (
            f"{name}.iss : installation utilisateur sans élévation (R14)"
        )


def test_use_previous_app_dir_no_for_modules(specs) -> None:
    for name in ("medium", "advanced"):
        sections = _sections(specs[name])
        assert _setup_value(sections, "UsePreviousAppDir") == "no", (
            f"{name}.iss : sans cette directive, le module irait polluer le "
            "dossier du noyau (dernière installation enregistrée, R15)"
        )


def test_default_dir_names_match_expected_layout(specs) -> None:
    expected = {
        "core": "{userappdata}\\Programs\\AOCRI",
        "medium": "{userappdata}\\Programs\\AOCRI\\modules\\medium",
        "advanced": "{userappdata}\\Programs\\AOCRI\\modules\\advanced",
    }
    for name, wanted in expected.items():
        sections = _sections(specs[name])
        assert _setup_value(sections, "DefaultDirName") == wanted


def test_no_hardcoded_absolute_paths(specs) -> None:
    for name, text in specs.items():
        matches = re.findall(r"[A-Za-z]:[/\\]", text)
        assert not matches, (
            f"{name}.iss : chemin absolu codé en dur {matches!r} — les sources "
            "doivent rester relatives au fichier .iss"
        )


def test_module_sources_target_their_own_folder(specs) -> None:
    medium_sections = _sections(specs["medium"])
    sources = [line for line in medium_sections.get("Files", []) if "Source:" in line]
    assert sources, "medium.iss doit livrer du contenu"
    assert any("modules\\medium" in line for line in sources)
    assert not any("modules\\advanced" in line for line in sources)

    advanced_sections = _sections(specs["advanced"])
    sources = [line for line in advanced_sections.get("Files", []) if "Source:" in line]
    assert sources, "advanced.iss doit livrer du contenu"
    assert any("modules\\advanced" in line for line in sources)
    assert not any("modules\\medium" in line for line in sources)


# --- [Run] ne lance jamais de worker ------------------------------------------

def test_run_section_never_launches_a_worker(specs) -> None:
    for name, text in specs.items():
        sections = _sections(text)
        for line in sections.get("Run", []):
            if not line.lower().startswith("filename:"):
                continue
            assert "worker" not in line.lower(), (
                f"{name}.iss [Run] lance un worker : {line!r} — le worker lit "
                "stdin, il bloquerait en fin d'installation"
            )


def test_module_installers_have_no_run_section(specs) -> None:
    for name in ("medium", "advanced"):
        assert "Run" not in _sections(specs[name]), (
            f"{name}.iss : rien à lancer en fin d'installation de module"
        )


def test_core_run_chains_match_module_output_names(specs) -> None:
    """Le chaînage [Run] du noyau référence les noms de sortie des modules."""
    core_sections = _sections(specs["core"])
    run_text = "\n".join(core_sections.get("Run", []))
    for name in ("medium", "advanced"):
        output = _setup_value(_sections(specs[name]), "OutputBaseFilename")
        assert output == f"AOCRI-{name}-setup"
        assert f"{{src}}\\{output}.exe" in run_text, (
            f"core.iss doit pouvoir chaîner {output}.exe (case [Tasks], "
            "entrée ignorée si le fichier est absent)"
        )


def test_core_installer_ships_worker_and_tessdata(specs) -> None:
    sections = _sections(specs["core"])
    files_text = "\n".join(sections.get("Files", []))
    assert "worker_simple" in files_text, "worker simple livré à côté du noyau"
    assert "tessdata" in files_text and "traineddata" in files_text, (
        "R4 : tessdata livrée par l'installateur, jamais téléchargée"
    )
    # Le worker simple est installé sous {app}\workers\worker_simple, layout
    # attendu par worker_commands en application gelée.
    assert "DestDir: \"{app}\\workers\\worker_simple\"" in files_text
