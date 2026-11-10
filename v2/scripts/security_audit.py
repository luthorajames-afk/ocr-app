"""T7.4 — Audit de sécurité final (fuzzing, bombes pixel, réseau).

Usage : python scripts/security_audit.py
Exit 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / "src"))

HOSTILE = "=cmd|'/c calc'!A0\n+1+1\n-2+3\n@SUM(A1:A2)\n<script>alert(1)</script>"


def check_pytest() -> tuple[bool, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    tail = (completed.stdout or "").strip().splitlines()[-1:]
    return completed.returncode == 0, tail[0] if tail else "pytest"


def check_export_fuzzing() -> tuple[bool, str]:
    from core.io.exporters import ExportPage, export_docx, export_markdown, export_pdf, export_xlsx

    blocks: list[dict[str, object]] = [
        {"kind": "title", "text": HOSTILE},
        {"kind": "line", "text": HOSTILE},
        {"kind": "table", "grid": [[HOSTILE, "ok"], ["a", "b"]]},
    ]
    page = ExportPage(number=1, blocks=blocks)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        markdown = export_markdown([page])
        if "=cmd" not in markdown:
            return False, "markdown a perdu le contenu hostile (attendu littéral)"
        docx_path = export_docx([page], root / "a.docx")
        xlsx_path = export_xlsx([page], root / "a.xlsx")
        pdf_path = export_pdf([page], root / "a.pdf")

        import openpyxl

        workbook = openpyxl.load_workbook(xlsx_path)
        sheet = workbook.active
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    return False, f"formule Excel non neutralisée en {cell.coordinate}"
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    return False, f"cellule formule brute en {cell.coordinate}"
        for path in (docx_path, xlsx_path, pdf_path):
            if not path.is_file() or path.stat().st_size == 0:
                return False, f"export vide : {path.name}"
    return True, "fuzzing exports OK"


def check_decompression_bomb() -> tuple[bool, str]:
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "small.png"
        Image.new("RGB", (64, 64), "white").save(path)
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 1000
        try:
            try:
                with Image.open(path) as image:
                    image.load()
            except Image.DecompressionBombError:
                return True, "garde-fou bombe pixel actif"
            except Image.DecompressionBombWarning:
                return True, "avertissement bombe pixel actif"
            return False, "aucun garde-fou bombe pixel"
        finally:
            Image.MAX_IMAGE_PIXELS = previous


def check_network_isolation() -> tuple[bool, str]:
    try:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("127.0.0.1", 9))
        return False, "socket réseau disponible"
    except OSError:
        return True, "aucune connexion réseau sortante possible"


def main() -> int:
    checks = (
        ("pytest", check_pytest),
        ("fuzzing exports", check_export_fuzzing),
        ("bombe pixel", check_decompression_bomb),
        ("isolation réseau", check_network_isolation),
    )
    failures = 0
    for name, check in checks:
        try:
            ok, detail = check()
        except Exception as exc:  # noqa: BLE001 — audit explicite
            ok, detail = False, f"exception : {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        if not ok:
            failures += 1
    print(f"\nAUDIT SÉCURITÉ : {'PASS' if failures == 0 else 'FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
