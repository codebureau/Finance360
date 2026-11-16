"""Captured Rule figures: the store, the effective pack and the typed capture service (ADR-0022).

A figure a person read from the official page lives in ``rule_capture`` and layers over the shipped
pack. **Services call ``effective_pack`` instead of ``rulepack.get``** and so never see the
difference except through provenance (``RulePack.captured``). The shipped pack is the registered
one (``core.rulepack``); nothing here imports ``rulepacks/``.

Writes (``capture_figure``, ``correct_figure``, ``withdraw_figure``) validate through
``core.figure_capture`` (the one validator the maintainer's script also uses), end rows rather than
edit them, and each writes an ``audit_event``. A stored capture that no longer fits the pack's
declarations after a release is ignored by the merge and listed by ``unfit`` (never fatal).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.core import audit, figure_capture, rulepack
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.ai_log import AiLog
from finance360.core.models.rule_capture import RuleCapture
from finance360.core.rulepack import Capture, FigureSpec, Layered, RulePack, Value, fy_label

MAX_QUOTE = 2000
MAX_NOTE = 500
MAX_URL = 500


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as the audit trail stores it


# --- reading: the effective pack -----------------------------------------------------------


def _as_capture(row: RuleCapture) -> Capture:
    return Capture(
        row.id,
        row.key,
        row.fy,
        row.value_json,
        row.provisional_note,
        row.quote,
        row.page_url,
        row.retrieved_on,
        row.captured_at,
        row.captured_kind,
        row.method,
    )


def _active(session: Session, jurisdiction: str) -> list[RuleCapture]:
    return list(
        session.scalars(
            select(RuleCapture)
            .where(RuleCapture.jurisdiction == jurisdiction, RuleCapture.ended_at.is_(None))
            .order_by(RuleCapture.fy, RuleCapture.key, RuleCapture.id)
        )
    )


def layered(session: Session, jurisdiction: str) -> Layered | None:
    """The merge of the registered pack and the active captures, or ``None`` with no pack."""
    shipped = rulepack.get(jurisdiction)
    if shipped is None:
        return None
    return rulepack.merge(shipped, [_as_capture(r) for r in _active(session, jurisdiction)])


def effective_pack(session: Session, jurisdiction: str) -> RulePack | None:
    """The pack every service reads: the shipped one with the active captures over it.

    With no captures it is the registered pack itself, so behaviour, label and pins are unchanged.
    """
    merged = layered(session, jurisdiction)
    return merged.pack if merged is not None else None


def unfit(session: Session, jurisdiction: str) -> list[tuple[Capture, str]]:
    """Active captures that no longer fit the pack's declarations, with why ("re-capture")."""
    merged = layered(session, jurisdiction)
    return list(merged.unfit) if merged is not None else []


# --- reading: one year's figures -----------------------------------------------------------


@dataclass(frozen=True)
class CaptureView:
    """An active capture, as the screen and the API show it."""

    id: int
    quote: str | None
    page_url: str | None
    retrieved_on: date
    captured_at: datetime
    captured_by: str
    provisional_note: str | None
    method: str


@dataclass(frozen=True)
class FigureRow:
    """One declared figure for one financial year: its state and what a person needs to enter it."""

    spec: FigureSpec
    #: ``captured`` (a capture applies), ``shipped`` (the pack holds it) or ``missing``.
    state: str
    #: Canonical text of the effective figure, or ``None`` when missing.
    value: str | None
    #: The shipped pack's figure when a capture changed it.
    shipped_value: str | None
    provisional_note: str | None
    #: The nearest earlier year's effective figure, as ``(fy, text)``.
    previous: tuple[int, str] | None
    #: The active capture, applied or not (a redundant one is still withdrawable).
    capture: CaptureView | None
    range_text: str
    entry_hint: str


