"""Exposure profile services (ADR-0030 sections 2 and 3): the one write path, ``record_profile``.

A manual entry, a later Provider, an AI proposal and the factsheet Fill target (#90) all call it.
Profiles are append-only, like Prices: a write at the same ``(Instrument, as_at)`` supersedes the
current one and the earlier version stays retrievable. Weights are stored exactly as entered, as
integer parts per million (``core.weight.Weight``); a Dimension's total is judged against
``WEIGHT_TOLERANCE_PPM`` here, and a total beyond it is rejected with the difference shown. How a
within-tolerance total resolves (shortfall to Not classified, excess trimmed from the largest
weight) is applied when the profile is read (``core.exposure.effective_weights``): nothing is
normalised on write. A profile may also hold fund-of-funds references (ADR-0030 section 5):
weighted pointers to other Instruments. A write that would make a loop of references, or nest them
deeper than ``MAX_DEPTH``, is rejected with a message naming it; a reference to an Instrument that
has no profile is allowed (it shows as No profile in a look-through). Every write is audited in
the caller's session (the caller owns the transaction); seeding the vocabulary is not.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance360.capabilities.reference.instruments import get_instrument
from finance360.core import audit
from finance360.core.audit import AuditContext
from finance360.core.errors import FieldError, NotFoundError, ValidationError
from finance360.core.exposure import (
    MAX_DEPTH,
    WEIGHT_TOLERANCE_PPM,
    OutsideTolerance,
    ProfileFacts,
    ReferenceCycle,
    ReferencesTooDeep,
    check_references,
    effective_weights,
    percent_string,
)
from finance360.core.models.exposure import (
    ExposureDimension,
    ExposureLabel,
    ExposureProfile,
    ExposureReference,
    ExposureWeight,
)
from finance360.core.models.instrument import Instrument
from finance360.core.rate import PPM
from finance360.core.weight import Weight, WeightError

NOTE_MAX = 2000
MANUAL = "manual"
#: ``ai`` means AI-proposed and owner-confirmed. A registered Provider's key joins these later.
SOURCES = (MANUAL, "ai")

#: Same file as ``household.FIXTURE_FILENAME``; ``reference`` depends on ``core`` only.
FIXTURE_FILENAME = "household.json"
FIXTURE_CONTEXT = AuditContext("system", "fixture", "fixture")


@dataclass(frozen=True)
class WeightInput:
    dimension_key: str
    label_key: str
    #: A percent decimal string, e.g. ``"62.5"``, at most 4 decimal places. Never a float.
    percent: str


@dataclass(frozen=True)
class ReferenceInput:
    """A fund-of-funds reference: part of the profile is another Instrument."""

    instrument_id: int
    #: A percent decimal string, at most 4 decimal places. Never a float.
    percent: str


@dataclass(frozen=True)
class ProfileInput:
    as_at: date
    weights: tuple[WeightInput, ...]
    provenance_note: str | None
    #: The day it was entered or retrieved; the caller supplies it (the Household's today).
    retrieved_on: date
    source: str = MANUAL
    references: tuple[ReferenceInput, ...] = ()


@dataclass(frozen=True)
class LabelView:
    key: str
    name: str


@dataclass(frozen=True)
class DimensionView:
    key: str
    name: str
    labels: tuple[LabelView, ...]


# --- vocabulary ----------------------------------------------------------------------------


def vocabulary(session: Session) -> list[DimensionView]:
    """Every Dimension with its Exposure labels, in seed order."""
    labels: dict[str, list[LabelView]] = {}
    query = select(ExposureLabel).order_by(ExposureLabel.dimension_key, ExposureLabel.position)
    for label in session.scalars(query):
        labels.setdefault(label.dimension_key, []).append(LabelView(label.key, label.name))
    return [
        DimensionView(d.key, d.name, tuple(labels.get(d.key, ())))
        for d in session.scalars(select(ExposureDimension).order_by(ExposureDimension.position))
    ]


def label_order(session: Session, dimension_key: str) -> list[str]:
    """The Dimension's label keys in seed order: display order and the trim tie-break."""
    return [
        k
        for (k,) in session.execute(
            select(ExposureLabel.key)
            .where(ExposureLabel.dimension_key == dimension_key)
            .order_by(ExposureLabel.position)
        )
    ]


