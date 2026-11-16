"""The Fill target and Document profile registries (ADR-0024 section 3, ADR-0026 section 4).

Both register at start-up from ``main.py``, the composition root, like Importers; the service looks
them up here and never imports a plug-in or a record-owning Capability. A **Fill target** is a form
a Reading can fill; the Capability that owns the form declares it (plain data, since it may not
import this package) and ``main.py`` registers it **before** the profiles that name it. A profile is
validated against its target when it registers, so a new document variant is a JSON profile only.
"""

from __future__ import annotations

from dataclasses import dataclass

from finance360.capabilities.documents.profile import TYPES, Profile, ProfileError


@dataclass(frozen=True)
class TargetField:
    key: str
    #: One of ``profile.TYPES``: ``money``, ``fy`` or ``text``.
    type: str


@dataclass(frozen=True)
class FillTarget:
    #: Such as ``tax.income_statement``: what a profile's ``target`` names.
    key: str
    title: str
    fields: tuple[TargetField, ...]
    #: Whether the form has a period (a financial year) that a Reading is checked against.
    has_period: bool
    #: The Link kind (``library`` registry key) the saved record gets its document under.
    link_kind: str


_TARGETS: dict[str, FillTarget] = {}
#: What reads: the shipped profile for each key, with an active saved one over it (ADR-0027
#: section 5). The matcher and ``service.read_document`` look only here.
_PROFILES: dict[str, Profile] = {}
#: The shipped profiles, registered by ``main.py``; never replaced by a saved one.
_SHIPPED: dict[str, Profile] = {}
#: Active saved profiles, by key. The app serves from one process, so these plain dicts are the
#: whole runtime state: a write is a single dict assignment (atomic under the GIL), and a
#: restart reloads them from the database.
_SAVED: dict[str, Profile] = {}
#: Saved rows that failed to load at start-up (row id -> reason), shown in Settings. The reason
#: names a key or path, never document text.
_FAILED: dict[int, tuple[str, str]] = {}


def register_target(target: FillTarget) -> None:
    """Add a Fill target. A different target under the same key is a programming error."""
    for f in target.fields:
        if f.type not in TYPES:
            raise ValueError(f"Fill target {target.key!r} field {f.key!r} has an unknown type")
    if len({f.key for f in target.fields}) != len(target.fields):
        raise ValueError(f"Fill target {target.key!r} repeats a field key")
    existing = _TARGETS.get(target.key)
    if existing is not None and existing != target:
        raise ValueError(f"A Fill target is already registered under {target.key!r}")
    _TARGETS[target.key] = target


def unregister_target(key: str) -> None:
    """For tests that register a stand-in."""
    _TARGETS.pop(key, None)


def get_target(key: str) -> FillTarget | None:
    return _TARGETS.get(key)


def all_targets() -> list[FillTarget]:
    return [_TARGETS[k] for k in sorted(_TARGETS)]


def validate(profile: Profile) -> None:
    """Raise ``ProfileError`` unless the profile's target is registered and every field key (with
    its type) is one the target declares. The message names keys, never values."""
    target = _TARGETS.get(profile.target)
    if target is None:
        raise ProfileError(
            f"The profile {profile.key!r} names an unknown target {profile.target!r}."
        )
    declared = {f.key: f.type for f in target.fields}
    for field in profile.fields:
        if field.key not in declared:
            raise ProfileError(
                f"The profile {profile.key!r} has field {field.key!r}, which target "
                f"{target.key!r} does not have."
            )
        if declared[field.key] != field.type:
            raise ProfileError(
                f"The profile {profile.key!r} reads field {field.key!r} as {field.type}, "
                f"but target {target.key!r} declares it as {declared[field.key]}."
            )


def register(profile: Profile) -> None:
    """Add a shipped profile, after validating it against its Fill target. Registering a
    different shipped profile under the same key is a programming error."""
    validate(profile)
    existing = _SHIPPED.get(profile.key)
    if existing is not None and existing != profile:
        raise ValueError(f"A Document profile is already registered under {profile.key!r}")
    _SHIPPED[profile.key] = profile
    _PROFILES[profile.key] = _SAVED.get(profile.key, profile)


def register_saved(profile: Profile) -> None:
    """Make an active saved profile the one that reads for its key: it overrides a shipped
    profile with the same key, and adds a different one (ADR-0027 section 5). Validates against the
    Fill target first (``ProfileError``), so a profile that no longer fits is never registered."""
    validate(profile)
    _SAVED[profile.key] = profile
    _PROFILES[profile.key] = profile


def unregister_saved(key: str) -> None:
    """Retire a saved profile from the running process: the shipped one with that key reads again,
    or nothing does."""
    _SAVED.pop(key, None)
    shipped = _SHIPPED.get(key)
    if shipped is not None:
        _PROFILES[key] = shipped
    else:
        _PROFILES.pop(key, None)


def clear_saved() -> None:
    """Drop every saved profile and failure record (start-up reloads them; tests reset them)."""
    for key in list(_SAVED):
        unregister_saved(key)
    _FAILED.clear()


def mark_failed(row_id: int, key: str, reason: str) -> None:
    _FAILED[row_id] = (key, reason)


def clear_failed(row_id: int) -> None:
    _FAILED.pop(row_id, None)


def failed() -> dict[int, tuple[str, str]]:
    """Saved rows that failed to load: id -> (key, reason)."""
    return dict(_FAILED)


def shipped_profiles() -> list[Profile]:
    return [_SHIPPED[k] for k in sorted(_SHIPPED)]


def shipped_profile(key: str) -> Profile | None:
    return _SHIPPED.get(key)


def saved_profile(key: str) -> Profile | None:
    """The active saved profile for ``key``, as registered (not the database row)."""
    return _SAVED.get(key)


def unregister(key: str) -> None:
    """For tests that register a stand-in."""
    _PROFILES.pop(key, None)
    _SHIPPED.pop(key, None)
    _SAVED.pop(key, None)


def all_profiles() -> list[Profile]:
    return [_PROFILES[k] for k in sorted(_PROFILES)]


def for_target(target: str) -> list[Profile]:
    return [p for p in all_profiles() if p.target == target]
