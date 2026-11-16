"""Plain types the API and the Ask service share."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Turn:
    """One earlier message of the in-session thread (the client keeps the thread; ADR-0021 s.8)."""

    role: Literal["user", "assistant"]
    text: str
