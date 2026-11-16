"""Due rules for Rule figures (ADR-0022 section 7): a figure that needs a person's attention.

Four rules, all deterministic reads of the effective pack (``core.rulepack_store``) and the Tax
records, so an item cannot disagree with the data:

* **Not captured**: a Tax record reads a figure for a year the pack lacks. Derived from the Record
  types' own hooks (``figures_read`` / ``missing_figures``, via the register's ``waiting_for``);
  there is no second list.
* **Release differs**: an active capture whose value differs from the shipped one. The capture is
  used. "Keep mine" is an acknowledgement stored as a Snooze that skips this item; its
  ``subject_key`` names the capture and the shipped value, so a later release that ships a
  different value raises it again, and a release that still ships the same one does not.
* **No longer fits**: an active capture the current release's validator rejects (ignored by the
  merge, never fatal).
* **Now firm**: a provisional capture where the release now ships a firm value (the capture is
  already treated as redundant).

Every item links to the Rule figures screen at the year; none of them is a failure, so none says
overdue. The Guide lines for these rules are in ``04-what-to-provide.md``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.tax import service
from finance360.core import figure_capture, rulepack, rulepack_store
from finance360.core.due import (
    IMPORTANT,
    OPTIONAL,
    Cadence,
    DueAction,
    DueItem,
    DueRule,
    EffortLevel,
    Every,
)
from finance360.core.models.rule_capture import RuleCapture
from finance360.core.models.tax_record import TaxRecord
from finance360.core.rulepack import fy_label

NOT_CAPTURED_RULE = "rule_figures.not_captured"
DIFFERS_RULE = "rule_figures.release_differs"
NO_LONGER_FITS_RULE = "rule_figures.no_longer_fits"
NOW_FIRM_RULE = "rule_figures.now_firm"

KEEP_MINE = "keep_mine"
USE_SHIPPED = "use_shipped"

#: These are events, not a rhythm: an item shows as soon as the condition is true, at every level
#: (the cadence table needs a value for each level; "straight away" is a one-day cadence).
STRAIGHT_AWAY: dict[EffortLevel, Cadence | None] = {
    "light": Every(1),
    "steady": Every(1),
    "thorough": Every(1),
}
#: "Now firm" is housekeeping (the release already supplies the figure), so Light does not ask.
STRAIGHT_AWAY_NOT_LIGHT: dict[EffortLevel, Cadence | None] = {
    "light": None,
    "steady": Every(1),
    "thorough": Every(1),
}


def _link(fy: int, *, missing: bool = False) -> str:
    return f"/rule-figures?fy={fy}" + ("&missing=1" if missing else "")


def _fy_start(fy: int) -> date:
    return date(fy - 1, 7, 1)


def _digest(value: object) -> str:
    """A short, stable name for a shipped value, so an acknowledgement is about that value."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:12]


def differs_subject(capture_id: int, shipped: rulepack.Value) -> str:
    """The Snooze subject of a "Release differs" item: this capture against this shipped value."""
    return f"capture:{capture_id}@shipped:{_digest(rulepack.value_json(shipped))}"


def _has_captures(session: Session) -> bool:
    return bool(session.scalar(select(func.count()).select_from(RuleCapture)))


def _spec(pack: rulepack.RulePack, key: str) -> rulepack.FigureSpec | None:
    return next((s for s in pack.specs if s.key == key), None)


def _label(pack: rulepack.RulePack, key: str) -> str:
    spec = _spec(pack, key)
    return spec.label if spec is not None else key


def _text(pack: rulepack.RulePack, key: str, value: rulepack.Value) -> str:
    spec = _spec(pack, key)
    return figure_capture.format_figure(spec, value) if spec is not None else str(value)


# --- Not captured --------------------------------------------------------------------------


def _records_exist(session: Session) -> bool:
    return bool(session.scalar(select(func.count()).select_from(TaxRecord)))


