# Deploying to your host

How to run a release on your host with `deploy <tag>`, and how to set a new host up with
`deploy setup` (ADR-0007, ADR-0012). The host builds from its own git clone: there
is no registry. The household reaches the app over **HTTPS on the LAN** through a Caddy sidecar;
devices away from home can use an **overlay VPN** (the deploy script supports one, below). Backups are set up separately
([`backup-setup.md`](backup-setup.md)); rolling back after damage is
[`restore-runbook.md`](restore-runbook.md).

## Releases

Real data moves to a new version only when the owner tags one. Tags are `vMAJOR.MINOR.PATCH`, with an
optional pre-release suffix (`v0.1.0`, `v0.2.0-rc1`), made on `main` after the PR has merged:

```
git tag v0.1.0 && git push origin v0.1.0
```

Tags are immutable. Never move one: `deploy` refuses a tag whose commit changed on the host. Make a
new tag instead.

## What runs on the host

Two containers in this app's own compose project; nothing else on the host changes:

- `finance360`: the app. It publishes **no port**; it is reachable only on the compose network.
- `proxy`: Caddy with `tls internal`. It terminates TLS with its own local CA and forwards to the
  app. It publishes one port, `FINANCE360_HTTPS_PORT` (default **8843**, chosen to stay clear of
  80, 443, 8080, 8443 and the usual admin UIs), mapped to its 443. Port 80 is never published.

The app answers only for the names in `FINANCE360_ALLOWED_HOSTS` (a comma list of `host[:port]`;
the LAN IP with the HTTPS port, and the overlay VPN's name for the host). Any other name gets **421**; `/health` is
exempt. The proxy builds its certificates from the same list. The app believes the proxy's
`X-Forwarded-Host` only when the request comes from the proxy container's address, so nothing a
client sends can choose its own host.

The Caddy CA lives in the `caddy-data` volume, which is **not** in the Backup set. If it is lost,
Caddy makes a new root and each device needs the new certificate: an inconvenience, not data loss.

## Host prerequisites (once)

`deploy setup` checks these and stops, naming what is missing. It never installs anything.

- Linux with Docker and the compose plugin, `git`, `ss` (iproute2), and an SSH server.
- Key-based SSH from the desktop (no password login), so `ssh <user>@<host>` works without prompting.
- The host's LAN IP should be static: it is an allowed host and the name in the certificate.
- The overlay VPN's client on the host and your devices, only if you want mobile access (the
  `--tailscale` option of `deploy setup`).
- rclone for the cloud storage Backup remote, configured once as in [`backup-setup.md`](backup-setup.md)
  section 2 (setup makes the folder; the sign-in is interactive and stays yours).

## Tell `deploy` about the host (once, on the desktop)

```
copy deploy\target.example.env deploy\target.local.env      (Windows)
cp deploy/target.example.env deploy/target.local.env        (Git Bash, Linux, macOS)
```

Edit `deploy/target.local.env` (gitignored; never commit it): the SSH target, the clone directory,
`DEPLOY_REPO_URL` (where setup clones from), and the app settings. The ones that matter here:

```
FINANCE360_HTTPS_PORT=8843
FINANCE360_ALLOWED_HOSTS=<lan-ip>:8843
FINANCE360_BACKUP_KEY_FILE=<host path for the key file>
FINANCE360_RCLONE_CONFIG_DIR=<host folder for rclone's config>
```

List the LAN IP **first** (the proxy uses it as the default certificate for browsers that send no
server name for an IP). Every `FINANCE360_*` line is written to the host's `.env` on each deploy,
which is also what plain `docker compose` commands on the host read. **Settings live in
`target.local.env`, not in the host's `.env`, which each deploy rewrites.** While a `<placeholder>`
remains, `deploy` refuses to run and names the lines.

