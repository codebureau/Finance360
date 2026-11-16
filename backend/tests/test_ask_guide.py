"""The Guide as the Ask panel reads it (ADR-0021 sections 2, 5 and 11).

The Guide is parsed twice, in TypeScript for the screen and in Python here, and this file holds the
two to the same slugs, titles and link targets. It also fails when the limits the Ask page states
drift from the configuration. (The Guide's size no longer matters to the prompt: a model gets only
the best Passages, and ``test_ask_search`` holds findability.)
"""

from __future__ import annotations

import re
from pathlib import Path

from finance360.capabilities.ask.guide import load
from finance360.core.ai_settings import (
    DEFAULT_ASK_MODEL,
    DEFAULT_DAILY_QUESTIONS,
    DEFAULT_LOG_DAYS,
    DEFAULT_MODEL,
    DEFAULT_MONTHLY_CAP_USD,
    AiSettings,
)

REPO = Path(__file__).resolve().parents[2]
GUIDE_DIR = REPO / "frontend" / "src" / "guide"
ASK_PAGE = GUIDE_DIR / "13-ask.md"
GUIDE = load(GUIDE_DIR)


def listing() -> list[tuple[str, str]]:
    """``(slug, title)`` read straight from the directory, without ``ask.guide``."""
    found: list[tuple[int, str, str]] = []
    for file in GUIDE_DIR.glob("*.md"):
        match = re.fullmatch(r"(\d+)-(.+)\.md", file.name)
        assert match, file.name
        title = next(
            ln[2:].strip() for ln in file.read_text(encoding="utf-8").splitlines() if ln[:2] == "# "
        )
        found.append((int(match.group(1)), match.group(2), title))
    return [(slug, title) for _, slug, title in sorted(found)]


def test_the_parsed_pages_are_the_directory_listing_in_order() -> None:
    assert [(p.slug, p.title) for p in GUIDE.pages] == listing()
    assert GUIDE.pages[0].slug == "start-here"
    assert GUIDE.page("rule-figures") is not None and GUIDE.page("nope") is None


def test_every_in_app_link_in_the_guide_is_a_valid_source_or_a_guide_page() -> None:
    slugs = {slug for slug, _ in listing()}
    targets = {
        m.group(1)
        for file in GUIDE_DIR.glob("*.md")
        for m in re.finditer(r"\]\((/[^)\s]*)\)", file.read_text(encoding="utf-8"))
    }
    assert targets, "the Guide links to screens"
    for target in targets:
        path = re.split(r"[?#]", target)[0] or "/"
        if path == "/guide":
            continue
        if path.startswith("/guide/"):
            assert path.removeprefix("/guide/") in slugs, target
        else:
            assert path in GUIDE.screens, target
    assert GUIDE.screens["/"] == "What's due"
    assert GUIDE.screens["/rule-figures"] == "Rule figures"  # the first link text to it


# --- the Ask page states the configured limits -------------------------------------------------


def limit_problems(text: str, config: AiSettings) -> list[str]:
    from finance360.core.ai_settings import micros_to_usd

    found: list[str] = []
    day = f"**{config.daily_questions} questions a day**"
    month = f"**${micros_to_usd(config.monthly_cap_usd_micros)} a month**"
    if day not in text:
        found.append(f"the Ask page does not state {day}")
    if month not in text:
        found.append(f"the Ask page does not state {month}")
    return found


def test_the_ask_page_states_the_limits_the_configuration_defaults_to() -> None:
    assert limit_problems(ASK_PAGE.read_text(encoding="utf-8"), AiSettings()) == []
    assert (DEFAULT_DAILY_QUESTIONS, DEFAULT_MONTHLY_CAP_USD) == (50, "5.00")


def test_the_check_notices_a_default_the_page_does_not_state() -> None:
    text = ASK_PAGE.read_text(encoding="utf-8")
    assert limit_problems(text, AiSettings(daily_questions=20)) == [
        "the Ask page does not state **20 questions a day**"
    ]
    assert limit_problems(text, AiSettings(monthly_cap_usd_micros=7_500_000)) == [
        "the Ask page does not state **$7.50 a month**"
    ]


def test_the_ask_page_names_each_setting_and_never_a_value_for_a_secret() -> None:
    text = ASK_PAGE.read_text(encoding="utf-8")
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    for name in re.findall(r"`(FINANCE360_AI_[A-Z_]+)`", text):
        assert f"# {name}=" in example, f"{name} is on the Ask page but not in .env.example"
    for name in ("PROVIDER", "API_KEY_FILE", "MODEL_ASK", "MONTHLY_CAP_USD", "DAILY_QUESTIONS"):
        assert f"`FINANCE360_AI_{name}`" in text
    assert not re.search(r"sk-[A-Za-z0-9]", text + example)


def test_env_example_states_the_model_defaults_and_the_log_retention_the_code_uses() -> None:
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert f"# FINANCE360_AI_MODEL={DEFAULT_MODEL}" in example
    assert f"# FINANCE360_AI_MODEL_ASK={DEFAULT_ASK_MODEL}" in example
    assert f"# FINANCE360_AI_MONTHLY_CAP_USD={DEFAULT_MONTHLY_CAP_USD}" in example
    assert f"# FINANCE360_AI_DAILY_QUESTIONS={DEFAULT_DAILY_QUESTIONS}" in example
    assert f"# FINANCE360_AI_LOG_DAYS={DEFAULT_LOG_DAYS}" in example
    assert DEFAULT_ASK_MODEL == "claude-haiku-5-5"


def test_the_image_copies_the_guide_and_context_and_points_the_backend_at_them() -> None:
    docker = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY frontend/src/guide /app/guide" in docker
    assert "FINANCE360_GUIDE_DIR=/app/guide" in docker
    # CONTEXT.md feeds Ask's vocabulary expansion (ADR-0023 section 2).
    assert "COPY CONTEXT.md /app/CONTEXT.md" in docker
    assert "FINANCE360_CONTEXT_FILE=/app/CONTEXT.md" in docker
    ignored = (REPO / ".dockerignore").read_text(encoding="utf-8").split()
    assert "CONTEXT.md" not in ignored and "*.md" not in ignored


def test_the_compose_file_passes_the_ai_settings_through_and_mounts_the_key_file_read_only() -> (
    None
):
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    for name in (
        "PROVIDER",
        "API_KEY_FILE",
        "MODEL",
        "MODEL_ASK",
        "MONTHLY_CAP_USD",
        "DAILY_QUESTIONS",
        "LOG_DAYS",
    ):
        assert f"FINANCE360_AI_{name}:" in compose, name
    assert "target: /run/secrets/finance360_ai_api_key" in compose
