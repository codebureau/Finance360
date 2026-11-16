"""The seed catalogue and its idempotent startup seeder (ADR-0009 section 6).

The data below mirrors ``docs/catalogue.md`` (a test parses that document and fails on drift).
The seeder is the single path for the initial seed, so fresh instances and upgrades behave alike:
it inserts rows whose key is missing and never touches an existing row, so user edits survive.
A change to an already-seeded row is a deliberate migration. Bump ``CATALOGUE_VERSION`` whenever
the catalogue changes. Seeding is not audited (system bootstrap); the version is recorded instead.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance360.core.models.catalogue import AccountType, Category, Meta

CATALOGUE_VERSION = 2
CATALOGUE_VERSION_KEY = "catalogue_version"


@dataclass(frozen=True)
class SeedCategory:
    key: str
    name: str
    class_: str


@dataclass(frozen=True)
class SeedAccountType:
    key: str
    name: str
    category_key: str
    behaviour_key: str


CATEGORIES = (
    SeedCategory("cash", "Cash", "asset"),
    SeedCategory("investments", "Investments", "asset"),
    SeedCategory("superannuation", "Superannuation", "asset"),
    SeedCategory("overseas_pensions", "Overseas pensions", "asset"),
    SeedCategory("property", "Property", "asset"),
    SeedCategory("vehicles", "Vehicles", "asset"),
    SeedCategory("loans_credit", "Loans & credit", "liability"),
)

#: Every seeded type runs on the simple balance Behaviour except ``brokerage_holdings``.
ACCOUNT_TYPES = (
    SeedAccountType("transaction_account", "Transaction account", "cash", "simple_balance"),
    SeedAccountType("savings_account", "Savings account", "cash", "simple_balance"),
    SeedAccountType("offset_account", "Offset account", "cash", "simple_balance"),
    SeedAccountType("brokerage_account", "Brokerage account", "investments", "simple_balance"),
    # Added at catalogue_version 2 (ADR-0013 section 4): a new row, so the seeded Brokerage account
    # type stays simple balance and an Account switches to this one by an audited edit.
    SeedAccountType(
        "brokerage_holdings", "Brokerage account (holdings)", "investments", "holdings_based"
    ),
    SeedAccountType("super_fund_account", "Super fund account", "superannuation", "simple_balance"),
    SeedAccountType(
        "overseas_dc_pension", "Overseas DC pension", "overseas_pensions", "simple_balance"
    ),
    SeedAccountType("principal_residence", "Principal residence", "property", "simple_balance"),
    SeedAccountType("vehicle", "Vehicle", "vehicles", "simple_balance"),
    SeedAccountType("home_loan", "Home loan", "loans_credit", "simple_balance"),
    SeedAccountType("credit_card", "Credit card", "loans_credit", "simple_balance"),
)


class UnknownBehaviourError(RuntimeError):
    """An Account type references a Behaviour that is not registered."""


def seed_catalogue(session: Session) -> None:
    """Insert any missing seeded Categories and Account types; never modify existing rows."""
    by_key = {c.key: c for c in session.scalars(select(Category))}
    for seed in CATEGORIES:
        if seed.key not in by_key:
            category = Category(key=seed.key, name=seed.name, class_=seed.class_, seeded=True)
            session.add(category)
            by_key[seed.key] = category
    session.flush()
    existing = set(session.scalars(select(AccountType.key)))
    for type_seed in ACCOUNT_TYPES:
        if type_seed.key not in existing:
            session.add(
                AccountType(
                    key=type_seed.key,
                    name=type_seed.name,
                    category_id=by_key[type_seed.category_key].id,
                    behaviour_key=type_seed.behaviour_key,
                    seeded=True,
                )
            )
    meta = session.get(Meta, CATALOGUE_VERSION_KEY)
    if meta is None:
        session.add(Meta(key=CATALOGUE_VERSION_KEY, value=str(CATALOGUE_VERSION)))
    else:
        meta.value = str(CATALOGUE_VERSION)
    session.flush()


def check_behaviour_keys(session: Session, registered: Collection[str]) -> None:
    """Raise if any stored Account type names a Behaviour that is not in ``registered``."""
    bad = sorted(
        f"{key} -> {behaviour_key}"
        for key, behaviour_key in session.execute(
            select(AccountType.key, AccountType.behaviour_key)
        )
        if behaviour_key not in registered
    )
    if bad:
        raise UnknownBehaviourError(
            "Account types reference unregistered Behaviours: " + ", ".join(bad)
        )
