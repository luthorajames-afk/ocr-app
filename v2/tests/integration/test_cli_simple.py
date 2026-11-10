"""T1.2 — Tests d'intégration du CLI Simple (Tesseract réel)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("tesserocr")

V2_ROOT = Path(__file__).resolve().parents[2]
DATA = V2_ROOT / "tests" / "data"
MANIFEST = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
BY_ID = {entry["id"]: entry for entry in MANIFEST["documents"]}


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(V2_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run_cli(source: Path, output: Path, dpi: int = 200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "core.cli",
            "process",
            "--input",
            str(source),
            "--output",
            str(output),
            "--dpi",
            str(dpi),
        ],
        cwd=V2_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=_cli_env(),
    )


def test_cli_info_reports_tesseract() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "core.cli", "info"],
        cwd=V2_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=_cli_env(),
    )
    assert completed.returncode == 0
    assert "AOCRI" in completed.stdout
    assert "Tesseract" in completed.stdout


def test_cli_native_pdf_uses_native_text(tmp_path: Path) -> None:
    entry = BY_ID["fact_001"]
    output = tmp_path / "sortie éàç.txt"
    completed = _run_cli(DATA / entry["document"], output, dpi=150)
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    text = output.read_text(encoding="utf-8")
    assert "Dupont" in text
    assert "natives 1, OCR 0" in completed.stdout
    assert "é" in text


def test_cli_scan_runs_ocr(tmp_path: Path) -> None:
    entry = next(e for e in MANIFEST["documents"] if e["document"].endswith(".jpg"))
    output = tmp_path / "scan éàç.txt"
    completed = _run_cli(DATA / entry["document"], output, dpi=200)
    assert completed.returncode == 0, completed.stderr
    text = output.read_text(encoding="utf-8")
    assert "OCR 1" in completed.stdout
    assert len(text.strip()) > 40
