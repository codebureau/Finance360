"""Account, ownership and Tag services (ADR-0009 sections 2 and 7).

Mutating functions take the caller's ``Session`` and an explicit ``AuditContext`` and write the
audit record in that same session, so the change and its record commit or roll back together.
They flush but never commit: the caller (the API request, the startup bootstrap) owns the
transaction.

Rules enforced here because SQLite cannot check them across rows: ownership shares are integer
basis points summing to exactly 10000; the Account type belongs to the Account's Category.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.household import service as household
from finance360.capabilities.position import valuations
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.models.account import (
    FULL_SHARE_BP,
    OWNERSHIP_MODES,
    Account,
    AccountOwner,
    AccountTag,
    Tag,
)
from finance360.core.models.catalogue import AccountType, Category
from finance360.core.models.household import Member
from finance360.core.money import (
    BASE_CURRENCY,
    Money,
    MoneyError,
    allocate,
    exponent,
    format_amount,
)
from finance360.core.valuation import NoValuation

NOTES_MAX = 2000
TAG_MAX = 50
TAGS_MAX = 20


class CloseNeedsConfirmationError(Exception):
    """The latest Valuation is non-zero and the caller has not confirmed closing (ADR-0009)."""


@dataclass(frozen=True)
class OwnerInput:
    member_id: int
    #: Basis points. ``None`` for every owner of a joint Account means "share evenly".
    share_bp: int | None = None


@dataclass(frozen=True)
class AccountInput:
    name: str
    category_key: str
    account_type_key: str
    ownership: str = "sole"
    owners: Sequence[OwnerInput] = ()
    currency: str = BASE_CURRENCY
    notes: str | None = None
    opened_on: date | None = None
    tags: Sequence[str] = field(default_factory=tuple)


# --- pure rules ----------------------------------------------------------------------------


def format_percent(bp: int) -> str:
    """9999 -> '99.99%', 6000 -> '60%'. Integer arithmetic only."""
    whole, frac = divmod(bp, 100)
    return f"{whole}.{frac:02d}".rstrip("0").rstrip(".") + "%"


def even_shares(member_ids: Sequence[int]) -> dict[int, int]:
    """Share 100% evenly in basis points; the leftover units go to the lowest ids (ADR-0009)."""
    parts = allocate(Money(FULL_SHARE_BP, BASE_CURRENCY), dict.fromkeys(member_ids, 1))
    return {member_id: money.amount_minor for member_id, money in parts.items()}


def resolve_shares(
    ownership: str, owners: Sequence[OwnerInput]
) -> tuple[dict[int, int], list[FieldError]]:
    """Validate the Ownership and return ``{member_id: share_bp}`` (summing to 10000) or errors.

    Pure: member existence is checked by the caller. Sole is exactly one Member at 10000. Joint is
    at least two distinct Members, each above 0, defaulting to even shares when no share is given.
    """

    def bad(message: str) -> tuple[dict[int, int], list[FieldError]]:
        return {}, [FieldError("owners", message)]

    if ownership not in OWNERSHIP_MODES:
        return {}, [FieldError("ownership", "Ownership must be sole or joint.")]
    ids = [o.member_id for o in owners]
    if len(set(ids)) != len(ids):
        return bad("Each Member can appear only once.")
    if ownership == "sole":
        if len(owners) != 1:
            return bad("A sole Account has exactly one Member.")
        if owners[0].share_bp not in (None, FULL_SHARE_BP):
            return bad("A sole Member holds 100%.")
        return {ids[0]: FULL_SHARE_BP}, []
    if len(owners) < 2:
        return bad("A joint Account needs at least two Members.")
    given = [o.share_bp for o in owners]
    if all(s is None for s in given):
        return even_shares(ids), []
    if any(s is None for s in given):
        return bad("Enter a share for every Member.")
    shares = {o.member_id: int(o.share_bp or 0) for o in owners}
    if any(s <= 0 for s in shares.values()):
        return bad("Each Member's share must be above 0%.")
    total = sum(shares.values())
    if total != FULL_SHARE_BP:
        return bad(f"Shares must add up to 100% (they add up to {format_percent(total)}).")
    return shares, []


def normalise_tags(raw: Sequence[str]) -> tuple[list[str], list[FieldError]]:
    """Trim, collapse spaces, drop blanks and case-insensitive duplicates (first spelling wins)."""
    seen: dict[str, str] = {}
    errors: list[FieldError] = []
    for tag in raw:
        clean = re.sub(r"\s+", " ", tag).strip()
        if not clean:
            continue
        if len(clean) > TAG_MAX:
            errors.append(FieldError("tags", f"A Tag must be at most {TAG_MAX} characters."))
        seen.setdefault(clean.casefold(), clean)
    tags = list(seen.values())
    if len(tags) > TAGS_MAX:
        errors.append(FieldError("tags", f"An Account can have at most {TAGS_MAX} Tags."))
    return tags, errors


# --- reads ---------------------------------------------------------------------------------


def get_account(session: Session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise NotFoundError(f"Account {account_id} does not exist.")
    return account


def list_accounts(session: Session, *, include_closed: bool = False) -> list[Account]:
    query = select(Account).order_by(Account.category_id, Account.name, Account.id)
    if not include_closed:
        query = query.where(Account.closed_on.is_(None))
    return list(session.scalars(query))


def list_tags(session: Session) -> list[str]:
    """Every Tag in use, for autocomplete."""
    return sorted(session.scalars(select(Tag.name)), key=str.casefold)


# --- audit snapshots -----------------------------------------------------------------------


def _snapshot(account: Account) -> dict[str, Any]:
    return {
        "name": account.name,
        "category": account.category.key,
        "account_type": account.account_type.key,
        "currency": account.currency,
        "notes": account.notes,
        "opened_on": account.opened_on.isoformat() if account.opened_on else None,
        "closed_on": account.closed_on.isoformat() if account.closed_on else None,
        "ownership": account.ownership,
        "owners": [
            {"member_id": o.member_id, "share_bp": o.share_bp}
            for o in sorted(account.owners, key=lambda o: o.member_id)
        ],
        "tags": sorted(t.name for t in account.tags),
    }


# --- validation against the database -------------------------------------------------------


@dataclass(frozen=True)
class _Checked:
    name: str
    category: Category
    account_type: AccountType
    currency: str
    notes: str | None
    opened_on: date | None
    shares: dict[int, int]
    tags: list[str]


def _check(session: Session, data: AccountInput, closed_on: date | None) -> _Checked:
    errors: list[FieldError] = []
    name, name_errors = household.validate_name(data.name, label="Account name")
    errors += name_errors

    category = session.scalar(select(Category).where(Category.key == data.category_key))
    account_type = session.scalar(
        select(AccountType).where(AccountType.key == data.account_type_key)
    )
    if category is None:
        errors.append(FieldError("category", "Choose a Category."))
    if account_type is None:
        errors.append(FieldError("account_type", "Choose an Account type."))
    elif category is not None and account_type.category_id != category.id:
        errors.append(
            FieldError("account_type", f"That Account type does not belong to {category.name}.")
        )

    currency = data.currency.strip().upper()
    try:
        exponent(currency)
    except MoneyError:
        errors.append(FieldError("currency", "Enter a supported ISO currency code, e.g. AUD."))

    notes = (data.notes or "").strip() or None
    if notes and len(notes) > NOTES_MAX:
        errors.append(FieldError("notes", f"Notes must be at most {NOTES_MAX} characters."))

    if data.opened_on and closed_on and data.opened_on >= closed_on:
        errors.append(FieldError("opened_on", "The Account must open before it closes."))

    shares, share_errors = resolve_shares(data.ownership, data.owners)
    errors += share_errors
    if not share_errors:
        known = set(session.scalars(select(Member.id).where(Member.id.in_(shares))))
        if known != set(shares):
            errors.append(FieldError("owners", "Choose Members of this Household."))

    tags, tag_errors = normalise_tags(data.tags)
    errors += tag_errors

    if errors or category is None or account_type is None:
        raise ValidationError(errors)
    return _Checked(name, category, account_type, currency, notes, data.opened_on, shares, tags)


def _apply_tags(session: Session, account: Account, names: list[str]) -> None:
    tags: list[Tag] = []
    for name in names:
        tag = session.scalar(select(Tag).where(func.lower(Tag.name) == name.casefold()))
        if tag is None:
            tag = Tag(name=name)
            session.add(tag)
        tags.append(tag)
    account.tags = tags
    session.flush()
    _prune_unused_tags(session)


def _prune_unused_tags(session: Session) -> None:
    """A Tag exists only while an Account carries it, so autocomplete never suggests stale ones."""
    used = select(AccountTag.tag_id)
    for tag in session.scalars(select(Tag).where(Tag.id.not_in(used))):
        session.delete(tag)
    session.flush()


def _apply_owners(session: Session, account: Account, shares: dict[int, int]) -> None:
    current = {o.member_id: o for o in account.owners}
    for member_id, owner in current.items():
        if member_id not in shares:
            account.owners.remove(owner)
    for member_id, share in shares.items():
        if member_id in current:
            current[member_id].share_bp = share
        else:
            account.owners.append(AccountOwner(member_id=member_id, share_bp=share))
    session.flush()


def _owner_names(account: Account) -> str:
    return ", ".join(sorted(o.member.name for o in account.owners))


# --- mutations -----------------------------------------------------------------------------


def create_account(session: Session, context: AuditContext, data: AccountInput) -> Account:
    checked = _check(session, data, closed_on=None)
    account = Account(
        name=checked.name,
        category_id=checked.category.id,
        account_type_id=checked.account_type.id,
        currency=checked.currency,
        ownership=data.ownership,
        notes=checked.notes,
        opened_on=checked.opened_on,
    )
    session.add(account)
    session.flush()
    _apply_owners(session, account, checked.shares)
    _apply_tags(session, account, checked.tags)
    session.refresh(account)
    audit.record(
        session,
        context,
        entity_type="account",
        entity_id=account.id,
        action="create",
        after=_snapshot(account),
        summary=f"Created Account '{account.name}' ({_owner_names(account)})",
    )
    return account


def update_account(
    session: Session, context: AuditContext, account_id: int, data: AccountInput
) -> Account:
    account = get_account(session, account_id)
    checked = _check(session, data, closed_on=account.closed_on)
    if checked.currency != account.currency and valuations.has_valuations(session, account.id):
        raise ValidationError(
            [FieldError("currency", "This Account has Valuations, so its currency can't change.")]
        )
    before = _snapshot(account)
    account.name = checked.name
    account.category_id = checked.category.id
    account.account_type_id = checked.account_type.id
    account.currency = checked.currency
    account.ownership = data.ownership
    account.notes = checked.notes
    account.opened_on = checked.opened_on
    session.flush()
    _apply_owners(session, account, checked.shares)
    _apply_tags(session, account, checked.tags)
    session.refresh(account)
    after = _snapshot(account)
    if after != before:
        audit.record(
            session,
            context,
            entity_type="account",
            entity_id=account.id,
            action="update",
            before=before,
            after=after,
            summary=f"Edited Account '{account.name}'",
        )
    return account


def _write_closing_valuation(
    session: Session,
    context: AuditContext,
    account: Account,
    closed_on: date,
    confirm_non_zero: bool,
) -> None:
    """ADR-0009 section 2: closing requires a zero balance.

    If the latest Valuation on or before ``closed_on`` is non-zero, the caller must confirm (a
    balance moved elsewhere, e.g. a super rollover); otherwise raise
    ``CloseNeedsConfirmationError``. Then write the zero Valuation dated ``closed_on`` in this same
    session, so the closure and its zero Valuation commit or roll back together. ``close_account``
    calls it before it sets ``closed_on``.
    """
    latest = valuations.best_valuation(session, account.id, closed_on)
    if (
        not isinstance(latest, NoValuation)
        and latest.amount.amount_minor != 0
        and not (confirm_non_zero)
    ):
        raise CloseNeedsConfirmationError(
            f"The latest Valuation is {latest.amount.currency} {format_amount(latest.amount)} "
            f"at {latest.as_at}, not zero. Closing records a zero Valuation on {closed_on}."
        )
    valuations.write_closing_valuation(session, context, account, closed_on)


def close_account(
    session: Session,
    context: AuditContext,
    account_id: int,
    closed_on: date,
    *,
    confirm_non_zero: bool = False,
) -> Account:
    account = get_account(session, account_id)
    if account.closed_on is not None:
        raise ValidationError([FieldError("closed_on", "This Account is already closed.")])
    if account.opened_on and closed_on <= account.opened_on:
        raise ValidationError(
            [FieldError("closed_on", f"Close date must be after it opened ({account.opened_on}).")]
        )
    if valuations.has_current_after(session, account.id, closed_on):
        raise ValidationError(
            [FieldError("closed_on", "This Account has Valuations after that date.")]
        )
    before = _snapshot(account)
    _write_closing_valuation(session, context, account, closed_on, confirm_non_zero)
    account.closed_on = closed_on
    session.flush()
    audit.record(
        session,
        context,
        entity_type="account",
        entity_id=account.id,
        action="close",
        before=before,
        after=_snapshot(account),
        summary=f"Closed Account '{account.name}' on {closed_on}",
    )
    return account


# --- Member delete guard -------------------------------------------------------------------


def member_delete_guard(session: Session, member_id: int) -> str | None:
    """Block deleting a Member who is an owner of any Account, open or closed (ADR-0009)."""
    owned = session.scalar(
        select(func.count()).select_from(AccountOwner).where(AccountOwner.member_id == member_id)
    )
    return "they hold Ownership of an Account" if owned else None


def register() -> None:
    """Register ``position``'s rules with ``household``. Idempotent; call at app start."""
    household.register_member_delete_guard(member_delete_guard)


# --- First run -----------------------------------------------------------------------------

FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


def load_fixture_accounts(session: Session, data_dir: Path) -> int:
    """Create the fixture's Accounts through the service, once, on a database with none.

    Reads ``accounts`` from ``<data_dir>/household.json`` (the review slot's synthetic household).
    Owners are named by Member name. Returns how many Accounts were created.
    """
    path = data_dir / household.FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(func.count()).select_from(Account)):
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("accounts", [])
    ids = {m.name: m.id for m in household.list_members(session)}
    for entry in entries:
        owners = entry["owners"]
        create_account(
            session,
            FIXTURE_CONTEXT,
            AccountInput(
                name=entry["name"],
                category_key=entry["category"],
                account_type_key=entry["account_type"],
                ownership="joint" if len(owners) > 1 else "sole",
                owners=[OwnerInput(ids[name]) for name in owners],
                currency=entry.get("currency", BASE_CURRENCY),
                notes=entry.get("notes"),
                opened_on=date.fromisoformat(entry["opened_on"]) if "opened_on" in entry else None,
                tags=entry.get("tags", []),
            ),
        )
    return len(entries)
