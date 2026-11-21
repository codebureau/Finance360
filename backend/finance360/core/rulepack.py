"""Rule pack contract, registry, ``materialise`` and the content hash (ADR-0018).

A Rule pack is versioned data plus a few pure functions; it holds no algorithm for a benefit or a
tax. This module is pure types and arithmetic: no I/O, no database. A jurisdiction's pack lives in
``rulepacks/<jurisdiction>/``, imports this contract and registers at start-up from ``main.py``;
services look a pack up here by jurisdiction key and never import ``rulepacks/``.

* A **financial year (FY)** runs 1 July to 30 June and is keyed by the year it ends in (FY 2026-27
  is ``2027``).
* **Ages** are exact: ``Age`` is whole years plus whole months (Age Pension age has half-year
  steps, 65 years 6 months). Never a float.
* **Carry-forward** beyond a parameter's last known FY (ADR-0018 section 3): ``cpi`` repeats the
  last value (constant in the real terms the engine works in); ``frozen`` is fixed in nominal
  dollars, so its real value falls by the run's inflation, one ``scale`` per year from the last
  known value (rounded once, never compounded).
* ``materialise`` returns a step function: a new span only where a value changes.
* **Tax keys** (prefix ``tax.``, ADR-0019) belong to the Tax capability, which reads exact
  financial years from the pack. They are never materialised for a projection and are not
  Scenario-overridable. The ``age_pension.`` keys (B11) are treated the same until the engine
  slice that reads them.
* A year's entry may mark a key **provisional** (announced, not yet law) with a note. The marker is
  in the content hash, so setting or clearing it needs a ``revision`` bump.
* **Captured figures** (ADR-0022) layer over the shipped pack: ``layer`` is a pure function from a
  pack and the active ``Capture`` values to a new, validated pack. The shipped ``content_hash`` is
  untouched; the layered pack's ``version_label`` counts the captures it carries and their digest.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, cast

from finance360.core.money import Money, opaque, scale
from finance360.core.rate import PPM, Rate

MONEY = "money"
RATE = "rate"
INT = "int"
#: An ordered marginal table: from each ``threshold`` upward the band's ``rate`` applies (ADR-0018
#: section 1, built for tax in ADR-0019 section 4).
BRACKETS = "brackets"
KINDS = (MONEY, RATE, INT, BRACKETS)

CPI = "cpi"
FROZEN = "frozen"
POLICIES = (CPI, FROZEN)

#: Keys of a Scenario override of a pack value (ADR-0018 section 5): ``rule.<pack key>``.
OVERRIDE_PREFIX = "rule."

#: Keys of the Tax capability's rules (ADR-0019 section 4).
TAX_PREFIX = "tax."

#: Keys of the Age Pension means test (B11). Like ``tax.`` keys they are read for an exact
#: financial year by their own calculation, not projected year by year and not yet overridable.
AGE_PENSION_PREFIX = "age_pension."


def is_exact_year_key(key: str) -> bool:
    """A key read for an exact FY by its own calculation, never materialised for a projection."""
    return key.startswith((TAX_PREFIX, AGE_PENSION_PREFIX))


class RulePackError(ValueError):
    """A malformed pack, or a request the pack cannot answer."""


# --- ages ----------------------------------------------------------------------------------


@dataclass(frozen=True, order=True, slots=True)
class Age:
    """An exact age: whole years and whole months (0 to 11). Orders by total months."""

    years: int
    months: int = 0

    def __post_init__(self) -> None:
        for name, value in (("years", opaque(self.years)), ("months", opaque(self.months))):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"An age's {name} must be an integer, got {type(value).__name__}")
        if self.years < 0 or not 0 <= self.months < 12:
            raise RulePackError("An age is 0 or more years and 0 to 11 months.")

    @property
    def total_months(self) -> int:
        return self.years * 12 + self.months

    @classmethod
    def from_months(cls, total: int) -> Age:
        if total < 0:
            raise RulePackError("An age is 0 or more years and 0 to 11 months.")
        return cls(*divmod(total, 12))

    @classmethod
    def parse(cls, text: str) -> Age:
        """``"67"`` or ``"66y6m"``. Anything else (including a decimal) is rejected."""
        match = _AGE.fullmatch(text.strip())
        if match is None:
            raise RulePackError("Enter an age in whole years, such as 67, or 66y6m.")
        return cls(int(match["years"]), int(match["months"] or 0))

    def __str__(self) -> str:
        return f"{self.years}y{self.months}m" if self.months else str(self.years)


_AGE = re.compile(r"(?P<years>[0-9]{1,3})(?:y(?P<months>[0-9]{1,2})m)?", re.ASCII)


# --- the contract --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bracket:
    """One step of a marginal table: from ``threshold`` upward the ``rate`` applies."""

    threshold: Money
    rate: Rate


Value = Money | Rate | int | tuple[Bracket, ...]


@dataclass(frozen=True, slots=True)
class Param:
    """One year-keyed parameter: what it holds, how it carries forward, and where it is from."""

    key: str
    kind: str
    policy: str
    source: str
    #: For money: ``year``, ``fortnight`` or ``hour``. Published figures are stored as published.
    unit: str | None = None
    #: Trace metadata: shown beside a figure a record used. Not in the content hash, so
    #: wording or an address can be corrected without a revision bump.
    label: str = ""
    page_url: str | None = None
    #: False until a person has opened the address and found it correct.
    url_confirmed: bool = False
    #: Appended to a whole-number figure when shown (``"%"`` for a whole percent).
    value_suffix: str = ""


@dataclass(frozen=True, slots=True)
class FigureSpec:
    """How a person is guided to a published figure (reused by B11 for the Age Pension).

    The declaration is guidance and validation only: it carries the page to open, where to look on
    it, the unit and the plausible range. The citation (``source``) is what the pack hashes; the
    address and wording of the guidance can be corrected without a revision bump.
    """

    key: str
    kind: str
    policy: str
    #: Plain-language name shown by the capture script.
    label: str
    #: Citation recorded on the pack parameter (document title).
    source: str
    page_url: str
    #: Where on the page to find the figure.
    where: str
    #: Plausible range in the kind's integer unit: money minor units, rate ppm, or the int itself.
    #: For ``brackets`` it bounds each band's threshold.
    low: int
    high: int
    unit: str | None = None
    #: ``brackets`` only: bounds on each band's rate, in ppm.
    rate_low: int = 0
    rate_high: int = PPM
    #: A year-on-year change above this many ppm of the previous figure is flagged.
    max_change_ppm: int = 200_000
    #: False until a person has opened the address and found it correct.
    url_confirmed: bool = False
    #: Appended to a whole-number figure when shown (``"%"`` for a whole percent).
    value_suffix: str = ""
    #: Plain text, guidance only (outside the content hash, like ``where``): a known change the
    #: maintainer has written down, such as "Rate is legislated to fall from 1 July 2027". Shown on
    #: a carried figure and beside Confirm, and keeps it out of the bulk confirm (ADR-0022 s4). Not
    #: evaluated, never used to alter a value.
    scheduled_change: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS or self.policy not in POLICIES:
            raise RulePackError(f"Figure {self.key!r} has an unknown kind or policy.")
        if self.low > self.high or self.rate_low > self.rate_high:
            raise RulePackError(f"Figure {self.key!r} has an empty plausible range.")

    def to_param(self) -> Param:
        return Param(
            self.key,
            self.kind,
            self.policy,
            self.source,
            self.unit,
            self.label,
            self.page_url,
            self.url_confirmed,
            self.value_suffix,
        )


@dataclass(frozen=True, slots=True)
class AgeRule:
    """An age fixed by law as a pure function of date of birth (not keyed by FY)."""

    key: str
    label: str
    source: str
    evaluate: Callable[[date], Age]


@dataclass(frozen=True)
class RulePack:
    pack_id: str
    jurisdiction: str
    #: Bumped by the author whenever any value, schema or age-function result changes.
    revision: int
    params: tuple[Param, ...]
    #: FY (the year it ends in) -> key -> value in force at 1 July of that FY.
    years: Mapping[int, Mapping[str, Value]]
    ages: tuple[AgeRule, ...] = field(default=())
    #: FY -> key -> note, for values announced but not yet law. Part of the content hash.
    provisional: Mapping[int, Mapping[str, str]] = field(default_factory=lambda: {})
    #: FY -> key -> ISO date the figure was captured from its source. Trace metadata: not in the
    #: content hash (a figure's value and citation are; when it was looked up is not).
    retrieved: Mapping[int, Mapping[str, str]] = field(default_factory=lambda: {})
    #: Parameters declared for capture but holding no figure yet, so a record can name what it is
    #: waiting for. Trace metadata: not in the content hash (a key joins ``params`` with a figure).
    declared: tuple[Param, ...] = ()
    #: How a person is guided to each capturable key (ADR-0022 section 1). Trace and validation
    #: metadata: not in the content hash, like ``declared``.
    specs: tuple[FigureSpec, ...] = ()
    #: The captures this pack carries over the shipped one (empty for a shipped pack), each with
    #: where it came from. Set only by ``layer``.
    captured: tuple[CapturedFigure, ...] = ()
    #: SHA-256 of the canonical JSON of the applied captures (empty with none), and the shipped
    #: pack's content hash it was layered over. Set only by ``layer``.
    capture_digest: str = ""
    shipped_hash: str = ""

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise RulePackError("A pack revision is 1 or more.")
        if not self.years:
            raise RulePackError("A pack declares at least one financial year.")
        schema: dict[str, Param] = {}
        for param in self.params:
            if param.key in schema:
                raise RulePackError(f"Parameter {param.key!r} is declared twice.")
            if param.kind not in KINDS:
                raise RulePackError(f"Parameter {param.key!r} has an unknown kind {param.kind!r}.")
            if param.policy not in POLICIES:
                raise RulePackError(f"Parameter {param.key!r} has an unknown policy.")
            if param.policy == FROZEN and param.kind != MONEY:
                raise RulePackError(f"Only a money parameter can be frozen: {param.key!r}.")
            schema[param.key] = param
        age_keys = [a.key for a in self.ages]
        if len(set(age_keys)) != len(age_keys) or set(age_keys) & set(schema):
            raise RulePackError("Parameter and age keys must each be declared once.")
        for fy, values in self.years.items():
            for key, value in values.items():
                if key not in schema:
                    raise RulePackError(f"FY {fy_label(fy)} has undeclared parameter {key!r}.")
                check_value(schema[key], value)
        for fy, notes in self.provisional.items():
            for key, note in notes.items():
                if key not in self.years.get(fy, {}):
                    raise RulePackError(
                        f"{key!r} is marked provisional for FY {fy_label(fy)} but has no value."
                    )
                if not note.strip():
                    raise RulePackError(f"A provisional {key!r} needs a note.")

    def provisional_note(self, fy: int, key: str) -> str | None:
        """The note if ``key`` is provisional in ``fy``, else ``None``."""
        return self.provisional.get(fy, {}).get(key)

    def describe(self, key: str) -> Param | None:
        """The parameter ``key`` if the pack holds it, else its declaration, else ``None``."""
        for param in (*self.params, *self.declared):
            if param.key == key:
                return param
        return None

    def retrieved_on(self, fy: int, key: str) -> date | None:
        """The date ``key`` was captured for ``fy``, or ``None`` when none is recorded."""
        text = self.retrieved.get(fy, {}).get(key)
        return date.fromisoformat(text) if text else None

    def provisional_keys(self, fy: int) -> Mapping[str, str]:
        """Every provisional key of ``fy`` with its note (empty when the year is firm)."""
        return dict(self.provisional.get(fy, {}))

    @property
    def last_fy(self) -> int:
        """The latest FY the pack publishes figures for; carry-forward starts after it."""
        return max(self.years)

    @property
    def version_label(self) -> str:
        """``au/2026-27/r1``: what ``projection_run.rule_pack_version`` records. With captures it
        reads ``au/2026-27/r6+3:a3f9c1``: the number applied, then the first 6 hex of their digest
        (ADR-0022 section 3), so two runs with one label used the same figures."""
        label = f"{self.pack_id}/{fy_label(self.last_fy)}/r{self.revision}"
        if self.captured:
            label += f"+{len(self.captured)}:{self.capture_digest[:6]}"
        return label

    def capture_of(self, fy: int, key: str) -> CapturedFigure | None:
        """The capture supplying ``key`` for ``fy``, or ``None`` when the shipped figure does."""
        for figure in self.captured:
            if figure.fy == fy and figure.key == key:
                return figure
        return None

    def effective_hash(self) -> str:
        """The content hash with no captures; otherwise the shipped hash plus the capture digest."""
        if not self.captured:
            return self.content_hash()
        text = f"{self.shipped_hash}:{self.capture_digest}"
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    def param(self, key: str) -> Param:
        for param in self.params:
            if param.key == key:
                return param
        raise RulePackError(f"Unknown pack parameter {key!r}.")

    def age_rule(self, key: str) -> AgeRule:
        for rule in self.ages:
            if rule.key == key:
                return rule
        raise RulePackError(f"Unknown pack age {key!r}.")

    @property
    def keys(self) -> tuple[str, ...]:
        """Every key a Scenario may override: the parameters (not ``tax.``), then the ages."""
        return (
            *(p.key for p in self.params if not is_exact_year_key(p.key)),
            *(a.key for a in self.ages),
        )

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON of the schema and years (ADR-0018 section 4). The age
        functions are covered by golden tests; their keys, labels and sources are hashed here."""
        document: dict[str, object] = {
            "pack_id": self.pack_id,
            "jurisdiction": self.jurisdiction,
            "params": [
                {
                    "key": p.key,
                    "kind": p.kind,
                    "policy": p.policy,
                    "source": p.source,
                    "unit": p.unit,
                }
                for p in self.params
            ],
            "years": {
                str(fy): {k: value_json(v) for k, v in values.items()}
                for fy, values in self.years.items()
            },
            "ages": [{"key": a.key, "label": a.label, "source": a.source} for a in self.ages],
        }
        if any(self.provisional.values()):
            # Present only when a marker exists, so a pack with none keeps its earlier hash.
            document["provisional"] = {
                str(fy): dict(notes) for fy, notes in self.provisional.items() if notes
            }
        text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("ascii")).hexdigest()


