"""The "every mutation is audited" guard (ADR-0009 section 5).

ADR-0009 chose an explicit ``audit.record`` call in every mutating service over automatic
auditing, and relies on this test to stop a future slice bypassing it. How it cannot be bypassed:

* **Discovery.** Every public function of the service modules that takes a ``session`` is found by
  introspection. One that takes an ``AuditContext`` is a mutator and must have an exerciser in
  ``MUTATORS``; any other must be listed in ``NOT_AUDITED`` with a reason. A new function that is
  in neither fails ``test_every_service_function_is_classified``.
* **Exercise.** Each mutator is actually called on a real migrated SQLite database and must add
  at least one ``audit_event`` (``test_every_mutator_writes_an_audit_event``). Disabling the
  ``audit.record`` call in a mutator makes its case fail, and
  ``test_the_guard_detects_a_skipped_writer``
  proves the harness itself notices.
* **Auth** has its own writer (``record_event`` -> ``auth_event``) and the same two-sided check.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import shutil
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pyotp
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

import finance360.capabilities.documents as documents_pkg
import finance360.capabilities.due as due_pkg
import finance360.capabilities.household as household_pkg
import finance360.capabilities.imports as imports_pkg
import finance360.capabilities.library as library_pkg
import finance360.capabilities.position as position_pkg
import finance360.capabilities.projection as projection_pkg
import finance360.capabilities.reference as reference_pkg
import finance360.capabilities.tax as tax_pkg
import finance360.core
from finance360.capabilities.auth import logins, totp
from finance360.capabilities.auth import service as auth_service
from finance360.capabilities.auth.types import AuthAttempt, Principal, Role
from finance360.capabilities.documents import registry as document_registry
from finance360.capabilities.documents import store as document_store
from finance360.capabilities.documents.reading import OK, Reading
from finance360.capabilities.due import service as due
from finance360.capabilities.household import service as household
from finance360.capabilities.imports import account_identifiers, commit, opening
from finance360.capabilities.imports import registry as importers
from finance360.capabilities.imports import service as import_service
from finance360.capabilities.library import profiles as library_profiles
from finance360.capabilities.library import registry as link_kinds
from finance360.capabilities.library import service as library
from finance360.capabilities.position import accounts, super_statements, valuations
from finance360.capabilities.projection import income_streams, scenarios
from finance360.capabilities.reference import fetch, fx_rates, instruments, prices
from finance360.capabilities.reference.providers import FxFetch, FxQuote, PriceBar, PriceSeries
from finance360.capabilities.tax import link_kind as tax_link_kind
from finance360.capabilities.tax import lodged as tax_lodged
from finance360.capabilities.tax import saved as tax_saved
from finance360.capabilities.tax import service as tax
from finance360.core import audit, rulepack_store
from finance360.core.audit import AuditContext
from finance360.core.catalogue import seed_catalogue
from finance360.core.models.audit import AuditEvent
from finance360.core.models.auth import AuthEvent
from finance360.core.models.price import Price
from finance360.main import register_due_rules
from finance360.plugins.importers.vanguard_pi import IMPORTER
from support import FakeFxProvider, FakePriceProvider, register_income_statement_target

importers.register(IMPORTER)  # the composition root does this at startup
link_kinds.register(tax_link_kind.KIND)  # and this: linking a document names a registered kind
register_due_rules()  # and so does this: snoozing names a registered Due rule
CTX = AuditContext("owner", "login:1", "api")
TODAY = date(2026, 10, 17)
NOW = datetime(2026, 10, 17, 1, 0, 0)
FIXTURE = Path(__file__).parents[2] / "fixtures" / "sample_household" / "household.json"


def _modules_of(package: ModuleType) -> list[ModuleType]:
    return [
        importlib.import_module(info.name)
        for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}.")
    ]


#: Every module of the data Capabilities and of core is scanned, so a new file is covered too.
SCANNED = (
    documents_pkg,
    due_pkg,
    household_pkg,
    imports_pkg,
    library_pkg,
    position_pkg,
    projection_pkg,
    reference_pkg,
    tax_pkg,
    finance360.core,
)


def _public_session_functions(module: ModuleType) -> dict[str, Callable[..., object]]:
    return {
        name: fn
        for name, fn in inspect.getmembers(module, inspect.isfunction)
        if not name.startswith("_")
        and fn.__module__ == module.__name__
        and "session" in inspect.signature(fn).parameters
    }


def _takes_audit_context(fn: Callable[..., object]) -> bool:
    return any(
        "AuditContext" in str(p.annotation) for p in inspect.signature(fn).parameters.values()
    )


# --- the exercisers ------------------------------------------------------------------------


class Env:
    """A migrated database with a Household, the catalogue and two Members."""

    def __init__(self, factory: sessionmaker[Session], data_dir: Path) -> None:
        self.factory = factory
        self.data_dir = data_dir

    def seed(
        self,
        s: Session,
        *,
        accounts_: bool = False,
        rates: bool = False,
        instruments_: bool = False,
        prices_: bool = False,
        super_account: bool = False,
        super_statement: bool = False,
        import_file: str | None = None,
    ) -> dict[str, int]:
        ids: dict[str, int] = {}
        household.ensure_household(s)
        seed_catalogue(s)
        a = household.add_member(s, CTX, "Alex Sample", date(1984, 3, 12), today=TODAY)
        b = household.add_member(s, CTX, "Jordan Sample", date(1986, 9, 30), today=TODAY)
        ids.update(alex=a.id, jordan=b.id)
        if accounts_:
            acct = accounts.create_account(s, CTX, _account(a.id))
            ids["account"] = acct.id
            v = valuations.add_valuation(s, CTX, acct.id, _valuation(), today=TODAY)
            ids["valuation"] = v.id
        if super_account or super_statement:
            fund = accounts.create_account(
                s,
                CTX,
                accounts.AccountInput(
                    name="Sample Super",
                    category_key="superannuation",
                    account_type_key="super_fund_account",
                    owners=[accounts.OwnerInput(a.id)],
                ),
            )
            ids["super_account"] = fund.id
        if super_statement:
            ids["statement"] = super_statements.create_super_statement(
                s, CTX, ids["super_account"], _super_statement(), today=TODAY
            ).id
        if rates:
            r = fx_rates.add_fx_rate(s, CTX, _rate())
            ids["rate"] = r.id
        if instruments_:
            inst = instruments.create_instrument(s, CTX, _instrument())
            ids["instrument"] = inst.id
            ids["identifier"] = inst.identifiers[0].id
            if prices_:
                ids["price"] = prices.add_price(s, CTX, inst.id, _price()).id
        if import_file is not None:
            staged = import_service.stage(
                s, CTX, IMPORT_FILES[import_file], IMPORT_NAMES[import_file], ids["account"]
            )
            ids["import"] = staged.import_.id
        return ids


#: Tiny synthetic Vanguard-shaped exports: one deposit (nothing to match), and the sample trades.
TRADES_NAME = "investment_transactions_accSAMPLE-PI-0001_03-Aug-2026_30-Sep-2026.csv"
IMPORT_NAMES = {
    "deposit": "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv",
    "trades": TRADES_NAME,
    "unknown_type": "cash_transactions_accSAMPLE-PI-0001_01-Jul-2026_30-Sep-2026.csv",
}
IMPORT_FILES = {
    "deposit": (
        b"Date,Type,Product Type,Product Name,Product ID,Units,Total\n"
        b"02-Jul-2026,Deposit,Cash account,Deposit from linked bank,CASH,,5000.00\n"
    ),
    "trades": (FIXTURE.parent / "imports" / TRADES_NAME).read_bytes(),
    "unknown_type": (
        b"Date,Type,Product Type,Product Name,Product ID,Units,Total\n"
        b"02-Jul-2026,Bonus,Cash account,Sample bonus,CASH,,5.00\n"
    ),
}


ONE_BUY_NAME = "investment_transactions_accSAMPLE-PI-0001_01-Oct-2026_01-Oct-2026.csv"
ONE_BUY = (
    b"Account number,Investment,Product ID,Product Type,Trade Date,Type,Unit Price,Quantity,"
    b"Value,Brokerage\n"
    b"SAMPLE-PI-0001,Sample ETF,SMPL,ETF,01-Oct-2026,Buy Trade,10.50,5,52.50,\n"
)


def _backfill_after_a_commit_without_prices(s: Session, e: Env, i: dict[str, int]) -> None:
    """Commit a one-buy file, then retire its import Price as if it had been committed before
    commits recorded them: the back-fill must record it again, and audit that."""
    ticker = instruments.IdentifierInput("ticker", "SMPL")
    instruments.add_identifier(s, CTX, i["instrument"], ticker)
    staged = import_service.stage(s, CTX, ONE_BUY, ONE_BUY_NAME, i["account"])
    commit.commit(s, CTX, staged.import_.id)
    s.execute(
        update(Price).where(Price.source == "import").values(superseded_at=datetime(2026, 10, 2))
    )
    assert commit.backfill_import_prices(s) == 1


def _account(member_id: int) -> accounts.AccountInput:
    return accounts.AccountInput(
        name="Everyday",
        category_key="cash",
        account_type_key="transaction_account",
        owners=[accounts.OwnerInput(member_id)],
    )


def _valuation(amount: str = "100.00") -> valuations.ValuationInput:
    return valuations.ValuationInput(date(2026, 10, 1), amount, "manual", "Checked")


def _super_statement(closing: str = "1220.00") -> super_statements.SuperStatementInput:
    return super_statements.SuperStatementInput(
        period_start=date(2025, 7, 1),
        period_end=date(2026, 6, 30),
        opening="1000.00",
        contributions_in="200.00",
        investment_returns="50.00",
        withdrawals="30.00",
        closing=closing,
        ticked_checks=["roll"],
    )


def _rate(rate: str = "2.0000") -> fx_rates.FxRateInput:
    return fx_rates.FxRateInput("GBP", date(2026, 10, 1), rate, "Checked")


def _instrument() -> instruments.InstrumentInput:
    ident = instruments.IdentifierInput("name", "Sample Alias")
    return instruments.InstrumentInput("Sample ETF", "etf", "AUD", None, (ident,))


def _price() -> prices.PriceInput:
    return prices.PriceInput(date(2026, 10, 1), "10.5000", "Checked")


def _stream(member_id: int, gross: str = "90000.00") -> income_streams.StreamInput:
    return income_streams.StreamInput(
        name="Sample salary",
        type_key="salary",
        gross_annual=gross,
        take_home_annual="70000.00",
        owners=[income_streams.OwnerInput(member_id)],
    )


def _with_fixture(env: Env) -> None:
    shutil.copy(FIXTURE, env.data_dir / household.FIXTURE_FILENAME)


#: name -> exerciser. Each runs in ``env.factory.begin()`` after a plain ``seed``; the ``needs``
#: flags say what else to create first. Keys are ``module.function`` so names cannot collide.
Exerciser = Callable[[Session, Env, dict[str, int]], object]

MUTATORS: dict[str, tuple[dict[str, bool | str], Exerciser]] = {
    "household.service.rename_household": (
        {},
        lambda s, e, i: household.rename_household(s, CTX, "The Sample Household"),
    ),
    "household.service.set_time_zone": (
        {},
        lambda s, e, i: household.set_time_zone(s, CTX, "Australia/Perth"),
    ),
    "household.service.set_effort_level": (
        {},
        lambda s, e, i: household.set_effort_level(s, CTX, "steady"),
    ),
    "household.service.set_household_facts": (
        {},
        lambda s, e, i: household.set_household_facts(s, CTX, couple_status="couple"),
    ),
    "due.service.snooze": (
        {},
        lambda s, e, i: due.snooze(s, CTX, "position.manual_valuation", "account:1@none", "week"),
    ),
    "due.service.unsnooze": (
        {},
        lambda s, e, i: _unsnooze(s),
    ),
    "core.rulepack_store.capture_figure": ({}, lambda s, e, i: _capture_rule_figure(s)),
    "core.rulepack_store.correct_figure": (
        {},
        lambda s, e, i: rulepack_store.correct_figure(
            s, CTX, _capture_rule_figure(s).id, _CAPTURE_TYPED, today=TODAY
        ),
    ),
    "core.rulepack_store.withdraw_figure": (
        {},
        lambda s, e, i: rulepack_store.withdraw_figure(s, CTX, _capture_rule_figure(s).id),
    ),
    "core.rulepack_store.carry_figures": (
        {},
        lambda s, e, i: rulepack_store.carry_figures(s, CTX, "au", 2027, today=TODAY),
    ),
    "core.rulepack_store.confirm_unchanged": (
        {},
        lambda s, e, i: rulepack_store.confirm_unchanged(
            s, CTX, _carried_rule_figure(s).id, today=TODAY
        ),
    ),
    "core.rulepack_store.confirm_page": (
        {},
        lambda s, e, i: _confirm_a_carried_page(s),
    ),
    "household.service.add_member": (
        {},
        lambda s, e, i: household.add_member(s, CTX, "Sam Sample", date(1990, 1, 1), today=TODAY),
    ),
    "household.service.update_member": (
        {},
        lambda s, e, i: household.update_member(
            s, CTX, i["alex"], "Alexandra Sample", date(1984, 3, 12), today=TODAY
        ),
    ),
    "household.service.delete_member": (
        {},
        lambda s, e, i: household.delete_member(s, CTX, i["jordan"]),
    ),
    "position.accounts.create_account": (
        {},
        lambda s, e, i: accounts.create_account(s, CTX, _account(i["alex"])),
    ),
    "position.accounts.update_account": (
        {"accounts": True},
        lambda s, e, i: accounts.update_account(
            s,
            CTX,
            i["account"],
            accounts.AccountInput(**{**_account(i["alex"]).__dict__, "notes": "n"}),
        ),
    ),
    "position.accounts.close_account": (
        {"accounts": True},
        lambda s, e, i: accounts.close_account(
            s, CTX, i["account"], date(2026, 10, 10), confirm_non_zero=True
        ),
    ),
    "position.valuations.add_valuation": (
        {"accounts": True},
        lambda s, e, i: valuations.add_valuation(
            s,
            CTX,
            i["account"],
            valuations.ValuationInput(date(2026, 10, 2), "5.00", "manual", "Checked"),
            today=TODAY,
        ),
    ),
    "position.valuations.correct_valuation": (
        {"accounts": True},
        lambda s, e, i: valuations.correct_valuation(
            s, CTX, i["valuation"], _valuation("250.00"), today=TODAY
        ),
    ),
    "position.valuations.remove_valuation": (
        {"accounts": True},
        lambda s, e, i: valuations.remove_valuation(s, CTX, i["valuation"]),
    ),
    "position.valuations.write_closing_valuation": (
        {"accounts": True},
        lambda s, e, i: valuations.write_closing_valuation(
            s, CTX, accounts.get_account(s, i["account"]), date(2026, 10, 10)
        ),
    ),
    "position.super_statements.create_super_statement": (
        {"super_account": True},
        lambda s, e, i: super_statements.create_super_statement(
            s, CTX, i["super_account"], _super_statement(), today=TODAY
        ),
    ),
    "position.super_statements.correct_super_statement": (
        {"super_statement": True},
        lambda s, e, i: super_statements.correct_super_statement(
            s, CTX, i["statement"], _super_statement("1221.00"), today=TODAY
        ),
    ),
    "position.super_statements.remove_super_statement": (
        {"super_statement": True},
        lambda s, e, i: super_statements.remove_super_statement(s, CTX, i["statement"]),
    ),
    "reference.fx_rates.add_fx_rate": ({}, lambda s, e, i: fx_rates.add_fx_rate(s, CTX, _rate())),
    "reference.fx_rates.correct_fx_rate": (
        {"rates": True},
        lambda s, e, i: fx_rates.correct_fx_rate(s, CTX, i["rate"], _rate("2.5000")),
    ),
    "reference.fx_rates.remove_fx_rate": (
        {"rates": True},
        lambda s, e, i: fx_rates.remove_fx_rate(s, CTX, i["rate"]),
    ),
    "reference.instruments.create_instrument": (
        {},
        lambda s, e, i: instruments.create_instrument(s, CTX, _instrument()),
    ),
    "reference.instruments.update_instrument": (
        {"instruments": True},
        lambda s, e, i: instruments.update_instrument(
            s, CTX, i["instrument"], instruments.InstrumentInput("Sample ETF 2", "etf", "AUD")
        ),
    ),
    "reference.instruments.add_identifier": (
        {"instruments": True},
        lambda s, e, i: instruments.add_identifier(
            s, CTX, i["instrument"], instruments.IdentifierInput("ticker", "SMPL")
        ),
    ),
    "reference.instruments.remove_identifier": (
        {"instruments": True},
        lambda s, e, i: instruments.remove_identifier(s, CTX, i["instrument"], i["identifier"]),
    ),
    "reference.prices.add_price": (
        {"instruments": True},
        lambda s, e, i: prices.add_price(s, CTX, i["instrument"], _price()),
    ),
    "reference.prices.correct_price": (
        {"instruments": True, "prices": True},
        lambda s, e, i: prices.correct_price(
            s,
            CTX,
            i["instrument"],
            i["price"],
            prices.PriceInput(date(2026, 10, 1), "11.0000", "Rechecked"),
        ),
    ),
    "reference.prices.remove_price": (
        {"instruments": True, "prices": True},
        lambda s, e, i: prices.remove_price(s, CTX, i["instrument"], i["price"]),
    ),
    "imports.service.stage": (
        {"accounts": True},
        lambda s, e, i: import_service.stage(s, CTX, IMPORT_FILES["trades"], "t.csv", i["account"]),
    ),
    "imports.service.discard": (
        {"accounts": True, "import_file": "trades"},
        lambda s, e, i: import_service.discard(s, CTX, i["import"]),
    ),
    "imports.service.map_reference": (
        {"accounts": True, "instruments": True, "import_file": "trades"},
        lambda s, e, i: import_service.map_reference(
            s, CTX, i["import"], "ticker", "SNHX", i["instrument"]
        ),
    ),
    "imports.service.create_for_reference": (
        {"accounts": True, "import_file": "trades"},
        lambda s, e, i: import_service.create_for_reference(
            s,
            CTX,
            i["import"],
            "ticker",
            "SNHX",
            instruments.InstrumentInput("Sample New Horizons ETF", "etf", "AUD"),
        ),
    ),
    "imports.service.map_type": (
        {"accounts": True, "import_file": "unknown_type"},
        lambda s, e, i: import_service.map_type(s, CTX, i["import"], "Bonus", "deposit"),
    ),
    "imports.commit.commit": (
        {"accounts": True, "import_file": "deposit"},
        lambda s, e, i: commit.commit(s, CTX, i["import"]),
    ),
    "imports.commit.backfill_import_prices": (
        {"accounts": True, "instruments": True},
        _backfill_after_a_commit_without_prices,
    ),
    "imports.opening.record": (
        {"accounts": True},
        lambda s, e, i: opening.record(
            s, CTX, i["account"], opening.OpeningInput("cash", date(2026, 7, 1), amount="250.00")
        ),
    ),
    "imports.account_identifiers.add_identifier": (
        {"accounts": True},
        lambda s, e, i: account_identifiers.add_identifier(
            s, CTX, i["account"], account_identifiers.IdentifierInput("last4", "1234")
        ),
    ),
    "imports.account_identifiers.remove_identifier": (
        {"accounts": True},
        lambda s, e, i: account_identifiers.remove_identifier(
            s,
            CTX,
            i["account"],
            account_identifiers.add_identifier(
                s, CTX, i["account"], account_identifiers.IdentifierInput("last4", "1234")
            ).id,
        ),
    ),
    "projection.scenarios.create_scenario": (
        {},
        lambda s, e, i: scenarios.create_scenario(s, CTX, "Sample Scenario"),
    ),
    "projection.scenarios.update_scenario": (
        {},
        lambda s, e, i: scenarios.update_scenario(
            s, CTX, scenarios.create_scenario(s, CTX, "Sample Scenario").id, "Renamed", "Notes"
        ),
    ),
    "projection.scenarios.set_drawdown_order": (
        {},
        lambda s, e, i: scenarios.set_drawdown_order(
            s,
            CTX,
            scenarios.create_scenario(s, CTX, "Sample Scenario").id,
            ["investments", "cash", "superannuation", "overseas_pensions"],
        ),
    ),
    "projection.scenarios.delete_scenario": (
        {},
        lambda s, e, i: scenarios.delete_scenario(
            s, CTX, scenarios.create_scenario(s, CTX, "Sample Scenario").id
        ),
    ),
    "projection.scenarios.set_assumptions": (
        {},
        lambda s, e, i: scenarios.set_assumptions(
            s, CTX, None, [scenarios.Change("inflation", "household", "", "2.5")]
        ),
    ),
    "projection.scenarios.set_spending_phases": (
        {},
        lambda s, e, i: scenarios.set_spending_phases(
            s, CTX, None, [("last_retirement", 0, "70000")]
        ),
    ),
    "projection.income_streams.create_stream": (
        {},
        lambda s, e, i: income_streams.create_stream(s, CTX, _stream(i["alex"])),
    ),
    "projection.income_streams.update_stream": (
        {},
        lambda s, e, i: income_streams.update_stream(
            s,
            CTX,
            income_streams.create_stream(s, CTX, _stream(i["alex"])).id,
            _stream(i["alex"], gross="95000.00"),
        ),
    ),
    "projection.income_streams.delete_stream": (
        {},
        lambda s, e, i: income_streams.delete_stream(
            s, CTX, income_streams.create_stream(s, CTX, _stream(i["alex"])).id
        ),
    ),
    "projection.runs.save_run": (
        {},
        lambda s, e, i: _save_sample_run(s, i),
    ),
    "projection.runs.rerun_with_current_engine": (
        {},
        lambda s, e, i: _rerun_sample_run(s, i),
    ),
    "reference.fetch.store_prices": (
        {"instruments": True},
        lambda s, e, i: fetch.store_prices(
            s,
            CTX,
            1,
            FakePriceProvider({}),
            fetch.PriceTarget(i["instrument"], "Sample ETF", "SMPL.AX", "AUD", date(2026, 10, 1)),
            PriceSeries("AUD", (PriceBar(date(2026, 10, 1), Decimal("10.5000")),)),
            TODAY,
        ),
    ),
    "reference.fetch.store_fx_rates": (
        {},
        lambda s, e, i: fetch.store_fx_rates(
            s,
            CTX,
            1,
            FakeFxProvider(FxFetch((), ())),
            "GBP",
            [FxQuote("GBP", date(2026, 10, 1), Decimal("2.0000"), "published A$1 = 0.5 GBP")],
            date(2026, 10, 1),
            TODAY,
        ),
    ),
    "tax.service.create_record": ({}, lambda s, e, i: _tax_record(s, i)),
    "tax.service.update_record": (
        {},
        lambda s, e, i: tax.update_record(
            s, CTX, _tax_record(s, i), _tax_input(i, "Sample tools, corrected")
        ),
    ),
    "tax.service.delete_record": ({}, lambda s, e, i: tax.delete_record(s, CTX, _tax_record(s, i))),
    "tax.saved.create_whatif": ({}, lambda s, e, i: _tax_whatif(s, i)),
    "tax.saved.update_whatif": (
        {},
        lambda s, e, i: tax_saved.update_whatif(
            s, CTX, _tax_whatif(s, i), tax_saved.WhatIfInput("Sample what-if, renamed", 2026)
        ),
    ),
    "tax.saved.delete_whatif": (
        {},
        lambda s, e, i: tax_saved.delete_whatif(s, CTX, _tax_whatif(s, i)),
    ),
    "tax.saved.save_estimate": ({}, lambda s, e, i: _save_tax_estimate(s, i)),
    "tax.saved.rerun_with_current_engine": ({}, lambda s, e, i: _rerun_tax_estimate(s, i)),
    "tax.lodged.create_lodged_result": ({}, lambda s, e, i: _lodged_result(s, i)),
    "tax.lodged.amend_lodged_result": (
        {},
        lambda s, e, i: tax_lodged.amend_lodged_result(
            s, CTX, _lodged_result(s, i), _lodged_input(i, "2026-10-09", "51000.00")
        ),
    ),
    "library.service.add_document": ({}, lambda s, e, i: _add_document(s, e)),
    "library.service.link": ({}, lambda s, e, i: _link_document(s, e, i)),
    "library.service.unlink": ({}, lambda s, e, i: _unlink_document(s, e, i)),
    "library.service.delete_document": ({}, lambda s, e, i: _delete_document(s, e)),
    "library.profiles.propose_profile": ({}, lambda s, e, i: _propose_profile(s, e)),
    "library.profiles.activate_profile": (
        {},
        lambda s, e, i: library_profiles.activate_profile(
            s, CTX, e.data_dir, _propose_profile(s, e)
        ),
    ),
    "documents.store.propose": ({}, lambda s, e, i: _store_propose(s)),
    "documents.store.activate": (
        {},
        lambda s, e, i: document_store.activate(s, CTX, _store_propose(s)),
    ),
    "documents.store.retire": (
        {},
        lambda s, e, i: document_store.retire(s, CTX, _store_propose(s)),
    ),
}

#: Fixture loaders audit through their own system AuditContext rather than taking one, so they
#: are exercised on an empty database (no seeding) with the synthetic fixture file in place.
LOADERS: dict[str, Exerciser] = {
    "household.service.bootstrap": lambda s, e, i: household.bootstrap(s, e.data_dir),
    "household.service.load_fixture_facts": lambda s, e, i: household.load_fixture_facts(
        s, e.data_dir
    ),
    "position.accounts.load_fixture_accounts": lambda s, e, i: accounts.load_fixture_accounts(
        s, e.data_dir
    ),
    "position.valuations.load_fixture_valuations": lambda s, e, i: (
        valuations.load_fixture_valuations(s, e.data_dir)
    ),
    "position.super_statements.load_fixture_super_statements": lambda s, e, i: (
        super_statements.load_fixture_super_statements(s, e.data_dir)
    ),
    "reference.fx_rates.load_fixture_fx_rates": lambda s, e, i: fx_rates.load_fixture_fx_rates(
        s, e.data_dir
    ),
    "reference.instruments.load_fixture_instruments": (
        lambda s, e, i: instruments.load_fixture_instruments(s, e.data_dir)
    ),
    "reference.prices.load_fixture_prices": lambda s, e, i: prices.load_fixture_prices(
        s, e.data_dir
    ),
    "imports.account_identifiers.load_fixture_account_identifiers": (
        lambda s, e, i: account_identifiers.load_fixture_account_identifiers(s, e.data_dir)
    ),
    "projection.scenarios.load_fixture_assumptions": (
        lambda s, e, i: scenarios.load_fixture_assumptions(s, e.data_dir)
    ),
    "projection.income_streams.load_fixture_income_streams": (
        lambda s, e, i: income_streams.load_fixture_income_streams(s, e.data_dir)
    ),
    "tax.service.load_fixture_tax_records": (
        lambda s, e, i: tax.load_fixture_tax_records(s, e.data_dir)
    ),
    "tax.saved.load_fixture_tax_whatifs": lambda s, e, i: _load_tax_whatifs(s, e),
    "tax.lodged.load_fixture_lodged_results": lambda s, e, i: _load_lodged_results(s, e),
    "library.service.load_fixture_documents": lambda s, e, i: _load_library_documents(s, e),
    "library.profiles.load_fixture_document_profiles": (
        lambda s, e, i: _load_document_profiles(s, e)
    ),
    "tax.service.load_fixture_tax_evidence": lambda s, e, i: _load_tax_evidence(s, e),
    "core.rulepack_store.load_fixture_rule_figures": (
        lambda s, e, i: rulepack_store.load_fixture_rule_figures(s, e.data_dir, today=TODAY)
    ),
}

#: The exact record each mutator must write: (entity_type, action). Counting rows is not enough:
#: a mutator that calls another audited mutator would pass with its own writer removed
#: (close_account also writes a closing Valuation).
EXPECTED: dict[str, tuple[str, str]] = {
    "household.service.rename_household": ("household", "update"),
    "household.service.set_time_zone": ("household", "update"),
    "household.service.set_effort_level": ("household", "update"),
    "household.service.set_household_facts": ("household", "update"),
    "household.service.load_fixture_facts": ("household", "update"),
    "due.service.snooze": ("due_snooze", "create"),
    "due.service.unsnooze": ("due_snooze", "delete"),
    "core.rulepack_store.capture_figure": ("rule_capture", "create"),
    "core.rulepack_store.correct_figure": ("rule_capture", "supersede"),
    "core.rulepack_store.withdraw_figure": ("rule_capture", "delete"),
    "core.rulepack_store.carry_figures": ("rule_capture", "create"),
    "core.rulepack_store.confirm_unchanged": ("rule_capture", "supersede"),
    "core.rulepack_store.confirm_page": ("rule_capture", "supersede"),
    "household.service.add_member": ("member", "create"),
    "household.service.update_member": ("member", "update"),
    "household.service.delete_member": ("member", "delete"),
    "position.accounts.create_account": ("account", "create"),
    "position.accounts.update_account": ("account", "update"),
    "position.accounts.close_account": ("account", "close"),
    "position.valuations.add_valuation": ("valuation", "create"),
    "position.valuations.correct_valuation": ("valuation", "supersede"),
    "position.valuations.remove_valuation": ("valuation", "supersede"),
    "position.valuations.write_closing_valuation": ("valuation", "create"),
    "reference.fx_rates.add_fx_rate": ("fx_rate", "create"),
    "reference.fx_rates.correct_fx_rate": ("fx_rate", "supersede"),
    "reference.fx_rates.remove_fx_rate": ("fx_rate", "supersede"),
    "household.service.bootstrap": ("member", "create"),
    "position.accounts.load_fixture_accounts": ("account", "create"),
    "position.valuations.load_fixture_valuations": ("valuation", "create"),
    "position.super_statements.create_super_statement": ("super_statement", "create"),
    "position.super_statements.correct_super_statement": ("super_statement", "supersede"),
    "position.super_statements.remove_super_statement": ("super_statement", "supersede"),
    "position.super_statements.load_fixture_super_statements": ("super_statement", "create"),
    "reference.fx_rates.load_fixture_fx_rates": ("fx_rate", "create"),
    "reference.instruments.create_instrument": ("instrument", "create"),
    "reference.instruments.update_instrument": ("instrument", "update"),
    "reference.instruments.add_identifier": ("instrument", "update"),
    "reference.instruments.remove_identifier": ("instrument", "update"),
    "reference.instruments.load_fixture_instruments": ("instrument", "create"),
    "reference.prices.add_price": ("price", "create"),
    "reference.prices.correct_price": ("price", "supersede"),
    "reference.prices.remove_price": ("price", "supersede"),
    "reference.prices.load_fixture_prices": ("price", "create"),
    "imports.service.stage": ("import", "create"),
    "imports.service.discard": ("import", "update"),
    "imports.service.map_reference": ("instrument", "update"),
    "imports.service.create_for_reference": ("instrument", "create"),
    "imports.service.map_type": ("import_source", "update"),
    "imports.commit.commit": ("import", "update"),
    "imports.commit.backfill_import_prices": ("price", "create"),
    "imports.opening.record": ("transaction", "create"),
    "imports.account_identifiers.add_identifier": ("account_identifier", "create"),
    "imports.account_identifiers.remove_identifier": ("account_identifier", "delete"),
    "imports.account_identifiers.load_fixture_account_identifiers": (
        "account_identifier",
        "create",
    ),
    "reference.fetch.store_prices": ("price", "create"),
    "reference.fetch.store_fx_rates": ("fx_rate", "create"),
    "projection.scenarios.create_scenario": ("scenario", "create"),
    "projection.scenarios.update_scenario": ("scenario", "update"),
    "projection.scenarios.set_drawdown_order": ("scenario", "update"),
    "projection.scenarios.delete_scenario": ("scenario", "delete"),
    "projection.scenarios.set_assumptions": ("assumption", "create"),
    "projection.scenarios.set_spending_phases": ("spending_phase", "create"),
    "projection.scenarios.load_fixture_assumptions": ("assumption", "create"),
    "projection.income_streams.create_stream": ("income_stream", "create"),
    "projection.income_streams.update_stream": ("income_stream", "update"),
    "projection.income_streams.delete_stream": ("income_stream", "delete"),
    "projection.income_streams.load_fixture_income_streams": ("income_stream", "create"),
    "projection.runs.save_run": ("projection_run", "create"),
    "projection.runs.rerun_with_current_engine": ("projection_run", "create"),
    "tax.service.create_record": ("tax_record", "create"),
    "tax.service.update_record": ("tax_record", "update"),
    "tax.service.delete_record": ("tax_record", "delete"),
    "tax.saved.create_whatif": ("tax_whatif", "create"),
    "tax.saved.update_whatif": ("tax_whatif", "update"),
    "tax.saved.delete_whatif": ("tax_whatif", "delete"),
    "tax.saved.save_estimate": ("tax_estimate", "create"),
    "tax.saved.rerun_with_current_engine": ("tax_estimate", "create"),
    "tax.lodged.create_lodged_result": ("tax_lodged_result", "create"),
    "tax.lodged.amend_lodged_result": ("tax_lodged_result", "supersede"),
    "tax.lodged.load_fixture_lodged_results": ("tax_lodged_result", "create"),
    "tax.saved.load_fixture_tax_whatifs": ("tax_estimate", "create"),
    "library.service.add_document": ("library_document", "create"),
    "library.service.link": ("document_link", "create"),
    "library.service.unlink": ("document_link", "delete"),
    "library.service.delete_document": ("library_document", "delete"),
    "library.profiles.propose_profile": ("document_profile", "create"),
    "library.profiles.activate_profile": ("document_profile", "update"),
    "documents.store.propose": ("document_profile", "create"),
    "documents.store.activate": ("document_profile", "update"),
    "documents.store.retire": ("document_profile", "close"),
    "library.profiles.load_fixture_document_profiles": ("document_profile", "create"),
    "tax.service.load_fixture_tax_records": ("tax_record", "create"),
    "library.service.load_fixture_documents": ("library_document", "create"),
    "tax.service.load_fixture_tax_evidence": ("document_link", "create"),
    "core.rulepack_store.load_fixture_rule_figures": ("rule_capture", "create"),
}


_CAPTURE_TYPED = rulepack_store.CaptureInput("0.91", quote="An invented sentence.")


def _capture_rule_figure(s: Session) -> Any:
    """Capture an invented car rate for FY 2026-27 (the shipped pack holds none for that year)."""
    return rulepack_store.capture_figure(
        s,
        CTX,
        "au",
        "tax.car.rate_per_km",
        2027,
        rulepack_store.CaptureInput("0.90", quote="An invented sentence."),
        today=TODAY,
    )


def _carried_rule_figure(s: Session) -> Any:
    """Start FY 2026-27 from the earlier year and return one carried row (the audit of the carry is
    earlier than what the caller measures only if the caller records after this returns)."""
    from sqlalchemy import select

    from finance360.core.models.rule_capture import RuleCapture

    rulepack_store.carry_figures(s, CTX, "au", 2027, today=TODAY)
    return s.scalars(
        select(RuleCapture).where(RuleCapture.method == "carried", RuleCapture.ended_at.is_(None))
    ).first()


def _confirm_a_carried_page(s: Session) -> Any:
    _carried_rule_figure(s)
    return rulepack_store.confirm_page(
        s,
        CTX,
        "au",
        2027,
        "https://www.ato.gov.au/individuals-and-families/income-deductions-offsets-and-records/"
        "tax-offsets/low-income-tax-offset",
        today=TODAY,
    )


def _unsnooze(s: Session) -> None:
    """Snooze one item, then take the Snooze away: the delete is what must be audited."""
    row = due.snooze(s, CTX, "position.manual_valuation", "account:1@none", "week")
    due.unsnooze(s, CTX, row.id)


def _save_sample_run(s: Session, ids: dict[str, int]) -> int:
    """Complete the Assumptions a projection needs, then save a run of Likely."""
    from sqlalchemy import select as _select

    from finance360.capabilities.projection import runs
    from finance360.capabilities.projection.seed import seed_assumptions
    from finance360.core.models.scenario import Scenario

    seed_assumptions(s)
    scenarios.set_assumptions(
        s,
        CTX,
        None,
        [
            scenarios.Change("annual_saving", "household", "", "0"),
            scenarios.Change("retirement_age", "member", str(ids["alex"]), "60"),
            scenarios.Change("retirement_age", "member", str(ids["jordan"]), "62"),
        ],
    )
    scenarios.set_spending_phases(s, CTX, None, [("last_retirement", 0, "70000")])
    likely = s.scalars(_select(Scenario).where(Scenario.key == "likely")).one()
    return runs.save_run(s, CTX, likely.id).id


def _rerun_sample_run(s: Session, ids: dict[str, int]) -> None:
    from finance360.capabilities.projection import engine, runs

    run_id = _save_sample_run(s, ids)
    engine.ENGINE_VERSION += 1
    try:
        runs.rerun_with_current_engine(s, CTX, run_id)
    finally:
        engine.ENGINE_VERSION -= 1


def _tax_input(ids: dict[str, int], description: str = "Sample tools") -> tax.RecordInput:
    return tax.RecordInput(
        member_id=ids["alex"],
        record_type="line",
        from_on=date(2026, 1, 10),
        description=description,
        label="work_other",
        amount="200.00",
    )


def _tax_whatif(s: Session, ids: dict[str, int]) -> int:
    return tax_saved.create_whatif(
        s,
        CTX,
        tax_saved.WhatIfInput(
            "Sample what-if",
            2026,
            [tax_saved.OverrideInput("set", member_id=ids["alex"], label="salary", amount="1.00")],
        ),
    ).id


def _save_tax_estimate(s: Session, ids: dict[str, int]) -> int:
    return tax_saved.save_estimate(
        s,
        CTX,
        financial_year=2026,
        dependants=0,
        member_ids=[ids["alex"], ids["jordan"]],
        whatif_id=_tax_whatif(s, ids),
    ).id


def _rerun_tax_estimate(s: Session, ids: dict[str, int]) -> None:
    from finance360.capabilities.tax import engine as tax_engine

    estimate_id = _save_tax_estimate(s, ids)
    tax_engine.TAX_ENGINE_VERSION += 1
    try:
        tax_saved.rerun_with_current_engine(s, CTX, estimate_id)
    finally:
        tax_engine.TAX_ENGINE_VERSION -= 1


def _load_tax_whatifs(s: Session, env: Env) -> None:
    shutil.copy(FIXTURE.parent / "tax_whatifs.json", env.data_dir / "tax_whatifs.json")
    tax.load_fixture_tax_records(s, env.data_dir)
    tax_saved.load_fixture_tax_whatifs(s, env.data_dir)


def _load_lodged_results(s: Session, env: Env) -> None:
    shutil.copy(FIXTURE.parent / "lodged_results.json", env.data_dir / "lodged_results.json")
    tax_lodged.load_fixture_lodged_results(s, env.data_dir)


def _lodged_input(ids: dict[str, int], assessed: str, taxable: str) -> tax_lodged.LodgedInput:
    return tax_lodged.LodgedInput(
        ids["alex"], 2026, date.fromisoformat(assessed), {"taxable_income": taxable}
    )


def _lodged_result(s: Session, ids: dict[str, int]) -> int:
    current = tax_lodged.current_lodged_result(s, ids["alex"], 2026)
    if current is not None:
        return current.id
    return tax_lodged.create_lodged_result(s, CTX, _lodged_input(ids, "2026-08-14", "50000.00")).id


def _tax_record(s: Session, ids: dict[str, int]) -> int:
    return tax.create_record(s, CTX, _tax_input(ids)).row.id


def _add_document(s: Session, env: Env) -> int:
    return library.add_document(
        s,
        CTX,
        env.data_dir,
        file_name="sample_receipt.txt",
        media_type="text/plain",
        content=b"Sample receipt",
    ).document.id


def _link_document(s: Session, env: Env, ids: dict[str, int]) -> tuple[int, int]:
    record_id = _tax_record(s, ids)
    document_id = _add_document(s, env)
    library.link(s, CTX, document_id, tax_link_kind.KEY, record_id)
    return record_id, document_id


def _unlink_document(s: Session, env: Env, ids: dict[str, int]) -> None:
    link = library.link(s, CTX, _add_document(s, env), tax_link_kind.KEY, _tax_record(s, ids))
    library.unlink(s, CTX, link.id)


def _delete_document(s: Session, env: Env) -> None:
    library.delete_document(s, CTX, env.data_dir, _add_document(s, env))


def _load_library_documents(s: Session, env: Env) -> None:
    shutil.copytree(FIXTURE.parent / "documents", env.data_dir / "documents")
    library.load_fixture_documents(s, env.data_dir)


PROFILE_DRAFT = {
    "key": "audit-guard-profile",
    "version": 1,
    "title": "Audit guard profile",
    "target": "tax.income_statement",
    "claim": ["Invented heading"],
    "fields": [{"key": "gross_payments", "type": "money", "labels": ["Total"], "pair": "same_row"}],
}


def _store_propose(s: Session) -> Any:
    """A Proposed profile written straight through the store (no Library document needed)."""
    register_income_statement_target()
    profile = document_store.validate_json(PROFILE_DRAFT)
    return document_store.propose(
        s,
        CTX,
        profile=profile,
        raw=dict(PROFILE_DRAFT),
        source="pasted",
        document_id=1,
        document_sha256="0" * 64,
        reading=Reading(OK, profile.key, 1, (), (), ()),
    )


def _propose_profile(s: Session, env: Env) -> int:
    """The sample ATO statement added to the Library and a profile tested on it and saved."""
    register_income_statement_target()
    content = (FIXTURE.parent / "documents" / "ato_income_statement_sample.pdf").read_bytes()
    added = library.add_document(
        s, CTX, env.data_dir, file_name="sample.pdf", media_type="application/pdf", content=content
    )
    draft = json.loads(
        (FIXTURE.parent / "documents" / "ato_income_statement_variant_profile.json").read_text(
            encoding="utf-8"
        )
    )
    row, _ = library_profiles.propose_profile(
        s, CTX, env.data_dir, json.dumps(draft), added.document.id
    )
    return row.id


def _load_document_profiles(s: Session, env: Env) -> None:
    register_income_statement_target()
    shutil.copytree(FIXTURE.parent / "documents", env.data_dir / "documents")
    library_profiles.load_fixture_document_profiles(s, env.data_dir)


def _load_tax_evidence(s: Session, env: Env) -> None:
    shutil.copytree(FIXTURE.parent / "evidence", env.data_dir / "evidence")
    tax.load_fixture_tax_records(s, env.data_dir)
    tax.load_fixture_tax_evidence(s, env.data_dir)


#: Public `session` functions that deliberately write no audit record, with the reason.
NOT_AUDITED: dict[str, str] = {
    # reads
    "household.service.find_household": "read",
    "household.service.get_household": "read",
    "household.service.household_today": "read",
    "household.service.list_members": "read",
    "household.service.effective_facts": "read (resolves the facts and their defaults)",
    "household.service.open_property_accounts": "read",
    "due.service.whats_due": "read (evaluates the Due rules; nothing is stored)",
    "due.service.current_level": "read",
    "due.service.due_today": "read",
    "due.service.important_count": "read",
    "due.service.get_snooze": "read",
    "household.service.get_member": "read",
    "position.accounts.get_account": "read",
    "position.accounts.list_accounts": "read",
    "position.accounts.list_tags": "read",
    "position.accounts.member_delete_guard": "read (a guard that refuses a delete)",
    "position.valuations.get_valuation": "read",
    "position.super_statements.check_super_statement": "read (runs the checks; nothing is stored)",
    "position.super_statements.current_super_statement": "read",
    "position.super_statements.current_valuation": "read",
    "position.super_statements.get_super_statement": "read",
    "position.super_statements.list_super_statements": "read",
    "position.super_statements.list_super_years": "read",
    "position.valuations.current_valuations": "read",
    "position.valuations.list_valuations": "read",
    "position.valuations.has_current_after": "read",
    "position.valuations.best_valuation": "read",
    "position.valuations.has_valuations": "read",
    "reference.fx_rates.get_fx_rate": "read",
    "reference.fx_rates.current_fx_rates": "read",
    "reference.fx_rates.list_fx_rates": "read",
    "reference.fx_rates.rate_for": "read",
    "reference.fx_rates.convert_on": "read",
    "reference.instruments.get_instrument": "read",
    "reference.instruments.list_instruments": "read",
    "reference.instruments.resolve": "read (the identifier crosswalk lookup)",
    "reference.prices.get_price": "read",
    "reference.prices.current_prices": "read",
    "reference.prices.list_prices": "read",
    "reference.prices.price_for": "read",
    "position.derived.account_view": "read (derived value, lines and reconciliation, on read)",
    "position.derived.derivers": "read (loads derive inputs for holdings-based Accounts)",
    "position.service.position_on": "read",
    "position.service.load_inputs": "read (loads Position inputs once for history)",
    "position.service.position_history": "read (Position at many dates)",
    "position.catalogue.get_catalogue": "read",
    "core.audit_query.list_audit": "read (the audit trail's own read side)",
    "core.audit_query.list_auth_events": "read (the auth trail's read side)",
    "core.catalogue.check_behaviour_keys": "read",
    # system operations recorded in backup_run, not audit_event (ADR-0011): they change no
    # household data, so the audit trail does not describe them
    "core.backup.claim_in": "system: takes the backup lock by inserting a backup_run row",
    "core.backup.fail_interrupted": "system: closes backup_run rows a restart left open",
    "core.backup.due_reason": "read (decides whether a scheduled backup is due)",
    "core.backup.maintenance_due": "read (decides whether maintenance is due)",
    "core.backup.status": "read (reports backup_run history)",
    # imports: reads (staging and commit are in MUTATORS)
    "imports.account_identifiers.find_accounts": "read",
    "imports.account_identifiers.list_identifiers": "read",
    "imports.account_identifiers.reveal": "read",
    "imports.commit.committed_summary": "read",
    "imports.commit.preview": "read",
    "reference.prices.record_import_price": "audited by imports.commit.commit's one event (counts)",
    "imports.service.check_committable": "read",
    "imports.service.detect": "read",
    "imports.service.existing_for": "read",
    "imports.service.get_import": "read",
    "imports.service.list_imports": "read",
    "imports.service.list_rows": "read",
    "imports.service.row_count": "read",
    "imports.service.summarise": "read",
    "imports.service.unresolved_references": "read",
    "imports.service.unmapped_types": "read",
    "imports.opening.current_for": "read",
    "imports.opening.coverage_conflicts": "read",
    "imports.transactions.holdings": "read",
    "imports.transactions.current_counts": "read",
    "imports.transactions.list_for_account": "read",
    "imports.transactions.list_removed": "read",
    "core.rulepack_store.effective_pack": "read (the shipped pack with the captures over it)",
    "core.rulepack_store.unfit": "read (active captures that no longer fit the pack)",
    "core.rulepack_store.layered": "read (the merge: what each capture did to the shipped pack)",
    "core.rulepack_store.figures_for_year": "read (one year's figures with their state)",
    "core.rulepack_store.capture_years": "read (the years a figure may be captured for)",
    "core.rulepack_store.carry_preview": "read (what Start from <FY> would write; writes nothing)",
    "core.transaction.cash_balance": "read",
    "core.transaction.holdings_as_at": "read",
    "core.transaction.current": "read",
    "core.transaction.units_held": "read",
    # provider fetch runs are recorded in fetch_run (like backup_run), not audit_event; what a run
    # writes (Prices, FX rates) is audited by store_prices / store_fx_rates
    "reference.fetch.claim_in": "system: takes the fetch lock by inserting a fetch_run row",
    "reference.fetch.fail_interrupted": "system: closes fetch_run rows a restart left open",
    "reference.fetch.get_run": "read",
    "reference.fetch.list_runs": "read",
    "reference.fetch.dataset_state": "read",
    "reference.fetch.is_due": "read (decides whether a start-up catch-up is due)",
    "reference.fetch.price_targets": "read (which Instruments to fetch, and from when)",
    "reference.fetch.fx_currencies": "read (the foreign currencies in use)",
    "reference.fetch.stale_instruments": "read",
    "reference.fetch.instruments_without_price": "read (cue to offer Fetch now after an Import)",
    "reference.trade_dates.earliest_trade_dates": "read",
    "core.transaction.earliest_trade_dates": "read (earliest current trade date per Instrument)",
    "core.transaction.stream_coverage": "read (latest Coverage per Stream of committed Imports)",
    # projection: reads (Scenario and Assumption writes are in MUTATORS)
    "projection.scenarios.list_scenarios": "read",
    "projection.scenarios.get_scenario": "read",
    "projection.scenarios.layer_rows": "read",
    "projection.scenarios.phase_rows": "read",
    "projection.scenarios.layer_phases": "read",
    "projection.scenarios.scenarios_with_own_spending": "read",
    "projection.scenarios.override_counts": "read",
    "projection.scenarios.overriding_scenarios": "read",
    "projection.scenarios.resolve_for": "read",
    "projection.income_streams.get_stream": "read",
    "projection.income_streams.list_streams": "read",
    "projection.income_streams.projection_streams": "read (streams as the projection uses them)",
    "projection.income_streams.salary_member_ids": "read",
    "projection.income_streams.member_delete_guard": "read (a guard that refuses a delete)",
    "projection.runs.get_run": "read",
    "projection.runs.list_runs": "read",
    "projection.service.build_input": "read",
    "projection.service.run": "read",
    "projection.service.run_scenario": "read",
    "projection.service.compare_scenarios": "read",
    "tax.service.get_record": "read",
    "tax.service.setup": "read (the years the effective Rule pack can estimate)",
    "tax.service.list_records": "read",
    "tax.service.previous_year": "read",
    "tax.service.label_totals": "read",
    "tax.service.uncounted_records": "read",
    "tax.service.build": "read (calculates an estimate's input and result; nothing is stored)",
    "tax.service.check_overrides": "read (checks a Tax what-if's overrides; nothing is stored)",
    "tax.lodged.get_lodged_result": "read",
    "tax.lodged.history": "read",
    "tax.lodged.current_lodged_result": "read",
    "tax.lodged.list_current": "read",
    "tax.lodged.worksheet": "read (calculates the estimate now; nothing is stored)",
    "tax.lodged.member_delete_guard": "read (a guard that refuses a delete)",
    "tax.saved.list_whatifs": "read",
    "tax.saved.get_whatif": "read",
    "tax.saved.calculate": "read (calculates now; nothing is stored)",
    "tax.saved.break_even": "read (searches for an amount now; nothing is stored)",
    "tax.saved.list_estimates": "read",
    "tax.saved.get_estimate": "read",
    "tax.saved.compare_estimates": "read",
    "library.service.get_document": "read",
    "library.service.list_documents": "read",
    "library.service.link_counts": "read",
    "library.service.describe_links": "read",
    "library.service.links_of_document": "read",
    "library.service.documents_of": "read",
    "library.service.get_link": "read",
    "library.service.find_link": "read",
    "library.service.document_file": "read",
    "library.service.check": "read (reports a missing or changed file, repairs nothing)",
    "library.service.remove_links_to": (
        "audited by the removing service (tax.service.delete_record names the documents)"
    ),
    "library.service.adopt_files": (
        "system: moves files from documents/tax/ to documents/library/ at start-up (verified, "
        "idempotent); no household data changes"
    ),
    "library.profiles.document_by_sha256": "read",
    "library.profiles.review_profile": "read (re-runs a profile over its document; stores nothing)",
    "library.profiles.test_profile_on_document": "read (a test run; stores nothing)",
    "documents.store.get": "read",
    "documents.store.list_rows": "read",
    "documents.store.active_row": "read",
    "documents.store.load_active": (
        "system: registers active saved profiles in the running process at start-up; "
        "no household data changes"
    ),
    "tax.service.member_delete_guard": "read (a guard that refuses a delete)",
    "tax.service.estimate": "read",
    "tax.service.income_reconciliation": "read (computed from the records, nothing stored)",
    # system bootstrap, ADR-0009 section 5: not user changes
    "projection.seed.seed_assumptions": "system bootstrap: seeds the shipped Best/Likely/Worst",
    "household.service.ensure_household": "system bootstrap: creates the single Household row",
    "core.catalogue.seed_catalogue": "system bootstrap: seeds the shipped catalogue",
}


def _short(module: ModuleType, name: str) -> str:
    return f"{module.__name__.removeprefix('finance360.capabilities.')}.{name}".removeprefix(
        "finance360."
    )


def _discovered() -> dict[str, Callable[..., object]]:
    found: dict[str, Callable[..., object]] = {}
    for module in (m for package in SCANNED for m in _modules_of(package)):
        for name, fn in _public_session_functions(module).items():
            found[_short(module, name)] = fn
    found.pop("core.audit.record", None)  # the writer itself
    return found


def test_every_service_function_is_classified() -> None:
    found = _discovered()
    classified = {*MUTATORS, *LOADERS, *NOT_AUDITED}
    unclassified = sorted(set(found) - classified)
    assert not unclassified, (
        f"New service functions with no audit coverage: {unclassified}. A function that changes "
        "data takes an AuditContext, calls audit.record and gets an exerciser in MUTATORS (or "
        "LOADERS); a read goes in NOT_AUDITED with the reason."
    )
    assert not sorted(classified - set(found)), "Stale entries for functions that no longer exist"
    # Taking an AuditContext makes a function a mutator, whatever it is called.
    hidden = sorted(n for n in NOT_AUDITED if _takes_audit_context(found[n]))
    assert not hidden, f"{hidden} take an AuditContext, so they must be in MUTATORS"
    untested = sorted(
        n for n, fn in found.items() if _takes_audit_context(fn) and n not in MUTATORS
    )
    assert not untested, f"Mutating functions without an exerciser: {untested}"


def _last_audit_id(factory: sessionmaker[Session]) -> int:
    with factory() as s:
        return s.scalar(select(func.max(AuditEvent.id))) or 0


def _written_since(factory: sessionmaker[Session], last_id: int) -> set[tuple[str, str]]:
    with factory() as s:
        rows = s.execute(
            select(AuditEvent.entity_type, AuditEvent.action).where(AuditEvent.id > last_id)
        )
        return {(t, a) for t, a in rows}


def _exercise(name: str, factory: sessionmaker[Session], data_dir: Path) -> None:
    try:
        _exercise_one(name, factory, data_dir)
    finally:
        document_registry.clear_saved()  # an activation registers in the process-wide registry


def _exercise_one(name: str, factory: sessionmaker[Session], data_dir: Path) -> None:
    env = Env(factory, data_dir)
    if name in LOADERS:
        _with_fixture(env)
        if name != "household.service.bootstrap":
            with factory.begin() as s:  # the loaders need what bootstrap and its siblings create
                household.bootstrap(s, data_dir)
                seed_catalogue(s)
                if (
                    "valuations" in name
                    or "account_identifiers" in name
                    or "super_statements" in name
                    or "load_fixture_facts" in name
                ):
                    accounts.load_fixture_accounts(s, data_dir)
                if "prices" in name:
                    instruments.load_fixture_instruments(s, data_dir)
        before = _last_audit_id(factory)
        with factory.begin() as s:
            LOADERS[name](s, env, {})
    else:
        needs, run = MUTATORS[name]
        with factory.begin() as s:
            ids = env.seed(
                s,
                accounts_=bool(needs.get("accounts", False)),
                rates=bool(needs.get("rates", False)),
                instruments_=bool(needs.get("instruments", False)),
                prices_=bool(needs.get("prices", False)),
                super_account=bool(needs.get("super_account", False)),
                super_statement=bool(needs.get("super_statement", False)),
                import_file=str(needs["import_file"]) if "import_file" in needs else None,
            )
        before = _last_audit_id(factory)
        with factory.begin() as s:
            run(s, env, ids)
    expected = EXPECTED[name]
    written = _written_since(factory, before)
    assert expected in written, (
        f"{name} wrote no audit record {expected} (it wrote {sorted(written) or 'nothing'})"
    )


def test_every_mutator_declares_what_it_writes() -> None:
    assert set(EXPECTED) == {*MUTATORS, *LOADERS}


@pytest.mark.parametrize("name", [*MUTATORS, *LOADERS])
def test_every_mutator_writes_an_audit_event(
    name: str, session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    _exercise(name, session_factory, tmp_path)


def test_the_guard_detects_a_skipped_writer(
    session_factory: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the writer switched off, the real exerciser must fail: that is the guard working."""
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    with pytest.raises(AssertionError, match="wrote no audit record"):
        _exercise("household.service.update_member", session_factory, tmp_path)


