"""Ask's keyword search (ADR-0023 section 2): the corpus and its Passages, the in-memory FTS5 index,
the query and the CONTEXT.md vocabulary expansion, and the **golden findability test**: plain-words
questions, each with the Guide page that must be in the top three Passages. A Guide edit that breaks
findability fails here, which is what lets the Guide grow safely. No AI is involved anywhere.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from finance360.capabilities.ask import corpus, search
from finance360.capabilities.ask.corpus import GuideCorpus, Passage
from finance360.capabilities.ask.guide import load
from finance360.capabilities.ask.synonyms import expansions_from
from finance360.config import SOURCE_CONTEXT_FILE, SOURCE_GUIDE_DIR

SOURCES = corpus.corpora(SOURCE_GUIDE_DIR)

#: Plain words a household member might type, and the page that must come back in the top three.
GOLDEN = [
    ("Where do I enter my salary?", "accounts-and-imports"),
    ("What should I do next?", "start-here"),
    ("What is a Rule figure?", "rule-figures"),
    ("How do I capture a Rule figure?", "rule-figures"),
    ("How do I keep the official rates current?", "rule-figures"),
    ("What is a provisional figure?", "rule-figures"),
    ("How do I import my brokerage transactions?", "accounts-and-imports"),
    ("Where do I upload a statement?", "accounts-and-imports"),
    ("How do I add a new account?", "accounts-and-imports"),
    ("How do I correct a wrong valuation?", "accounts-and-imports"),
    ("Where do prices and exchange rates come from?", "accounts-and-imports"),
    ("Why do my numbers look wrong?", "position"),
    ("What does the Position screen show?", "position"),
    ("What is a Scenario?", "projection"),
    ("Will the funds last?", "projection"),
    ("How do I compare two projections?", "projection"),
    ("How do I claim home office hours?", "tax"),
    ("Where do I record my car kilometres?", "tax"),
    ("How do I record a depreciating asset?", "tax"),
    ("What is income reconciliation?", "tax"),
    ("What do I need to provide each year?", "what-to-provide"),
    ("What should I do after 30 June?", "what-to-provide"),
    ("What does the AI log show?", "ask"),
    ("How do I ask a question?", "ask"),
    ("How do the areas link up?", "how-the-pieces-fit"),
    ("What questions does Finance360 answer?", "what-finance360-answers"),
    ("How do I keep track of tax?", "path-tax"),
    ("I just want to track my investments", "path-investments"),
    ("I want the whole picture of our finances", "path-whole-picture"),
    ("Where did a figure come from?", "what-finance360-answers"),
    # The Question cards' canned Ask prompts (ADR-0032 section 4): the Guide section for the
    # Question must come back in the top three. The same strings as the registry (checked below).
    ("What do we own and owe, and what does the Position need to be right?", "position"),
    ("Are we on track for retirement, and how does the Projection work it out?", "projection"),
    ("How much tax will we pay, and what does the Tax estimate need?", "tax"),
]


def top_pages(question: str, limit: int = 3) -> list[str]:
    return [p.page for p in search.search(SOURCES, question, limit, SOURCE_CONTEXT_FILE)]


@pytest.mark.parametrize(("question", "page"), GOLDEN, ids=[q for q, _ in GOLDEN])
def test_the_guide_page_that_answers_a_plain_question_is_in_the_top_three(
    question: str, page: str
) -> None:
    assert page in top_pages(question), f"{question!r} -> {top_pages(question)}"


def test_every_question_ask_prompt_is_in_the_golden_list() -> None:
    """A new Question's canned prompt must join the findability test (ADR-0032 section 4)."""
    from finance360.core import questions as core_questions
    from finance360.main import register_questions

    register_questions()
    golden = {q for q, _ in GOLDEN}
    assert {q.ask_prompt for q in core_questions.questions()} <= golden


def test_the_golden_list_is_big_enough_to_be_a_bar() -> None:
    assert len(GOLDEN) >= 25
    assert {page for _, page in GOLDEN} <= {p.slug for p in load(SOURCE_GUIDE_DIR).pages}


