"""Tests étendus du cœur de l'application : cas limites.

Complète ``tests/test_smoke.py`` sans le modifier. Couvre :

- le roundtrip JSON de ``Page`` / ``Document`` avec accents, tableaux,
  cellules ``None`` et ``merged_from`` ;
- ``Page.save_json`` / ``Page.load_json`` sur un chemin accentué avec espaces (R14) ;
- ``Exporter`` : document vide, tableau vide, cellules ``None``, unicode ;
- ``TaskQueue`` : transitions invalides, mise à jour d'un identifiant inconnu,
  callbacks ;
- ``ModuleRegistry`` : dossier de modules factice (manifeste valide, JSON
  corrompu, manifeste absent).

Aucune dépendance à PySide6 ni à un moteur OCR.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ocr_app.core.data_model import (
    Block,
    BlockType,
    Cell,
    Document,
    Page,
    Table,
)
from ocr_app.core.exports import Exporter
from ocr_app.core.task_queue import TaskQueue, TaskStatus
from ocr_app.modules.module_manifest import (
    ModuleManifest,
    ModuleRegistry,
    sha256_file,
)

UNICODE_TEXT = "éèàç ÀÉÎÕÜ 漢字 Кириллица 😀 — “guillemets” ’apostrophe’"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rich_page() -> Page:
    """Page représentative : accents, tableau, cellule None, fusion, unicode."""
    table = Table(
        rows=2,
        cols=2,
        grid=[
            [
                Cell(
                    text="En-tête éàç",
                    row_index=0,
                    col_index=0,
                    merged_from=(0, 0, 0, 1),
                ),
                Cell(text="Valeur 漢字", row_index=0, col_index=1),
            ],
            [
                None,
                Cell(text=UNICODE_TEXT, row_index=1, col_index=1),
            ],
        ],
    )
    return Page(
        number=3,
        source=r"C:\dossier accentué\document éàç.pdf",
        confidence=0.87,
        blocks=[
            Block(
                type=BlockType.TITLE,
                text="Titre éàç 😀",
                bbox=(10.0, 20.0, 300.0, 45.0),
                confidence=0.99,
                block_id="titre-1",
            ),
            Block(
                type=BlockType.PARAGRAPH,
                text=UNICODE_TEXT,
                bbox=(10.0, 50.0, 300.0, 80.0),
                confidence=0.95,
            ),
            Block(
                type=BlockType.TABLE,
                text="Tableau fusionné",
                bbox=(10.0, 90.0, 300.0, 180.0),
                confidence=0.9,
                table=table,
                block_id="tableau-1",
            ),
            Block(
                type=BlockType.IMAGE,
                text="",
                bbox=(10.0, 190.0, 300.0, 250.0),
                confidence=0.5,
            ),
        ],
    )


def _write_valid_module(parent: Path, module_id: str = "medium-ocr") -> Path:
    """Crée un module factice valide (worker + module.json avec SHA-256 correct)."""
    parent.mkdir(parents=True)
    worker_path = parent / "worker_medium.exe"
    worker_path.write_bytes(f"worker factice {module_id} éà".encode("utf-8"))
    manifest = {
        "id": module_id,
        "version": "1.2.0",
        "min_app_version": "1.0.0",
        "mode": "medium",
        "worker": worker_path.name,
        "sha256": sha256_file(str(worker_path)),
    }
    (parent / "module.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return parent


# ---------------------------------------------------------------------------
# Modèle de données : roundtrip JSON
# ---------------------------------------------------------------------------
def test_page_json_roundtrip_with_accents_tables_none_and_merged(tmp_path):
    page = _rich_page()

    # Roundtrip en mémoire (dict) : les tuples sont conservés.
    restored = Page.from_dict(page.to_dict())
    assert restored.number == 3
    assert restored.source == page.source
    assert restored.confidence == pytest.approx(0.87)
    assert [b.type for b in restored.blocks] == [
        BlockType.TITLE,
        BlockType.PARAGRAPH,
        BlockType.TABLE,
        BlockType.IMAGE,
    ]
    assert restored.blocks[0].text == "Titre éàç 😀"
    assert restored.blocks[0].block_id == "titre-1"
    assert restored.blocks[1].text == UNICODE_TEXT
    assert restored.blocks[2].bbox == (10.0, 90.0, 300.0, 180.0)

    table = restored.blocks[2].table
    assert table is not None
    assert (table.rows, table.cols) == (2, 2)
    assert table.grid[0][0].merged_from == (0, 0, 0, 1)
    assert table.grid[1][0] is None
    assert table.grid[1][1].text == UNICODE_TEXT
    assert restored.blocks[3].table is None

    # Roundtrip réellement JSON : save_json/load_json via fichier.
    # (JSON transforme les tuples en listes ; les valeurs restent identiques.)
    path = tmp_path / "page_003.json"
    page.save_json(str(path))
    loaded = Page.load_json(str(path))

    assert loaded.number == 3
    assert loaded.source == page.source
    assert loaded.confidence == pytest.approx(0.87)
    assert loaded.blocks[2].table.grid[0][0].merged_from is not None
    assert tuple(loaded.blocks[2].table.grid[0][0].merged_from) == (0, 0, 0, 1)
    assert loaded.blocks[2].table.grid[1][0] is None
    assert loaded.blocks[1].text == UNICODE_TEXT


def test_page_roundtrip_empty_defaults():
    page = Page(number=1)
    restored = Page.from_dict(page.to_dict())

    assert restored.number == 1
    assert restored.blocks == []
    assert restored.source is None
    assert restored.confidence == 1.0


def test_page_save_load_json_accented_space_path(tmp_path):
    """R14 : dossier et fichier avec accents et espaces."""
    folder = tmp_path / "travail accentué éà" / "sous dossier avec espaces"
    folder.mkdir(parents=True)
    path = folder / "page 003 éàç.json"

    page = _rich_page()
    page.save_json(str(path))

    assert path.is_file()
    raw = path.read_text(encoding="utf-8")
    # ensure_ascii=False : les accents sont écrits tels quels, pas en \uXXXX.
    assert "éàç" in raw
    assert "\\u00e9" not in raw

    loaded = Page.load_json(str(path))
    assert loaded.number == page.number
    assert loaded.source == page.source
    assert loaded.confidence == pytest.approx(page.confidence)
    assert len(loaded.blocks) == len(page.blocks)
    assert tuple(loaded.blocks[2].table.grid[0][0].merged_from) == (0, 0, 0, 1)
    assert loaded.blocks[2].table.grid[1][0] is None


def test_document_json_roundtrip_accented_path(tmp_path):
    doc = Document(
        title="Rapport été 2026 — éàç",
        source_path=r"D:\projets accentué\entrée éàç.pdf",
    )
    doc.add_page(_rich_page())
    doc.add_page(Page(number=4))

    folder = tmp_path / "sorties accentuées avec espaces"
    folder.mkdir()
    path = folder / "document éàç.json"
    doc.save_json(str(path))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "Rapport été 2026 — éàç"
    assert data["source_path"] == r"D:\projets accentué\entrée éàç.pdf"
    assert [p["number"] for p in data["pages"]] == [3, 4]

    restored_pages = [Page.from_dict(p) for p in data["pages"]]
    assert restored_pages[0].blocks[0].text == "Titre éàç 😀"
    assert restored_pages[0].blocks[2].table.grid[1][0] is None
    assert restored_pages[1].blocks == []


def test_document_save_json_empty(tmp_path):
    doc = Document(title="Document vide éà")
    path = tmp_path / "vide éà.json"
    doc.save_json(str(path))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "Document vide éà"
    assert data["pages"] == []
    assert data["source_path"] is None


# ---------------------------------------------------------------------------
# Exporter : cas limites
# ---------------------------------------------------------------------------
def test_exporter_empty_document(tmp_path):
    exporter = Exporter(Document(title="Document vide éà"))

    assert exporter.to_plain_text() == ""
    assert exporter.to_markdown() == "# Document vide éà\n"

    html = exporter.to_html()
    assert "<title>Document vide éà</title>" in html
    assert "<h1>Document vide éà</h1>" in html
    assert "Page" not in html
    assert "<table" not in html

    folder = tmp_path / "exports accentués avec espaces"
    folder.mkdir()
    txt_path = folder / "vide éà.txt"
    md_path = folder / "vide éà.md"
    html_path = folder / "vide éà.html"

    exporter.save_plain_text(str(txt_path))
    exporter.save_markdown(str(md_path))
    exporter.save_html(str(html_path))

    assert txt_path.read_text(encoding="utf-8") == ""
    assert md_path.read_text(encoding="utf-8") == "# Document vide éà\n"
    assert html_path.read_text(encoding="utf-8") == html


def test_exporter_empty_table():
    doc = Document(title="Tableaux vides")
    doc.add_page(
        Page(
            number=1,
            blocks=[
                Block(
                    type=BlockType.TABLE,
                    table=Table(rows=0, cols=0, grid=[]),
                ),
                Block(type=BlockType.TABLE, table=None),
            ],
        )
    )
    exporter = Exporter(doc)

    md = exporter.to_markdown()
    assert "[tableau vide]" in md
    assert md.count("[tableau]") == 1  # bloc TABLE sans objet Table

    html = exporter.to_html()
    assert "<p>[tableau vide]</p>" in html
    assert "<p>[tableau]</p>" in html
    assert "<table" not in html

    txt = exporter.to_plain_text()
    assert txt.count("[tableau]") == 2


def test_exporter_table_with_none_cells():
    table = Table(
        rows=2,
        cols=3,
        grid=[
            [
                Cell(text="A", row_index=0, col_index=0),
                None,
                Cell(text="C", row_index=0, col_index=2),
            ],
            [
                None,
                Cell(text="éèàç", row_index=1, col_index=1),
                None,
            ],
        ],
    )
    doc = Document(title="Cellules nulles")
    doc.add_page(
        Page(number=1, blocks=[Block(type=BlockType.TABLE, table=table)])
    )
    exporter = Exporter(doc)

    md = exporter.to_markdown()
    assert "| A |  | C |" in md
    assert "|  | éèàç |  |" in md
    assert "None" not in md

    html = exporter.to_html()
    assert "<table" in html
    assert "<th></th>" in html
    assert html.count("<td></td>") == 2
    assert "None" not in html

    txt = exporter.to_plain_text()
    assert "[tableau]" in txt


def test_exporter_unicode_content(tmp_path):
    doc = Document(title="Rapport unicode — éàç")
    doc.add_page(
        Page(
            number=1,
            blocks=[
                Block(type=BlockType.TITLE, text="Été 2026 : 漢字 😀"),
                Block(type=BlockType.PARAGRAPH, text=UNICODE_TEXT),
                Block(
                    type=BlockType.TABLE,
                    table=Table(
                        rows=1,
                        cols=1,
                        grid=[[Cell(text="Кириллица — 漢字")]],
                    ),
                ),
            ],
        )
    )
    exporter = Exporter(doc)

    md = exporter.to_markdown()
    html = exporter.to_html()
    txt = exporter.to_plain_text()
    for output in (md, html, txt):
        assert UNICODE_TEXT in output
        assert "漢字" in output
        assert "😀" in output
    assert "Кириллица — 漢字" in md
    assert "Кириллица — 漢字" in html

    folder = tmp_path / "exports unicode éà"
    folder.mkdir()
    for name, save in (
        ("doc éà.md", exporter.save_markdown),
        ("doc éà.html", exporter.save_html),
        ("doc éà.txt", exporter.save_plain_text),
    ):
        path = folder / name
        save(str(path))
        assert UNICODE_TEXT in path.read_text(encoding="utf-8")


def test_exporter_block_types_markdown_and_html():
    doc = Document(title="Types de blocs")
    doc.add_page(
        Page(
            number=1,
            blocks=[
                Block(type=BlockType.TITLE, text="Titre éà"),
                Block(type=BlockType.PARAGRAPH, text="Paragraphe éà"),
                Block(type=BlockType.IMAGE, text="image ignorée"),
                Block(type=BlockType.HEADER, text="En-tête"),
                Block(type=BlockType.FOOTER, text="Pied de page"),
                Block(
                    type=BlockType.TABLE,
                    table=Table(
                        rows=2,
                        cols=2,
                        grid=[
                            [Cell(text="H1"), Cell(text="H2")],
                            [Cell(text="V1"), Cell(text="V2")],
                        ],
                    ),
                ),
            ],
        )
    )
    exporter = Exporter(doc)

    md = exporter.to_markdown()
    assert "## Page 1" in md
    assert "# Titre éà\n" in md
    assert "Paragraphe éà" in md
    assert "[image]" in md
    assert "En-tête" in md
    assert "Pied de page" in md
    assert "| H1 | H2 |" in md
    assert "| V1 | V2 |" in md

    html = exporter.to_html()
    assert "<h1>Titre éà</h1>" in html
    assert "<p>Paragraphe éà</p>" in html
    assert "<div class='image'>[image]</div>" in html
    assert "<p>En-tête</p>" in html
    assert "<p>Pied de page</p>" in html
    assert "<table border='1'>" in html


# ---------------------------------------------------------------------------
# TaskQueue : cas limites
# ---------------------------------------------------------------------------
def test_task_queue_unknown_id_returns_none_and_no_callback():
    queue = TaskQueue()
    events: list[object] = []
    queue.set_on_change(events.append)

    assert queue.get("inconnu") is None
    assert queue.update_progress("inconnu", status=TaskStatus.RUNNING) is None
    assert queue.update_progress("inconnu", pages_done=5) is None
    assert queue.pause("inconnu") is None
    assert queue.resume("inconnu") is None
    assert queue.cancel("inconnu") is None

    assert events == []
    assert queue.list() == []


def test_task_queue_callbacks_on_add_and_update():
    queue = TaskQueue()
    seen: list[tuple[str, TaskStatus]] = []
    queue.set_on_change(lambda task: seen.append((task.id, task.status)))

    first = queue.add("premier éà.pdf", mode="simple")
    second = queue.add("second.pdf", mode="medium", total_pages=4)
    assert [(task_id, status) for task_id, status in seen] == [
        (first.id, TaskStatus.PENDING),
        (second.id, TaskStatus.PENDING),
    ]

    queue.update_progress(first.id, status=TaskStatus.RUNNING, pages_done=1)
    queue.cancel(second.id)

    assert [(task_id, status) for task_id, status in seen] == [
        (first.id, TaskStatus.PENDING),
        (second.id, TaskStatus.PENDING),
        (first.id, TaskStatus.RUNNING),
        (second.id, TaskStatus.CANCELLED),
    ]
    assert all(task_id in (first.id, second.id) for task_id, _ in seen)


def test_task_queue_partial_update_preserves_other_fields():
    queue = TaskQueue()
    task = queue.add(r"C:\chemin accentué\doc éà.pdf", mode="advanced", total_pages=10)
    created_at = task.updated_at

    time.sleep(0.05)
    updated = queue.update_progress(task.id, stage="ocr")

    assert updated is task
    assert updated.stage == "ocr"
    assert updated.status == TaskStatus.PENDING
    assert updated.pages_done == 0
    assert updated.current_page == 0
    assert updated.total_pages == 10
    assert updated.error_message is None
    assert updated.updated_at > created_at

    queue.update_progress(task.id, total_pages=12, error_message="échec éà")
    assert task.total_pages == 12
    assert task.error_message == "échec éà"


def test_task_queue_invalid_transitions_are_applied_without_validation():
    """Le squelette n'a pas de machine à états : documente le comportement réel.

    Les transitions sont appliquées sans contrôle (pause après done, reprise
    après annulation, etc.) et ne lèvent jamais d'exception.
    """
    queue = TaskQueue()
    task = queue.add("doc.pdf", mode="simple")

    queue.update_progress(task.id, status=TaskStatus.DONE)
    assert task.status == TaskStatus.DONE

    assert queue.pause(task.id).status == TaskStatus.PAUSED
    assert queue.resume(task.id).status == TaskStatus.RUNNING

    queue.update_progress(task.id, status=TaskStatus.ERROR, error_message="boom")
    assert queue.cancel(task.id).status == TaskStatus.CANCELLED


def test_task_queue_double_cancel_is_idempotent():
    queue = TaskQueue()
    task = queue.add("doc.pdf", mode="simple")

    first = queue.cancel(task.id)
    second = queue.cancel(task.id)

    assert first is task
    assert second is task
    assert task.status == TaskStatus.CANCELLED
    assert len(queue.list()) == 1


def test_task_queue_list_order_and_identity():
    queue = TaskQueue()
    first = queue.add("a.pdf", mode="simple")
    second = queue.add("b.pdf", mode="medium")
    third = queue.add("c.pdf", mode="advanced")

    listed = queue.list()
    assert [task.id for task in listed] == [first.id, second.id, third.id]
    assert queue.get(first.id) is first
    assert queue.get(second.id) is second
    assert queue.get(third.id) is third

    queue.cancel(second.id)
    # Une tâche annulée reste présente et à sa position d'origine.
    assert [task.id for task in queue.list()] == [first.id, second.id, third.id]


# ---------------------------------------------------------------------------
# ModuleRegistry : dossier factice
# ---------------------------------------------------------------------------
def test_module_registry_scan_valid_corrupt_missing(tmp_path):
    root = tmp_path / "modules accentués éà"
    root.mkdir()

    valid_dir = _write_valid_module(root / "module medium éà", "medium-ocr")

    corrupt_dir = root / "module corrompu"
    corrupt_dir.mkdir()
    (corrupt_dir / "module.json").write_text(
        '{ "id": "corrompu", "version": ', encoding="utf-8"
    )

    missing_dir = root / "module sans manifeste"
    missing_dir.mkdir()

    incomplete_dir = root / "module incomplet"
    incomplete_dir.mkdir()
    (incomplete_dir / "module.json").write_text(
        json.dumps({"id": "incomplet", "version": "1.0.0"}), encoding="utf-8"
    )

    # Un fichier au niveau racine ne doit pas être considéré comme un module.
    (root / "fichier parasite.txt").write_text("pas un module", encoding="utf-8")

    registry = ModuleRegistry(str(root))
    found = registry.scan()

    assert [manifest.id for manifest in found] == ["medium-ocr"]
    assert registry.get("medium-ocr") is not None
    assert registry.get("medium-ocr").path == str(valid_dir)
    assert registry.get("corrompu") is None
    assert registry.get("incomplet") is None
    assert registry.get("inconnu") is None
    assert len(registry.list()) == 1

    # Un second scan ne duplique rien et ignore toujours les entrées invalides.
    assert [manifest.id for manifest in registry.scan()] == ["medium-ocr"]
    assert len(registry.list()) == 1


def test_module_registry_scan_missing_directory(tmp_path):
    registry = ModuleRegistry(str(tmp_path / "dossier absent éà"))

    assert registry.scan() == []
    assert registry.list() == []
    assert registry.get("medium-ocr") is None


def test_module_manifest_verify_sha256(tmp_path):
    folder = tmp_path / "module accentué"
    folder.mkdir()
    worker = folder / "worker.exe"
    worker.write_bytes("contenu original éà".encode("utf-8"))

    manifest = ModuleManifest(
        id="medium-ocr",
        version="1.0.0",
        min_app_version="1.0.0",
        mode="medium",
        worker="worker.exe",
        sha256=sha256_file(str(worker)),
        path=str(folder),
    )
    assert manifest.verify_sha256() is True

    worker.write_bytes(b"contenu altere")
    assert manifest.verify_sha256() is False

    missing_worker = ModuleManifest(
        id="medium-ocr",
        version="1.0.0",
        min_app_version="1.0.0",
        mode="medium",
        worker="absent.exe",
        sha256=manifest.sha256,
        path=str(folder),
    )
    assert missing_worker.verify_sha256() is False

    no_path = ModuleManifest(
        id="medium-ocr",
        version="1.0.0",
        min_app_version="1.0.0",
        mode="medium",
        worker="worker.exe",
        sha256=manifest.sha256,
        path=None,
    )
    assert no_path.verify_sha256() is False


def test_module_manifest_dict_roundtrip():
    manifest = ModuleManifest(
        id="advanced-éà",
        version="2.0.0",
        min_app_version="1.0.0",
        mode="advanced",
        worker="worker_advanced.exe",
        sha256="a" * 64,
        path=r"C:\Modules accentués\éà",
    )

    restored = ModuleManifest.from_dict(manifest.to_dict())

    assert restored == manifest
    assert restored.path.endswith("éà")

    # min_app_version est optionnel côté JSON.
    minimal = ModuleManifest.from_dict(
        {
            "id": "simple-module",
            "version": "1.0.0",
            "mode": "simple",
            "worker": "worker.exe",
            "sha256": "0" * 64,
        }
    )
    assert minimal.min_app_version == "1.0.0"
    assert minimal.path is None


def test_module_registry_is_compatible(tmp_path):
    root = tmp_path / "modules"
    _write_valid_module(root / "module medium", "medium-ocr")
    registry = ModuleRegistry(str(root))
    registry.scan()

    assert registry.is_compatible("medium-ocr", "1.0.0") is True
    assert registry.is_compatible("medium-ocr", "1.2.0") is True
    assert registry.is_compatible("medium-ocr", "9.9.9") is False
    assert registry.is_compatible("inconnu", "1.0.0") is False
