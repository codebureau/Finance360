"""Rule figure capture, reading a page (ADR-0022 section 5, ADR-0023 section 6): read a pasted
official page and *propose* figures. Nothing here stores anything; a person confirms each figure
through the same capture write path as typed entry (``core.rulepack_store``), which validates again.

With a model set up, ``propose`` makes one gateway call per chunk of at most ``MAX_KEYS_PER_CALL``
figures for one financial year and parses the reply as untrusted data. With none, or when the model
is capped or failing, the deterministic word matcher (``reader.py``, not an AI call) proposes
instead. Either way every proposal runs the checks of ``checks.py``: one that fails is not a
candidate, it comes back as "not found" with the reason. Reads core and the AI gateway only.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

from sqlalchemy.orm import Session

from finance360.capabilities.ai import errors as ai_errors
from finance360.capabilities.ai import gateway, providers
from finance360.capabilities.rulecapture import checks, prompt, reader
from finance360.core import figure_capture, rulepack_store
from finance360.core.ai_settings import AiSettings
from finance360.core.errors import FieldError, ValidationError
from finance360.core.rulepack import FigureSpec, fy_label

TASK = "rule_capture"
WHY = "Rule figure capture"
MAX_KEYS_PER_CALL = 12
MAX_PAGE_CHARS = 40_000
MAX_QUOTE = rulepack_store.MAX_QUOTE
MAX_NOTE = rulepack_store.MAX_NOTE
CONFIDENCES = ("high", "medium", "low")
#: Used when the page says a figure is announced but the proposal gave no words for it.
DEFAULT_PROVISIONAL_NOTE = "Announced, not yet law (as stated on the pasted page)."

NOT_ON_PAGE = None


@dataclass(frozen=True)
class Result:
    """What came of one asked figure: a candidate to confirm, or "not found; enter it"."""

    key: str
    label: str
    status: Literal["candidate", "not_found"]
    #: Why a *proposal* was rejected; ``None`` when the page simply did not state the figure.
    reason: str | None = None
    #: Canonical text of the parsed value (a candidate only).
    value: str | None = None
    quote: str | None = None
    #: Set when the proposal is provisional (announced, not yet law).
    provisional_note: str | None = None
    #: The proposal's own remark, shown beside it.
    note: str = ""
    #: The model's own estimate; ``None`` for the word matcher, which has none.
    confidence: str | None = None
    #: Last year's figure moved by more than the spec allows: the person must tick to confirm.
    jump: str | None = None
    #: The log row of the call this came from; ``None`` for the word matcher.
    ai_log_id: int | None = None
    #: What was proposed, kept beside the confirmed value (a candidate only).
    proposal: dict[str, object] | None = None


@dataclass(frozen=True)
class Proposed:
    results: list[Result]
    #: Who read the page: a model (``ai``) or the word matcher (``matched``).
    method: Literal["ai", "matched"]
    #: Why the word matcher read it though a model is set up (capped or failing), else ``None``.
    fallback: str | None = None


def _invalid(field: str, message: str) -> ValidationError:
    return ValidationError([FieldError(field, message)])


def _chunks(specs: Sequence[FigureSpec]) -> list[list[FigureSpec]]:
    return [list(specs[i : i + MAX_KEYS_PER_CALL]) for i in range(0, len(specs), MAX_KEYS_PER_CALL)]


def _parse(text: str) -> dict[str, Any] | None:
    """The reply's JSON object, tolerating a code fence or a line of chatter around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def _text(item: dict[str, Any], field: str) -> str | None:
    value = item.get(field)
    return value if isinstance(value, str) else None


def _year(item: dict[str, Any]) -> int | None:
    raw = item.get("fy")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().isascii() and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _not_found(spec: FigureSpec, reason: str | None, log_id: int | None) -> Result:
    return Result(spec.key, spec.label, "not_found", reason=reason, ai_log_id=log_id)


def _check(
    session: Session,
    jurisdiction: str,
    spec: FigureSpec,
    fy: int,
    item: dict[str, Any],
    page_text: str,
    restore: Callable[[str], str],
    log_id: int | None,
) -> Result:
    """Run every deterministic check on one proposal; a failure is a "not found" with its reason.
    ``log_id`` is ``None`` for the word matcher's proposals, which carry no confidence."""

    def fail(reason: str) -> Result:
        return _not_found(spec, reason, log_id)

    proposed = _year(item)
    if proposed is None:
        return fail("The AI did not say which financial year the figure is for.")
    if proposed != fy:
        return fail(f"The AI read a figure for FY {fy_label(proposed)}, not FY {fy_label(fy)}.")
    raw_value = _text(item, "value")
    if raw_value is None:
        return fail("The AI gave no figure as text.")
    try:
        parsed = figure_capture.parse_figure(spec, raw_value)
    except figure_capture.FigureError as error:
        return fail(f"The proposed figure {raw_value.strip()!r} is not valid: {error}")
    raw_quote = _text(item, "quote")
    quote = " ".join(restore(raw_quote).split()) if raw_quote else ""
    if not quote:
        return fail("The AI gave no sentence from the page to back the figure.")
    if len(quote) > MAX_QUOTE:
        return fail("The quoted sentence is too long to be one sentence.")
    if not checks.quote_in_text(quote, page_text):
        return fail("The quoted sentence does not appear in the pasted text.")
    if not checks.number_in_quote(parsed, quote):
        return fail("The figure does not appear in the quoted sentence.")

    canonical = figure_capture.format_figure(spec, parsed)
    provisional = item.get("provisional") is True
    note = (_text(item, "note") or "").strip()[:MAX_NOTE]
    confidence: str | None = None
    if log_id is not None:
        confidence = (_text(item, "confidence") or "low").strip().lower()
        if confidence not in CONFIDENCES:
            confidence = "low"
    jump = None
    pack = rulepack_store.effective_pack(session, jurisdiction)
    before = rulepack_store.previous_in(pack, spec.key, fy) if pack is not None else None
    if before is not None:
        jump = figure_capture.large_change(spec, before[1], parsed)
    return Result(
        spec.key,
        spec.label,
        "candidate",
        value=canonical,
        quote=quote,
        provisional_note=(note or DEFAULT_PROVISIONAL_NOTE) if provisional else None,
        note=note,
        confidence=confidence,
        jump=jump,
        ai_log_id=log_id,
        proposal={
            "key": spec.key,
            "fy": fy,
            "value": raw_value.strip(),
            "quote": quote,
            "provisional": provisional,
            "note": note,
            **({"confidence": confidence} if confidence is not None else {}),
        },
    )