# --- reads ---------------------------------------------------------------------------------


def get_profile(session: Session, instrument_id: int, profile_id: int) -> ExposureProfile:
    profile = session.get(ExposureProfile, profile_id)
    if profile is None or profile.instrument_id != instrument_id:
        raise NotFoundError(
            f"Exposure profile {profile_id} does not exist for Instrument {instrument_id}."
        )
    return profile


def list_profiles(session: Session, instrument_id: int) -> list[ExposureProfile]:
    """The whole history, superseded versions included: by date, current before superseded."""
    get_instrument(session, instrument_id)
    query = (
        select(ExposureProfile)
        .where(ExposureProfile.instrument_id == instrument_id)
        .order_by(
            ExposureProfile.as_at.desc(),
            ExposureProfile.superseded_at.is_not(None),
            ExposureProfile.recorded_at.desc(),
            ExposureProfile.id.desc(),
        )
    )
    return list(session.scalars(query))


def facts(profile: ExposureProfile) -> ProfileFacts:
    return ProfileFacts(
        profile.id,
        profile.instrument_id,
        profile.as_at,
        profile.age_from,
        profile.source,
        tuple((w.dimension_key, w.label_key, w.weight_ppm) for w in profile.weights),
        tuple((r.instrument_id, r.weight_ppm) for r in profile.references),
    )


def current_profile_facts(
    session: Session, instrument_ids: Sequence[int]
) -> dict[int, tuple[ProfileFacts, ...]]:
    """The current (non-superseded) profiles of each Instrument, oldest ``as_at`` first."""
    if not instrument_ids:
        return {}
    query = (
        select(ExposureProfile)
        .where(
            ExposureProfile.instrument_id.in_(set(instrument_ids)),
            ExposureProfile.superseded_at.is_(None),
        )
        .order_by(ExposureProfile.as_at, ExposureProfile.id)
    )
    found: dict[int, list[ProfileFacts]] = {}
    for profile in session.scalars(query):
        found.setdefault(profile.instrument_id, []).append(facts(profile))
    return {k: tuple(v) for k, v in found.items()}


def profile_facts_closure(
    session: Session, instrument_ids: Sequence[int]
) -> dict[int, tuple[ProfileFacts, ...]]:
    """The current profiles of these Instruments and of every Instrument they reference, however
    deep (a loop in bad data ends where it starts): all the pure resolver needs, preloaded."""
    found: dict[int, tuple[ProfileFacts, ...]] = {}
    queue = list(dict.fromkeys(instrument_ids))
    while queue:
        batch = [i for i in queue if i not in found]
        if not batch:
            break
        loaded = current_profile_facts(session, batch)
        for instrument_id in batch:
            found[instrument_id] = loaded.get(instrument_id, ())
        queue = [
            ref
            for profiles in loaded.values()
            for profile in profiles
            for ref, _ in profile.references
        ]
    return {k: v for k, v in found.items() if v}


def instrument_names(session: Session, instrument_ids: Collection[int]) -> dict[int, str]:
    """The names of these Instruments (for a fund-of-funds' references and look-through routes)."""
    if not instrument_ids:
        return {}
    query = select(Instrument.id, Instrument.name).where(Instrument.id.in_(set(instrument_ids)))
    return {instrument_id: name for instrument_id, name in session.execute(query)}


@dataclass(frozen=True)
class DimensionResolution:
    """How one Dimension of a stored profile resolves (shown on the profile and the panel)."""

    dimension_key: str
    total_ppm: int
    #: Total minus 100%: negative is a shortfall (to Not classified), positive an excess (trimmed).
    difference_ppm: int
    not_classified_ppm: int
    trimmed: tuple[tuple[str, int], ...]
    #: ``(instrument_id, ppm)`` trimmed from the largest reference when no direct weight took it.
    trimmed_references: tuple[tuple[int, int], ...] = ()


