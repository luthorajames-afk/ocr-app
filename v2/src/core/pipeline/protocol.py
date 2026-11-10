"""T3.1 — Protocole JSON Lines (IHM ↔ worker), schémas Pydantic stricts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProgressMessage(_Base):
    type: Literal["progress"] = "progress"
    page: int = Field(ge=1)
    total: int = Field(ge=1)
    stage: str


class PageDoneMessage(_Base):
    type: Literal["page_done"] = "page_done"
    page: int = Field(ge=1)
    result_path: str


class ErrorMessage(_Base):
    type: Literal["error"] = "error"
    page: int = Field(ge=0)
    message: str


class CommandMessage(_Base):
    type: Literal["command"] = "command"
    action: Literal["start", "pause", "resume", "cancel"]
    page: int | None = Field(default=None, ge=1)


WorkerMessage = Annotated[
    ProgressMessage | PageDoneMessage | ErrorMessage | CommandMessage,
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[WorkerMessage] = TypeAdapter(WorkerMessage)


class ProtocolError(ValueError):
    """Ligne invalide : JSON malformé, type inconnu ou champ manquant."""


def parse_line(line: str) -> WorkerMessage:
    """Parse une ligne JSON Lines ; lève ProtocolError si invalide."""
    if not line or not line.strip():
        raise ProtocolError("ligne vide")
    try:
        return _ADAPTER.validate_json(line)
    except ValidationError as exc:
        raise ProtocolError(str(exc)) from exc


def dumps(message: WorkerMessage) -> str:
    """Sérialise un message en une ligne JSON (UTF-8, sans saut final)."""
    return message.model_dump_json()
