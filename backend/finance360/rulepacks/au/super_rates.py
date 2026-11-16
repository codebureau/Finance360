"""Superannuation Guarantee and contributions tax, by financial year (ADR-0018).

A financial year (FY) is keyed by the year it ends in (FY 2025-26 is ``2026``). Each value is the
one in force at 1 July of that FY. Both are ``cpi`` parameters: constant in the real terms the
engine works in, so the last known value carries forward (ADR-0018 section 3).

**Source verification status (stated plainly).** The author could not fetch ATO pages (HTTP 403).
These figures are from the author's knowledge of the legislated schedule and are **unverified
against the ATO page**: the owner confirms them at review. A web search corroborated only the 12%
rate from 1 July 2025 (an ATO notice dated 5 June 2025, seen second-hand). The earlier SG
steps and the 15% contributions tax are unverified by any fetch.

Retrieval date for the citations below: not retrieved (2026-10-31).
"""

from __future__ import annotations

from finance360.core.rate import Rate

#: Superannuation Guarantee (Administration) Act 1992 s 19 (the charge percentage, by year, with
#: the legislated steps 10% to 12%); ATO, "Super guarantee" under "Key superannuation rates and
#: thresholds" https://www.ato.gov.au/tax-rates-and-codes/key-superannuation-rates-and-thresholds/super-guarantee
#: The rate is 12% from 1 July 2025 and stays there. UNVERIFIED against the ATO page.
SG_RATE_SOURCE = (
    "Superannuation Guarantee (Administration) Act 1992 s 19; ATO, Key superannuation rates and "
    "thresholds: Super guarantee (unverified)"
)
SG_RATE: dict[int, Rate] = {
    2022: Rate.from_percent("10"),
    2023: Rate.from_percent("10.5"),
    2024: Rate.from_percent("11"),
    2025: Rate.from_percent("11.5"),
    2026: Rate.from_percent("12"),
    2027: Rate.from_percent("12"),
}

#: The flat tax a super fund pays on concessional (employer) contributions: 15% of the
#: contribution. Income Tax Rates Act 1986 (tax on a complying fund's taxable contributions);
#: ATO, "Key superannuation rates and thresholds: Contributions"
#: https://www.ato.gov.au/tax-rates-and-codes/key-superannuation-rates-and-thresholds
#: Division 293 (an extra 15% for high incomes) is not modelled. UNVERIFIED against the ATO page.
CONTRIBUTIONS_TAX_RATE_SOURCE = (
    "Income Tax Rates Act 1986 (tax on a fund's taxable contributions); ATO, Key superannuation "
    "rates and thresholds (unverified)"
)
CONTRIBUTIONS_TAX_RATE: dict[int, Rate] = {fy: Rate.from_percent("15") for fy in SG_RATE}