def resolutions(session: Session, profile: ExposureProfile) -> list[DimensionResolution]:
    """Each Dimension the profile has weights for: its total and how it resolves."""
    found: list[DimensionResolution] = []
    stored = facts(profile)
    for dimension_key in dict.fromkeys(d for d, _, _ in stored.weights):
        direct = stored.for_dimension(dimension_key)
        total = sum(ppm for _, ppm in direct) + sum(ppm for _, ppm in stored.references)
        effective = effective_weights(
            direct, label_order(session, dimension_key), stored.references
        )
        found.append(
            DimensionResolution(
                dimension_key,
                total,
                total - PPM,
                effective.not_classified_ppm,
                effective.trimmed,
                effective.trimmed_references,
            )
        )
    return found


# --- audit snapshots -----------------------------------------------------------------------


def _snapshot(profile: ExposureProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "instrument_id": profile.instrument_id,
        "as_at": profile.as_at.isoformat(),
        "age_from": profile.age_from,
        "source": profile.source,
        "retrieved_on": profile.retrieved_on.isoformat(),
        "provenance_note": profile.provenance_note,
        "weights": [
            {
                "dimension": w.dimension_key,
                "label": w.label_key,
                "percent": Weight(w.weight_ppm).to_percent(),  # a string, never a float
            }
            for w in profile.weights
        ],
        "references": [
            {
                "instrument_id": r.instrument_id,
                "percent": Weight(r.weight_ppm).to_percent(),
            }
            for r in profile.references
        ],
    }


# --- validation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Checked:
    as_at: date
    retrieved_on: date
    source: str
    note: str
    weights: tuple[tuple[str, str, int], ...]
    references: tuple[tuple[int, int], ...] = ()


_points = percent_string


def _reference_edges(session: Session, replacing: int | None) -> dict[int, set[int]]:
    """The graph of current references: Instrument to the Instruments its current profiles
    reference, leaving out the profile being superseded."""
    edges: dict[int, set[int]] = {}
    query = (
        select(ExposureProfile.instrument_id, ExposureReference.instrument_id)
        .join(ExposureReference, ExposureReference.profile_id == ExposureProfile.id)
        .where(ExposureProfile.superseded_at.is_(None))
    )
    if replacing is not None:
        query = query.where(ExposureProfile.id != replacing)
    for holder, referenced in session.execute(query):
        edges.setdefault(holder, set()).add(referenced)
    return edges


def _check_references(
    session: Session, instrument: Instrument, data: ProfileInput, replacing: int | None
) -> tuple[list[tuple[int, int]], list[FieldError]]:
    """Parse and judge the references: each exists, once, with a valid weight; no loop; no deeper
    than ``MAX_DEPTH``. Returns the ``(instrument_id, ppm)`` pairs and any errors."""
    errors: list[FieldError] = []
    parsed: list[tuple[int, int]] = []
    seen: set[int] = set()
    for entry in data.references:
        where = f"references.{entry.instrument_id}"
        if session.get(Instrument, entry.instrument_id) is None:
            errors.append(FieldError(where, "This Instrument does not exist."))
            continue
        if entry.instrument_id in seen:
            errors.append(FieldError(where, "This Instrument is entered twice."))
            continue
        seen.add(entry.instrument_id)
        try:
            weight = Weight.from_percent(entry.percent)
        except (WeightError, TypeError, ValueError):
            errors.append(
                FieldError(
                    where,
                    "Enter a percentage above 0 and at most 100, with at most 4 decimal places.",
                )
            )
            continue
        parsed.append((entry.instrument_id, weight.ppm))
    total = sum(ppm for _, ppm in parsed)
    if total > PPM + WEIGHT_TOLERANCE_PPM:
        errors.append(
            FieldError(
                "references",
                f"The underlying funds total {_points(total)}%, over 100% by "
                f"{_points(total - PPM)} points. Up to {_points(WEIGHT_TOLERANCE_PPM)} point "
                "either way is accepted: check the figures.",
            )
        )
    if errors or not parsed:  # no reference can make a loop or a deeper chain
        return parsed, errors
    names = {i.id: i.name for i in session.scalars(select(Instrument))}
    try:
        check_references(
            _reference_edges(session, replacing), instrument.id, [i for i, _ in parsed]
        )
    except ReferenceCycle as loop:
        chain = " > ".join(names.get(i, str(i)) for i in loop.loop)
        errors.append(
            FieldError(
                "references",
                f"This would make a loop of funds: {chain}. A fund cannot hold itself, "
                "directly or through other funds.",
            )
        )
    except ReferencesTooDeep as deep:
        errors.append(
            FieldError(
                "references",
                f"Funds would nest {deep.depth} levels deep; a look-through follows at most "
                f"{MAX_DEPTH}.",
            )
        )
    return parsed, errors