def _read(
    session: Session,
    jurisdiction: str,
    fy: int,
    chunk: list[FigureSpec],
    page_text: str,
    result: gateway.AiResult,
) -> list[Result]:
    reply = _parse(result.text)
    if reply is None:
        gateway.mark_unsupported(session, result.log_id)
        return [_not_found(s, "The AI's reply could not be read.", result.log_id) for s in chunk]
    by_key = {s.key: s for s in chunk}
    taken: dict[str, dict[str, Any]] = {}
    proposals = reply.get("proposals")
    if isinstance(proposals, list):
        for entry in cast(list[object], proposals):
            if not isinstance(entry, dict):
                continue
            item = cast(dict[str, Any], entry)
            key = item.get("key")
            # An unasked key is dropped; so is a repeat of one already taken (the first stands).
            if isinstance(key, str) and key in by_key and key not in taken:
                taken[key] = item
    out: list[Result] = []
    for spec in chunk:
        item = taken.get(spec.key)
        if item is None:
            out.append(_not_found(spec, NOT_ON_PAGE, result.log_id))
        else:
            out.append(
                _check(
                    session, jurisdiction, spec, fy, item, page_text, result.restore, result.log_id
                )
            )
    return out


def propose(
    session: Session,
    settings: AiSettings,
    jurisdiction: str,
    fy: int,
    keys: Sequence[str],
    page_text: str,
    *,
    today: date,
) -> Proposed:
    """One result per asked key, in the order asked. Raises ``ValidationError`` for bad input.

    A model reads the page when one is set up and may be called; otherwise, and whenever the call is
    refused or fails (capped, busy, provider down), the word matcher does and ``fallback`` says why.
    The AI log keeps every model call either way."""
    specs, text = _specs(session, jurisdiction, fy, keys, page_text, today)
    if settings.provider is None or providers.get(settings.provider) is None:
        return Proposed(_by_matching(session, jurisdiction, fy, specs, text), "matched")
    try:
        return Proposed(_by_model(session, settings, jurisdiction, fy, specs, text), "ai")
    except ai_errors.AiError as problem:
        why = f"{problem.message} The page was read by matching words instead."
        return Proposed(_by_matching(session, jurisdiction, fy, specs, text), "matched", why)


def _specs(
    session: Session, jurisdiction: str, fy: int, keys: Sequence[str], page_text: str, today: date
) -> tuple[list[FigureSpec], str]:
    """Validate the request: the asked figures' specs and the trimmed page text."""
    pack = rulepack_store.effective_pack(session, jurisdiction)
    if pack is None:
        raise _invalid("jurisdiction", f"There is no Rule pack for {jurisdiction!r}.")
    years = rulepack_store.capture_years(session, jurisdiction, today)
    if years is not None and not years[0] <= fy <= years[1]:
        raise _invalid(
            "financial_year",
            f"Choose a year from FY {fy_label(years[0])} to FY {fy_label(years[1])}.",
        )
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        raise _invalid("keys", "Choose at least one figure to look for.")
    specs: list[FigureSpec] = []
    for key in wanted:
        spec = next((s for s in pack.specs if s.key == key), None)
        if spec is None:
            raise _invalid(
                "keys", f"{key!r} can't be captured: no figure by that name is declared."
            )
        specs.append(spec)
    text = page_text.strip()
    if not text:
        raise _invalid("page_text", "Paste the text of the official page.")
    if len(text) > MAX_PAGE_CHARS:
        raise _invalid(
            "page_text",
            f"The pasted text is {len(text):,} characters; the most that can be read at once is "
            f"{MAX_PAGE_CHARS:,}. Paste only the part of the page that has the figures.",
        )
    return specs, text


def _by_model(
    session: Session,
    settings: AiSettings,
    jurisdiction: str,
    fy: int,
    specs: list[FigureSpec],
    text: str,
) -> list[Result]:
    results: list[Result] = []
    for chunk in _chunks(specs):
        reply = gateway.complete(
            session,
            settings,
            gateway.AiRequest(
                task=TASK,
                why=WHY,
                data_class=gateway.REFERENCE,
                parts=prompt.build_parts(fy, chunk, text),
                max_tokens=prompt.MAX_OUTPUT_TOKENS,
            ),
        )
        results.extend(_read(session, jurisdiction, fy, chunk, text, reply))
    return results


def _by_matching(
    session: Session, jurisdiction: str, fy: int, specs: list[FigureSpec], text: str
) -> list[Result]:
    """The word matcher's proposals, put through the same checks as a model's."""
    by_key = {str(p["key"]): p for p in reader.read(specs, fy, text)}
    return [
        _check(session, jurisdiction, spec, fy, by_key[spec.key], text, str, None)
        if spec.key in by_key
        else _not_found(spec, NOT_ON_PAGE, None)
        for spec in specs
    ]
