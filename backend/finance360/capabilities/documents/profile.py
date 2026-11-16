"""A Document profile: data, validated against a JSON Schema on load (ADR-0024 section 3).

Data, not code, so a shipped profile, a saved profile and an AI proposal are one format and a
proposal cannot contain behaviour. The matcher and its vocabulary are code and closed: ``pair`` is
one of ``PAIRS`` and a new rule is added there as code plus a schema value, never as profile
freedom.

**A profile holds no values and no personal text.** A label, a phrase or a section marker that
contains a run of ``MAX_DIGIT_RUN`` or more digits is invalid, so a name, an ABN or an account
number cannot be saved inside a profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import jsonschema

from finance360.capabilities.documents.text import normalise

TYPES = ("money", "fy", "text")
#: The closed set of pairing rules (ADR-0024 section 3). ``below`` is added only if a real sample
#: needs it.
PAIRS = ("same_row", "next_line")
MAX_DIGIT_RUN = 6

_KEY = {"type": "string", "pattern": r"^[a-z][a-z0-9_.-]{0,63}$"}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 120}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "version", "title", "target", "claim", "fields"],
    "properties": {
        "key": _KEY,
        "version": {"type": "integer", "minimum": 1},
        "title": _TEXT,
        "target": _KEY,
        "claim": {"type": "array", "minItems": 1, "maxItems": 20, "items": _TEXT},
        "sections": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "start"],
                "properties": {"key": _KEY, "start": _TEXT, "end": _TEXT},
            },
        },
        "fields": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "type", "labels", "pair"],
                "properties": {
                    "key": _KEY,
                    "type": {"enum": list(TYPES)},
                    "labels": {"type": "array", "minItems": 1, "maxItems": 10, "items": _TEXT},
                    "section": _KEY,
                    "pair": {"enum": list(PAIRS)},
                    "required": {"type": "boolean"},
                    "absent_means_zero": {"type": "boolean"},
                    "negative_ok": {"type": "boolean"},
                    "repeats": {"type": "boolean"},
                    "zero_in_sums": {"type": "boolean"},
                },
            },
        },
        "sums": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["total", "plus"],
                "properties": {
                    "total": _KEY,
                    "plus": {"type": "array", "minItems": 1, "maxItems": 20, "items": _KEY},
                },
            },
        },
    },
}


class ProfileError(ValueError):
    """The profile is not valid. The message names a key or path, never a value."""


@dataclass(frozen=True)
class Section:
    key: str
    start: str
    end: str | None = None


@dataclass(frozen=True)
class FieldSpec:
    key: str
    type: str
    labels: tuple[str, ...]
    pair: str
    section: str | None = None
    required: bool = False
    absent_means_zero: bool = False
    negative_ok: bool = False
    #: The label may appear on several rows (such as one per leave payment): every row is read and
    #: the amounts are added (text rows are joined), each row kept for the trace.
    repeats: bool = False
    #: A row the document does not state counts as zero in a sum, instead of skipping the check.
    #: The field itself stays "not found": nothing is filled for it.
    zero_in_sums: bool = False


@dataclass(frozen=True)
class SumSpec:
    total: str
    plus: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    key: str
    version: int
    title: str
    target: str
    claim: tuple[str, ...]
    sections: tuple[Section, ...]
    fields: tuple[FieldSpec, ...]
    sums: tuple[SumSpec, ...]


def _digit_run(text: str) -> bool:
    """A run of six digits, or of nine once spaces and dashes are removed (an ABN or TFN)."""
    plain = re.search(rf"\d{{{MAX_DIGIT_RUN},}}", text) is not None
    spaced = re.search(r"\d{9,}", re.sub(r"[\s\-]", "", text)) is not None
    return plain or spaced


def load_profile(raw: object) -> Profile:
    """Validate ``raw`` (parsed JSON) and return the Profile. Raises ``ProfileError``."""
    try:
        jsonschema.validate(raw, SCHEMA)
    except jsonschema.ValidationError as error:
        where = "/".join(str(p) for p in error.absolute_path) or "(top)"
        raise ProfileError(f"The profile is not valid at {where}: {error.validator}.") from None
    data: dict[str, Any] = raw  # type: ignore[assignment]  # validated above
    sections = tuple(Section(s["key"], s["start"], s.get("end")) for s in data.get("sections", []))
    fields = tuple(
        FieldSpec(
            f["key"],
            f["type"],
            tuple(f["labels"]),
            f["pair"],
            f.get("section"),
            f.get("required", False),
            f.get("absent_means_zero", False),
            f.get("negative_ok", False),
            f.get("repeats", False),
            f.get("zero_in_sums", False),
        )
        for f in data["fields"]
    )
    sums = tuple(SumSpec(s["total"], tuple(s["plus"])) for s in data.get("sums", []))
    profile = Profile(
        data["key"],
        data["version"],
        data["title"],
        data["target"],
        tuple(data["claim"]),
        sections,
        fields,
        sums,
    )
    _check(profile)
    return profile


def _check(profile: Profile) -> None:
    phrases = [
        *profile.claim,
        *(s.start for s in profile.sections),
        *(s.end for s in profile.sections if s.end),
        *(label for f in profile.fields for label in f.labels),
    ]
    for phrase in phrases:
        if _digit_run(phrase):
            raise ProfileError(
                f"The profile has a label with a run of {MAX_DIGIT_RUN} or more digits; "
                "a profile holds no values."
            )
        if not normalise(phrase):
            raise ProfileError("The profile has a label with no letters or digits.")
    keys = [f.key for f in profile.fields]
    if len(set(keys)) != len(keys):
        raise ProfileError("The profile repeats a field key.")
    section_keys = {s.key for s in profile.sections}
    if len(section_keys) != len(profile.sections):
        raise ProfileError("The profile repeats a section key.")
    for f in profile.fields:
        if f.section is not None and f.section not in section_keys:
            raise ProfileError(f"Field '{f.key}' names a section the profile does not have.")
        if f.repeats and f.type == "fy":
            raise ProfileError(f"Field '{f.key}' repeats, which a period cannot.")
        if f.zero_in_sums and f.type != "money":
            raise ProfileError(f"Field '{f.key}' counts as zero in a sum, but is not money.")
    by_key = {f.key: f for f in profile.fields}
    for s in profile.sums:
        for key in (s.total, *s.plus):
            if key not in by_key or by_key[key].type != "money":
                raise ProfileError(f"A sum names '{key}', which is not a money field.")


def to_json(profile: Profile) -> dict[str, Any]:
    """The profile as the JSON it loads from (``load_profile(to_json(p)) == p``), for showing a
    shipped profile read-only."""
    fields: list[dict[str, Any]] = []
    for f in profile.fields:
        item: dict[str, Any] = {
            "key": f.key,
            "type": f.type,
            "labels": list(f.labels),
            "pair": f.pair,
        }
        if f.section is not None:
            item["section"] = f.section
        for flag in ("required", "absent_means_zero", "negative_ok", "repeats", "zero_in_sums"):
            if getattr(f, flag):
                item[flag] = True
        fields.append(item)
    out: dict[str, Any] = {
        "key": profile.key,
        "version": profile.version,
        "title": profile.title,
        "target": profile.target,
        "claim": list(profile.claim),
    }
    if profile.sections:
        out["sections"] = [
            {"key": s.key, "start": s.start, **({"end": s.end} if s.end else {})}
            for s in profile.sections
        ]
    out["fields"] = fields
    if profile.sums:
        out["sums"] = [{"total": s.total, "plus": list(s.plus)} for s in profile.sums]
    return out