def capture_years(session: Session, jurisdiction: str, today: date) -> tuple[int, int] | None:
    """The financial years a figure may be captured for: the pack's first year to the current
    year plus one (next year's announced figures). ``None`` with no Rule pack."""
    pack = rulepack.get(jurisdiction)
    if pack is None:
        return None
    return min(pack.years), rulepack.fy_of(today) + 1


def _text(spec: FigureSpec, value: Value | None) -> str | None:
    return figure_capture.format_figure(spec, value) if value is not None else None


def previous_in(pack: RulePack, key: str, fy: int) -> tuple[int, Value] | None:
    """The latest figure for ``key`` in a year before ``fy``."""
    earlier = [y for y, values in pack.years.items() if y < fy and key in values]
    if not earlier:
        return None
    year = max(earlier)
    return year, pack.years[year][key]


def figures_for_year(session: Session, jurisdiction: str, fy: int) -> list[FigureRow]:
    """Every declared figure for ``fy`` with its state, in the pack's declared order."""
    merged = layered(session, jurisdiction)
    if merged is None:
        return []
    pack = merged.pack
    active = {r.key: r for r in _active(session, jurisdiction) if r.fy == fy}
    rows: list[FigureRow] = []
    for spec in pack.specs:
        value = pack.years.get(fy, {}).get(spec.key)
        captured = pack.capture_of(fy, spec.key)
        row = active.get(spec.key)
        before = previous_in(pack, spec.key, fy)
        rows.append(
            FigureRow(
                spec,
                "captured" if captured else "shipped" if value is not None else "missing",
                _text(spec, value),
                _text(spec, captured.shipped_value) if captured else None,
                pack.provisional_note(fy, spec.key),
                (before[0], figure_capture.format_figure(spec, before[1])) if before else None,
                CaptureView(
                    row.id,
                    row.quote,
                    row.page_url,
                    row.retrieved_on,
                    row.captured_at,
                    row.captured_by,
                    row.provisional_note,
                    row.method,
                )
                if row
                else None,
                figure_capture.range_text(spec),
                figure_capture.entry_hint(spec),
            )
        )
    return rows


# --- writing: typed capture, correct, withdraw ---------------------------------------------


#: The ``ai_log.task`` of a Rule figure proposal (ADR-0022 section 5).
AI_TASK = "rule_capture"


@dataclass(frozen=True)
class AiProvenance:
    """A proposal the person confirmed, and who made it: a model (method ``ai``, with the log row of
    its call) or the word matcher (method ``matched``, which is not an AI call and has no log row).

    The confirmed value still goes through the same validation as a typed one; the proposal is
    stored beside it so a corrected value is visible against what was proposed (``ai_value_json``).
    """

    log_id: int | None
    proposal: dict[str, object]
    method: Literal["ai", "matched"] = "ai"


@dataclass(frozen=True)
class CaptureInput:
    """What a person typed. ``value`` is the canonical figure text (the grammar the spec parses)."""

    value: str
    quote: str | None = None
    page_url: str | None = None
    #: A note makes the figure provisional (announced, not yet law); ``None`` or blank is firm.
    provisional_note: str | None = None
    retrieved_on: date | None = None
    #: The person has seen last year's figure and says a large change is right.
    confirm_jump: bool = False
    #: Set when the figure came from a proposal the person confirmed (method ``ai`` or ``matched``).
    ai: AiProvenance | None = None
    #: Written only by ``carry_figures`` (method ``carried``): an earlier year's figure, accepted.
    carried: bool = False


@dataclass(frozen=True)
class _Checked:
    spec: FigureSpec
    value: Value
    quote: str | None
    page_url: str | None
    note: str | None
    retrieved_on: date
    ai: AiProvenance | None = None
    carried: bool = False


def _blank_to_none(text: str | None) -> str | None:
    return text.strip() or None if text is not None else None


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError([FieldError(field, message)])


