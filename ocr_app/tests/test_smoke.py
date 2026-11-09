"""Tests smoke du squelette : import, modèle de données, protocole, file d'attente."""

import json
import tempfile
from pathlib import Path

from ocr_app import __version__
from ocr_app.core.data_model import (
    Page, Block, Cell, Table, BlockType, Document,
)
from ocr_app.core.protocol import (
    ProgressMessage, PageDoneMessage, ErrorMessage, CommandMessage, parse_line,
)
from ocr_app.core.task_queue import TaskQueue, TaskStatus
from ocr_app.core.exports import Exporter


def test_version():
    assert __version__ == "1.0.0"


def test_block_types():
    assert BlockType.TITLE.value == "title"
    assert BlockType.PARAGRAPH.value == "paragraph"
    assert BlockType.TABLE.value == "table"


def test_page_serialization_roundtrip():
    page = Page(
        number=1,
        blocks=[
            Block(
                type=BlockType.TITLE,
                text="Titre",
                bbox=(10.0, 20.0, 200.0, 40.0),
                confidence=0.99,
                block_id="b1",
            ),
            Block(
                type=BlockType.PARAGRAPH,
                text="Paragraphe de test avec accents : éèàç.",
                bbox=(10.0, 50.0, 300.0, 70.0),
                confidence=0.95,
            ),
        ],
    )
    data = page.to_dict()
    assert data["number"] == 1
    assert len(data["blocks"]) == 2
    assert data["blocks"][0]["type"] == "title"
    assert data["blocks"][0]["block_id"] == "b1"

    restored = Page.from_dict(data)
    assert restored.number == 1
    assert restored.blocks[0].type == BlockType.TITLE
    assert restored.blocks[0].text == "Titre"
    assert restored.blocks[1].text == "Paragraphe de test avec accents : éèàç."


def test_table_cell_merge():
    cell = Cell(text="Fusionnée", row_index=0, col_index=0, merged_from=(0, 0, 1, 1))
    assert cell.merged_from == (0, 0, 1, 1)
    d = cell.to_dict()
    assert d["merged_from"] == (0, 0, 1, 1)


def test_document_save_load_json():
    doc = Document(title="Doc test", source_path="/chemin/avec/accès/é.pdf")
    doc.add_page(Page(number=1, blocks=[Block(type=BlockType.PARAGRAPH, text="bonjour")]))
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "doc.json"
        doc.save_json(str(p))
        loaded = Document.__new__(Document)
        # réutiliser from_dict via page load
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["title"] == "Doc test"
        assert data["pages"][0]["blocks"][0]["text"] == "bonjour"


def test_protocol_messages():
    prog = ProgressMessage(page=3, total=12, stage="tables")
    data = json.loads(prog.to_json())
    assert data["type"] == "progress"
    assert data["page"] == 3
    assert data["stage"] == "tables"

    done = PageDoneMessage(page=3, result_path=r"C:\work\page_003.json")
    data = json.loads(done.to_json())
    assert data["type"] == "page_done"
    assert data["result_path"] == r"C:\work\page_003.json"

    err = ErrorMessage(page=4, message="échec de lecture")
    data = json.loads(err.to_json())
    assert data["type"] == "error"
    assert data["message"] == "échec de lecture"

    cmd = CommandMessage(action="pause", page=2)
    data = json.loads(cmd.to_json())
    assert data["type"] == "command"
    assert data["action"] == "pause"
    assert data["page"] == 2

    parsed = parse_line(prog.to_json())
    assert parsed["type"] == "progress"


def test_task_queue_lifecycle():
    q = TaskQueue()
    t = q.add("/chemin/doc avec espaces.pdf", mode="simple", total_pages=10)
    assert t.status == TaskStatus.PENDING
    assert t.pages_done == 0

    q.update_progress(t.id, status=TaskStatus.RUNNING, pages_done=3, current_page=3, stage="ocr")
    t2 = q.get(t.id)
    assert t2.status == TaskStatus.RUNNING
    assert t2.pages_done == 3
    assert t2.stage == "ocr"

    q.pause(t.id)
    assert q.get(t.id).status == TaskStatus.PAUSED

    q.resume(t.id)
    assert q.get(t.id).status == TaskStatus.RUNNING

    q.cancel(t.id)
    assert q.get(t.id).status == TaskStatus.CANCELLED

    assert len(q.list()) == 1


def test_exporter_outputs():
    doc = Document(title="Rapport", source_path="doc.pdf")
    doc.add_page(Page(number=1, blocks=[
        Block(type=BlockType.TITLE, text="Titre du document"),
        Block(type=BlockType.PARAGRAPH, text="Contenu avec accents : éèàç."),
    ]))
    exporter = Exporter(doc)
    md = exporter.to_markdown()
    assert "# Rapport" in md
    assert "Titre du document" in md
    assert "éèàç" in md

    html = exporter.to_html()
    assert "<h1>Rapport</h1>" in html

    txt = exporter.to_plain_text()
    assert "Titre du document" in txt
    assert "éèàç" in txt


def test_table_export():
    from ocr_app.core.data_model import Cell
    table = Table(rows=2, cols=2, grid=[
        [Cell(text="A", row_index=0, col_index=0), Cell(text="B", row_index=0, col_index=1)],
        [Cell(text="C", row_index=1, col_index=0), Cell(text="D", row_index=1, col_index=1)],
    ])
    block = Block(type=BlockType.TABLE, table=table)
    doc = Document(title="T", source_path="x.pdf")
    doc.add_page(Page(number=1, blocks=[block]))
    exporter = Exporter(doc)
    md = exporter.to_markdown()
    assert "| A | B |" in md
    assert "| C | D |" in md
    html = exporter.to_html()
    assert "<table" in html
    assert "A" in html and "D" in html