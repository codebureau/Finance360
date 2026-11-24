"""The Investment option mapping (ADR-0030 section 4, #86).

A super Investment option is mapped, per Account, to an Instrument (kind ``fund``), which holds the
option's Exposure profile like any other Instrument. The mapping is edited in place and audited
(entity ``investment_option_link``; the trail is its history). Super statement option lines resolve
through it by the normalised name, so a later statement with the same name needs no new mapping.
Every mutation writes its audit record in the caller's session (the caller owns the transaction).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance360.capabilities.position import accounts
from finance360.capabilities.position.super_statements import NAME_MAX, SUPER_ACCOUNT_TYPE
from finance360.capabilities.reference import instruments
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, ValidationError
from finance360.core.exposure import normalise_option_name
from finance360.core.models.account import Account
from finance360.core.models.exposure import InvestmentOptionLink
from finance360.core.models.instrument import Instrument

#: Same file as ``household.FIXTURE_FILENAME``.
FIXTURE_FILENAME = "household.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


def links_for_account(session: Session, account_id: int) -> dict[str, InvestmentOptionLink]:
    """The Account's mappings, keyed by normalised option name."""
    return {
        link.option_name_norm: link
        for link in session.scalars(
            select(InvestmentOptionLink).where(InvestmentOptionLink.account_id == account_id)
        )
    }


def _snapshot(link: InvestmentOptionLink, instrument: Instrument) -> dict[str, Any]:
    return {
        "id": link.id,
        "account_id": link.account_id,
        "option_name": link.option_name,
        "instrument_id": instrument.id,
        "instrument_name": instrument.name,
    }


def _tidy(name: str) -> str:
    errors: list[FieldError] = []
    tidy = " ".join(name.split())
    if not tidy:
        errors.append(FieldError("option_name", "Enter the Investment option's name."))
    elif len(tidy) > NAME_MAX:
        errors.append(FieldError("option_name", f"At most {NAME_MAX} characters."))
    if errors:
        raise ValidationError(errors)
    return tidy


def map_option(
    session: Session,
    context: AuditContext,
    account_id: int,
    option_name: str,
    *,
    instrument_id: int | None = None,
    create_instrument: bool = False,
) -> InvestmentOptionLink:
    """Map the Account's Investment option to an existing Instrument (``instrument_id``) or to a
    new ``fund`` Instrument created from the option's name, in the Account's currency
    (``create_instrument``). An existing mapping for the same normalised name is re-pointed in
    place."""
    account: Account = accounts.get_account(session, account_id)
    if account.account_type.key != SUPER_ACCOUNT_TYPE:
        raise ValidationError(
            [FieldError("account_id", "Investment options belong to a super fund Account.")]
        )
    name = _tidy(option_name)
    if (instrument_id is None) == (not create_instrument):
        raise ValidationError(
            [FieldError("instrument_id", "Pick a fund, or create one from the option's name.")]
        )
    if instrument_id is not None:
        instrument = instruments.get_instrument(session, instrument_id)
    else:
        titled = f"{name} ({account.name})"
        instrument = instruments.create_instrument(
            session,
            context,
            instruments.InstrumentInput(
                titled if len(titled) <= instruments.NAME_MAX else name,
                "fund",
                account.currency,
                notes=f"Created from the {name} Investment option of {account.name}.",
            ),
        )
    norm = normalise_option_name(name)
    existing = links_for_account(session, account_id).get(norm)
    if existing is not None:
        if existing.instrument_id == instrument.id and existing.option_name == name:
            return existing
        before = _snapshot(existing, session.get_one(Instrument, existing.instrument_id))
        existing.option_name, existing.instrument_id = name, instrument.id
        session.flush()
        audit.record(
            session,
            context,
            entity_type="investment_option_link",
            entity_id=existing.id,
            action="update",
            before=before,
            after=_snapshot(existing, instrument),
            summary=f"Mapped {name} on {account.name} to {instrument.name}",
        )
        return existing
    link = InvestmentOptionLink(
        account_id=account_id,
        option_name=name,
        option_name_norm=norm,
        instrument_id=instrument.id,
    )
    session.add(link)
    try:
        session.flush()
    except IntegrityError:  # a concurrent writer mapped the same name first
        raise ValidationError(
            [FieldError("option_name", "This Investment option is already mapped.")]
        ) from None
    audit.record(
        session,
        context,
        entity_type="investment_option_link",
        entity_id=link.id,
        action="create",
        after=_snapshot(link, instrument),
        summary=f"Mapped {name} on {account.name} to {instrument.name}",
    )
    return link


def load_fixture_option_links(session: Session, data_dir: Path) -> int:
    """Map the fixture's Investment options through the service, once, while there are none.

    Reads ``investment_option_links`` from ``<data_dir>/household.json``: rows of ``account``,
    ``option`` and ``instrument`` (names; the Instrument is the fixture's own).
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(func.count()).select_from(InvestmentOptionLink)):
        return 0
    rows = json.loads(path.read_text(encoding="utf-8")).get("investment_option_links", [])
    account_ids = {a.name: a.id for a in session.scalars(select(Account))}
    instrument_ids = {i.name: i.id for i in session.scalars(select(Instrument))}
    created = 0
    for row in rows:
        if row["account"] not in account_ids or row["instrument"] not in instrument_ids:
            continue
        map_option(
            session,
            FIXTURE_CONTEXT,
            account_ids[row["account"]],
            row["option"],
            instrument_id=instrument_ids[row["instrument"]],
        )
        created += 1
    return created