def _check(
    session: Session, instrument: Instrument, data: ProfileInput, replacing: int | None = None
) -> _Checked:
    errors: list[FieldError] = []
    source = data.source.strip().lower()
    if source not in SOURCES:
        errors.append(FieldError("source", f"Choose one of: {', '.join(SOURCES)}."))
    note = (data.provenance_note or "").strip()
    if not note:
        errors.append(FieldError("provenance_note", "Say where this profile came from."))
    elif len(note) > NOTE_MAX:
        errors.append(
            FieldError("provenance_note", f"The note must be at most {NOTE_MAX} characters.")
        )
    if not data.weights and not data.references:
        errors.append(FieldError("weights", "Enter at least one weight or underlying fund."))
    references, reference_errors = _check_references(session, instrument, data, replacing)
    errors.extend(reference_errors)

    names = {d.key: d.name for d in vocabulary(session)}
    known = {(d.key, label.key) for d in vocabulary(session) for label in d.labels}
    parsed: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for entry in data.weights:
        key = (entry.dimension_key, entry.label_key)
        where = f"weights.{entry.dimension_key}.{entry.label_key}"
        if entry.dimension_key not in names:
            errors.append(FieldError(where, f"Unknown Dimension {entry.dimension_key!r}."))
            continue
        if key not in known:
            errors.append(
                FieldError(where, f"{entry.label_key!r} is not a label of {names[key[0]]}.")
            )
            continue
        if key in seen:
            errors.append(FieldError(where, "This label is entered twice."))
            continue
        seen.add(key)
        try:
            weight = Weight.from_percent(entry.percent)
        except (WeightError, TypeError, ValueError):
            errors.append(
                FieldError(
                    where,
                    "Enter a percentage above 0 and at most 100, with at most 4 decimal places.",
                )
            )
            continue
        parsed.append((entry.dimension_key, entry.label_key, weight.ppm))

    if not errors:
        for dimension_key in dict.fromkeys(d for d, _, _ in parsed):
            direct = [(label, ppm) for d, label, ppm in parsed if d == dimension_key]
            try:
                effective_weights(direct, label_order(session, dimension_key), references)
            except OutsideTolerance as outside:
                gap = abs(outside.difference_ppm)
                way = "short of" if outside.difference_ppm < 0 else "over"
                errors.append(
                    FieldError(
                        "weights",
                        f"{names[dimension_key]} weights"
                        f"{' and underlying funds' if references else ''} "
                        f"total {_points(outside.total_ppm)}% "
                        f"({_points(gap)} points {way} 100%). Up to "
                        f"{_points(WEIGHT_TOLERANCE_PPM)} point either way is accepted: "
                        "check the figures.",
                    )
                )
    if errors:
        raise ValidationError(errors)
    return _Checked(data.as_at, data.retrieved_on, source, note, tuple(parsed), tuple(references))


# --- mutations -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, as the audit trail stores it


def _current_at(session: Session, instrument_id: int, as_at: date) -> ExposureProfile | None:
    return session.scalar(
        select(ExposureProfile).where(
            ExposureProfile.instrument_id == instrument_id,
            ExposureProfile.as_at == as_at,
            ExposureProfile.age_from.is_(None),
            ExposureProfile.superseded_at.is_(None),
        )
    )


