# 0010. Single-user authentication: server-side Sessions, argon2id, optional TOTP, auth seam

**Status:** Accepted
**Amended by ADR-0012:** Secure cookie, Origin check and trusted-proxy handling (explicit allowed hosts).
**Date:** 2026-10-17
**Issue:** — (part of the M1 Household position epic)

## Context

Until S18 (deploy) the app is open; the review slot has no login. Requirements §5d fix the shape:
one local account, strong password, optional TOTP, long-lived Sessions on trusted devices, no
external identity provider, auth as a pluggable seam, *owner* and *viewer* roles designed in but only
*owner* built. The app is reached only over an overlay VPN (ADR-0007), so the threat is a stolen or shared
device, a hostile peer on the VPN, or a drive-by browser request, not the open internet. ADR-0009's
`AuditContext` currently hard-codes `("owner", "owner", "api")` in `api/household.py`. This ADR
replaces that with a real principal.

## Decision

**Structure.** `capabilities/auth/` holds the seam, Session service and policy. `plugins/auth/` holds
providers (one: `password`). A `REGISTRY` of providers, keyed like `behaviours.REGISTRY` (ADR-0004).
`FINANCE360_AUTH_PROVIDERS` (default `password`) selects the active ones.

**Seam (what a provider supplies).** Providers only *establish identity*; they never touch Sessions.

```python
class AuthProvider(Protocol):
    key: str

    def authenticate(self, req: AuthAttempt) -> Principal | None: ...


# AuthAttempt: headers, client address, body fields (a plain mapping). Principal: login_id, role.
```

Core issues the Session for any `Principal`, so a header-trusting SSO provider or a second-factor
variant later is a new plug-in with no change to Session, route-guard or audit code. Roles are an
enum `owner | viewer` carried on the `Login`. Routes depend on `current_principal` (any role, reads)
or `require_owner` (writes). With only *owner* existing, the split is a no-op today, and *viewer*
becomes a data change.

**Sessions: server-side, opaque, in SQLite.** Table `auth_session` (`id_hash`, `login_id`,
`created_at`, `last_seen_at`, `expires_at`, `trusted`). The id is 256 random bits, stored only as
SHA-256. The cookie `f360_session` is `HttpOnly; Secure; SameSite=Strict; Path=/`. `Secure` is the
default and `FINANCE360_COOKIE_SECURE=false` exists for plain-http hosts that need it (Chromium and
Firefox accept `Secure` on `http://localhost`, so the review slot is fine).
- Normal Session: 12 hours absolute.
- Trusted device ("keep me signed in" at login): 90 days idle, sliding, capped at 1 year absolute.
- Logout deletes the row. A password change or reset revokes all other Sessions. `last_seen_at`
  updates at most once a minute; expired rows are purged opportunistically at login.
- **Rejected: signed-cookie or JWT.** It can't be revoked (logout, reset) without a server list
  anyway, and SQLite is already there.

**CSRF.** `SameSite=Strict`, JSON-only API (no form posts), and unsafe methods (POST, PUT, PATCH,
DELETE) are rejected unless `Origin` (else `Referer`) matches the request host. No CSRF tokens. No
CORS (same origin).

**Credential storage: in the database, not the secrets file.** Table `login` (`id`, `role`,
`password_hash`, `totp_secret`, `totp_pending_secret`, `totp_last_step`, `failed_count`,
`locked_until`). It is user-changeable state, so it belongs with the data and travels with the
(encrypted) backups; a secrets file can't hold state the user edits in the UI. Secrets *files* remain
for operator-supplied values (below). Exactly one `Login` row is possible in M1 (the app refuses a
second). A Member is not a Login (CONTEXT.md).

**Password hashing: argon2id** via `argon2-cffi`, `m=64 MiB, t=3, p=1` (well above the OWASP floor,
about 100 ms on a small home server). Parameters live in the PHC string, so they can be raised later; a
successful login rehashes when parameters are stale. Policy: 12 to 128 characters, no composition
rules. No breached-password list in M1.

**First run and seeding. No default password anywhere.**
1. *Interactive:* with no `Login` row, the API reports `setup_required` and `POST /api/auth/setup` is
   open. It requires a **setup code**: random, written to `<data_dir>/setup-code` (mode 0600) at
   startup, deleted once setup succeeds, never logged or sent in a response. Reading it needs shell
   or volume access on the host, which is the same proof of ownership as recovery. Setup asks for a
   password, offers (does not require) TOTP enrolment, and signs in.
2. *Seeded (test instances, review slot, unattended installs):* `FINANCE360_OWNER_PASSWORD_FILE`
   (preferred) or `FINANCE360_OWNER_PASSWORD` creates the `Login` at startup **only if none exists**
   and never overwrites. It goes through the same password policy. Placeholders in `.env.example`.
   The review slot sets a documented, synthetic, localhost-only fixture password via
   `scripts/review.py` (ADR-0006). Production simply doesn't set it.

**Recovery: a host-side CLI, no recovery codes.** `python -m finance360.auth reset` (run via
`docker compose exec`) prompts for a new password, clears TOTP, and revokes all Sessions. Shell access
to the host is the root of trust already (ADR-0007). It writes an `auth_event`. The backup key and
host credentials are in the owner's password manager, findable by any other household member who may
need to recover, so recovery codes would add a second thing to store for no extra security.

**TOTP (optional).** RFC 6238: SHA-1, 6 digits, 30 s, accept ±1 step, via `pyotp`.
- Enrol: signed in, `POST totp/begin` creates a 160-bit secret into `totp_pending_secret` and returns
  the secret and `otpauth://` URI (the UI renders the QR). `POST totp/confirm` with a valid code
  promotes it to `totp_secret`. Until then login is unchanged.
