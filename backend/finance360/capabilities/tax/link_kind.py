"""The ``tax_record`` Link kind (ADR-0026 section 3): a Tax record can have documents.

Built here, in the Capability that owns the records, and registered by ``main.py`` with the
Library's registry. The Library never imports tax. A Tax record is edited in place and deleted by a
service call (ADR-0019 section 2), so its lineage is itself alone.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.library.registry import Described, LinkKind
from finance360.core.models.tax_record import TaxRecord

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