# --- captured figures (ADR-0022) -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Capture:
    """An active captured figure as the store holds it: the pure input of ``layer``."""

    id: int
    key: str
    fy: int
    #: ``value_json`` of the figure, integers and strings only.
    value_json: object
    #: ``None`` for a firm figure; a note when announced but not yet law.
    provisional_note: str | None
    quote: str | None
    page_url: str | None
    retrieved_on: date
    captured_at: datetime
    #: The actor kind (``owner``), as the audit trail names it.
    captured_by: str
    #: Who supplied it: ``typed``, ``ai`` or ``matched`` (ADR-0023). Display only; not digested.
    method: str = "typed"


@dataclass(frozen=True, slots=True)
class CapturedFigure:
    """A capture that changed the layered pack, and the shipped value it replaced (if different)."""

    id: int
    key: str
    fy: int
    quote: str | None
    page_url: str | None
    captured_at: datetime
    captured_by: str
    shipped_value: Value | None
    method: str = "typed"


@dataclass(frozen=True)
class Layered:
    """The result of ``merge``: the pack, and what happened to each capture that did not apply."""

    pack: RulePack
    #: Captures that changed the pack.
    applied: tuple[Capture, ...]
    #: Captures that add nothing: the shipped figure is the same, or is now firm.
    redundant: tuple[Capture, ...]
    #: Captures that no longer validate against the pack's specs, with why. Never fatal.
    unfit: tuple[tuple[Capture, str], ...]


