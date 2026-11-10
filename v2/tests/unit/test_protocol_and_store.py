"""T3.1/T3.3 — Tests du protocole et du store de pages."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.pipeline.job_store import (
    PageRecord,
    load_page,
    migrate,
    page_path,
    save_page,
    saved_pages,
)
from core.pipeline.protocol import (
    CommandMessage,
    ErrorMessage,
    PageDoneMessage,
    ProgressMessage,
    ProtocolError,
    dumps,
    parse_line,
)


def test_progress_roundtrip() -> None:
    message = ProgressMessage(page=2, total=5, stage="ocr")
    parsed = parse_line(dumps(message))
    assert isinstance(parsed, ProgressMessage)
    assert parsed.page == 2 and parsed.stage == "ocr"


def test_command_parse_and_validate() -> None:
    parsed = parse_line('{"type":"command","action":"pause"}')
    assert isinstance(parsed, CommandMessage)
    assert parsed.action == "pause"
    with pytest.raises(ProtocolError):
        parse_line('{"type":"command","action":"explode"}')
    with pytest.raises(ProtocolError):
        parse_line('{"type":"progress","page":0,"total":5,"stage":"ocr"}')


def test_unknown_type_and_malformed_json_rejected() -> None:
    with pytest.raises(ProtocolError):
        parse_line('{"type":"unknown"}')
    with pytest.raises(ProtocolError):
        parse_line("{not json")
    with pytest.raises(ProtocolError):
        parse_line("   ")


def test_extra_fields_rejected() -> None:
    with pytest.raises(ProtocolError):
        parse_line('{"type":"error","page":1,"message":"x","surprise":1}')


def test_error_and_page_done_serialization() -> None:
    done = PageDoneMessage(page=3, result_path=r"C:\work\page_003.json")
    assert json.loads(dumps(done))["result_path"].endswith("page_003.json")
    error = ErrorMessage(page=0, message="annulé")
    assert json.loads(dumps(error))["message"] == "annulé"


def test_page_store_roundtrip_atomic(tmp_path: Path) -> None:
    record = PageRecord(
        schema_version=1,
        page=1,
        source="doc éàç.pdf",
        engine="tesseract",
        engine_version="5",
        duration_ms=12.5,
        text="Bonjour éàç",
        blocks=[{"text": "Bonjour", "bbox": [0, 0, 10, 10], "confidence": 0.9, "kind": "line"}],
    )
    path = save_page(record, tmp_path)
    assert path == page_path(tmp_path, 1)
    loaded = load_page(tmp_path, 1)
    assert loaded is not None
    assert loaded.text == "Bonjour éàç"
    assert loaded.blocks[0]["confidence"] == 0.9
    assert not list(tmp_path.glob("*.tmp"))


def test_saved_pages_and_missing_page(tmp_path: Path) -> None:
    assert saved_pages(tmp_path) == set()
    for number in (1, 3):
        save_page(
            PageRecord(1, number, "s", "e", "v", 0.0, f"page {number}", []),
            tmp_path,
        )
    assert saved_pages(tmp_path) == {1, 3}
    assert load_page(tmp_path, 2) is None


def test_schema_newer_than_kernel_is_rejected() -> None:
    with pytest.raises(ValueError):
        migrate({"schema_version": 999})
