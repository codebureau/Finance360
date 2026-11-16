"""The AI gateway: the one way any feature reaches a model (ADR-0005, ADR-0021).

``complete`` takes a *purpose* (``task`` and ``why``), a *data class* that declares which parts a
call may carry and which of them are public, and the parts themselves. It then:

1. refuses a part its data class does not declare;
2. stops, **before the provider is touched**, when AI is off or a cap in the log is reached;
3. redacts every non-public part against the household's vocabulary;
4. calls the registered provider with the task's model;
5. logs what was sent (redacted) and received (still tokenised), with the cost;
6. restores names in the reply.

Later AI features add a data class and a caller; this module does not change. The caller passes its
``Session`` for reads and to find the database; each log row commits in its own short transaction
(see ``log.py``), so a refusal or failure is kept even if the caller's transaction rolls back.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from finance360.capabilities.ai import caps, pricing, providers, vocabulary
from finance360.capabilities.ai import log as ai_log
from finance360.capabilities.ai.errors import AiOff, AiUnavailable, Busy, CapReached
from finance360.capabilities.ai.redact import Redactor
from finance360.core.ai_settings import AiSettings
from finance360.core.models.ai_log import AiLog

log = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class DataClass:
    """What a call may carry: part name -> whether that part is public (sent unredacted)."""

    name: str
    parts: Mapping[str, bool]
    #: Non-public parts whose amounts and digit runs are sent as they are (names, identifiers and
    #: e-mail addresses are still replaced). Only the exemptions in ``_FIGURES_KEPT`` are honoured:
    #: ``complete`` refuses any other, so a new class cannot quietly switch stripping off.
    figures_kept: frozenset[str] = frozenset()


#: The narrowest class (ADR-0021 section 3): the Guide, the user's whereabouts, What's due wording
#: and the in-session conversation. No Position, Valuation, Holding, Tax record or amount.
GUIDANCE = DataClass(
    "guidance",
    {
        "instructions": True,
        "guide": True,
        "location": True,
        "due_items": False,
        "conversation": False,
    },
)


#: Rule figure capture (ADR-0022 section 5): public product text (the instructions and the figures
#: asked for) and the official page the person pasted. The page keeps its amounts, because the
#: figures are what the call is for; it is still redacted for names and e-mail addresses, as a
#: signed-in banner may have been pasted with it. No Position, Valuation, Holding or Tax record
#: belongs in this class.
REFERENCE = DataClass(
    "reference",
    {"instructions": True, "specs": True, "page_text": False},
    figures_kept=frozenset({"page_text"}),
)

#: The only (class, part) pairs allowed to skip amount and digit-run stripping.
_FIGURES_KEPT: dict[str, frozenset[str]] = {REFERENCE.name: frozenset({"page_text"})}


@dataclass(frozen=True)
class Part:
    name: str
    text: str
    role: Role = "system"
    #: Mark the end of a stable prefix worth caching (system parts only).
    cache: bool = False
    #: Shown in the log beside a public part's digest, in place of its text.
    note: str = ""


@dataclass(frozen=True)
class AiRequest:
    task: str
    #: A short fixed reason for the log, e.g. "Ask panel question".
    why: str
    data_class: DataClass
    parts: Sequence[Part]
    max_tokens: int


@dataclass(frozen=True)
class AiResult:
    #: The reply as received, still tokenised. A caller that parses it (JSON) parses this, then
    #: ``restore``s only the text it will show, so a restored name can never break the structure.
    text: str
    #: Puts names back in a piece of the reply. For the client only; never logged.
    restore: Callable[[str], str]
    log_id: int
    #: The model that wrote it, as the log shows it.
    model: str = ""


class UndeclaredPartError(ValueError):
    """A programming error: the data class does not declare this part."""


#: One call at a time per instance. It also keeps the count-then-call of the caps race-free.
_LOCK = threading.Lock()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_parts(request: AiRequest) -> None:
    kept = request.data_class.figures_kept
    if kept and not kept <= _FIGURES_KEPT.get(request.data_class.name, frozenset()):
        raise UndeclaredPartError(
            f"Data class {request.data_class.name!r} may not send amounts unredacted in "
            f"{sorted(kept - _FIGURES_KEPT.get(request.data_class.name, frozenset()))}."
        )
    if any(request.data_class.parts.get(name) is not False for name in kept):
        raise UndeclaredPartError("Only a non-public part can keep its figures.")
    for part in request.parts:
        if part.name not in request.data_class.parts:
            raise UndeclaredPartError(
                f"Data class {request.data_class.name!r} does not admit the part {part.name!r}."
            )


def _row(
    now: datetime,
    request: AiRequest,
    settings: AiSettings,
    model: str,
    outcome: str,
    *,
    sent: str = "",
    received: str | None = None,
    usage: providers.ProviderResponse | None = None,
) -> AiLog:
    cost, estimated = (0, False)
    if usage is not None:
        cost, estimated = pricing.cost_micros(
            model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        )
    public = [
        {"name": p.name, "sha256": _digest(p.text), "note": p.note}
        for p in request.parts
        if request.data_class.parts[p.name]
    ]
    return AiLog(
        occurred_at=now,
        task=request.task,
        data_class=request.data_class.name,
        provider=settings.provider or "",
        model=model,
        outcome=outcome,
        sent=sent,
        public_parts=json.dumps(public),
        received=received,
        input_tokens=(usage.input_tokens + usage.cache_write_tokens) if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        cache_read_tokens=usage.cache_read_tokens if usage else 0,
        cost_usd_micros=cost,
        cost_estimated=estimated,
        why=request.why,
    )


def complete(
    session: Session,
    settings: AiSettings,
    request: AiRequest,
    *,
    now: datetime | None = None,
) -> AiResult:
    _check_parts(request)
    provider = providers.get(settings.provider) if settings.provider else None
    if provider is None:
        raise AiOff
    model = provider.fixed_model or settings.model_for(request.task)
    now = now or ai_log.utcnow()
    if not _LOCK.acquire(blocking=False):
        raise Busy
    try:
        status = caps.check(session, settings, now, request.task)
        if status.reason is not None:
            ai_log.write(session, settings, _row(now, request, settings, model, "refused_cap"))
            raise CapReached(status.reason, status.resets_on)

        redactor = Redactor(vocabulary.load(session))
        system: list[providers.SystemBlock] = []
        messages: list[providers.Message] = []
        sent: list[str] = []
        for part in request.parts:
            public = request.data_class.parts[part.name]
            text = (
                part.text
                if public
                else redactor.redact(
                    part.text, keep_figures=part.name in request.data_class.figures_kept
                )
            )
            if not public:
                sent.append(f"[{part.name}:{part.role}]\n{text}")
            if part.role == "system":
                system.append(providers.SystemBlock(text, part.cache))
            else:
                messages.append(providers.Message(part.role, text))
        sent_text = "\n\n".join(sent)

        try:
            reply = provider.complete(
                providers.ProviderRequest(model, tuple(system), tuple(messages), request.max_tokens)
            )
        except Exception as problem:
            # Never log or show the exception text: it could echo the request or a credential.
            log.warning("AI provider %s failed (%s)", settings.provider, type(problem).__name__)
            ai_log.write(
                session, settings, _row(now, request, settings, model, "error", sent=sent_text)
            )
            message = (
                problem.message
                if isinstance(problem, providers.ProviderError)
                else providers.ProviderError().message
            )
            raise AiUnavailable(message) from None

        row = ai_log.write(
            session,
            settings,
            _row(
                now,
                request,
                settings,
                model,
                "ok",
                sent=sent_text,
                received=reply.text,
                usage=reply,
            ),
        )
        return AiResult(reply.text, redactor.restore, row.id, model)
    finally:
        _LOCK.release()


def mark_unsupported(session: Session, log_id: int) -> None:
    """The caller's validation rejected the reply: record it (ADR-0021 section 5)."""
    ai_log.set_outcome(session, log_id, "unsupported")
