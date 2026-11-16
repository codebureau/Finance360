"""Authentication endpoints (ADR-0010). Thin: every rule lives in ``capabilities.auth``.

Public: ``status``, ``setup``, ``login``. Everything else needs a signed-in Principal.

Failed attempts return a response instead of raising, because the failure counter and the auth
event must commit with the request (an exception would roll the transaction back).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr
from sqlalchemy.orm import Session

from finance360.api.deps import COOKIE_NAME, PrincipalDep, SessionDep
from finance360.capabilities.auth import logins, sessions, setup_code
from finance360.capabilities.auth import service as auth_service
from finance360.capabilities.auth.policy import TRUSTED_CAP, utcnow
from finance360.capabilities.auth.service import LoginOutcome
from finance360.capabilities.auth.types import AuthAttempt, LockedOut, Principal, Role
from finance360.core.models.auth import Login

router = APIRouter(prefix="/api/auth", tags=["auth"])


class StatusOut(BaseModel):
    setup_required: bool
    totp_required: bool
    authenticated: bool


class MessageOut(BaseModel):
    message: str


class SetupIn(BaseModel):
    setup_code: SecretStr
    password: SecretStr
    trust_device: bool = False


class LoginIn(BaseModel):
    password: SecretStr
    totp: SecretStr | None = None
    trust_device: bool = False


class SecurityOut(BaseModel):
    totp_enabled: bool


class PasswordChangeIn(BaseModel):
    current_password: SecretStr
    new_password: SecretStr


class TotpBeginOut(BaseModel):
    secret: str
    uri: str


class TotpConfirmIn(BaseModel):
    code: SecretStr


class TotpDisableIn(BaseModel):
    password: SecretStr
    code: SecretStr


def _detail(request: Request) -> dict[str, Any]:
    return {
        "client": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _status(session: Session, request: Request) -> StatusOut:
    token = request.cookies.get(COOKIE_NAME)
    authenticated = bool(token) and sessions.resolve(session, token or "", utcnow()) is not None
    login = logins.get_login(session)
    return StatusOut(
        setup_required=login is None,
        totp_required=login is not None and logins.totp_enabled(login),
        authenticated=authenticated,
    )


def _set_cookie(request: Request, response: Response, token: str, trusted: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(TRUSTED_CAP.total_seconds()) if trusted else None,  # else: browser session
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _locked(retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"message": f"Too many attempts. Try again in {retry_after} seconds."},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


def _field_error(field: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse({"errors": [{"field": field, "message": message}]}, status_code=status)


def _attempt(request: Request, session: Session, fields: dict[str, Any]) -> AuthAttempt:
    return AuthAttempt(
        headers=request.headers,
        client=request.client.host if request.client else None,
        fields=fields,
        session=session,
        now=utcnow(),
    )


def _signed_in(request: Request, session: Session, outcome: LoginOutcome) -> JSONResponse:
    # The new Session is not yet readable by the cookie in this request, so say so directly.
    body = _status(session, request).model_copy(update={"authenticated": True})
    response = JSONResponse(body.model_dump())
    _set_cookie(request, response, outcome.token or "", outcome.trusted)
    return response


def _login_for(session: Session, principal: Principal) -> Login | None:
    return logins.get_by_id(session, principal.login_id)


@router.get("/status", response_model=StatusOut, operation_id="getAuthStatus")
def get_status(request: Request, session: SessionDep) -> StatusOut:
    return _status(session, request)


@router.post(
    "/setup",
    response_model=StatusOut,
    operation_id="setUp",
    responses={403: {"model": MessageOut}, 409: {"model": MessageOut}},  # type: ignore[arg-type]
)
def set_up(body: SetupIn, request: Request, session: SessionDep) -> Response:
    data_dir = request.app.state.settings.data_dir
    if not logins.setup_required(session):
        return JSONResponse({"message": "Already set up."}, status_code=409)
    if not setup_code.matches(data_dir, body.setup_code.get_secret_value()):
        return JSONResponse({"message": "Setup code not recognised."}, status_code=403)
    now = utcnow()
    try:
        login = logins.create_login(
            session,
            body.password.get_secret_value(),
            now=now,
            detail=_detail(request),
        )
    except logins.PasswordPolicyError as problem:
        return _field_error("password", problem.message)
    except logins.LoginExists:
        return JSONResponse({"message": "Already set up."}, status_code=409)
    setup_code.remove(data_dir)
    outcome = auth_service.sign_in(
        session,
        Principal(login.id, Role(login.role)),
        _attempt(request, session, {}),
        trust_device=body.trust_device,
    )
    return _signed_in(request, session, outcome)


@router.post(
    "/login",
    response_model=StatusOut,
    operation_id="logIn",
    responses={401: {"model": MessageOut}, 429: {"model": MessageOut}},  # type: ignore[arg-type]
)
def log_in(body: LoginIn, request: Request, session: SessionDep) -> Response:
    fields = {
        "password": body.password.get_secret_value(),
        "totp": body.totp.get_secret_value() if body.totp else None,
    }
    providers = request.app.state.auth_providers
    outcome = auth_service.login(
        session,
        providers,
        _attempt(request, session, fields),
        trust_device=body.trust_device,
    )
    if outcome.kind == "locked":
        return _locked(outcome.retry_after)
    if outcome.kind == "invalid":
        return JSONResponse({"message": "Invalid credentials."}, status_code=401)
    return _signed_in(request, session, outcome)


@router.post("/logout", status_code=204, operation_id="logOut")
def log_out(request: Request, session: SessionDep, principal: PrincipalDep) -> Response:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        auth_service.logout(session, token, principal, utcnow())
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return response


@router.get("/security", response_model=SecurityOut, operation_id="getSecurity")
def get_security(session: SessionDep, principal: PrincipalDep) -> SecurityOut:
    login = _login_for(session, principal)
    return SecurityOut(totp_enabled=login is not None and logins.totp_enabled(login))


@router.post(
    "/password",
    status_code=204,
    operation_id="changePassword",
    responses={422: {"model": MessageOut}, 429: {"model": MessageOut}},  # type: ignore[arg-type]
)
def change_password(
    body: PasswordChangeIn, request: Request, session: SessionDep, principal: PrincipalDep
) -> Response:
    login = _login_for(session, principal)
    if login is None:
        return JSONResponse({"message": "Not signed in."}, status_code=401)
    now = utcnow()
    try:
        correct = logins.confirm_password(
            session, login, body.current_password.get_secret_value(), now, _detail(request)
        )
    except LockedOut as locked:
        return _locked(locked.retry_after)
    if not correct:
        return _field_error("current_password", "That is not your current password.")
    try:
        logins.change_password(
            session,
            login,
            body.new_password.get_secret_value(),
            now=now,
            keep_token=request.cookies.get(COOKIE_NAME),
            detail=_detail(request),
        )
    except logins.PasswordPolicyError as problem:
        return _field_error("new_password", problem.message)
    return Response(status_code=204)


@router.post(
    "/totp/begin",
    response_model=TotpBeginOut,
    operation_id="beginTotp",
    responses={409: {"model": MessageOut}},  # type: ignore[arg-type]
)
def begin_totp(request: Request, session: SessionDep, principal: PrincipalDep) -> Response:
    login = _login_for(session, principal)
    if login is None:
        return JSONResponse({"message": "Not signed in."}, status_code=401)
    try:
        secret, uri = logins.totp_begin(session, login, utcnow(), _detail(request))
    except logins.TotpStateError as problem:
        return JSONResponse({"message": str(problem)}, status_code=409)
    return JSONResponse(TotpBeginOut(secret=secret, uri=uri).model_dump())


@router.post(
    "/totp/confirm",
    status_code=204,
    operation_id="confirmTotp",
    responses={409: {"model": MessageOut}, 422: {"model": MessageOut}, 429: {"model": MessageOut}},  # type: ignore[arg-type]
)
def confirm_totp(
    body: TotpConfirmIn, request: Request, session: SessionDep, principal: PrincipalDep
) -> Response:
    login = _login_for(session, principal)
    if login is None:
        return JSONResponse({"message": "Not signed in."}, status_code=401)
    try:
        confirmed = logins.totp_confirm(
            session, login, body.code.get_secret_value(), utcnow(), _detail(request)
        )
    except LockedOut as locked:
        return _locked(locked.retry_after)
    except logins.TotpStateError as problem:
        return JSONResponse({"message": str(problem)}, status_code=409)
    if not confirmed:
        return _field_error("code", "That code is not right. Check the app and try again.")
    return Response(status_code=204)


@router.post(
    "/totp/disable",
    status_code=204,
    operation_id="disableTotp",
    responses={409: {"model": MessageOut}, 422: {"model": MessageOut}, 429: {"model": MessageOut}},  # type: ignore[arg-type]
)
def disable_totp(
    body: TotpDisableIn, request: Request, session: SessionDep, principal: PrincipalDep
) -> Response:
    login = _login_for(session, principal)
    if login is None:
        return JSONResponse({"message": "Not signed in."}, status_code=401)
    try:
        disabled = logins.totp_disable(
            session,
            login,
            body.password.get_secret_value(),
            body.code.get_secret_value(),
            utcnow(),
            _detail(request),
        )
    except LockedOut as locked:
        return _locked(locked.retry_after)
    except logins.TotpStateError as problem:
        return JSONResponse({"message": str(problem)}, status_code=409)
    if not disabled:
        return _field_error(
            "code", "The password or code is not right. A code can be used only once."
        )
    return Response(status_code=204)