def _insert(session: Session, instrument: Instrument, checked: _Checked) -> ExposureProfile:
    profile = ExposureProfile(
        instrument_id=instrument.id,
        as_at=checked.as_at,
        age_from=None,
        source=checked.source,
        retrieved_on=checked.retrieved_on,
        provenance_note=checked.note,
        recorded_at=_now(),
    )
    profile.weights = [
        ExposureWeight(dimension_key=d, label_key=label, weight_ppm=ppm)
        for d, label, ppm in checked.weights
    ]
    profile.references = [
        ExposureReference(instrument_id=instrument_id, weight_ppm=ppm)
        for instrument_id, ppm in checked.references
    ]
    session.add(profile)
    session.flush()
    return profile


def _describe(instrument: Instrument, profile: ExposureProfile) -> str:
    return f"{profile.source} Exposure profile of {instrument.name} as at {profile.as_at}"


def record_profile(
    session: Session, context: AuditContext, instrument_id: int, data: ProfileInput
) -> ExposureProfile:
    """Record an Exposure profile; at an ``as_at`` that already has one, supersede it.

    Returns the current profile. Writing exactly what is already current (the same weights, note
    and source) changes nothing and writes no audit event. The earlier version stays in the
    history, linked to its replacement.
    """
    instrument = get_instrument(session, instrument_id)
    old = _current_at(session, instrument.id, data.as_at)
    checked = _check(session, instrument, data, old.id if old else None)
    if old is None:
        profile = _insert(session, instrument, checked)
        audit.record(
            session,
            context,
            entity_type="exposure_profile",
            entity_id=profile.id,
            action="create",
            after=_snapshot(profile),
            summary=f"Added {_describe(instrument, profile)}",
        )
        return profile
    same = (
        sorted((w.dimension_key, w.label_key, w.weight_ppm) for w in old.weights)
        == sorted(checked.weights)
        and sorted((r.instrument_id, r.weight_ppm) for r in old.references)
        == sorted(checked.references)
        and old.provenance_note == checked.note
        and old.source == checked.source
    )
    if same:
        return old
    before = _snapshot(old)
    old.superseded_at = _now()  # frees the unique slot first; linked once the new row exists
    session.flush()
    new = _insert(session, instrument, checked)
    old.superseded_by_id = new.id
    session.flush()
    audit.record(
        session,
        context,
        entity_type="exposure_profile",
        entity_id=new.id,
        action="supersede",
        before=before,
        after=_snapshot(new),
        summary=f"Corrected {_describe(instrument, new)}",
    )
    return new


# --- First run -----------------------------------------------------------------------------


def load_fixture_exposure_profiles(session: Session, data_dir: Path) -> int:
    """Create the fixture's Exposure profiles through the service, once, while there are none.

    Reads ``exposure_profiles`` from each Instrument entry in ``<data_dir>/household.json``
    (matched by name): rows of ``as_at``, ``note`` and ``weights``, a map of Dimension key to a
    map of label key to a percent string. An optional ``references`` map of Instrument name to a
    percent string makes the profile a fund-of-funds (the Instruments are the fixture's own).
    """
    path = data_dir / FIXTURE_FILENAME
    if not path.is_file() or session.scalar(select(func.count()).select_from(ExposureProfile)):
        return 0
    entries = json.loads(path.read_text(encoding="utf-8")).get("instruments", [])
    ids = {i.name: i.id for i in session.scalars(select(Instrument))}
    created = 0
    for entry in entries:
        if entry["name"] not in ids:
            continue
        for item in entry.get("exposure_profiles", []):
            as_at = date.fromisoformat(item["as_at"])
            record_profile(
                session,
                FIXTURE_CONTEXT,
                ids[entry["name"]],
                ProfileInput(
                    as_at=as_at,
                    retrieved_on=as_at,
                    provenance_note=item["note"],
                    weights=tuple(
                        WeightInput(dimension, label, str(percent))
                        for dimension, labels in item.get("weights", {}).items()
                        for label, percent in labels.items()
                    ),
                    references=tuple(
                        ReferenceInput(ids[name], str(percent))
                        for name, percent in item.get("references", {}).items()
                    ),
                ),
            )
            created += 1
    return created