def _check(
    session: Session,
    jurisdiction: str,
    key: str,
    fy: int,
    data: CaptureInput,
    today: date,
) -> _Checked:
    pack = effective_pack(session, jurisdiction)
    if pack is None:
        raise _fail("jurisdiction", f"There is no Rule pack for {jurisdiction!r}.")
    spec = next((s for s in pack.specs if s.key == key), None)
    if spec is None:
        raise _fail("key", f"{key!r} can't be captured: no figure by that name is declared.")
    first, last = min(pack.years), rulepack.fy_of(today) + 1
    if not first <= fy <= last:
        raise _fail(
            "fy", f"Choose a year from FY {fy_label(first)} to FY {fy_label(last)} (next year's)."
        )
    errors: list[FieldError] = []
    value: Value | None = None
    try:
        value = figure_capture.parse_figure(spec, data.value)
    except figure_capture.FigureError as error:
        errors.append(FieldError("value", str(error)))
    quote = _blank_to_none(data.quote)
    if quote is not None and len(quote) > MAX_QUOTE:
        errors.append(FieldError("quote", f"Keep the quote to {MAX_QUOTE} characters."))
    url = _blank_to_none(data.page_url)
    if url is not None and (
        not url.lower().startswith(("http://", "https://")) or len(url) > MAX_URL
    ):
        errors.append(
            FieldError("page_url", "Enter the page address, starting http:// or https://.")
        )
    note = _blank_to_none(data.provisional_note)
    if note is not None and len(note) > MAX_NOTE:
        errors.append(FieldError("provisional_note", f"Keep the note to {MAX_NOTE} characters."))
    if data.ai is not None:
        if quote is None:
            errors.append(FieldError("quote", "A proposed figure needs the sentence it came from."))
        if data.ai.method == "matched":
            if data.ai.log_id is not None:
                errors.append(
                    FieldError("ai_log_id", "A figure matched by words has no AI log entry.")
                )
        else:
            logged = session.get(AiLog, data.ai.log_id) if data.ai.log_id is not None else None
            if logged is None or logged.task != AI_TASK or logged.outcome != "ok":
                errors.append(
                    FieldError("ai_log_id", "That is not a Rule figure proposal this app logged.")
                )
    retrieved = data.retrieved_on or today
    if retrieved > today:
        errors.append(
            FieldError("retrieved_on", "The day you read the page can't be in the future.")
        )
    if value is not None and not data.confirm_jump:
        before = previous_in(pack, key, fy)
        if before is not None and (warning := figure_capture.large_change(spec, before[1], value)):
            errors.append(
                FieldError(
                    "confirm_jump",
                    f"{warning} Last year's figure ({fy_label(before[0])}) is shown beside it; "
                    "confirm it is right to keep it.",
                )
            )
    if errors or value is None:
        raise ValidationError(errors)
    return _Checked(spec, value, quote, url, note, retrieved, data.ai, data.carried)


def _snapshot(row: RuleCapture) -> dict[str, object]:
    return {
        "id": row.id,
        "jurisdiction": row.jurisdiction,
        "key": row.key,
        "fy": row.fy,
        "value": row.value_json,
        "provisional_note": row.provisional_note,
        "quote": row.quote,
        "page_url": row.page_url,
        "retrieved_on": row.retrieved_on.isoformat(),
        "method": row.method,
        "ai_log_id": row.ai_log_id,
    }


def _describe(spec: FigureSpec, fy: int, value: Value) -> str:
    return f"{spec.label} for FY {fy_label(fy)} ({figure_capture.format_figure(spec, value)})"


def _insert(
    session: Session, context: AuditContext, jurisdiction: str, key: str, fy: int, c: _Checked
) -> RuleCapture:
    row = RuleCapture(
        jurisdiction=jurisdiction,
        key=key,
        fy=fy,
        value_json=rulepack.value_json(c.value),
        provisional_note=c.note,
        quote=c.quote,
        page_url=c.page_url,
        retrieved_on=c.retrieved_on,
        method="carried" if c.carried else "typed" if c.ai is None else c.ai.method,
        ai_log_id=c.ai.log_id if c.ai else None,
        ai_value_json=c.ai.proposal if c.ai else None,
        captured_at=_now(),
        captured_kind=context.actor_kind,
        captured_by=context.actor_ref,
    )
    session.add(row)
    session.flush()
    return row