- The last accepted time-step is stored and codes at or before it are rejected, so one code can't be
  replayed.
- Disable: requires password and a current code. Enrolment, confirmation and disabling are
  `auth_event`s.
- Stored plain in the DB (it must be recoverable, unlike the password hash). Anyone holding the
  database holds all the data anyway; protection is host access and encrypted backups (ADR-0007). No
  app-level key, so no new secret to manage.
- Login is stateless and single-step: `POST /api/auth/login {password, totp?, trust_device}`.
  `GET /api/auth/status` (public) returns `setup_required` and `totp_required`, so the UI shows the
  code field up front. This avoids a half-authenticated state and doesn't reveal password
  correctness.

**Rate limiting and lockout.** Counter on the `Login` row (survives restarts). After 5 consecutive
failures (wrong password or code) the Login locks, 1 minute doubling to a 15-minute cap. The lock
rejects even correct credentials (HTTP 429, `Retry-After`). Success resets the counter. Responses are
uniformly "invalid credentials". No per-IP limit: behind the overlay VPN's HTTPS front end the peer address is the proxy
and one account makes per-IP moot. A temporary lock that lets an attacker annoy the owner is accepted;
the owner can still use the CLI.

**Route protection: default deny.** The API routers are included with a router-level dependency on
`current_principal`; only `/health`, `/api/auth/status|setup|login` and the static SPA bundle are
public. Unauthenticated gets 401 (the UI redirects to login). A guard test walks `app.routes` and
fails if any route outside an explicit allow-list lacks the dependency, so a new route can't
accidentally ship open.

**Audit mapping (ADR-0009).** The authenticated `Principal` becomes the request's `AuditContext`:
`actor_kind="owner"`, `actor_ref="login:<id>"` (replacing the hard-coded stub), `source="api"`.
Existing rows with `actor_ref="owner"` stay (append-only). Login, failed login, lockout, logout,
password change or reset, and TOTP changes are **not** `audit_event` rows: that table is for changes
to data with before/after snapshots and a fixed `action` enum. They go to a separate append-only
`auth_event` table (`occurred_at`, `kind`, `login_id` nullable, `detail` JSON of non-sensitive facts
such as the client address and user agent), with the same reject-`UPDATE`/`DELETE` triggers. No
secrets or codes are ever written.

**Tests, review slot, CI, MCP: no runtime bypass.** There is deliberately no "auth off" switch, since
a flag that exists can be set in production. Instead:
- *Unit and API tests* override the `current_principal` dependency in code
  (`app.dependency_overrides`) via a shared fixture, plus dedicated auth tests that exercise the real
  flow against a seeded password. This can't be enabled by configuration.
- *Review slot and agent temp instances* log in for real with the seeded fixture password.
- *MCP* (ADR-0005) is a local, read-only, in-process or stdio layer over the service layer, so it has
  no HTTP Session and records `actor_kind="agent"`. If an HTTP-reachable MCP is wanted later it needs
  its own token ADR.

## Consequences

- Deploying (S18) can't expose open data: default deny plus the guard test.
- Adding *viewer*, a second Login, or reverse-proxy SSO is a data change or a new provider plug-in,
  not a rewrite. Nothing in M1 has to be undone.
- A lost password and lost TOTP both need host access to fix. Acceptable for a self-hosted
  single-household tool, and not for a multi-tenant one.
- Every request costs one SQLite lookup for the Session. Negligible at this scale.
- Adds two dependencies (`argon2-cffi`, `pyotp`) and three tables (`login`, `auth_session`,
  `auth_event`), one migration.
- Trusted devices stay signed in for months: a stolen unlocked device is the main residual risk.
  Mitigation: revoke by password change, and a future "sign out everywhere" button (below).

## Resolved questions

The maintainer accepted all four on 2026-10-17:
1. **Recovery:** the host-side CLI reset is the only recovery path. There are no recovery codes.
2. **First-run setup:** setup requires the one-off code from `<data_dir>/setup-code`. There is no open first-run screen.
3. **Session lifetimes:**
   - a normal Session lasts 12 hours;
   - a trusted device lasts 90 days sliding, with a 1-year cap.
4. **Auth events table:** auth events go to their own append-only `auth_event` table, not `audit_event`. The audit trail view should present both.

## Not decided here / routine follow-on

Developer implements without further design:
- Migration, models, the three tables, append-only triggers on `auth_event`.
- API routes and UI: setup page, login page (password, optional code, trust-device tick), logout, a
  security settings screen for TOTP enrol/disable and password change. Existing design language;
  **no new iconography**. The QR rendering is content, not a new icon.
- `.env.example` and compose placeholders for `FINANCE360_OWNER_PASSWORD(_FILE)`,
  `FINANCE360_AUTH_PROVIDERS`, `FINANCE360_COOKIE_SECURE`; `scripts/review.py` seeding the documented
  fixture password; README/docs "how to log in" for the review slot.
- The shared test fixture for the dependency override; the route-guard test; Hypothesis/unit tests
  for lockout backoff and TOTP step replay.
- Replacing `get_audit_context` in `api/household.py` and the SPA fall-through staying public.
- Secret hygiene: redact `password`, `totp` and the cookie from request logging.
- Out of scope: *viewer* role behaviour, Member logins, OAuth/SSO and any SSO provider, an
  HTTP-reachable MCP token, a "sign out everywhere" and Session-list UI, breached-password checking,
  WebAuthn/passkeys, per-IP limiting.
