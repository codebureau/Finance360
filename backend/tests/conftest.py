"""Shared fixtures: a real, fully migrated temporary SQLite database (never a mock)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from finance360.core.db import db_path, make_engine, make_session_factory
from finance360.core.migrate import migrate

#: Tier 2 (AGENTS.md "Test tiers"): real servers, concurrency, threads and subprocesses, the
#: populated-database upgrade. CI runs them on every PR; a bare `pytest` skips them. The measured
#: slowest files; add a new real-server or concurrency test file here.
SLOW_FILES = frozenset(
    {
        "test_ai_concurrency.py",
        "test_allowed_hosts.py",
        "test_architecture.py",
        "test_ask_real_server.py",
        "test_auth_concurrency.py",
        "test_commit_before_response.py",
        "test_document_profiles_real_server.py",
        "test_due_real_server.py",
        "test_fetch_api.py",
        "test_fetch_real_server.py",
        "test_imports_commit_real_server.py",
        "test_opening_real_server.py",
        "test_imports_real_server.py",
        "test_library_real_server.py",
        "test_reference_real_server.py",
        "test_rule_figures_real_server.py",
        "test_scenarios_real_server.py",
        "test_scheduler_fetch.py",
        "test_super_statements_real_server.py",
        "test_tax_real_server.py",
        "test_upgrade_populated.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.name in SLOW_FILES:
            item.add_marker(pytest.mark.slow)


class NetworkInTests(BaseException):  # noqa: N818 - not an Exception on purpose: nothing may swallow it
    """A test reached for the outside world. Derives from BaseException so the fetch service's
    "contain any plug-in error" handlers cannot hide it: it must fail the test loudly."""


@pytest.fixture(autouse=True, scope="session")
def au_rule_pack() -> None:
    """The AU Rule pack is registered at start-up by ``create_app``; tests of the service layer
    that build no app need it registered too (the test composition root)."""
    from finance360.core.rulepack import register
    from finance360.rulepacks.au import PACK

    register(PACK)


@pytest.fixture(autouse=True)
def no_provider_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test ever reaches Yahoo or the RBA (AGENTS.md: no network in tests or CI). The live
    transports are replaced for every test; tests of a transport pass it a fake ticker or feed."""
    import finance360.plugins.providers.rba as rba
    import finance360.plugins.providers.yfinance as yfinance_plugin

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise NetworkInTests(
            "A test tried to reach a live provider; use a fake or sample transport."
        )

    monkeypatch.setattr(yfinance_plugin, "yfinance_history", refuse)
    monkeypatch.setattr(rba, "fetch_f11", refuse)


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """Sessions on a database built by the real migrations (so triggers and CHECKs exist)."""
    migrate(tmp_path)
    engine = make_engine(db_path(tmp_path))
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def fast_hash(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Cheap argon2 parameters so tests don't pay ~100 ms per hash. The real parameters are
    asserted separately (test_auth_service.test_hashes_use_the_documented_argon2id_parameters)."""
    from argon2 import PasswordHasher

    from finance360.capabilities.auth import passwords

    monkeypatch.setattr(
        passwords, "_HASHER", PasswordHasher(memory_cost=1024, time_cost=1, parallelism=1)
    )
    yield


@pytest.fixture
def sample_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """A migrated database filled from the synthetic household (Accounts, Instruments and the
    sample PI Account's identifier), loaded through the real services."""
    import shutil

    from finance360.capabilities.household.service import bootstrap
    from finance360.capabilities.imports import account_identifiers
    from finance360.capabilities.imports import registry as importers
    from finance360.capabilities.position import accounts as position_accounts
    from finance360.capabilities.reference import instruments as reference_instruments
    from finance360.core.catalogue import seed_catalogue
    from finance360.plugins.importers.vanguard_pi import IMPORTER

    repo = Path(__file__).resolve().parents[2]
    source = repo / "fixtures" / "sample_household" / "household.json"
    shutil.copy(source, tmp_path / "household.json")
    migrate(tmp_path)
    engine = make_engine(db_path(tmp_path))
    factory = make_session_factory(engine)
    importers.register(IMPORTER)
    with factory.begin() as session:
        seed_catalogue(session)
        bootstrap(session, tmp_path)
        position_accounts.load_fixture_accounts(session, tmp_path)
        reference_instruments.load_fixture_instruments(session, tmp_path)
        account_identifiers.load_fixture_account_identifiers(session, tmp_path)
    yield factory
    engine.dispose()
