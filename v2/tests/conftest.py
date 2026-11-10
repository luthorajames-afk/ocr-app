"""Configuration pytest AOCRI v2 : `src/` importable + étanchéité réseau.

AGENTS.md §1 : aucun appel réseau dans le code — vérifié par pytest-socket
(sockets désactivées pour toute la suite).
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def pytest_configure(config) -> None:
    """Interdit toute connexion réseau pendant les tests (politique hors-ligne)."""
    try:
        import pytest_socket
    except ImportError:  # pragma: no cover — plugin de développement
        return
    pytest_socket.disable_socket(allow_unix_socket=False)
