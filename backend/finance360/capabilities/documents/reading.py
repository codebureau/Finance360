"""The Reading: what a Document profile found in a Document, and whether it can be trusted
(ADR-0024 section 4).

A Reading is returned to the screen and then forgotten: it is never stored. It carries the figures
found (value, page, the profile's label that matched) and the figures not found (with a reason code
and whether the profile requires them). **Nothing in it but ``Found.value`` comes from the
document**; a reason and a check message are fixed wording that names keys, never values.
"""

from __future__ import annotations

from dataclasses import dataclass

from finance360.core.money import Money

#: Outcomes.
#: No profile claims the document.
UNRECOGNISED = "unrecognised"
#: A profile claimed it but a required figure is missing; the figures that were found are still
#: returned, to fill what can be filled.
NEEDS_REPAIR = "needs_repair"
#: Required figures found; a check disagrees or an optional figure is missing.
WARNINGS = "warnings"
#: Everything found and every check passed (still not stored).
OK = "ok"

#: How a figure was found.
READ = "read"  # beside its label
ABSENT = "absent"  # the statement omits a nil line: taken as zero

#: Why a figure was not found.
LABEL_NOT_FOUND = "label_not_found"
AMBIGUOUS = "ambiguous"  # the label matched more than once in its scope
NO_VALUE = "no_value"  # the label is there with nothing paired to it
SECTION_NOT_FOUND = "section_not_found"
NOT_AN_AMOUNT = "not_an_amount"
NOT_A_YEAR = "not_a_year"
NEGATIVE = "negative"
TOO_LONG = "too_long"

#: Check results.
AGREE = "agree"
DISAGREE = "disagree"
SKIPPED = "skipped"  # a part is missing, so the sum could not be checked


@dataclass(frozen=True)
class Part:
    """One source row of a figure that adds up several (a field that ``repeats``)."""

    page: int  # 1-based
    value: Money | str


@dataclass(frozen=True)
class Found:
    key: str
    value: Money | str | int  # Money for money, text for text, the year a period ends in for fy
    page: int  # 1-based; 0 when the line was absent. A repeated figure: the first row's page
    label: str  # the profile's label variant that matched; empty when absent
    how: str = READ
    #: Every source row, in reading order, for a field that ``repeats`` (empty otherwise).
    parts: tuple[Part, ...] = ()


@dataclass(frozen=True)
class NotFound:
    key: str
    reason: str
    required: bool


@dataclass(frozen=True)
class Check:
    kind: str  # "sum" or "period"
    status: str  # AGREE, DISAGREE or SKIPPED
    fields: tuple[str, ...]  # the total first, then its parts (for a period, the period field)
    message: str
    #: Parts the document did not state that the sum counted as zero (the profile says so per
    #: field), so the person can see what the check assumed.
    assumed_zero: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reading:
    outcome: str
    profile_key: str | None
    profile_version: int | None
    found: tuple[Found, ...]
    not_found: tuple[NotFound, ...]
    checks: tuple[Check, ...]
