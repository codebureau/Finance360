"""The Ask panel's prompt, built as gateway parts of the ``guidance`` data class (ADR-0021).

Public parts (sent as they are): the instructions, the Guide, the user's whereabouts. Non-public
parts (redacted by the gateway): What's due wording and the conversation. Nothing else may be added:
no Position, Valuation, Holding, Tax record or amount belongs in this data class.
"""

from __future__ import annotations

from collections.abc import Sequence

from finance360.capabilities.ai.gateway import Part
from finance360.capabilities.ask.corpus import Passage
from finance360.capabilities.ask.guide import Guide
from finance360.capabilities.ask.types import Turn
from finance360.core.due import DueItem

MAX_OUTPUT_TOKENS = 600
MAX_DUE_ITEMS = 10
#: The Passages a model is given (ADR-0023 section 4): the best few, never the whole Guide.
MAX_PASSAGES = 6
PASSAGE_MARKER = "### PASSAGE page={slug} title={title} heading={heading}"

INSTRUCTIONS = """\
You answer questions about Finance360, a household wealth-planning app. Answer the user's \
question using ONLY the Guide passages and the "What's due" items given below.

Rules:
- If the passages and the What's due items do not cover the question, set "answered" to false.
- Say what you rely on: base your answer on the Guide page(s) you cite.
- Never give tax, legal or investment advice, and never predict returns.
- Do not ask for or repeat figures, balances, names or account numbers. Text such as [MEMBER_1] or \
[ACCOUNT_2] stands for a name that was hidden from you: use it as written.
- Plain text only: no Markdown, no links in the text, at most a short paragraph or a few lines.
- You cannot change anything in the app. You can only explain and point.

Reply with ONE JSON object and nothing else:
{"answered": true|false, "answer": "<plain text>", \
"sources": [{"kind": "guide", "ref": "<page slug>"}, {"kind": "screen", "ref": "<screen path>"}]}
Cite at least one source when "answered" is true. A "guide" ref must be one of the page slugs \
below; a "screen" ref must be one of the screen paths below.\
"""


def _sources_text(guide: Guide) -> str:
    slugs = ", ".join(p.slug for p in guide.pages)
    screens = ", ".join(guide.screens)
    return f"Page slugs: {slugs}\nScreen paths: {screens}"


def render_passages(passages: Sequence[Passage]) -> str:
    blocks = [
        PASSAGE_MARKER.format(slug=p.page, title=p.page_title, heading=p.shown_heading)
        + f"\n{p.text}"
        for p in passages
    ]
    return "PASSAGES FROM THE GUIDE (best match first)\n\n" + "\n\n".join(blocks)


def render_due(items: Sequence[DueItem]) -> str:
    if not items:
        return "WHAT'S DUE\n(nothing is due right now)"
    lines = [
        f"- [{item.importance}] {item.title}. {item.effect} "
        f"(rule {item.rule_key}, screen {item.link})"
        for item in items[:MAX_DUE_ITEMS]
    ]
    return "WHAT'S DUE (most important first)\n" + "\n".join(lines)


def render_location(screen: str | None, guide_slug: str | None) -> str:
    where = f"The user is on the screen {screen}." if screen else "The user's screen is unknown."
    if guide_slug:
        where += f" They are reading the Guide page {guide_slug}."
    return where


def build_parts(
    guide: Guide,
    passages: Sequence[Passage],
    *,
    screen: str | None,
    guide_slug: str | None,
    due: Sequence[DueItem],
    history: Sequence[Turn],
    question: str,
) -> list[Part]:
    parts = [
        Part("instructions", INSTRUCTIONS + "\n\n" + _sources_text(guide), note="Ask instructions"),
        Part(
            "guide",
            render_passages(passages),
            note="Guide passages: " + ", ".join(dict.fromkeys(p.page for p in passages)),
        ),
        Part("location", render_location(screen, guide_slug), note=f"{screen} {guide_slug or ''}"),
        Part("due_items", render_due(due)),
    ]
    parts.extend(Part("conversation", t.text, role=t.role) for t in history)
    parts.append(Part("conversation", question, role="user"))
    return parts
