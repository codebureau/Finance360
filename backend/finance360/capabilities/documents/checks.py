"""Check: parse, range, sums that must agree, period (ADR-0024 section 4).

Pure: what the matcher found and the profile in, a Reading out. Money is exact: a value is text
until ``parse_money`` makes it integer minor units, and a sum is integer addition with tolerance
zero. A value that does not parse is *not found*, never rounded or guessed. A sum whose part is
missing is skipped ("could not check"), never treated as zero.
"""

from __future__ import annotations

import re

from finance360.capabilities.documents import reading as r
from finance360.capabilities.documents.matcher import Hit, Matches
from finance360.capabilities.documents.profile import FieldSpec, Profile
from finance360.core.money import BASE_CURRENCY, Money, MoneyError, parse_amount

#: The longest text value taken (a short text fact such as a leave type).
TEXT_MAX = 80

# An amount as a statement prints it: $, a thousands separator (exactly three digits a group), up
# to two decimals, a leading minus (before or after the $) or brackets for a negative.
_MONEY = re.compile(
    r"""^(?P<open>\()?\s*(?P<neg1>-)?\s*\$?\s*(?P<neg2>-)?\s*
        (?P<whole>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d{1,2}))?\s*(?P<close>\))?$""",
    re.VERBOSE,
)
#: Dashes a statement may print as a minus: hyphen, non-breaking hyphen, figure dash, en and em
#: dash, and the minus sign.
_DASHES = str.maketrans(dict.fromkeys("‐‑‒–—−", "-"))


def parse_money(text: str) -> Money | None:
    """Exact ``Money`` (AUD) from an amount as printed, or ``None`` if it is not one."""
    match = _MONEY.match(text.translate(_DASHES).strip())
    if match is None or bool(match["open"]) != bool(match["close"]):
        return None
    negatives = [bool(match["open"]), bool(match["neg1"]), bool(match["neg2"])]
    if sum(negatives) > 1:
        return None
    clean = match["whole"].replace(",", "") + (f".{match['frac']}" if match["frac"] else "")
    try:
        amount = parse_amount(clean, BASE_CURRENCY)
    except MoneyError:
        return None
    return -amount if any(negatives) else amount


_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_YYYY = r"((?:19|20)\d{2})"
# A date as printed: 30/06/2026 (day first), 2026-06-30, or 30 Jun 2026 / 30 June 2026. Each is the
# pattern and the groups holding the year and the month.
_DATES = (
    (re.compile(rf"\b(\d{{1,2}})[/.-](\d{{1,2}})[/.-]{_YYYY}\b"), (3, 2)),
    (re.compile(rf"\b{_YYYY}-(\d{{2}})-(\d{{2}})\b"), (1, 2)),
    (re.compile(rf"\b(\d{{1,2}})\s+([A-Za-z]{{3,9}})\.?\s+{_YYYY}\b"), (3, 2)),
)
_SPAN = re.compile(r"\s*((?:19|20)\d{2})\s*[-/]\s*((?:19|20)?\d{2})\s*")


def _month(token: str) -> int | None:
    if token.isdigit():
        return int(token) if 1 <= int(token) <= 12 else None
    return _MONTHS.get(token[:3].lower())


def parse_fy(text: str) -> int | None:
    """The financial year (the year it ends in) a period names, or ``None``.

    A range of dates is the year its *last* date falls in (1 July starts the next year), so a
    part-year statement is still for its year. ``2025-26`` and ``2025-2026`` are 2026.
    """
    best: tuple[int, int, int] | None = None  # (position, year, month)
    for pattern, (year_group, month_group) in _DATES:
        for m in pattern.finditer(text):
            month = _month(m[month_group])
            if month is not None and (best is None or m.start() > best[0]):
                best = (m.start(), int(m[year_group]), month)
    if best is not None:
        return best[1] + 1 if best[2] >= 7 else best[1]
    span = _SPAN.fullmatch(text)
    if span:
        first = int(span[1])
        last = int(span[2]) if len(span[2]) == 4 else first // 100 * 100 + int(span[2])
        return last if last == first + 1 else None
    return None


def _money_row(spec: FieldSpec, text: str) -> Money | str:
    """One row's amount, or the reason code it could not be taken."""
    money = parse_money(text)
    if money is None:
        return r.NOT_AN_AMOUNT
    if money.amount_minor < 0 and not spec.negative_ok:
        return r.NEGATIVE
    return money


def _text_row(text: str) -> str:
    return " ".join(text.split())