def capture_digest(captures: Sequence[Capture]) -> str:
    """SHA-256 of the canonical JSON of ``(fy, key, value, provisional note)``, sorted."""
    rows = sorted(
        ([c.fy, c.key, c.value_json, c.provisional_note] for c in captures),
        key=lambda r: (r[0], r[1]),
    )
    text = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _decode(pack: RulePack, capture: Capture) -> tuple[Value | None, str | None]:
    """The capture's value, or why it no longer fits the pack's declarations."""
    from finance360.core import figure_capture  # deferred: figure_capture imports this module

    spec = next((s for s in pack.specs if s.key == capture.key), None)
    if spec is None:
        return None, f"{capture.key!r} is no longer declared for capture."
    if capture.fy < min(pack.years):
        return None, f"FY {fy_label(capture.fy)} is before the pack's first year."
    try:
        value = value_from_json(spec.kind, capture.value_json)
        check_value(spec.to_param(), value)
        figure_capture.parse_figure(spec, figure_capture.format_figure(spec, value))
    except (RulePackError, figure_capture.FigureError) as error:
        return None, str(error)
    return value, None


def layer(pack: RulePack, captures: Sequence[Capture]) -> RulePack:
    """``pack`` with the active ``captures`` placed over its figures (ADR-0022 section 1)."""
    return merge(pack, captures).pack