def _check_not_captured(session: Session, today: date, level: EffortLevel) -> tuple[DueItem, ...]:
    shipped = rulepack.get(service.JURISDICTION)
    pack = rulepack_store.effective_pack(session, service.JURISDICTION)
    if shipped is None or pack is None:
        return ()
    first, last = min(shipped.years), rulepack.fy_of(today) + 1
    wanted: dict[tuple[str, int], str] = {}
    for view in service.list_records(session):
        for waiting in view.waiting_for:
            held = pack.years.get(waiting.financial_year, {}).get(waiting.key)
            if held is None and _spec(pack, waiting.key) is not None:
                if first <= waiting.financial_year <= last:
                    wanted[(waiting.key, waiting.financial_year)] = waiting.name
    return tuple(
        DueItem(
            rule_key=NOT_CAPTURED_RULE,
            subject_key=f"{key}@fy{fy}",
            importance=IMPORTANT,
            title=f"FY {fy_label(fy)}: {label.lower()} not captured",
            effect=(
                "A record in this year needs the figure, so it adds nothing to the totals until "
                "you capture it from the official page."
            ),
            due_on=_fy_start(fy),
            link=_link(fy, missing=True),
            suits=("tax", "whole"),
        )
        for (key, fy), label in sorted(wanted.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    )


# --- the three states of an active capture -------------------------------------------------


def _check_differs(session: Session, today: date, level: EffortLevel) -> tuple[DueItem, ...]:
    items: list[DueItem] = []
    for shipped in rulepack.registered():
        merged = rulepack_store.layered(session, shipped.jurisdiction)
        if merged is None:
            continue
        for figure in merged.pack.captured:
            old = figure.shipped_value
            if old is None:
                continue  # the release has no figure for that year, so nothing differs
            mine = merged.pack.years[figure.fy][figure.key]
            label = _label(shipped, figure.key)
            items.append(
                DueItem(
                    rule_key=DIFFERS_RULE,
                    subject_key=differs_subject(figure.id, old),
                    importance=IMPORTANT,
                    title=f"FY {fy_label(figure.fy)}: {label.lower()} differs from release "
                    f"r{shipped.revision}",
                    effect=(
                        f"Your captured figure ({_text(shipped, figure.key, mine)}) is in use. "
                        f"The release ships {_text(shipped, figure.key, old)}."
                    ),
                    due_on=_fy_start(figure.fy),
                    link=_link(figure.fy),
                    suits=("tax", "whole"),
                    actions=(
                        DueAction(KEEP_MINE, "Keep mine", figure.id),
                        DueAction(USE_SHIPPED, "Use shipped", figure.id),
                    ),
                )
            )
    return tuple(sorted(items, key=lambda i: (i.due_on, i.title)))


def _check_no_longer_fits(session: Session, today: date, level: EffortLevel) -> tuple[DueItem, ...]:
    items: list[DueItem] = []
    for shipped in rulepack.registered():
        for capture, why in rulepack_store.unfit(session, shipped.jurisdiction):
            label = _label(shipped, capture.key)
            items.append(
                DueItem(
                    rule_key=NO_LONGER_FITS_RULE,
                    subject_key=f"capture:{capture.id}",
                    importance=IMPORTANT,
                    title=f"FY {fy_label(capture.fy)}: your captured {label.lower()} no longer "
                    f"fits release r{shipped.revision}",
                    effect=(
                        f"It is ignored until you capture it again. {why} "
                        "The figure the release ships (if any) is used meanwhile."
                    ),
                    due_on=_fy_start(capture.fy),
                    link=_link(capture.fy),
                    suits=("tax", "whole"),
                )
            )
    return tuple(sorted(items, key=lambda i: (i.due_on, i.title)))


def _check_now_firm(session: Session, today: date, level: EffortLevel) -> tuple[DueItem, ...]:
    items: list[DueItem] = []
    for shipped in rulepack.registered():
        merged = rulepack_store.layered(session, shipped.jurisdiction)
        if merged is None:
            continue
        for capture in merged.redundant:
            firm = shipped.years.get(capture.fy, {}).get(capture.key) is not None and (
                shipped.provisional_note(capture.fy, capture.key) is None
            )
            if capture.provisional_note is None or not firm:
                continue
            label = _label(shipped, capture.key)
            items.append(
                DueItem(
                    rule_key=NOW_FIRM_RULE,
                    subject_key=f"capture:{capture.id}@firm",
                    importance=OPTIONAL,
                    title=f"FY {fy_label(capture.fy)}: {label.lower()} is now firm in release "
                    f"r{shipped.revision}",
                    effect=(
                        "You captured it as announced, not yet law. The release now ships the "
                        "settled figure and it is used; withdraw yours to tidy up."
                    ),
                    due_on=_fy_start(capture.fy),
                    link=_link(capture.fy),
                    suits=("tax", "whole"),
                    actions=(DueAction(USE_SHIPPED, "Withdraw mine", capture.id),),
                )
            )
    return tuple(sorted(items, key=lambda i: (i.due_on, i.title)))


RULES: tuple[DueRule, ...] = (
    DueRule(
        key=NOT_CAPTURED_RULE,
        importance=IMPORTANT,
        cadence=STRAIGHT_AWAY,
        suits=("tax", "whole"),
        guide_item="A Rule figure for each year your records need",
        interested=_records_exist,
        check=_check_not_captured,
    ),
    DueRule(
        key=DIFFERS_RULE,
        importance=IMPORTANT,
        cadence=STRAIGHT_AWAY,
        suits=("tax", "whole"),
        guide_item="A captured Rule figure that a release now ships differently",
        interested=_has_captures,
        check=_check_differs,
    ),
    DueRule(
        key=NO_LONGER_FITS_RULE,
        importance=IMPORTANT,
        cadence=STRAIGHT_AWAY,
        suits=("tax", "whole"),
        guide_item="A captured Rule figure that no longer fits",
        interested=_has_captures,
        check=_check_no_longer_fits,
    ),
    DueRule(
        key=NOW_FIRM_RULE,
        importance=OPTIONAL,
        cadence=STRAIGHT_AWAY_NOT_LIGHT,
        suits=("tax", "whole"),
        guide_item="A provisional Rule figure that is now firm",
        interested=_has_captures,
        check=_check_now_firm,
    ),
)
