"""What a Behaviour is. The derive hook's shapes are in ``core.derived`` (ADR-0013 section 3).

A Behaviour is an identity plus, optionally, a ``derive`` hook: a pure function from an Account's
recorded facts to a value on a date. ``simple_balance`` has none (its value is its latest
Valuation). ``core`` never imports this package; the composition root registers each hook into the
Position service, which adds the result as a ``derived`` Valuation point.
"""

from __future__ import annotations

from dataclasses import dataclass

from finance360.core.derived import DeriveHook


@dataclass(frozen=True)
class Behaviour:
    key: str
    name: str
    derive: DeriveHook | None = None
