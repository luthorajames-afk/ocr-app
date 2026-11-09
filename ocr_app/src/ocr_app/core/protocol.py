"""Protocole de communication entre l'interface et les workers.

Les échanges se font par lignes JSON sur stdin/stdout :

    {"type": "progress", "page": 3, "total": 12, "stage": "tables"}
    {"type": "page_done", "page": 3, "result_path": "...\\\\page_003.json"}
    {"type": "error", "page": 4, "message": "..."}

Le protocole est volontairement générique : un worker factice peut l'implémenter
pour valider la progression et l'annulation avant d'y brancher un moteur OCR.

Toutes les lignes sont encodées en UTF-8 (R14) et ``parse_line`` garantit qu'un
message décodé est toujours un objet JSON (dict), jamais une liste ou un scalaire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MessageType(str, Enum):
    PROGRESS = "progress"
    PAGE_DONE = "page_done"
    ERROR = "error"
    COMMAND = "command"
    ACK = "ack"


@dataclass
class ProgressMessage:
    page: int
    total: int
    stage: str

    def to_json(self) -> str:
        return json.dumps(
            {"type": MessageType.PROGRESS.value,
             "page": self.page, "total": self.total, "stage": self.stage},
            ensure_ascii=False,
        )


@dataclass
class PageDoneMessage:
    page: int
    result_path: str

    def to_json(self) -> str:
        return json.dumps(
            {"type": MessageType.PAGE_DONE.value,
             "page": self.page, "result_path": self.result_path},
            ensure_ascii=False,
        )


@dataclass
class ErrorMessage:
    page: int
    message: str

    def to_json(self) -> str:
        return json.dumps(
            {"type": MessageType.ERROR.value,
             "page": self.page, "message": self.message},
            ensure_ascii=False,
        )


@dataclass
class CommandMessage:
    """Commande envoyée de l'interface vers le worker : start/pause/resume/cancel."""
    action: str
    page: Optional[int] = None

    def to_json(self) -> str:
        payload = {"type": MessageType.COMMAND.value, "action": self.action}
        if self.page is not None:
            payload["page"] = self.page
        return json.dumps(payload, ensure_ascii=False)


def parse_line(line: str) -> dict:
    """Parse une ligne JSON brute en dictionnaire.

    Lève ``ValueError`` si la ligne est vide, n'est pas du JSON valide ou ne
    représente pas un objet JSON (un worker ne doit jamais envoyer de liste).
    """
    if not isinstance(line, str):
        raise ValueError("ligne de protocole non textuelle")
    stripped = line.strip().lstrip("\ufeff")
    if not stripped:
        raise ValueError("ligne vide")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"ligne JSON invalide (ligne {exc.lineno}, colonne {exc.colno}) : "
            f"{exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"message JSON invalide : objet attendu, {type(data).__name__} reçu"
        )
    return data