# --- FTS5 is there, and says so clearly when it is not ------------------------------------------


def test_this_pythons_sqlite_has_fts5() -> None:
    """CI's Python and the image's must carry FTS5; if one did not, search could not work."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize = 'porter unicode61')")
    except sqlite3.OperationalError as problem:  # pragma: no cover - only where FTS5 is missing
        pytest.fail(f"SQLite {sqlite3.sqlite_version} has no FTS5 (porter unicode61): {problem}")
    finally:
        conn.close()


def test_a_missing_fts5_module_is_reported_in_plain_words(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoFts:
        def execute(self, *_: object) -> None:
            raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: NoFts())
    with pytest.raises(search.Fts5Missing, match="no FTS5 module"):
        search.Index([])


# --- Passages ---------------------------------------------------------------------------------


def test_a_page_becomes_one_passage_per_section_with_its_heading_and_link() -> None:
    passages = list(GuideCorpus(SOURCE_GUIDE_DIR).passages())
    rule = [p for p in passages if p.page == "rule-figures"]
    headings = [p.heading for p in rule]
    assert "How to capture one" in headings and "" in headings  # the opening has no heading
    assert {p.link for p in rule} == {"/guide/rule-figures"}
    assert {p.link for p in passages if p.page == "start-here"} == {"/guide"}
    assert all(p.corpus == "guide" and p.text.strip() for p in passages)
    opening = next(p for p in rule if p.heading == "")
    assert opening.shown_heading == opening.page_title


def test_a_long_section_is_split_at_paragraph_breaks() -> None:
    paragraph = "word " * 100  # 500 characters
    body = "## Big\n\n" + "\n\n".join([paragraph.strip()] * 8)
    page = corpus.GuidePage("big", "Big page", body)
    parts = [p for p in corpus.page_passages("guide", page) if p.heading == "Big"]
    assert len(parts) >= 2
    assert all(len(p.text) <= corpus.SPLIT_AT + 500 for p in parts)
    assert "\n\n".join(p.text for p in parts) == body.removeprefix("## Big\n\n")


def test_an_excerpt_is_plain_words_cut_at_a_word() -> None:
    p = Passage("guide", "x", "X", "H", "See [the screen](/tax) for **bold** things. " * 20, "/x")
    text = corpus.excerpt(p, 80)
    assert "](" not in text and "*" not in text
    assert text.endswith("…") and len(text) <= 82


def test_a_corpus_that_is_not_public_never_reaches_a_model() -> None:
    class Notes:
        name = "notes"
        public = False

        def passages(self) -> list[Passage]:
            return []

        def digest(self) -> str:
            return "n"

    mine = Passage("notes", "n", "Notes", "", "private text", "/n")
    guide = Passage("guide", "g", "Guide", "", "public text", "/g")
    by_name = {"notes": Notes(), "guide": SOURCES[0]}
    assert corpus.answering_passages([mine, guide], by_name) == [guide]


# --- the index follows the corpus ---------------------------------------------------------------


def test_the_index_is_rebuilt_when_the_guide_changes(tmp_path: Path) -> None:
    guide = tmp_path / "guide"
    shutil.copytree(SOURCE_GUIDE_DIR, guide)
    sources = corpus.corpora(guide)
    assert search.search(sources, "zorblax", 3) == []
    with (guide / "06-position.md").open("a", encoding="utf-8") as f:
        f.write("\n## Zorblax\nThe zorblax screen is invented for this test.\n")
    found = search.search(sources, "zorblax", 3)
    assert [(p.page, p.heading) for p in found] == [("position", "Zorblax")]


def test_the_index_holds_the_current_guide_not_a_previous_one(tmp_path: Path) -> None:
    guide = tmp_path / "guide"
    shutil.copytree(SOURCE_GUIDE_DIR, guide)
    sources = corpus.corpora(guide)
    page = guide / "06-position.md"
    page.write_text(page.read_text(encoding="utf-8") + "\n## Frobnitz\nA frobnitz.\n", "utf-8")
    assert search.search(sources, "frobnitz", 3)
    gone = page.read_text(encoding="utf-8").replace("Frobnitz", "Gone").replace("frobnitz", "thing")
    page.write_text(gone, "utf-8")
    assert search.search(sources, "frobnitz", 3) == []


# --- the query ---------------------------------------------------------------------------------


def test_the_query_is_the_quoted_words_with_stop_words_removed_joined_by_or() -> None:
    assert search.build_match("What is the Rule figure for Medicare?") == (
        '"rule" OR "figure" OR "medicare"'
    )
    assert search.build_match("NEAR(a, b) AND * \" ' -x") == '"near" OR "b" OR "x"'


def test_a_question_with_no_searchable_word_finds_nothing() -> None:
    for question in ("", "   ", "what is it?", "?!*", "the of and"):
        assert search.build_match(question) == ""
        assert search.search(SOURCES, question, 3, SOURCE_CONTEXT_FILE) == []


def test_a_question_that_matches_nothing_finds_nothing() -> None:
    assert search.search(SOURCES, "Zzzz qqqq xylophone", 3, SOURCE_CONTEXT_FILE) == []


@pytest.mark.parametrize("hostile", ['"', "'; DROP TABLE passage; --", "a OR", "(((", "col:x", "*"])
def test_fts5_syntax_in_a_question_cannot_break_the_query(hostile: str) -> None:
    search.search(SOURCES, hostile, 3, SOURCE_CONTEXT_FILE)  # must not raise


# --- vocabulary expansion from CONTEXT.md ------------------------------------------------------

CONTEXT = """\
**Valuation**:
A dated value of an Account.
_Avoid_: Balance (on its own), Snapshot, Price