def _active_row(session: Session, jurisdiction: str, key: str, fy: int) -> RuleCapture | None:
    return session.scalar(
        select(RuleCapture).where(
            RuleCapture.jurisdiction == jurisdiction,
            RuleCapture.key == key,
            RuleCapture.fy == fy,
            RuleCapture.ended_at.is_(None),
        )
    )


def _get_active(session: Session, capture_id: int) -> RuleCapture:
    row = session.get(RuleCapture, capture_id)
    if row is None:
        raise NotFoundError(f"Captured figure {capture_id} does not exist.")
    if row.ended_at is not None:
        raise _fail("id", "That captured figure has already been withdrawn or replaced.")
    return row


def capture_figure(
    session: Session,
    context: AuditContext,
    jurisdiction: str,
    key: str,
    fy: int,
    data: CaptureInput,
    *,
    today: date,
) -> RuleCapture:
    """Capture a figure typed from the official page. One active capture per figure: a second is
    refused, because correcting it is a different act (``correct_figure``)."""
    checked = _check(session, jurisdiction, key, fy, data, today)
    existing = _active_row(session, jurisdiction, key, fy)
    if existing is not None:
        raise _fail(
            "key",
            f"FY {fy_label(fy)} already has a captured {checked.spec.label.lower()}. "
            "Correct it instead.",
        )
    row = _insert(session, context, jurisdiction, key, fy, checked)
    audit.record(
        session,
        context,
        entity_type="rule_capture",
        entity_id=row.id,
        action="create",
        after=_snapshot(row),
        summary=f"Captured {_describe(checked.spec, fy, checked.value)}",
    )
    return row


def correct_figure(
    session: Session,
    context: AuditContext,
    capture_id: int,
    data: CaptureInput,
    *,
    today: date,
) -> RuleCapture:
    """Replace an active capture: the corrected row is inserted, the old one ended (kept)."""
    old = _get_active(session, capture_id)
    checked = _check(session, old.jurisdiction, old.key, old.fy, data, today)
    before = _snapshot(old)
    old.ended_at = _now()
    old.ended_by = context.actor_ref
    old.ended_kind = "superseded"
    session.flush()  # frees the active slot for the replacement
    new = _insert(session, context, old.jurisdiction, old.key, old.fy, checked)
    old.superseded_by_id = new.id
    session.flush()
    audit.record(
        session,
        context,
        entity_type="rule_capture",
        entity_id=new.id,
        action="supersede",
        before=before,
        after=_snapshot(new),
        summary=f"Corrected {_describe(checked.spec, new.fy, checked.value)}",
    )
    return new


def withdraw_figure(session: Session, context: AuditContext, capture_id: int) -> RuleCapture:
    """End an active capture, replacing nothing: the shipped figure (or "missing") applies again."""
    old = _get_active(session, capture_id)
    before = _snapshot(old)
    old.ended_at = _now()
    old.ended_by = context.actor_ref
    old.ended_kind = "withdrawn"
    session.flush()
    audit.record(
        session,
        context,
        entity_type="rule_capture",
        entity_id=old.id,
        action="delete",
        before=before,
        summary=f"Withdrew the captured figure for {old.key} FY {fy_label(old.fy)}",
    )
    return old


# --- starting a year from earlier figures --------------------------------------------

#: The provisional note of a carried figure; ``{fy}`` is the immediate source year's label.
CARRIED_NOTE = "Same as {fy}, not checked against the page"


@dataclass(frozen=True)
class CarryProposal:
    """One missing figure and the earlier year's value proposed for it."""

    spec: FigureSpec
    #: Canonical text of the earlier figure.
    value: str
    #: The nearest earlier year whose effective pack holds the key.
    source_fy: int
    #: The provisional note it would be written with.
    note: str
    #: The day the source figure was read, copied across.
    retrieved_on: date