def merge(pack: RulePack, captures: Sequence[Capture]) -> Layered:
    """Layer ``captures`` over ``pack``. Pure; never raises for a bad capture.

    A capture overrides exactly its ``(key, fy)``. It is ignored (``redundant``) when the shipped
    figure is the same, or when it is provisional and the shipped figure is firm; it is ignored and
    reported (``unfit``) when it no longer validates. The result is a new pack, validated as a
    shipped one is; with nothing applied it is ``pack`` itself.
    """
    years = {fy: dict(values) for fy, values in pack.years.items()}
    provisional = {fy: dict(notes) for fy, notes in pack.provisional.items()}
    retrieved = {fy: dict(days) for fy, days in pack.retrieved.items()}
    params = list(pack.params)
    held = {p.key for p in params}
    applied: list[Capture] = []
    redundant: list[Capture] = []
    unfit: list[tuple[Capture, str]] = []
    figures: list[CapturedFigure] = []
    for capture in sorted(captures, key=lambda c: (c.fy, c.key, c.id)):
        value, problem = _decode(pack, capture)
        if value is None:
            unfit.append((capture, problem or "It no longer fits."))
            continue
        shipped = pack.years.get(capture.fy, {}).get(capture.key)
        shipped_note = pack.provisional_note(capture.fy, capture.key)
        now_firm = shipped is not None and shipped_note is None
        same = shipped == value and (capture.provisional_note is None) == (shipped_note is None)
        if same or (capture.provisional_note is not None and now_firm):
            redundant.append(capture)
            continue
        years.setdefault(capture.fy, {})[capture.key] = value
        if capture.provisional_note is None:
            provisional.get(capture.fy, {}).pop(capture.key, None)
        else:
            provisional.setdefault(capture.fy, {})[capture.key] = capture.provisional_note
        retrieved.setdefault(capture.fy, {})[capture.key] = capture.retrieved_on.isoformat()
        if capture.key not in held:
            params.append(next(s for s in pack.specs if s.key == capture.key).to_param())
            held.add(capture.key)
        applied.append(capture)
        figures.append(
            CapturedFigure(
                capture.id,
                capture.key,
                capture.fy,
                capture.quote,
                capture.page_url,
                capture.captured_at,
                capture.captured_by,
                shipped if shipped != value else None,
                capture.method,
            )
        )
    if not applied:
        return Layered(pack, (), tuple(redundant), tuple(unfit))
    layered = replace(
        pack,
        params=tuple(params),
        years=years,
        provisional=provisional,
        retrieved=retrieved,
        captured=tuple(figures),
        capture_digest=capture_digest(applied),
        shipped_hash=pack.content_hash(),
    )
    return Layered(layered, tuple(applied), tuple(redundant), tuple(unfit))


