"""Tests des réglages persistants ``AppSettings`` (phase 8).

Couvre ``ocr_app.ui.app_settings`` : roundtrip save/load (chemin explicite ou
surchargé par la variable d'environnement ``AOCRI_SETTINGS_DIR``), robustesse
(fichier absent, JSON corrompu, clés invalides → défauts sans lever), DPI borné
à 300 (R7), langues vides → repli fra+eng, écriture **atomique** (aucun
``.tmp`` résiduel), chargement paresseux (aucun fichier créé à l'import ni à la
lecture) et R14 (dossier accentué/espacé, UTF-8 explicite).

Module volontairement sans Qt : ``app_settings`` n'en dépend pas.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ocr_app.ui.app_settings import (
    AppSettings,
    DEFAULT_DPI,
    ENV_SETTINGS_DIR,
    MAX_DPI,
    default_settings,
    load_settings,
    save_settings,
    settings_dir,
    settings_path,
    settings_summary,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings_file(tmp_path: Path) -> Path:
    """Chemin de réglages accentué/espacé hors du vrai %LOCALAPPDATA% (R14)."""
    return tmp_path / "réglages éàç ü" / "settings.json"


# --- Roundtrip ----------------------------------------------------------------

def test_save_load_roundtrip_explicit_path(settings_file: Path) -> None:
    save_settings(AppSettings(languages=["eng"], dpi=150), settings_file)
    loaded = load_settings(settings_file)
    assert loaded.languages == ["eng"]
    assert loaded.dpi == 150

    # Le fichier est un JSON UTF-8 lisible, sans échappement ASCII (R14).
    raw = settings_file.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data == {"languages": ["eng"], "dpi": 150}


def test_roundtrip_canonical_language_order(settings_file: Path) -> None:
    """L'ordre des langues est canonique (fra avant eng), où qu'elles viennent."""
    save_settings(AppSettings(languages=["eng", "fra"], dpi=200), settings_file)
    assert json.loads(settings_file.read_text(encoding="utf-8"))["languages"] == [
        "fra",
        "eng",
    ]
    loaded = load_settings(settings_file)
    assert loaded.languages == ["fra", "eng"]
    assert loaded.language_string() == "fra+eng"


def test_save_creates_missing_parent_directories(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "réglages éàç" / "settings.json"
    save_settings(default_settings(), nested)
    assert nested.is_file()
    assert load_settings(nested).dpi == DEFAULT_DPI


def test_language_string_deduplicates(settings_file: Path) -> None:
    assert AppSettings(languages=["eng", "fra", "eng"]).language_string() == (
        "fra+eng"
    )


# --- Défauts robustes ---------------------------------------------------------

def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    loaded = load_settings(tmp_path / "n'importe où éàç" / "settings.json")
    assert loaded.languages == ["fra", "eng"]
    assert loaded.dpi == DEFAULT_DPI
    # Identique aux défauts officiels.
    assert loaded.languages == default_settings().languages
    assert loaded.dpi == default_settings().dpi


@pytest.mark.parametrize(
    "content",
    [
        "pas du json {",
        "",
        "[1, 2, 3]",  # JSON valide mais pas un objet
        '{"languages": "fra", "dpi": 300}',  # langues hors liste
        '{"languages": [42], "dpi": 300}',  # éléments non textuels
        '{"languages": ["deu"], "dpi": 300}',  # langue inconnue seule
        '{"dpi": "beaucoup"}',  # DPI non numérique
    ],
)
def test_corrupt_or_invalid_json_returns_defaults_without_crash(
    settings_file: Path, content: str
) -> None:
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(content, encoding="utf-8")
    loaded = load_settings(settings_file)  # ne doit jamais lever
    assert loaded.languages == ["fra", "eng"]
    assert loaded.dpi == DEFAULT_DPI


def test_invalid_utf8_bytes_return_defaults(settings_file: Path) -> None:
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_bytes(b"\xff\xfe{\x00}")  # ni UTF-8 ni JSON
    loaded = load_settings(settings_file)
    assert loaded.languages == ["fra", "eng"]
    assert loaded.dpi == DEFAULT_DPI


def test_zero_or_empty_languages_fall_back_to_fra_eng(settings_file: Path) -> None:
    """0 langue dans le JSON → repli cohérent sur fra+eng (jamais vide)."""
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(
        json.dumps({"languages": [], "dpi": 200}), encoding="utf-8"
    )
    loaded = load_settings(settings_file)
    assert loaded.languages == ["fra", "eng"]
    assert loaded.dpi == 200

    # Les clés absentes utilisent aussi les défauts.
    settings_file.write_text("{}", encoding="utf-8")
    loaded = load_settings(settings_file)
    assert loaded.languages == ["fra", "eng"]
    assert loaded.dpi == DEFAULT_DPI


# --- DPI borné (R7) -----------------------------------------------------------

def test_dpi_600_is_capped_to_300(settings_file: Path) -> None:
    """DPI 600 → 300, à la fois en mémoire, au chargement et à l'écriture (R7)."""
    assert AppSettings(dpi=600).effective_dpi() == MAX_DPI

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(
        json.dumps({"languages": ["fra"], "dpi": 600}), encoding="utf-8"
    )
    loaded = load_settings(settings_file)
    assert loaded.dpi == 300

    save_settings(AppSettings(languages=["fra"], dpi=600), settings_file)
    assert json.loads(settings_file.read_text(encoding="utf-8"))["dpi"] == 300


