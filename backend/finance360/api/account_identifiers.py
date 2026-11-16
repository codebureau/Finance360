"""Account identifier endpoints (ADR-0013 section 5a). Thin: every rule lives in
``capabilities.imports.account_identifiers``.

Identifiers are sensitive real values. Every response here carries the **masked** value
(``••• 1234``); the one way to read the real value is the explicit ``reveal`` call. Errors never
echo a value.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from finance360.api.deps import OwnerDep
from finance360.api.household import ERRORS, ContextDep, SessionDep
from finance360.capabilities.imports import account_identifiers as service
from finance360.core.account_identifier import mask_value
from finance360.core.models.account_identifier import AccountIdentifier

router = APIRouter(prefix="/api/accounts/{account_id}/identifiers", tags=["account identifiers"])


class AccountIdentifierOut(BaseModel):
    id: int
    kind: str
    #: Always masked, e.g. ``••• 0001``. Use ``reveal`` for the real value.
    masked_value: str
    institution: str | None


class AccountIdentifierIn(BaseModel):
    kind: str = Field(default="", description="bsb_account | last4 | member_number | ...")
    value: str = ""
    institution: str | None = None


class RevealedOut(BaseModel):
    value: str


def _out(ident: AccountIdentifier) -> AccountIdentifierOut:
    return AccountIdentifierOut(
        id=ident.id,
        kind=ident.kind,
        masked_value=mask_value(ident.value),
        institution=ident.institution,
    )


@router.get(
    "",
    response_model=list[AccountIdentifierOut],
    operation_id="listAccountIdentifiers",
    responses=ERRORS,  # type: ignore[arg-type]
)
def list_identifiers(account_id: int, session: SessionDep) -> list[AccountIdentifierOut]:
    return [_out(i) for i in service.list_identifiers(session, account_id)]


@router.post(
    "",
    status_code=201,
    response_model=AccountIdentifierOut,
    operation_id="addAccountIdentifier",
    responses=ERRORS,  # type: ignore[arg-type]
)
def add_identifier(
    account_id: int, body: AccountIdentifierIn, session: SessionDep, ctx: ContextDep
) -> AccountIdentifierOut:
    data = service.IdentifierInput(body.kind, body.value, body.institution)
    return _out(service.add_identifier(session, ctx, account_id, data))


@router.delete(
    "/{identifier_id}",
    status_code=204,
    operation_id="removeAccountIdentifier",
    responses=ERRORS,  # type: ignore[arg-type]
)
def remove_identifier(
    account_id: int, identifier_id: int, session: SessionDep, ctx: ContextDep
) -> Response:
    service.remove_identifier(session, ctx, account_id, identifier_id)
    return Response(status_code=204)


@router.post(
    "/{identifier_id}/reveal",
    response_model=RevealedOut,
    operation_id="revealAccountIdentifier",
    responses=ERRORS,  # type: ignore[arg-type]
)
def reveal_identifier(
    account_id: int, identifier_id: int, session: SessionDep, _owner: OwnerDep
) -> RevealedOut:
    """The real value, for the owner only. Nothing else returns it unmasked."""
    return RevealedOut(value=service.reveal(session, account_id, identifier_id))