def check_value(param: Param, value: object) -> None:
    """Raise unless ``value`` is of the kind ``param`` declares (a float or a bool never is)."""
    ok = {
        MONEY: isinstance(value, Money),
        RATE: isinstance(value, Rate),
        INT: isinstance(value, int) and not isinstance(value, bool),
        BRACKETS: isinstance(value, tuple)
        and all(isinstance(b, Bracket) for b in cast(tuple[object, ...], value)),
    }[param.kind]
    if not ok:
        raise RulePackError(f"{param.key!r} must be a {param.kind} value.")
    if param.kind == BRACKETS:
        bands = cast(tuple[Bracket, ...], value)
        if not bands:
            raise RulePackError(f"{param.key!r} needs at least one band.")
        for earlier, later in zip(bands, bands[1:], strict=False):
            if later.threshold <= earlier.threshold:
                raise RulePackError(f"{param.key!r} bands must ascend strictly.")


def value_json(value: Value) -> object:
    """The canonical JSON form of a value: integers, strings and lists only (no float)."""
    if isinstance(value, Money):
        return {"ccy": value.currency, "minor": value.amount_minor}
    if isinstance(value, Rate):
        return {"ppm": value.ppm}
    if isinstance(value, tuple):
        return [{"rate_ppm": b.rate.ppm, "threshold": value_json(b.threshold)} for b in value]
    return value