**Withdrawal strategy**:
How money is drawn down.
_Avoid_: Decumulation plan, Pay-out order

**Feed**:
A recurring source.
_Avoid_: Sync, Upload
_Also_: Import (within a Feed)

**Import**:
One ingestion.
_Avoid_: Upload, Sync

**Account**:
A holding.

**Income stream**:
A flow.
_Avoid_: Account, Benefit
"""


def test_a_plain_avoid_word_adds_its_term_and_a_qualified_one_does_not() -> None:
    exp = expansions_from(CONTEXT)
    assert exp.terms_for(["my", "snapshot"]) == ["Valuation"]
    assert exp.terms_for(["balance"]) == []  # "Balance (on its own)" is a disambiguation
    assert exp.terms_for(["price"]) == [
        "Valuation"
    ]  # a plain entry, listed beside the qualified one


def test_a_multi_word_phrase_and_a_plural_both_match() -> None:
    exp = expansions_from(CONTEXT)
    assert exp.terms_for(["a", "decumulation", "plans"]) == ["Withdrawal strategy"]
    assert exp.terms_for(["decumulation"]) == []  # only the phrase


def test_a_word_listed_under_two_terms_adds_both_and_a_defined_term_is_never_a_synonym() -> None:
    exp = expansions_from(CONTEXT)
    assert exp.terms_for(["sync"]) == ["Feed", "Import"]
    assert exp.terms_for(["account"]) == []  # "Account" is itself a term, listed only to be avoided


def test_the_users_own_word_is_never_replaced() -> None:
    exp = expansions_from(CONTEXT)
    assert search.build_match("upload", exp) == '"upload" OR "feed" OR "import"'


def test_the_real_context_file_expands_a_word_the_household_uses() -> None:
    exp = search.read_expansions(SOURCE_CONTEXT_FILE)
    assert exp is not None
    assert "Drawdown policy" in exp.terms_for(["withdrawal", "strategy"])
    assert "Valuation" in exp.terms_for(["snapshot"])


def test_without_context_md_search_still_works(tmp_path: Path) -> None:
    assert search.read_expansions(tmp_path / "absent.md") is None
    assert search.read_expansions(None) is None
    assert "rule-figures" in [
        p.page for p in search.search(SOURCES, "Rule figure", 3, tmp_path / "absent.md")
    ]
