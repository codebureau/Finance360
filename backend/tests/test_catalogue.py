"""Seeded Categories and Account types: seeder, Behaviour check and drift against the catalogue doc.

Integration tests run on a real temporary SQLite database built by the real migrations.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from finance360.behaviours import REGISTRY
from finance360.core import catalogue as seed
from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import migrate
from finance360.core.models.audit import AuditEvent
from finance360.core.models.catalogue import AccountType, Category, Meta

REPO = Path(__file__).resolve().parents[2]
CATALOGUE_DOC = REPO / "docs" / "catalogue.md"


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _snapshot(session: Session) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    cats = [(c.key, c.name, c.class_, c.seeded) for c in session.scalars(select(Category))]
    types = [
        (t.key, t.name, t.category_id, t.behaviour_key, t.seeded)
        for t in session.scalars(select(AccountType))
    ]
    return sorted(cats), sorted(types)


# ---- seeder -------------------------------------------------------------------------------------


def test_a_fresh_database_gets_every_catalogue_row(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        seed.seed_catalogue(s)
    with session_factory() as s:
        assert _count(s, Category) == 7
        assert _count(s, AccountType) == 12
        classes = {c.key: c.class_ for c in s.scalars(select(Category))}
        assert classes["loans_credit"] == "liability"
        assert {k for k, v in classes.items() if v == "asset"} == {
            "cash", "investments", "superannuation", "overseas_pensions", "property", "vehicles",
        }  # fmt: skip
        assert all(c.seeded for c in s.scalars(select(Category)))
        assert s.get(Meta, "catalogue_version").value == str(seed.CATALOGUE_VERSION)  # type: ignore[union-attr]


def test_reseeding_creates_no_duplicates(session_factory: sessionmaker[Session]) -> None:
    for _ in range(3):
        with session_factory.begin() as s:
            seed.seed_catalogue(s)
    with session_factory() as s:
        assert (_count(s, Category), _count(s, AccountType), _count(s, Meta)) == (7, 12, 1)


def test_seeding_is_not_audited(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        seed.seed_catalogue(s)
    with session_factory() as s:
        assert _count(s, AuditEvent) == 0


def test_user_edits_survive_a_reseed(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as s:
        seed.seed_catalogue(s)
    with session_factory.begin() as s:
        cat = s.scalars(select(Category).where(Category.key == "cash")).one()
        cat.name = "Everyday cash"
        cat.seeded = False
        t = s.scalars(select(AccountType).where(AccountType.key == "vehicle")).one()
        t.name = "Car"
    with session_factory.begin() as s:
        seed.seed_catalogue(s)
    with session_factory() as s:
        cat = s.scalars(select(Category).where(Category.key == "cash")).one()
        t = s.scalars(select(AccountType).where(AccountType.key == "vehicle")).one()
        assert (cat.name, cat.seeded, t.name) == ("Everyday cash", False, "Car")


@pytest.fixture(scope="module")
def shared_factory(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sessionmaker[Session]]:
    data = tmp_path_factory.mktemp("catalogue-hyp")
    migrate(data)
    engine = make_engine(db_path(data))
    yield make_session_factory(engine)
    engine.dispose()


@settings(max_examples=40, deadline=None)
@given(
    drop_types=st.sets(st.sampled_from([t.key for t in seed.ACCOUNT_TYPES])),
    drop_cats=st.sets(st.sampled_from([c.key for c in seed.CATEGORIES])),
    renames=st.sets(st.sampled_from([c.key for c in seed.CATEGORIES])),
    runs=st.integers(1, 3),
)
def test_seeding_converges_from_any_partial_state_without_overwriting(
    shared_factory: sessionmaker[Session],
    drop_types: set[str],
    drop_cats: set[str],
    renames: set[str],
    runs: int,
) -> None:
    with shared_factory.begin() as s:
        s.execute(delete(AccountType))
        s.execute(delete(Category))
        s.execute(delete(Meta))
        seed.seed_catalogue(s)
    # Damage it: rename some Categories (a user edit) and drop some rows (a partial instance).
    # A dropped Category takes its Account types with it so foreign keys hold.
    with shared_factory.begin() as s:
        for key in renames:
            s.scalars(select(Category).where(Category.key == key)).one().name = f"edited {key}"
        s.flush()
        doomed = {c.id for c in s.scalars(select(Category).where(Category.key.in_(drop_cats)))}
        s.execute(
            delete(AccountType).where(
                AccountType.key.in_(drop_types) | AccountType.category_id.in_(doomed)
            )
        )
        s.execute(delete(Category).where(Category.id.in_(doomed)))
    for _ in range(runs):
        with shared_factory.begin() as s:
            seed.seed_catalogue(s)
    with shared_factory() as s:
        assert {c.key for c in s.scalars(select(Category))} == {c.key for c in seed.CATEGORIES}
        assert sorted(t.key for t in s.scalars(select(AccountType))) == sorted(
            t.key for t in seed.ACCOUNT_TYPES
        )
        for key in renames - drop_cats:  # edited and still present: untouched
            assert s.scalars(select(Category).where(Category.key == key)).one().name == (
                f"edited {key}"
            )
        before = _snapshot(s)
    with shared_factory.begin() as s:
        seed.seed_catalogue(s)
    with shared_factory() as s:
        assert _snapshot(s) == before


# ---- Behaviours ---------------------------------------------------------------------------------


def test_the_registered_behaviours() -> None:
    assert set(REGISTRY) == {"simple_balance", "holdings_based"}
    assert REGISTRY["simple_balance"].name == "Simple balance"
    assert REGISTRY["simple_balance"].derive is None  # its value is its latest Valuation
    assert REGISTRY["holdings_based"].derive is not None


def test_every_seeded_account_type_resolves_to_a_registered_behaviour() -> None:
    assert {t.behaviour_key for t in seed.ACCOUNT_TYPES} <= set(REGISTRY)


def test_the_startup_check_passes_on_the_seed_and_fails_on_an_unknown_key(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as s:
        seed.seed_catalogue(s)
    with session_factory.begin() as s:
        seed.check_behaviour_keys(s, REGISTRY.keys())
        s.scalars(
            select(AccountType).where(AccountType.key == "vehicle")
        ).one().behaviour_key = "no_such_behaviour"
    with session_factory() as s, pytest.raises(seed.UnknownBehaviourError, match="vehicle"):
        seed.check_behaviour_keys(s, REGISTRY.keys())


# ---- drift against docs/catalogue.md ------------------------------------------------------------


def _table(heading: str) -> list[dict[str, str]]:
    """Rows of the markdown table under ``## <heading>`` in the catalogue doc."""
    text = CATALOGUE_DOC.read_text(encoding="utf-8")
    section = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert section, f"no '{heading}' section in docs/catalogue.md"
    lines = [ln for ln in section.group(1).splitlines() if ln.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    return [
        dict(zip(header, (c.strip() for c in ln.strip("|").split("|")), strict=True))
        for ln in lines[2:]
    ]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def test_the_seed_matches_the_categories_in_the_catalogue_doc() -> None:
    doc = [(_slug(r["Category"]), r["Category"], r["Class"].lower()) for r in _table("Categories")]
    assert doc == [(c.key, c.name, c.class_) for c in seed.CATEGORIES]


def test_the_seed_matches_the_account_types_in_the_catalogue_doc() -> None:
    category_keys = {c.name: c.key for c in seed.CATEGORIES}
    # A key that is not the slug of its name (a deliberate, shorter stable key).
    keys = {"Brokerage account (holdings)": "brokerage_holdings"}
    doc = [
        (
            keys.get(r["Account type"], _slug(r["Account type"])),
            r["Account type"],
            category_keys[r["Category"]],
            _slug(r["M1 Behaviour"]),
        )
        for r in _table("Account types")
    ]
    assert doc == [(t.key, t.name, t.category_key, t.behaviour_key) for t in seed.ACCOUNT_TYPES]