def value_from_json(kind: str, data: object) -> Value:
    """Inverse of ``value_json`` for a value of ``kind``."""
    try:
        if kind == MONEY:
            doc = cast(dict[str, Any], data)
            return Money(doc["minor"], doc["ccy"])
        if kind == RATE:
            return Rate(cast(dict[str, Any], data)["ppm"])
        if kind == BRACKETS:
            return tuple(
                Bracket(
                    Money(band["threshold"]["minor"], band["threshold"]["ccy"]),
                    Rate(band["rate_ppm"]),
                )
                for band in cast(list[dict[str, Any]], data)
            )
        if kind == INT and isinstance(data, int) and not isinstance(data, bool):
            return data
    except (KeyError, TypeError) as error:
        raise RulePackError(f"Not a {kind} value in JSON: {error}") from None
    raise RulePackError(f"Not a {kind} value in JSON.")


# --- financial years -----------------------------------------------------------------------


def fy_of(day: date) -> int:
    """The FY containing ``day``, keyed by the year it ends in (1 July 2026 is FY ``2027``)."""
    return day.year + 1 if day.month >= 7 else day.year


def fy_label(fy: int) -> str:
    """``2027`` -> ``2026-27``."""
    return f"{fy - 1}-{fy % 100:02d}"


# --- materialise ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Span:
    """From projection year ``first_t`` until the next span, these values hold."""

    first_t: int
    values: Mapping[str, Value]


def _carried(param: Param, last: Value, behind: int, inflation: Rate) -> Value:
    """The value ``behind`` years after the last known one, in real terms."""
    if behind == 0 or param.policy == CPI:
        return last
    # frozen: nominal dollars fixed, so the real value is last / (1 + i)^behind, rounded once.
    if not isinstance(last, Money):  # RulePack validates that only money is frozen
        raise RulePackError(f"{param.key!r} is frozen but not money")
    return scale(last, PPM**behind, (PPM + inflation.ppm) ** behind)