@pytest.mark.parametrize(
    "raw,expected",
    [(150, 150), (200, 200), (300, 300), (0, 300), (-50, 300), ("abc", 300), (None, 300)],
)
def test_dpi_values_are_sanitized(raw, expected, tmp_path: Path) -> None:
    assert AppSettings(dpi=raw).effective_dpi() == expected

    # Même borne au chargement d'un JSON portant la valeur brute.
    if raw is not None:
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps({"languages": ["fra"], "dpi": raw}), encoding="utf-8"
        )
        assert load_settings(path).dpi == expected


# --- Écriture atomique --------------------------------------------------------

def test_atomic_write_leaves_no_tmp_residue(settings_file: Path) -> None:
    save_settings(AppSettings(languages=["eng"], dpi=150), settings_file)
    assert settings_file.is_file()
    assert list(settings_file.parent.glob("*.tmp")) == [], (
        "aucun fichier temporaire ne doit résider après une écriture réussie"
    )

    # Réécriture par-dessus un fichier existant : même garantie.
    save_settings(AppSettings(languages=["fra"], dpi=300), settings_file)
    assert list(settings_file.parent.glob("*.tmp")) == []
    loaded = load_settings(settings_file)
    assert loaded.languages == ["fra"] and loaded.dpi == 300
    assert settings_file.read_text(encoding="utf-8").endswith("\n")


def test_atomic_write_failure_keeps_previous_settings_and_cleans_tmp(
    settings_file: Path, monkeypatch
) -> None:
    """Si ``os.replace`` échoue, l'ancien réglage reste intact, pas de .tmp."""
    save_settings(AppSettings(languages=["fra"], dpi=200), settings_file)
    original = settings_file.read_text(encoding="utf-8")

    real_replace = os.replace

    def broken_replace(src, dst):
        raise OSError("disque plein (simulation)")

    monkeypatch.setattr(
        "ocr_app.ui.app_settings.os.replace", broken_replace
    )
    with pytest.raises(OSError):
        save_settings(AppSettings(languages=["eng"], dpi=150), settings_file)

    monkeypatch.setattr("ocr_app.ui.app_settings.os.replace", real_replace)
    assert list(settings_file.parent.glob("*.tmp")) == [], (
        "le .tmp d'une écriture échouée doit être nettoyé"
    )
    assert settings_file.read_text(encoding="utf-8") == original, (
        "l'ancien réglage reste intact"
    )


# --- Surcharge par variable d'environnement -----------------------------------

def test_env_var_overrides_settings_dir(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "profil éàç"
    monkeypatch.setenv(ENV_SETTINGS_DIR, str(override))

    assert settings_dir() == override
    assert settings_path() == override / "settings.json"

    # save/load sans argument explicite passent par le dossier surchargé.
    save_settings(AppSettings(languages=["eng"], dpi=150))
    assert (override / "settings.json").is_file()
    loaded = load_settings()
    assert loaded.languages == ["eng"] and loaded.dpi == 150

    # Aucune fuite vers le vrai %LOCALAPPDATA% : avec la variable retirée,
    # le chemin retourné retombe sur l'emplacement standard.
    monkeypatch.delenv(ENV_SETTINGS_DIR)
    assert override not in settings_path().parents


def test_env_var_empty_falls_back_to_localappdata(monkeypatch) -> None:
    monkeypatch.setenv(ENV_SETTINGS_DIR, "")
    monkeypatch.setenv("LOCALAPPDATA", str(Path(r"C:\Utilisateurs éàç")))
    assert settings_dir() == Path(r"C:\Utilisateurs éàç") / "AOCRI"


# --- Chargement paresseux -----------------------------------------------------

def test_import_creates_no_settings_file(tmp_path: Path) -> None:
    """À l'import du module, aucun fichier ni dossier n'est créé (lazy)."""
    target = tmp_path / "import lazy éàç"
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    env["PYTHONUTF8"] = "1"
    env[ENV_SETTINGS_DIR] = str(target)
    subprocess.run(
        [sys.executable, "-c", "import ocr_app.ui.app_settings"],
        check=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert not target.exists(), (
        "l'import ne doit créer ni le dossier ni settings.json"
    )


def test_load_creates_no_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "lecture lazy éàç"
    monkeypatch.setenv(ENV_SETTINGS_DIR, str(target))
    loaded = load_settings()  # fichier absent : défauts, sans création
    assert loaded.dpi == DEFAULT_DPI
    assert not target.exists(), "la lecture ne doit rien créer"


def test_module_import_has_no_side_effect() -> None:
    """Sanity : le module n'exécute aucune écriture à l'import (lazy)."""
    import ocr_app.ui.app_settings as module

    # Le module ne doit référencer aucune écriture exécutée au chargement :
    # ses seules écritures vivent dans save_settings (appel explicite).
    assert callable(module.load_settings)
    assert callable(module.save_settings)
    assert module.settings_path().name == "settings.json"


# --- Résumé (toolbar) ---------------------------------------------------------

def test_settings_summary_labels() -> None:
    assert settings_summary(default_settings()) == "FR + EN · 300 DPI"
    assert settings_summary(AppSettings(languages=["eng"], dpi=150)) == (
        "EN · 150 DPI"
    )
    # Langues vides : repli affiché sur les défauts (jamais une chaîne vide).
    assert settings_summary(AppSettings(languages=[], dpi=150)) == (
        "FR + EN · 150 DPI"
    )
