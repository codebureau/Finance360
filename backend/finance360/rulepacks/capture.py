"""Guided Rule figure capture: the logic behind ``scripts/rulepack_update.py``.

"Open this page, tell me figure x." A ``FigureSpec`` (``core.rulepack``) declares, per key, the
page, where to look, the unit and the plausible range. This module parses what a person types,
validates unit and range, flags a large year-on-year change, records a figure the person cannot
confirm as ``provisional`` with a note, and renders the data module back out. It is pure apart from
the ``ask``/``say`` callables it is given, so it is tested without a terminal. It holds no figure.

Jurisdiction-agnostic on purpose: the Age Pension slice (B11) declares its own specs and reuses it.
Figures are held as canonical text (``"123.45"`` dollars, ``"16.5%"``, brackets as
``"over:rate,over:rate"``) and parsed at import, exactly as ADR-0018 section 6 asks.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from finance360.core.figure_capture import (
    CURRENCY,
    FigureError,
    entry_hint,
    format_figure,
    large_change,
    parse_figure,
    range_text,
)
from finance360.core.rulepack import (
    FigureSpec,
    Param,
    RulePackError,
    Value,
    check_value,
    fy_label,
)

__all__ = [
    "CURRENCY",
    "FigureData",
    "FigureError",
    "bump_revision",
    "capture_year",
    "format_figure",
    "large_change",
    "load_figures",
    "pack_parts",
    "parse_figure",
    "previous_figure",
    "render_figures_module",
]

# --- the data a pack holds -----------------------------------------------------------------


@dataclass
class FigureData:
    """What ``tax_figures.py``-style modules hold: values, provisional notes and retrieval dates,
    each by financial year then key."""

    figures: dict[int, dict[str, Value]] = field(default_factory=lambda: {})
    provisional: dict[int, dict[str, str]] = field(default_factory=lambda: {})
    retrieved: dict[int, dict[str, str]] = field(default_factory=lambda: {})


def load_figures(
    specs: Sequence[FigureSpec],
    figures: Mapping[int, Mapping[str, str]],
    provisional: Mapping[int, Mapping[str, str]] | None = None,
    retrieved: Mapping[int, Mapping[str, str]] | None = None,
) -> FigureData:
    """Parse a data module's text into values, validating each against its spec."""
    by_key = {s.key: s for s in specs}
    data = FigureData()
    for fy, texts in figures.items():
        for key, text in texts.items():
            spec = by_key.get(key)
            if spec is None:
                raise RulePackError(f"FY {fy_label(fy)} has a figure for undeclared key {key!r}.")
            try:
                data.figures.setdefault(fy, {})[key] = parse_figure(spec, text)
            except FigureError as error:
                raise RulePackError(f"{key!r} FY {fy_label(fy)}: {error}") from None
    for fy, notes in (provisional or {}).items():
        data.provisional[fy] = dict(notes)
    for fy, dates in (retrieved or {}).items():
        data.retrieved[fy] = dict(dates)
    return data


def pack_parts(
    specs: Sequence[FigureSpec], data: FigureData
) -> tuple[tuple[Param, ...], dict[int, dict[str, Value]]]:
    """The parameters (only those with at least one figure) and the years to merge into a pack."""
    held = {key for values in data.figures.values() for key in values}
    params = tuple(s.to_param() for s in specs if s.key in held)
    by_key = {p.key: p for p in params}
    for values in data.figures.values():
        for key, value in values.items():
            check_value(by_key[key], value)
    return params, {fy: dict(values) for fy, values in data.figures.items()}


# --- the guided session --------------------------------------------------------------------

Ask = Callable[[str], str]
Say = Callable[[str], None]


def previous_figure(data: FigureData, key: str, fy: int) -> tuple[int, Value] | None:
    """The latest figure for ``key`` in a year before ``fy``."""
    earlier = [y for y, values in data.figures.items() if y < fy and key in values]
    if not earlier:
        return None
    year = max(earlier)
    return year, data.figures[year][key]