def materialise(
    pack: RulePack,
    first_fy: int,
    years: int,
    inflation: Rate,
    overrides: Mapping[str, Value] | None = None,
) -> tuple[Span, ...]:
    """The pack's parameters for projection years ``0 .. years - 1`` as a step function.

    Year ``t`` uses FY ``first_fy + t`` (ADR-0018 section 2). Within the known years a value steps
    at the FY that publishes it; beyond the last known FY of that parameter its policy carries it
    forward. ``overrides`` (key to value) replace a parameter for every year. A new span starts
    only where some value differs from the year before, so a ``cpi``-only pack yields one span.
    """
    if years < 1:
        raise RulePackError("Materialise at least one year.")
    overrides = overrides or {}
    for key, value in overrides.items():
        check_value(pack.param(key), value)
    spans: list[Span] = []
    for t in range(years):
        fy = first_fy + t
        values: dict[str, Value] = {}
        for param in pack.params:
            if is_exact_year_key(param.key):
                continue  # read for an exact FY (Tax, Age Pension), never projected
            if param.key in overrides:
                values[param.key] = overrides[param.key]
                continue
            known = sorted(f for f, v in pack.years.items() if param.key in v and f <= fy)
            if not known:
                raise RulePackError(f"{param.key!r} has no value in or before FY {fy_label(fy)}.")
            latest = known[-1]
            later = any(param.key in v for f, v in pack.years.items() if f > latest)
            behind = 0 if later else fy - latest
            values[param.key] = _carried(param, pack.years[latest][param.key], behind, inflation)
        if not spans or spans[-1].values != values:
            spans.append(Span(t, values))
    return tuple(spans)


@dataclass(frozen=True, slots=True)
class Carried:
    """One parameter's value for a financial year, and the FY whose published figure it is from.

    ``behind`` is how many years the value was carried (0 when the pack publishes the year, or a
    later year). A ``frozen`` figure that was carried has fallen in real terms by the run's
    inflation (ADR-0018 section 3); a ``cpi`` one is repeated.
    """

    value: Value
    source_fy: int
    behind: int
    policy: str


def carried_value(pack: RulePack, key: str, fy: int, inflation: Rate) -> Carried | None:
    """``key`` in financial year ``fy`` by the same rule as ``materialise``, or ``None`` when the
    pack holds no figure for it in or before ``fy``.

    Unlike ``materialise`` it serves a ``tax.`` key too: the Tax capability reads an exact year for
    an estimate, and the projection reads this for a year the pack has not published (ADR-0019
    section 4). The value is in real terms, like every projected amount.
    """
    known = sorted(f for f, v in pack.years.items() if key in v and f <= fy)
    if not known:
        return None
    param = pack.param(key)
    latest = known[-1]
    later = any(key in v for f, v in pack.years.items() if f > latest)
    behind = 0 if later else fy - latest
    return Carried(
        _carried(param, pack.years[latest][key], behind, inflation), latest, behind, param.policy
    )


# --- registry ------------------------------------------------------------------------------

_PACKS: dict[str, RulePack] = {}


def register(pack: RulePack) -> None:
    """Add a pack under its jurisdiction. A different pack under the same key is an error."""
    existing = _PACKS.get(pack.jurisdiction)
    if existing is not None and existing is not pack:
        raise ValueError(f"A Rule pack is already registered for {pack.jurisdiction!r}")
    _PACKS[pack.jurisdiction] = pack


def unregister(jurisdiction: str) -> None:
    """For tests that register a stand-in."""
    _PACKS.pop(jurisdiction, None)


def get(jurisdiction: str) -> RulePack | None:
    return _PACKS.get(jurisdiction)


def registered() -> Sequence[RulePack]:
    return [_PACKS[k] for k in sorted(_PACKS)]