def test_a_mutator_cannot_hide_behind_another_that_audits(
    session_factory: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """close_account's own record is switched off but its closing Valuation is still audited:
    the guard must still fail, because it looks for ('account', 'close') specifically."""
    real = audit.record

    def skip_account_close(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("entity_type") == "account" and kwargs.get("action") == "close":
            return None
        return real(*args, **kwargs)

    monkeypatch.setattr(audit, "record", skip_account_close)
    with pytest.raises(AssertionError, match=r"wrote no audit record \('account', 'close'\)"):
        _exercise("position.accounts.close_account", session_factory, tmp_path)


# --- auth: its own writer, auth_event ------------------------------------------------------

AUTH_MODULES: tuple[ModuleType, ...] = (logins, auth_service)

#: function -> the auth_event kind(s) it must write when exercised.
AUTH_MUTATORS = {
    "logins.create_login": "setup",
    "logins.seed_owner": "setup",
    "logins.record_failure": "login_failed",
    "logins.confirm_password": "login_failed",
    "logins.change_password": "password_changed",
    "logins.reset_password": "password_reset",
    "logins.totp_begin": "totp_begin",
    "logins.totp_confirm": "totp_confirmed",
    "logins.totp_disable": "totp_disabled",
    "service.sign_in": "login",
    "service.logout": "logout",
}
AUTH_NOT_AUDITED = {
    "logins.get_login": "read",
    "logins.get_by_id": "read",
    "logins.setup_required": "read",
    "logins.charge_attempt": "counts an attempt; the failure is audited by record_failure",
    "service.login": "delegates to sign_in; a failed login is written by the provider",
}


def _auth_discovered() -> set[str]:
    return {
        f"{m.__name__.rsplit('.', 1)[1]}.{n}"
        for m in AUTH_MODULES
        for n in _public_session_functions(m)
    }


def test_every_auth_function_is_classified() -> None:
    unclassified = sorted(_auth_discovered() - {*AUTH_MUTATORS, *AUTH_NOT_AUDITED})
    assert not unclassified, (
        f"New auth functions with no coverage: {unclassified}. A mutating one must write an "
        "auth_event (record_event) and get an entry in AUTH_MUTATORS; a read goes in "
        "AUTH_NOT_AUDITED."
    )
    assert not sorted({*AUTH_MUTATORS, *AUTH_NOT_AUDITED} - _auth_discovered())


def _code(secret: str, moment: datetime) -> str:
    """The valid authenticator code for ``moment``, generated the way an app would."""
    return pyotp.TOTP(secret).at(totp.step_of(moment) * totp.STEP_SECONDS)


def test_a_wrong_totp_code_is_recorded_as_a_failed_sign_in(
    session_factory: sessionmaker[Session], fast_hash: None
) -> None:
    with session_factory.begin() as s:
        login = logins.create_login(s, "a-long-sample-passphrase", now=NOW, event=None)
        logins.totp_begin(s, login, NOW)
        assert not logins.totp_confirm(s, login, "000000", NOW)
    assert "login_failed" in _auth_kinds(session_factory)


def _auth_kinds(factory: sessionmaker[Session]) -> list[str]:
    with factory() as s:
        return list(s.scalars(select(AuthEvent.kind).order_by(AuthEvent.id)))


@pytest.mark.parametrize("name", list(AUTH_MUTATORS))
def test_every_auth_mutator_writes_an_auth_event(
    name: str, session_factory: sessionmaker[Session], fast_hash: None
) -> None:
    password = "a-long-sample-passphrase"
    with session_factory.begin() as s:
        if name != "logins.create_login" and name != "logins.seed_owner":
            logins.create_login(s, password, now=NOW, event=None)
    with session_factory.begin() as s:
        before = len(_auth_kinds(session_factory))
        login = logins.get_login(s)
        attempt = AuthAttempt({}, "127.0.0.1", {}, s, NOW)
        match name:
            case "logins.create_login":
                logins.create_login(s, password, now=NOW)
            case "logins.seed_owner":
                logins.seed_owner(s, password, now=NOW)
            case "logins.record_failure":
                assert login
                logins.record_failure(s, login, NOW, 0)
            case "logins.confirm_password":
                assert login
                logins.confirm_password(s, login, "wrong-sample-passphrase", NOW)
            case "logins.change_password":
                assert login
                logins.change_password(
                    s, login, "another-long-sample-passphrase", now=NOW, keep_token=None
                )
            case "logins.reset_password":
                assert login
                logins.reset_password(
                    s, login, "another-long-sample-passphrase", now=NOW, detail={}
                )
            case "logins.totp_begin":
                assert login
                logins.totp_begin(s, login, NOW)
            case "logins.totp_confirm":
                assert login
                secret, _ = logins.totp_begin(s, login, NOW)
                assert logins.totp_confirm(s, login, _code(secret, NOW), NOW)
            case "logins.totp_disable":
                assert login
                secret, _ = logins.totp_begin(s, login, NOW)
                assert logins.totp_confirm(s, login, _code(secret, NOW), NOW)
                later = NOW + timedelta(seconds=90)  # a later step: the confirm code is spent
                assert logins.totp_disable(s, login, password, _code(secret, later), later)
            case "service.sign_in":
                assert login
                auth_service.sign_in(
                    s, Principal(login.id, Role.OWNER), attempt, trust_device=False
                )
            case "service.logout":
                assert login
                token = auth_service.sessions.issue(s, login.id, trusted=False, now=NOW)
                auth_service.logout(s, token, Principal(login.id, Role.OWNER), NOW)
            case _:
                raise AssertionError(f"No exerciser for {name}")
    kinds = _auth_kinds(session_factory)
    assert AUTH_MUTATORS[name] in kinds[before:], f"{name} wrote no {AUTH_MUTATORS[name]} event"
