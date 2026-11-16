# Finance360

A personal, self-hosted household wealth-planning platform: total financial position, look-through
investment analytics, and retirement projection with best/likely/worst Scenarios, in an Australian
context.

**Status:** under active development, with a working app. The [roadmap](docs/roadmap/index.md)
shows what exists and what is planned.

- [Requirements & scope](docs/requirements.md)
- [Controlled vocabulary](CONTEXT.md)
- [Architecture decisions](docs/adr/)
- [Roadmap](docs/roadmap/index.md)
- [Agent working rules](AGENTS.md)

## Reviewing a PR (pre-merge)

Needs Python 3.10+, `git`, an authenticated `gh`, and Docker Desktop running. Put `scripts\` on
`PATH` (PowerShell: `$env:Path += ";<path-to-your-clone>\scripts"`, or add it
permanently in Windows environment settings) or call `scripts\review.cmd` directly.

```
review <PR#>     # check the PR out in the review slot, serve on http://localhost:8360, print "How to test"
review --stop    # stop the slot and free the port
review --reset   # reset the slot's data to the synthetic household (fixtures/sample_household)
```

The slot is a separate worktree, by default a sibling of your clone named `<repo>-review` (created
on first use; an existing detached worktree there is reused) and never touches your working copy. Defaults can be
overridden with `FINANCE360_REVIEW_SLOT`, `FINANCE360_REVIEW_DATA`, `FINANCE360_REVIEW_PORT` and
`FINANCE360_REVIEW_PROJECT`. See [ADR-0006](docs/adr/0006-pre-merge-review-slot-and-instance-isolation.md).

### Signing in (ADR-0010)

The app is behind a login; there is no default password and no switch that turns it off.

- **Review slot and test instances** are seeded with a synthetic, localhost-only password from
  `fixtures/sample_household/owner-password` (`sample-household-login`). `review` prints it when it
  serves. `review --reset` restores it.
- **A real install** reads a one-off setup code from `<data dir>/setup-code` on the host (with
  Docker: `docker compose exec finance360 cat /data/setup-code`), then the setup page asks for a
  password. Alternatively set `FINANCE360_OWNER_PASSWORD_FILE` (see `.env.example`).
- **Lost password:** on the host, `docker compose exec finance360 python -m finance360.auth reset`
  sets a new password, turns TOTP off and signs every device out.

Built personal-first, and open source.

## Synthetic data only

The repo never contains real financial data: every test, fixture and the sample household is
invented, and CI and the commit hooks reject anything that looks real (see [SECURITY.md](SECURITY.md),
which also says how to report a vulnerability privately).

## Licence

Finance360 is licensed under the GNU Affero General Public License, version 3 or (at your option)
any later version (`AGPL-3.0-or-later`). The full text is in [LICENSE](LICENSE).
