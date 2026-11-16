"""The seeded Household defaults and Best / Likely / Worst Scenarios (ADR-0016 section 6).

ILLUSTRATIVE PLACEHOLDERS, NOT ADVICE. The screen says so, and every value is an ordinary editable
Assumption: nothing about these rows is special-cased.

Principle (ADR-0016 s6): Likely is the median, and Best and Worst are roughly the 90th and 10th
percentile of the long-horizon annualised return for the Category.

Source and method, stated honestly. No public dataset states those percentiles directly for these
Categories, so the numbers are derived, then rounded to 0.5 percentage points:

* Central (Likely) returns are round, nominal, before-fee figures consistent with the long-run
  Australian record: the Livewire / Russell Investments "Long Term Investing Report 2025" (20 years
  to 30 June 2025) shows roughly 7.5% a year for Australian shares, 3.0% for cash and 2.9% CPI.
  Those figures are my reading of that chart pack and have NOT been checked against the original;
  re-check before relying on them. Superannuation and Investments are diversified growth, hence 7%.
  Overseas pensions are taken a little lower, 6%.
* Spread: for an N-year annualised return with annual volatility sigma, the 10th / 90th percentile
  is about the median -/+ 1.28 * sigma / sqrt(N) (normal approximation). N = 25 years and sigma
  (illustrative) of 1.5% cash, 14% for Investments and Superannuation, 12% for Overseas pensions
  give +/-0.4, +/-3.6 and +/-3.1 points, rounded to +/-0.5, +/-3.5 and +/-3.0.
* Worst also assumes 1 point higher inflation, which erodes spending power the same way.

Real wage growth is 0% (``real_wage_growth``, added in seed version 2): pay keeps pace with
inflation, in the real terms the engine works in. Illustrative, like every row here.

Seeding is idempotent: it runs only when ``assumption_seed_version`` in ``meta`` is below
``ASSUMPTION_SEED_VERSION``, then inserts only the rows that are missing **and were added after the
stored version**, so an edit, a deliberately removed default or a removed override survives every
restart and every version bump. Seeding is not audited (ADR-0009 section 5).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.capabilities.projection import assumptions
from finance360.capabilities.projection.scenarios import store_value
from finance360.core.models.catalogue import Meta
from finance360.core.models.scenario import Assumption, Scenario

ASSUMPTION_SEED_VERSION = 2
SEED_VERSION_KEY = "assumption_seed_version"

HH = assumptions.HOUSEHOLD
CAT = assumptions.CATEGORY

#: (key, scope_type, scope_ref, percent / whole number / amount text)
SeedRow = tuple[str, str, str, str]

HOUSEHOLD_DEFAULTS: tuple[SeedRow, ...] = (
    ("inflation", HH, "", "2.5"),
    ("return_rate", CAT, "cash", "3"),
    ("return_rate", CAT, "investments", "7"),
    ("return_rate", CAT, "superannuation", "7"),
    ("return_rate", CAT, "overseas_pensions", "6"),
    ("horizon_age", HH, "", "95"),
    ("real_wage_growth", HH, "", "0"),
)

#: The seed version that first wrote a row, where it is later than 1. A database already seeded at
#: an earlier version gets only the rows newer than it, so nothing the household removed returns.
SEEDED_IN: dict[tuple[str, str, str], int] = {("real_wage_growth", HH, ""): 2}


def _since(seed: SeedRow) -> int:
    return SEEDED_IN.get(seed[:3], 1)


#: (key, name, overrides). Likely states nothing: it is the Household default.
SCENARIOS: tuple[tuple[str, str, tuple[SeedRow, ...]], ...] = (
    (
        "best",
        "Best",
        (
            ("return_rate", CAT, "cash", "3.5"),
            ("return_rate", CAT, "investments", "10.5"),
            ("return_rate", CAT, "superannuation", "10.5"),
            ("return_rate", CAT, "overseas_pensions", "9"),
        ),
    ),
    ("likely", "Likely", ()),
    (
        "worst",
        "Worst",
        (
            ("inflation", HH, "", "3.5"),
            ("return_rate", CAT, "cash", "2.5"),
            ("return_rate", CAT, "investments", "3.5"),
            ("return_rate", CAT, "superannuation", "3.5"),
            ("return_rate", CAT, "overseas_pensions", "3"),
        ),
    ),
)


def _add(session: Session, scenario_id: int | None, seed: SeedRow) -> None:
    key, scope_type, scope_ref, text = seed
    row = Assumption(scenario_id=scenario_id, key=key, scope_type=scope_type, scope_ref=scope_ref)
    store_value(row, assumptions.parse_value(key, text))
    session.add(row)


def seed_assumptions(session: Session) -> None:
    """Insert any missing seeded rows when the stored seed version is behind; never overwrite."""
    meta = session.get(Meta, SEED_VERSION_KEY)
    stored = int(meta.value) if meta is not None else 0
    if stored >= ASSUMPTION_SEED_VERSION:
        return
    have_default = {
        (r.key, r.scope_type, r.scope_ref)
        for r in session.scalars(select(Assumption).where(Assumption.scenario_id.is_(None)))
    }
    for seed in HOUSEHOLD_DEFAULTS:
        if _since(seed) > stored and seed[:3] not in have_default:
            _add(session, None, seed)
    by_key = {s.key: s for s in session.scalars(select(Scenario)) if s.key}
    now = datetime.now(UTC).replace(tzinfo=None)
    for key, name, overrides in SCENARIOS:
        scenario = by_key.get(key)
        if scenario is None:
            scenario = Scenario(key=key, name=name, created_at=now)
            session.add(scenario)
            session.flush()
        have = {
            (r.key, r.scope_type, r.scope_ref)
            for r in session.scalars(
                select(Assumption).where(Assumption.scenario_id == scenario.id)
            )
        }
        for seed in overrides:
            if _since(seed) > stored and seed[:3] not in have:
                _add(session, scenario.id, seed)
    if meta is None:
        session.add(Meta(key=SEED_VERSION_KEY, value=str(ASSUMPTION_SEED_VERSION)))
    else:
        meta.value = str(ASSUMPTION_SEED_VERSION)
    session.flush()