Put `deploy` on your `PATH` as you did `review` (add the repo's `deploy/` folder), or run
`deploy\deploy.cmd` / `python deploy/deploy.py`. It needs Python 3.10+ and `ssh`; nothing else.

## Set up a new host: `deploy setup`

```
deploy setup v0.1.0                 # LAN only
deploy setup v0.1.0 --tailscale     # also mobile access over the overlay VPN
```

In order, stopping at the first problem:

1. **Checks** Docker, compose, git, free disk (3 GB) and that `FINANCE360_HTTPS_PORT` is free on the
   host. A taken port is refused, with nothing changed; pick another in `target.local.env`.
2. **Makes the folders** (the clone, the key's folder and rclone's folder, mode 700) and clones the
   repo. It tries `chown` to the container's uid 10001 and, if it cannot, prints the `sudo chown`
   to run.
3. **Generates the Backup key** (a random 64-character key from the desktop's CSPRNG), writes it to
   the host file (mode 400) and **shows it once**. Put it in the password manager as *Finance360
   Backup key* before pressing Enter. It is never printed or logged again, and never passed on a
   command line.
4. **Deploys the tag** through the normal path (`deploy <tag> --first` on a new host).
5. **Exports Caddy's root certificate** to your Desktop (`finance360-root.crt`, or `--cert-out
   PATH`) and prints its SHA-256 and the steps for each device.
6. With `--tailscale`: reads the host's overlay VPN name (`*.ts.net`), appends it to
   `FINANCE360_ALLOWED_HOSTS` in `target.local.env` (before the deploy, so the host picks it up),
   runs `tailscale serve --bg --https=443 https+insecure://127.0.0.1:<HTTPS port>` and checks that
   Serve kept the Host (below).

It is **idempotent**: running it again never regenerates the key, never rewrites
`target.local.env` (beyond adding the ts.net name once), never re-clones, and does not count its
own proxy as a taken port. A re-run after the first is just a normal deploy plus a fresh copy of
the certificate.

Then open `https://<lan-ip>:8843` once the device trusts the certificate. The one-off setup code
is in the volume: `docker compose exec finance360 cat /data/setup-code` on the host. Finish setup
in the browser (ADR-0010), then switch Backups on as in [`backup-setup.md`](backup-setup.md).

### Trust the certificate on each device (once)

Copy `finance360-root.crt` to the device. Check the SHA-256 shown by setup if you like.

- **Windows:** double-click the file, *Install Certificate*, *Local Machine*, *Place all
  certificates in the following store*, *Trusted Root Certification Authorities*. Restart the
  browser.
- **iOS:** open the file (AirDrop or mail), *Settings > Profile Downloaded > Install*; then
  *Settings > General > About > Certificate Trust Settings* and switch it on. Both steps are needed.
- **Android:** *Settings > Security > (Encryption & credentials) > Install a certificate > CA
  certificate*, pick the file, accept the warning. Chrome uses user certificates; some apps do not,
  which is fine for a browser-only app.
- **Firefox** has its own store: *Settings > Privacy & Security > Certificates > View
  Certificates > Authorities > Import*, or set `security.enterprise_roots.enabled` to `true`.

Devices on the overlay VPN use the `https://<name>.ts.net` address with the VPN's own certificate and
need none of this. Caddy renews its short-lived site certificates itself; the root lasts about ten
years.

### The overlay VPN's HTTPS front keeps the Host (check)

Its `serve` front end points at the proxy, not the app, so one place sets forwarded headers. The proxy takes the
name the browser used from `Host`, so it must pass the original Host through. `deploy setup
--tailscale` checks this by POSTing an empty body to the login route as the `*.ts.net` name, from
the host: **422** means the request got past the host and Origin checks (good); 403 or 421 means
it changed the Host, and setup prints a warning. To check by hand: sign in at
`https://<name>.ts.net`; a "Cross-site request refused" error means the same thing. If it ever
happens, the fix is a decision, not a tweak: raise it with the Architect (ADR-0012 names a
loopback-only plain-HTTP listener on the proxy as the fallback).

## A normal deploy

```
deploy v0.2.0
```

1. Checks the clone on the host is clean and notes what it runs now (the rollback target).
2. Fetches the tag (never overwriting one).
3. **Takes the pre-deploy Backup** inside the running app. Any failure aborts here, with the
   previous release still running and nothing changed.
4. Checks out the tag, writes `.env` and runs `docker compose up -d --build`. The app's own startup
   migration takes a further copy of the database first if the schema changes.
5. Waits (up to 2 minutes) for `/health` to report the tag's `GIT_SHA` and the schema's current
   revision, then reports both, the previous release and the Backup.

A stopped app is refused unless you pass `--first`: a missing Backup is never silently skipped.

A new name or a changed LAN IP is a change to `FINANCE360_ALLOWED_HOSTS` in `target.local.env`,
then `deploy <current tag>`. Until then the new name gets a clear 421 (and the proxy a certificate
error for an unlisted name).

## Prices and FX rates (optional)

Fetching is **off by default**: a fresh deploy, with or without a network, never reaches out for
data. To switch it on, add to `target.local.env` and `deploy <current tag>`:

```
FINANCE360_PRICE_PROVIDER=yfinance   # daily Closes for Instruments that carry a `yahoo` identifier
FINANCE360_FX_PROVIDER=rba           # foreign to AUD rates, only for currencies in use
# FINANCE360_FETCH_TIME=18:30        # Household local time; also a catch-up at start-up if the
#                                    # last success is over 24 hours old
```

The Reference data page (Data > Reference data) shows the last success and last error of each, lists
every run, and has **Fetch now**. A provider being down, throttled or unreachable only produces a
failed run there; it never blocks the app or start-up. Prices and FX rates you enter by hand always
win over fetched ones for the same date. Once the RBA provider is on, a newer fetched rate is used
in place of an older manual one (nothing is deleted), and the Position line names the rate's source.

## Switching an Account to a derived value (holdings based)

Nothing switches by itself. The deploy adds one Account type, **Brokerage account (holdings)**
(`brokerage_holdings`, catalogue version 2), as a new row at start-up. Existing Account types, Accounts,
Valuations, FX rates and audit rows are not touched, and the plain *Brokerage account* type stays a
simple balance. To value an Account from its Holdings:

1. Commit its trade **and** cash exports (Data > Imports), and make sure each held Instrument has a
   Price (fetched or entered by hand).
2. Open the Account, choose **Edit**, change **Account type** to *Brokerage account (holdings)* and
   save. It is an ordinary audited Account edit; nothing is rewritten.
3. The Account page now shows a **Derived value** with its lines, the Price and FX rate behind each
   (with their dates), a stale-Price flag, which Stream is missing or stale, and the difference to the
   best recorded Valuation. Position and history take a `derived` point wherever both Streams'
   Coverage reaches the date; a recorded Valuation dated exactly on a date still wins that date.

To undo it, edit the Account back to *Brokerage account*. Valuations, Transactions and Prices stay;
the manual-only series returns exactly. An Account on the holdings type with no committed Import
behaves as a simple balance. A Price counts as stale after `FINANCE360_PRICE_STALE_DAYS` (7).

## Roll back

If a deploy fails, or the new release is wrong, `deploy` prints these steps:

- **Same schema (nothing migrated):** `deploy <previous tag>`. It takes a Backup again and starts
  the old release.
- **The schema changed:** an older release cannot read a newer schema. Restore the pre-deploy
  Backup instead, with the older tag checked out:
  [`restore-runbook.md`](restore-runbook.md). The schema version before and after is in the
  report of the last good deploy and in `/health`.

## Trying it without a real host

`transport` `local` runs the same commands on the desktop, so a throwaway Docker "host" needs no
SSH. Use a free HTTPS port (not 8843 if it is taken, never the review slot's 8360).

1. Make a throwaway repo with two tags somewhere outside this repo (a copy of the working tree, one
   commit, `v0.0.1`, then another commit and `v0.0.2`).
2. In a target file elsewhere: `DEPLOY_TRANSPORT=local`, `DEPLOY_REMOTE_DIR=<a not-yet-existing
   clone folder>`, `DEPLOY_REPO_URL=<the throwaway repo>`, `DEPLOY_COMPOSE_PROJECT=f360-httpstest`,
   `FINANCE360_HTTPS_PORT=<free port>`, `FINANCE360_ALLOWED_HOSTS=localhost:<port>,127.0.0.1:<port>`,
   `FINANCE360_BACKUP_KEY_FILE=<a path in a throwaway folder>` and
   `FINANCE360_RCLONE_CONFIG_DIR=<another>`.
3. `deploy setup v0.0.1 --config <that file> --cert-out <a path>`: it builds, starts the app and
   proxy and writes the certificate.
4. `curl --cacert <the cert> https://localhost:<port>/api/auth/status` answers 200 with the CA
   trusted; with `-H "Host: other.example"` it answers 421. The sign-in cookie carries `Secure`.
5. Run `deploy setup v0.0.2 --config ...` again: the key is "left alone", nothing in the file
   changes, a normal deploy runs. Start something listening on a different `FINANCE360_HTTPS_PORT`
   and a different `DEPLOY_COMPOSE_PROJECT`, and setup refuses the port.
6. Tidy up: `docker compose -p f360-httpstest down -v --rmi local` (in the clone folder).
