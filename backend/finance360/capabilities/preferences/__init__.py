"""The Preferences Capability (ADR-0031 section 3): Display preferences, stored per Login.

A Display preference remembers a presentation choice (a chart form). It is not audited and not a
Household fact: it changes no figure, and nothing here is read to compute one. It reads core only.
"""
