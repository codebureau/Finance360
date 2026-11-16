"""The ATO income statement Document profile (ADR-0024 section 7).

The profile is data: ``profile.json`` beside this file, validated by ``load_profile`` when this
package is imported, so a bad edit fails the start-up loudly. ``main.py`` registers ``PROFILE``.
See ``README.md`` beside it for how to adjust or add a profile.
"""

from __future__ import annotations

import json
from pathlib import Path

from finance360.capabilities.documents.profile import Profile, load_profile

PROFILE: Profile = load_profile(
    json.loads((Path(__file__).with_name("profile.json")).read_text(encoding="utf-8"))
)
