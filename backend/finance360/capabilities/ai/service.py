"""The AI service layer: status and the log's read side (ADR-0021 sections 6 and 7).

The API calls this, never the gateway's internals. Nothing here is secret: the status carries no
key, provider credential or model name beyond what the log already shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from finance360.capabilities.ai import caps, providers
from finance360.capabilities.ai import log as ai_log
from finance360.core.ai_settings import AiSettings
from finance360.core.models.ai_log import AiLog

#: ``reason`` when AI is not configured (nothing is wrong; it is simply off).
OFF = "off"

DEFAULT_LOG_LIMIT = 50
MAX_LOG_LIMIT = 200


#: How Ask finds Passages (ADR-0023 section 1). Keyword search is always on; embeddings come later.
RETRIEVAL_KEYWORD = "keyword"


@dataclass(frozen=True)
class AiStatus:
    enabled: bool
    #: None when enabled; ``off``, ``monthly_cap`` or ``daily_limit`` otherwise.
    reason: str | None
    #: The local date a cap lifts.
    resets_on: date | None = None
    #: Whether a model is set up on the host to write answers (even if a cap has paused it now).
    answering: bool = False
    retrieval: str = RETRIEVAL_KEYWORD


def status(session: Session, settings: AiSettings, task: str = "ask") -> AiStatus:
    """Whether ``task`` may call the model now (the daily limit applies to Ask only). Search and the
    Rule figure word matcher need no model, so ``enabled`` says nothing about them."""
    if settings.provider is None or providers.get(settings.provider) is None:
        return AiStatus(False, OFF)
    capped = caps.check(session, settings, ai_log.utcnow(), task)
    if capped.reason is not None:
        return AiStatus(False, capped.reason, capped.resets_on, answering=True)
    return AiStatus(True, None, answering=True)


def log_entries(session: Session, limit: int = DEFAULT_LOG_LIMIT) -> list[AiLog]:
    """The AI log, newest first."""
    return ai_log.recent(session, max(1, min(limit, MAX_LOG_LIMIT)))