@dataclass(frozen=True)
class CarryPreview:
    """What "Start from <FY>" would write. Nothing is written until it is accepted."""

    fy: int
    #: The latest earlier year that is the source of any proposal; ``None`` with no proposal.
    source_fy: int | None
    proposals: tuple[CarryProposal, ...]
    #: Missing keys with no earlier figure at all: they stay missing ("no earlier figure; capture
    #: it").
    no_earlier: tuple[FigureSpec, ...]


def carry_preview(session: Session, jurisdiction: str, fy: int, *, today: date) -> CarryPreview:
    """Propose, for every declared key with no figure for ``fy`` and no active capture for it, the
    nearest earlier year's figure. Reads only; ``build_rules`` never sees a proposal."""
    pack = effective_pack(session, jurisdiction)
    if pack is None:
        raise _fail("jurisdiction", f"There is no Rule pack for {jurisdiction!r}.")
    held = {r.key for r in _active(session, jurisdiction) if r.fy == fy}
    proposals: list[CarryProposal] = []
    none_earlier: list[FigureSpec] = []
    for spec in pack.specs:
        if spec.key in pack.years.get(fy, {}) or spec.key in held:
            continue
        before = previous_in(pack, spec.key, fy)
        if before is None:
            none_earlier.append(spec)
            continue
        source_fy, value = before
        note = CARRIED_NOTE.format(fy=fy_label(source_fy))
        if (earlier_note := pack.provisional_note(source_fy, spec.key)) is not None:
            note = f"{note}. {fy_label(source_fy)} was provisional: {earlier_note}"
        proposals.append(
            CarryProposal(
                spec,
                figure_capture.format_figure(spec, value),
                source_fy,
                note[:MAX_NOTE],
                min(pack.retrieved_on(source_fy, spec.key) or today, today),
            )
        )
    return CarryPreview(
        fy,
        max((p.source_fy for p in proposals), default=None),
        tuple(proposals),
        tuple(none_earlier),
    )


def carry_figures(
    session: Session, context: AuditContext, jurisdiction: str, fy: int, *, today: date
) -> CarryPreview:
    """Accept "Start from <FY>": one provisional ``carried`` capture per proposed key, in the
    caller's transaction (all or none), one audit event each. Returns what was written."""
    preview = carry_preview(session, jurisdiction, fy, today=today)
    for proposal in preview.proposals:
        spec = proposal.spec
        checked = _check(
            session,
            jurisdiction,
            spec.key,
            fy,
            CaptureInput(
                value=proposal.value,
                page_url=spec.page_url,
                provisional_note=proposal.note,
                retrieved_on=proposal.retrieved_on,
                carried=True,
            ),
            today,
        )
        row = _insert(session, context, jurisdiction, spec.key, fy, checked)
        audit.record(
            session,
            context,
            entity_type="rule_capture",
            entity_id=row.id,
            action="create",
            after=_snapshot(row),
            summary=(
                f"Carried {_describe(spec, fy, checked.value)} "
                f"from FY {fy_label(proposal.source_fy)}"
            ),
        )
    return preview


def _carried_row(session: Session, capture_id: int) -> RuleCapture:
    row = _get_active(session, capture_id)
    if row.method != "carried":
        raise _fail("id", "That figure was not carried from an earlier year.")
    return row


def confirm_unchanged(
    session: Session, context: AuditContext, capture_id: int, *, today: date
) -> RuleCapture:
    """Confirm a carried figure as unchanged without re-typing it. This is a supersession through
    ``correct_figure``: the carried row ends, a ``typed`` row with the same value, the spec's page
    and today's date replaces it, firm (ADR-0022 section 4). The owner attests, as typed entry
    does."""
    old = _carried_row(session, capture_id)
    pack = effective_pack(session, old.jurisdiction)
    spec = next((s for s in pack.specs if s.key == old.key), None) if pack else None
    if spec is None:
        raise _fail("key", f"{old.key!r} can't be captured: no figure by that name is declared.")
    value = rulepack.value_from_json(spec.kind, old.value_json)
    return correct_figure(
        session,
        context,
        capture_id,
        CaptureInput(
            value=figure_capture.format_figure(spec, value),
            page_url=spec.page_url,
            retrieved_on=today,
        ),
        today=today,
    )


