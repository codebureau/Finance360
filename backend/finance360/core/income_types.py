"""The Income stream type registry: pure data (ADR-0017 section 2).

It lives in ``core`` so that Tax can ask whether a stream pays employer super without importing
projection, which sits above it (ADR-0019 section 1). ``capabilities/projection/income.py``
re-exports every name here, so projection's callers are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Tax-relevant kinds (ADR-0017 section 2). Held by the type, never stored on a stream.
EMPLOYMENT = "employment"
BUSINESS = "business"
INVESTMENT = "investment"
GOVERNMENT = "government"
TAX_KINDS = (EMPLOYMENT, BUSINESS, INVESTMENT, GOVERNMENT)

#: How a type indexes: ``real_wage_growth`` is the Assumption of that name (ADR-0017 section 5).
REAL_WAGE_GROWTH = "real_wage_growth"

SALARY = "salary"


@dataclass(frozen=True)
class StreamType:
    key: str
    label: str
    tax_kind: str
    indexation: str
    #: True: an employer pays super on top of the cash amount (SG, section 4).
    employer_super: bool


#: M3 creates only ``salary``; the service rejects any other key until a slice builds it.
TYPES: dict[str, StreamType] = {
    SALARY: StreamType(SALARY, "Salary", EMPLOYMENT, REAL_WAGE_GROWTH, True),
}
