# 0012. LAN access with HTTPS: a Caddy sidecar, explicit allowed hosts, alongside an overlay VPN

**Status:** Accepted
**Date:** 2026-10-18
**Issue:** — (amends ADR-0007 and ADR-0010; precedes the `deploy setup` task)

## Context

ADR-0007 assumed the overlay VPN's HTTPS front end is the only way in. In practice the household uses the app from the
LAN; the overlay VPN is for mobile. A host may already run other apps on common ports, over plain
HTTP, with no reverse proxy, and the owner may not want to restructure them. The `Secure` Session cookie
(ADR-0010) needs HTTPS on the LAN, and the Origin check plus the blanket `FINANCE360_TRUSTED_PROXY`
switch trusted forwarded headers from anyone who could reach the port. Real hostnames and
IPs stay out of the repo (`deploy/target.local.env`, gitignored).

## Decision

**1. HTTPS: a Caddy sidecar in this app's compose, `tls internal`.** A second service (`proxy`,
official `caddy` image) terminates TLS and reverse-proxies to the app. Caddy's own local CA signs the
site certificates, including for a bare LAN IP. Self-contained: nothing outside this compose changes.
- Sites served: each entry of `FINANCE360_ALLOWED_HOSTS` (below), generated into the Caddyfile from
  that one variable, so there is a single list to maintain.
- **Trust on devices:** the CA root certificate (`root.crt`) is installed once per device; renewals
  are then invisible. `deploy setup` exports it. Windows: import into *Trusted Root Certification
  Authorities*. iOS: install the profile, then enable it under *Settings > General > About >
  Certificate Trust Settings*. Android: *Settings > Security > Install a certificate > CA
  certificate* (a warning is shown; Chrome honours user CAs, not every app, which is fine for a
  browser-only app). Firefox keeps its own store unless set to use the OS's.
- **Renewal:** Caddy renews its short-lived leaf certificates itself; the root lasts about ten
  years. The `caddy-data` named volume holds the CA. If it is lost, Caddy mints a new root and devices
  must reinstall it. That is an inconvenience, not data loss, so the volume is **not** in the Backup
  set (ADR-0011).
- Overlay VPN devices need none of this: they use the VPN's private HTTPS name with its own real certificate.

**2. Allowed hosts replace `FINANCE360_TRUSTED_PROXY`** (removed).
`FINANCE360_ALLOWED_HOSTS` is a comma-separated list of `host[:port]` entries: the LAN name or IP
with the HTTPS port, and the overlay VPN's private name for the host. An entry without a port matches any port. Default
`localhost,127.0.0.1` (dev and the review slot work unchanged).
- *Effective host* = the first `X-Forwarded-Host` value if the request came **from the proxy**, else
  the `Host` header. If it is not on the list the app answers **421**. `/health` is exempt (the
  container healthcheck).
- *Who is trusted:* the app's port is not published, so its only peers are containers on the compose
  network. Forwarded headers are honoured only when the TCP peer is the `proxy` service's address
  (resolved from the compose service name). There is no env switch; LAN clients cannot reach the app
  directly, and Caddy overwrites any `X-Forwarded-*` the client supplied.
- *Origin check (ADR-0010):* unchanged in shape. `Origin` (else `Referer`) must equal the effective
  host, which has already passed the list. A cross-site page cannot satisfy both.

**3. Cookies.** `Secure` stays on everywhere real data lives. `FINANCE360_COOKIE_SECURE=false` remains
only as the documented plain-http escape hatch, and `deploy setup` never writes it. **The overlay VPN's HTTPS front end
points at the proxy, not the app** (its serve command targets `https+insecure://127.0.0.1:<proxy
port>`). One path in, one trust boundary, one place that sets forwarded headers. The VPN's private name is
also a Caddy site, so the forwarded Host passes the same list. (Developer verifies the front end preserves
the Host header; if not, the fallback is a loopback-only plain-HTTP listener on the proxy, decided
then.)

**4. Ports.** The app publishes nothing on a deploy. Only `proxy` publishes one port,
`FINANCE360_HTTPS_PORT`, default **8843** (chosen to dodge 80/443/8080/8443/9443/3000 and the usual
admin-UI ports), mapped to the proxy's 443. Port 80 is never published. `deploy setup` checks the
chosen port is free on the host and refuses otherwise. The review slot and dev flow keep the direct
`FINANCE360_PORT` publish via a compose override; the production file stays minimal.

**5. `deploy setup` (first-time host bootstrap), decisions only:**
1. Prerequisites check over SSH: Docker and Compose, git, free disk, the chosen port free. Fail
   loudly with what is missing; never install packages itself.
2. Create the folders (clone, data, backup key, rclone config) with restrictive modes.
3. Generate the Backup key, show it **once** for the owner's password manager, and never print it again.
4. First deploy of a chosen tag through the normal `deploy` path (ADR-0007).
5. Export the Caddy root certificate to the desktop for installing on devices, with the per-OS steps
   above printed.
6. Optional (an overlay VPN flag): configure the VPN's HTTPS front end to the proxy and add its private
   name to the allowed hosts.

It is idempotent: re-running never regenerates the key or overwrites `.env` or `target.local.env`.

## Consequences

- LAN use is HTTPS with a one-off root install per household device; the app and its cookies are
  identical on LAN and the overlay VPN.
- One more container (Caddy, small) and a `caddy-data` volume. No effect on the host's other apps.
- The allowed-hosts list must include every name the household types; a new name or an IP change
  means editing the list and redeploying (a clear 421, not a silent failure).
- Anyone who obtains the CA root key could mint certificates trusted by the household's devices. It
  lives only in the host's `caddy-data` volume; host access is already the root of trust (ADR-0007).
- No new iconography or design-language additions. No new CONTEXT.md terms.

## Rejected

- **Host-wide shared proxy:** would require restructuring the other apps and ports, and couples
  Finance360 to them.
- **mkcert:** manual issue and renewal on the host, regeneration per name, and the CA key usually
  sits on a desktop. Caddy does the same job automatically.
- **Overlay VPN everywhere:** LAN use should work without the VPN up; it stays for mobile.
- **A blanket "trusted proxy" flag:** trusts whoever can reach the port.

## Resolved questions

The maintainer accepted all three on 2026-10-18:
1. **Default HTTPS port:** `FINANCE360_HTTPS_PORT` defaults to **8843**, and `deploy setup` refuses if it's taken.
2. **LAN address:** the host has a static LAN IP, so the household reaches it by that IP. A local DNS name can be added to the allowed hosts later.
3. **Unlisted hosts:** strict. Any name not on `FINANCE360_ALLOWED_HOSTS` gets 421, except `/health`.

## Not decided here / routine follow-on

Developer, without further design: the compose `proxy` service and Caddyfile generation, the
`caddy-data` volume, the expose/publish split and dev/review override; `config.py` parsing of
`FINANCE360_ALLOWED_HOSTS` and removal of `FINANCE360_TRUSTED_PROXY`; host middleware (421), the
`csrf.py` change and tests (spoofed `X-Forwarded-Host` from a non-proxy peer; unlisted host; overlay VPN
host via the proxy); `.env.example` and docs (`docs/deploy.md`, device trust steps); the `deploy setup`
script itself.

**Amends ADR-0007** (host reachable on the LAN over HTTPS; an overlay VPN for mobile) and
**ADR-0010** (Secure cookie, Origin check, trusted-proxy handling).
