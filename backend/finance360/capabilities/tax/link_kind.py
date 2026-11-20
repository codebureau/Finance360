"""The ``tax_record`` and ``tax_lodged_result`` Link kinds (ADR-0026 section 3): a Tax record and
a Lodged result (the notice's copy) can have documents.

Built here, in the Capability that owns the records, and registered by ``main.py`` with the
Library's registry. The Library never imports tax. A Tax record is edited in place and deleted by a
service call (ADR-0019 section 2), so its lineage is itself alone.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.library.registry import Described, LinkKind
from finance360.core.models.household import Member
from finance360.core.models.tax_lodged_result import TaxLodgedResult
from finance360.core.models.tax_record import TaxRecord
from finance360.core.rulepack import fy_label

KEY = "tax_record"
TITLE = "Tax record"
CANDIDATE_LIMIT = 20


def _exists(session: Session, record_id: int) -> bool:
    return session.get(TaxRecord, record_id) is not None


def _lineage(_session: Session, record_id: int) -> Sequence[int]:
    return [record_id]


def _describe(session: Session, ids: Sequence[int]) -> dict[int, Described]:
    found = {
        row.id: row.description
        for row in session.scalars(select(TaxRecord).where(TaxRecord.id.in_(list(ids))))
    }
    return {
        i: Described(found[i], f"/tax/records/{i}", False)
        if i in found
        else Described(f"{TITLE} {i}", None, True)
        for i in ids
    }


def _candidates(session: Session, text: str) -> Sequence[tuple[int, str]]:
    statement = select(TaxRecord).order_by(TaxRecord.from_on.desc(), TaxRecord.id.desc())
    if text.strip():
        statement = statement.where(TaxRecord.description.icontains(text.strip(), autoescape=True))
    rows = session.scalars(statement.limit(CANDIDATE_LIMIT))
    return [(row.id, row.description) for row in rows]


KIND = LinkKind(
    key=KEY,
    title=TITLE,
    exists=_exists,
    lineage=_lineage,
    describe=_describe,
    candidates=_candidates,
)


# --- Lodged result --------------------------------------------------------------------------

LODGED_KEY = "tax_lodged_result"
LODGED_TITLE = "Lodged result"


def _lodged_row(session: Session, lodged_id: int) -> TaxLodgedResult | None:
    return session.get(TaxLodgedResult, lodged_id)


def _lodged_exists(session: Session, lodged_id: int) -> bool:
    return _lodged_row(session, lodged_id) is not None


def _lodged_lineage(session: Session, lodged_id: int) -> Sequence[int]:
    """Every notice for the Member and year: the amended notice keeps the earlier notice's documents
    (a Lodged result is superseded, so its lineage is the whole set for that Member and year)."""
    row = _lodged_row(session, lodged_id)
    if row is None:
        return [lodged_id]
    ids = session.scalars(
        select(TaxLodgedResult.id).where(
            TaxLodgedResult.member_id == row.member_id,
            TaxLodgedResult.financial_year == row.financial_year,
        )
    )
    return sorted({lodged_id, *ids})


def _lodged_label(session: Session, row: TaxLodgedResult) -> str:
    member = session.get(Member, row.member_id)
    name = member.name if member is not None else f"Member {row.member_id}"
    return f"{LODGED_TITLE}, {name}, FY {fy_label(row.financial_year)}"


def _lodged_describe(session: Session, ids: Sequence[int]) -> dict[int, Described]:
    out: dict[int, Described] = {}
    for i in ids:
        row = _lodged_row(session, i)
        if row is None:
            out[i] = Described(f"{LODGED_TITLE} {i}", None, True)
            continue
        route = f"/tax/lodged?member={row.member_id}&fy={row.financial_year}"
        out[i] = Described(_lodged_label(session, row), route, False)
    return out


def _lodged_candidates(session: Session, text: str) -> Sequence[tuple[int, str]]:
    statement = (
        select(TaxLodgedResult)
        .join(Member, Member.id == TaxLodgedResult.member_id)
        .where(TaxLodgedResult.superseded_at.is_(None))
        .order_by(TaxLodgedResult.financial_year.desc(), TaxLodgedResult.id.desc())
    )
    if text.strip():
        statement = statement.where(Member.name.icontains(text.strip(), autoescape=True))
    rows = session.scalars(statement.limit(CANDIDATE_LIMIT))
    return [(row.id, _lodged_label(session, row)) for row in rows]


LODGED_KIND = LinkKind(
    key=LODGED_KEY,
    title=LODGED_TITLE,
    exists=_lodged_exists,
    lineage=_lodged_lineage,
    describe=_lodged_describe,
    candidates=_lodged_candidates,
)
