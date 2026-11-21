"""Instance configuration, read from the environment (ADR-0006).

Every running instance is identified by its own ``FINANCE360_DATA_DIR`` and ``FINANCE360_PORT``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from finance360.core.ai_settings import AiSettings

DEFAULT_PORT = 8000
DEFAULT_GIT_SHA = "dev"
#: What a build with no release tag reports as its version.
DEFAULT_APP_VERSION = "dev"
DEFAULT_AUTH_PROVIDERS = ("password",)
DEFAULT_BACKUP_TIME = "02:00"
DEFAULT_FETCH_TIME = "18:30"
DEFAULT_PRICE_STALE_DAYS = 7
PROVIDER_TRANSPORTS = ("live", "sample")
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1")
#: One allowed-hosts entry: a DNS name or IPv4 address, optionally ``:port`` (none = any port).
_HOST_ENTRY = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$")
_FALSE = {"0", "false", "no", "off"}
#: The Guide's Markdown when run from source; the image sets ``FINANCE360_GUIDE_DIR`` (ADR-0021).
SOURCE_GUIDE_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "guide"
#: CONTEXT.md, whose vocabulary Ask's search expands from; the image sets
#: ``FINANCE360_CONTEXT_FILE``.
SOURCE_CONTEXT_FILE = Path(__file__).resolve().parents[2] / "CONTEXT.md"


def _owner_password(e: Mapping[str, str]) -> str | None:
    """``FINANCE360_OWNER_PASSWORD_FILE`` (preferred) or ``FINANCE360_OWNER_PASSWORD``."""
    if file := e.get("FINANCE360_OWNER_PASSWORD_FILE"):
        text = Path(file).read_text(encoding="utf-8")  # a missing file is a loud startup error
        return text.rstrip("\r\n") or None
    return e.get("FINANCE360_OWNER_PASSWORD") or None


def _backup_time(e: Mapping[str, str]) -> time:
    """``FINANCE360_BACKUP_TIME`` as ``HH:MM`` (Household local time); a bad value fails loudly."""
    raw = (e.get("FINANCE360_BACKUP_TIME") or DEFAULT_BACKUP_TIME).strip()
    try:
        return time.fromisoformat(raw).replace(second=0, microsecond=0)
    except ValueError:
        raise ValueError(f"FINANCE360_BACKUP_TIME must be HH:MM, got {raw!r}") from None


def _fetch_time(e: Mapping[str, str]) -> time:
    """``FINANCE360_FETCH_TIME`` as ``HH:MM`` (Household local time); a bad value fails loudly."""
    raw = (e.get("FINANCE360_FETCH_TIME") or DEFAULT_FETCH_TIME).strip()
    try:
        return time.fromisoformat(raw).replace(second=0, microsecond=0)
    except ValueError:
        raise ValueError(f"FINANCE360_FETCH_TIME must be HH:MM, got {raw!r}") from None


def _stale_days(e: Mapping[str, str]) -> int:
    raw = (e.get("FINANCE360_PRICE_STALE_DAYS") or str(DEFAULT_PRICE_STALE_DAYS)).strip()
    try:
        days = int(raw)
    except ValueError:
        days = 0
    if not 1 <= days <= 365:
        raise ValueError(
            f"FINANCE360_PRICE_STALE_DAYS must be a whole number of days, 1 to 365: {raw!r}"
        )
    return days


def _transport(e: Mapping[str, str]) -> str:
    raw = (e.get("FINANCE360_PROVIDER_TRANSPORT") or "live").strip().lower()
    if raw not in PROVIDER_TRANSPORTS:
        raise ValueError(
            "FINANCE360_PROVIDER_TRANSPORT must be one of "
            f"{', '.join(PROVIDER_TRANSPORTS)}: {raw!r}"
        )
    return raw


def parse_allowed_hosts(raw: str) -> tuple[str, ...]:
    """Split a comma-separated list; lower-cased, de-duplicated; a bad entry fails loudly."""
    entries: list[str] = []
    for item in raw.split(","):
        entry = item.strip().lower()
        if not entry:
            continue
        if not _HOST_ENTRY.match(entry):
            raise ValueError(
                f"FINANCE360_ALLOWED_HOSTS: {item.strip()!r} is not host or host:port "
                "(a name or IPv4 address, no scheme or path)"
            )
        if entry not in entries:
            entries.append(entry)
    return tuple(entries)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    port: int
    git_sha: str
    static_dir: Path | None
    #: The release tag this build was made from (``v0.10.0``), or ``dev`` for a build with none.
    #: Separate from ``git_sha``, which stays the commit (and what Backups record).
    app_version: str = DEFAULT_APP_VERSION
    #: Active auth providers, by ``plugins.auth.REGISTRY`` key (ADR-0010).
    auth_providers: tuple[str, ...] = DEFAULT_AUTH_PROVIDERS
    #: ``Secure`` on the Session cookie. Default on; off only for plain-http hosts that need it.
    cookie_secure: bool = True
    #: Names the app answers for, ``host[:port]``; anything else gets 421 (ADR-0012).
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    #: Seeds the owner Login at startup if none exists (never overwrites). Kept out of ``repr``.
    owner_password: str | None = field(default=None, repr=False)
    #: Path of the file holding the restic repo password (the Backup key). The key itself is never
    #: read into Settings: only restic reads it, through RESTIC_PASSWORD_FILE (ADR-0011).
    backup_key_file: Path | None = None
    #: Local time of day of the nightly backup, in the Household's time zone.
    backup_time: time = time(2, 0)
    #: Off-site restic repo, e.g. ``rclone:onedrive:Finance360``. None = local repo only.
    backup_remote: str | None = None
    #: Price provider key (``plugins.providers`` registry, e.g. ``yfinance``). None = no fetching:
    #: the instance never touches the network for Prices (ADR-0013).
    price_provider: str | None = None
    #: FX provider key (e.g. ``rba``). None = no fetching.
    fx_provider: str | None = None
    #: ``live`` reaches the real services; ``sample`` serves invented data offline (review, demos).
    provider_transport: str = "live"
    #: Local time of day of the daily fetch, in the Household's time zone.
    fetch_time: time = time(18, 30)
    #: A Price older than this many days is shown as stale (ADR-0013 section 3).
    price_stale_days: int = DEFAULT_PRICE_STALE_DAYS
    #: AI settings (ADR-0021). Provider unset = AI off, and Finance360 works fully without it.
    ai: AiSettings = field(default_factory=AiSettings)
    #: The Guide's Markdown pages, which the Ask panel answers from (ADR-0021 section 2).
    guide_dir: Path = SOURCE_GUIDE_DIR
    #: CONTEXT.md, for Ask's vocabulary expansion (ADR-0023 section 2). Absent file = expansion off.
    context_file: Path = SOURCE_CONTEXT_FILE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        port = int(e.get("FINANCE360_PORT") or DEFAULT_PORT)
        if not 0 < port < 65536:
            raise ValueError(f"FINANCE360_PORT out of range: {port}")
        static = e.get("FINANCE360_STATIC_DIR")
        return cls(
            data_dir=Path(e.get("FINANCE360_DATA_DIR") or "data").resolve(),
            port=port,
            git_sha=e.get("GIT_SHA") or DEFAULT_GIT_SHA,
            app_version=e.get("APP_VERSION") or DEFAULT_APP_VERSION,
            static_dir=Path(static).resolve() if static else None,
            auth_providers=tuple(
                k.strip()
                for k in (e.get("FINANCE360_AUTH_PROVIDERS") or "password").split(",")
                if k.strip()
            ),
            cookie_secure=(e.get("FINANCE360_COOKIE_SECURE") or "true").strip().lower()
            not in _FALSE,
            allowed_hosts=parse_allowed_hosts(e.get("FINANCE360_ALLOWED_HOSTS") or "")
            or DEFAULT_ALLOWED_HOSTS,
            owner_password=_owner_password(e),
            backup_key_file=Path(key_file)
            if (key_file := e.get("FINANCE360_BACKUP_KEY_FILE"))
            else None,
            backup_time=_backup_time(e),
            backup_remote=(e.get("FINANCE360_BACKUP_RCLONE_REMOTE") or "").strip() or None,
            price_provider=(e.get("FINANCE360_PRICE_PROVIDER") or "").strip().lower() or None,
            fx_provider=(e.get("FINANCE360_FX_PROVIDER") or "").strip().lower() or None,
            provider_transport=_transport(e),
            fetch_time=_fetch_time(e),
            price_stale_days=_stale_days(e),
            ai=AiSettings.from_env(e),
            guide_dir=Path(guide).resolve()
            if (guide := e.get("FINANCE360_GUIDE_DIR"))
            else SOURCE_GUIDE_DIR,
            context_file=Path(context).resolve()
            if (context := e.get("FINANCE360_CONTEXT_FILE"))
            else SOURCE_CONTEXT_FILE,
        )