@dataclass(frozen=True)
class PageConfirmation:
    confirmed: tuple[RuleCapture, ...]
    #: Carried figures on the page that have a scheduled change: each must be confirmed on its own.
    needs_own_confirm: tuple[FigureSpec, ...]


def confirm_page(
    session: Session,
    context: AuditContext,
    jurisdiction: str,
    fy: int,
    page_url: str,
    *,
    today: date,
) -> PageConfirmation:
    """Confirm every carried figure of ``fy`` whose spec points at ``page_url`` as unchanged, in
    the caller's transaction (all or none), one audit event per key. A figure whose spec declares
    a ``scheduled_change`` is left for its own row."""
    pack = effective_pack(session, jurisdiction)
    specs = {s.key: s for s in pack.specs} if pack else {}
    confirmed: list[RuleCapture] = []
    own: list[FigureSpec] = []
    for row in _active(session, jurisdiction):
        spec = specs.get(row.key)
        if row.fy != fy or row.method != "carried" or spec is None or spec.page_url != page_url:
            continue
        if spec.scheduled_change is not None:
            own.append(spec)
            continue
        confirmed.append(confirm_unchanged(session, context, row.id, today=today))
    return PageConfirmation(tuple(confirmed), tuple(own))


# --- First run -----------------------------------------------------------------------------

FIXTURE_FILENAME = "household.json"
CARRY_FILENAME = "rule_figures_carry.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


def load_fixture_rule_figures(session: Session, data_dir: Path, *, today: date) -> int:
    """Capture the fixture's invented Rule figures through the service, once, while there are none.

    Reads ``rule_figures`` from ``<data_dir>/household.json``: each entry names the ``key``, the
    ``fy`` (the year it ends in), the ``value`` text, a ``quote`` and a ``page_url`` and the day
    it was ``retrieved_on``. They are invented, so the review slot can show a captured figure.

    With ``rule_figures`` present, an optional ``<data_dir>/rule_figures_carry.json``
    (``{"fy": 2028, "confirm": [key, ...]}``) then starts that year from the earlier figures through
    ``carry_figures`` and confirms the listed keys as unchanged, so the sample shows carried figures
    some confirmed and some still provisional. It is a separate file so a test that loads
    only ``household.json`` sees no carried year. Without ``rule_figures`` nothing is loaded.
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file():
        return 0
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("rule_figures", [])
    carry_path = data_dir / CARRY_FILENAME
    carry = json.loads(carry_path.read_text(encoding="utf-8")) if carry_path.is_file() else None
    if not entries or session.scalar(select(func.count()).select_from(RuleCapture)):
        return 0
    for e in entries:
        capture_figure(
            session,
            FIXTURE_CONTEXT,
            e.get("jurisdiction", "au"),
            e["key"],
            e["fy"],
            CaptureInput(
                value=e["value"],
                quote=e.get("quote"),
                page_url=e.get("page_url"),
                retrieved_on=date.fromisoformat(e["retrieved_on"]),
            ),
            today=today,
        )
    written = len(entries)
    if carry:
        jurisdiction = carry.get("jurisdiction", "au")
        carried = carry_figures(session, FIXTURE_CONTEXT, jurisdiction, carry["fy"], today=today)
        written += len(carried.proposals)
        for key in carry.get("confirm", []):
            row = _active_row(session, jurisdiction, key, carry["fy"])
            if row is None:
                raise ValueError(f"The fixture confirms {key!r}, which was not carried.")
            confirm_unchanged(session, FIXTURE_CONTEXT, row.id, today=today)
    return written