def capture_year(
    specs: Sequence[FigureSpec],
    fy: int,
    data: FigureData,
    ask: Ask,
    say: Say,
    today: date,
) -> list[str]:
    """Walk ``specs`` for financial year ``fy``, updating ``data`` in place.

    Blank skips a key (its current figure stays); ``q`` stops early and keeps what is done.
    Returns the keys whose figure or provisional marker changed.
    """
    changed: list[str] = []
    say(f"Financial year {fy_label(fy)}. Enter skips a figure; q stops and keeps your answers.")
    for number, spec in enumerate(specs, start=1):
        say("")
        say(f"[{number}/{len(specs)}] {spec.label}  ({spec.key})")
        suffix = (
            "" if spec.url_confirmed else "  (address not yet confirmed: correct it if it moved)"
        )
        say(f"  Open:   {spec.page_url}{suffix}")
        say(f"  Look:   {spec.where}")
        say(f"  Enter:  {entry_hint(spec)}; plausible {range_text(spec)}")
        current = data.figures.get(fy, {}).get(spec.key)
        before = previous_figure(data, spec.key, fy)
        if before is not None:
            say(f"  Last year ({fy_label(before[0])}): {format_figure(spec, before[1])}")
        if current is not None:
            marker = data.provisional.get(fy, {}).get(spec.key)
            say(
                f"  Held for {fy_label(fy)}: {format_figure(spec, current)}"
                + (f"  [provisional: {marker}]" if marker else "")
            )
        try:
            value = _ask_figure(spec, before, ask, say)
        except _Stop:
            break
        if value is None:
            continue
        confirmed = ask("  Confirmed on the source page? [Y/n]: ").strip().lower() not in (
            "n",
            "no",
        )
        note: str | None = None
        if not confirmed:
            while not (note := ask("  Why is it provisional (a note)? ").strip()):
                say("  A note is required for a figure you cannot confirm.")
        held_note = data.provisional.get(fy, {}).get(spec.key)
        if current == value and held_note == note:
            say("  Unchanged.")
            continue
        data.figures.setdefault(fy, {})[spec.key] = value
        data.retrieved.setdefault(fy, {})[spec.key] = today.isoformat()
        if note is None:
            data.provisional.get(fy, {}).pop(spec.key, None)
        else:
            data.provisional.setdefault(fy, {})[spec.key] = note
        changed.append(spec.key)
    return changed


class _Stop(Exception):  # noqa: N818 - control flow inside this module only
    """The person typed ``q``."""


def _ask_figure(
    spec: FigureSpec, before: tuple[int, Value] | None, ask: Ask, say: Say
) -> Value | None:
    """A validated figure, or ``None`` to skip. Raises ``_Stop`` on ``q``."""
    while True:
        text = ask("  Figure: ").strip()
        if not text:
            return None
        if text.lower() == "q":
            raise _Stop
        try:
            value = parse_figure(spec, text)
        except FigureError as error:
            say(f"  Rejected: {error}")
            continue
        if before is not None and (warning := large_change(spec, before[1], value)):
            say(f"  Large change. {warning}")
            if ask("  Keep this figure? [y/N]: ").strip().lower() not in ("y", "yes"):
                continue
        return value


# --- writing the data module ---------------------------------------------------------------


def _literal(mapping: Mapping[int, Mapping[str, str]]) -> str:
    if not any(mapping.values()):
        return "{}"
    lines = ["{"]
    for fy in sorted(mapping):
        if not mapping[fy]:
            continue
        lines.append(f"    {fy}: {{")
        for key in sorted(mapping[fy]):
            lines.append(f"        {json.dumps(key)}: {json.dumps(mapping[fy][key])},")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


def render_figures_module(docstring: str, specs: Sequence[FigureSpec], data: FigureData) -> str:
    """The text of the data module: deterministic, so a re-run with no change is a no-diff."""
    by_key = {s.key: s for s in specs}
    texts = {
        fy: {k: format_figure(by_key[k], v) for k, v in values.items()}
        for fy, values in data.figures.items()
    }
    return (
        f'"""{docstring}"""\n\n'
        "from __future__ import annotations\n\n"
        f"FIGURES: dict[int, dict[str, str]] = {_literal(texts)}\n\n"
        f"PROVISIONAL: dict[int, dict[str, str]] = {_literal(data.provisional)}\n\n"
        f"RETRIEVED: dict[int, dict[str, str]] = {_literal(data.retrieved)}\n"
    )


_REVISION = re.compile(r"^REVISION = (\d+)$", re.MULTILINE)


def bump_revision(source: str) -> tuple[str, int]:
    """``source`` with its ``REVISION = n`` line incremented, and the new number."""
    match = _REVISION.search(source)
    if match is None:
        raise RulePackError("No 'REVISION = n' line to bump.")
    new = int(match.group(1)) + 1
    return source[: match.start()] + f"REVISION = {new}" + source[match.end() :], new
