"""The Rule pack values a projection reports, per Member (ADR-0018 sections 4 and 5).

The ages come from the registered pack (``core.rulepack``), or from a ``rule.<key>`` Assumption that
replaces the pack's age for every Member. They are recorded in the run's provenance and shown beside
the Assumptions. **They do not enter ``ProjectionInput`` and change no projection result yet**:
super access by age is B6. The engine and the input never see this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from finance360.capabilities.projection.assumptions import Source
from finance360.core.rulepack import TAX_PREFIX, Age, RulePack

#: Where a value came from, beside the Assumption layers ``scenario`` and ``household``.
LAYER_PACK = "rule_pack"


@dataclass(frozen=True)
class RuleParameter:
    """A pack key, as a reader sees it: its label and its public source."""

    key: str
    label: str
    source: str


@dataclass(frozen=True)
class RuleValue:
    member_id: int
    key: str
    value: Age
    layer: str


@dataclass(frozen=True)
class CapturedUsed:
    """A figure captured in the app that the pack carried (ADR-0022): the capture row always
    resolves to its quote and page, even after the capture is withdrawn."""

    key: str
    fy: int
    capture_id: int


@dataclass(frozen=True)
class RulesUsed:
    """The pack version and every rule value a run reported."""

    pack_version: str
    parameters: tuple[RuleParameter, ...]
    values: tuple[RuleValue, ...]
    #: Captured figures among the pack's non-``tax.`` keys, the ones a projection reads.
    captured: tuple[CapturedUsed, ...] = ()


def member_ages(
    pack: RulePack,
    members: Sequence[tuple[int, date]],
    overrides: Mapping[str, Source],
) -> RulesUsed:
    """Each Member's pack ages (``members`` is ``(id, date of birth)``), with any override."""
    values: list[RuleValue] = []
    for member_id, born_on in members:
        for rule in pack.ages:
            stated = overrides.get(rule.key)
            if stated is not None:
                if not isinstance(stated.value, Age):
                    raise TypeError(f"Override of {rule.key!r} is not an age")
                values.append(RuleValue(member_id, rule.key, stated.value, stated.layer))
            else:
                values.append(RuleValue(member_id, rule.key, rule.evaluate(born_on), LAYER_PACK))
    parameters = tuple(RuleParameter(a.key, a.label, a.source) for a in pack.ages)
    captured = tuple(
        CapturedUsed(c.key, c.fy, c.id) for c in pack.captured if not c.key.startswith(TAX_PREFIX)
    )
    return RulesUsed(pack.version_label, parameters, tuple(values), captured)