def _value(spec: FieldSpec, hit: Hit) -> r.Found | str:
    """The Found for a hit, or the reason code it could not be taken."""
    if spec.repeats and spec.type in ("money", "text"):
        return _repeated(spec, hit)
    if spec.type == "money":
        money = _money_row(spec, hit.text)
        return r.Found(spec.key, money, hit.page, hit.label) if isinstance(money, Money) else money
    if spec.type == "fy":
        year = parse_fy(hit.text)
        return r.Found(spec.key, year, hit.page, hit.label) if year else r.NOT_A_YEAR
    text = _text_row(hit.text)
    if len(text) > TEXT_MAX:
        return r.TOO_LONG
    return r.Found(spec.key, text, hit.page, hit.label)


def _repeated(spec: FieldSpec, hit: Hit) -> r.Found | str:
    """A field that ``repeats``: money rows are added exactly, text rows are joined (each distinct
    value once). Every source row is kept as a Part for the trace. One bad row fails the figure."""
    rows = hit.rows or ((hit.text, hit.page),)
    parts: list[r.Part] = []
    if spec.type == "money":
        total = Money.zero(BASE_CURRENCY)
        for text, page in rows:
            money = _money_row(spec, text)
            if not isinstance(money, Money):
                return money
            total = total + money
            parts.append(r.Part(page, money))
        return r.Found(spec.key, total, hit.page, hit.label, parts=tuple(parts))
    values: list[str] = []
    for text, page in rows:
        value = _text_row(text)
        parts.append(r.Part(page, value))
        if value not in values:
            values.append(value)
    joined = "; ".join(values)
    if len(joined) > TEXT_MAX:
        return r.TOO_LONG
    return r.Found(spec.key, joined, hit.page, hit.label, parts=tuple(parts))


def _minor(item: r.Found) -> int:
    """The exact integer minor units of a money figure (a profile's sums name money fields)."""
    if not isinstance(item.value, Money):
        raise TypeError("A sum names a field that is not money.")
    return item.value.amount_minor


def _sum_checks(profile: Profile, found: dict[str, r.Found]) -> list[r.Check]:
    zeroable = {f.key for f in profile.fields if f.zero_in_sums}
    checks: list[r.Check] = []
    for rule in profile.sums:
        fields = (rule.total, *rule.plus)
        # A part the document does not state counts as zero only where the profile says so for
        # that field; any other missing figure skips the check ("could not check"), never a guess.
        stated = [k for k in rule.plus if k in found]
        assumed = tuple(k for k in rule.plus if k not in found and k in zeroable)
        missing = [k for k in fields if k not in found and k not in assumed]
        if missing:
            checks.append(
                r.Check("sum", r.SKIPPED, fields, f"Could not check: {missing[0]} missing.")
            )
            continue
        agree = _minor(found[rule.total]) == sum(_minor(found[k]) for k in stated)
        checks.append(
            r.Check(
                "sum",
                r.AGREE if agree else r.DISAGREE,
                fields,
                "The parts add up to the total."
                if agree
                else "The parts do not add up to the total.",
                assumed,
            )
        )
    return checks


def _period_check(
    profile: Profile, found: dict[str, r.Found], expected: int | None
) -> list[r.Check]:
    if expected is None:
        return []
    fields = tuple(f.key for f in profile.fields if f.type == "fy" and f.key in found)
    if not fields:
        return []
    same = all(found[k].value == expected for k in fields)
    return [
        r.Check(
            "period",
            r.AGREE if same else r.DISAGREE,
            fields,
            "The statement is for the form's financial year."
            if same
            else "The statement is for a different financial year from the form's.",
        )
    ]


def evaluate(profile: Profile, result: Matches, *, expected_fy: int | None = None) -> r.Reading:
    """The Reading for a document the profile claimed."""
    found: dict[str, r.Found] = {}
    not_found: list[r.NotFound] = []
    for spec in profile.fields:
        reason: str | None = None
        if spec.key in result.hits:
            outcome = _value(spec, result.hits[spec.key])
            if isinstance(outcome, r.Found):
                found[spec.key] = outcome
            else:
                reason = outcome
        else:
            reason = result.misses[spec.key].reason
            if reason == r.LABEL_NOT_FOUND and spec.absent_means_zero and spec.type == "money":
                found[spec.key] = r.Found(spec.key, Money.zero(BASE_CURRENCY), 0, "", how=r.ABSENT)
                reason = None
        if reason is not None:
            not_found.append(r.NotFound(spec.key, reason, spec.required))
    checks = _sum_checks(profile, found) + _period_check(profile, found, expected_fy)
    # A claimed layout with a required figure missing is ``needs_repair`` (loud), but the figures
    # that were read are still returned, so the person only copies the rest.
    repair = any(n.required for n in not_found)
    clean = not not_found and all(c.status == r.AGREE for c in checks)
    return r.Reading(
        r.NEEDS_REPAIR if repair else r.OK if clean else r.WARNINGS,
        profile.key,
        profile.version,
        tuple(found[s.key] for s in profile.fields if s.key in found),
        tuple(not_found),
        tuple(checks),
    )
