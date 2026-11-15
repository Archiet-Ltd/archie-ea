"""A chat answer reads in a customer's words, not the product's internal names."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.ai_chat.services.customer_words import WRITE_FAILED, in_customer_words
from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

SQL = 'null value in column "organization_id" of relation "risk_application_links" violates not-null constraint'


def test_genome_patch_becomes_a_proposed_change_to_your_model():
    text = in_customer_words("I can queue a genome patch. Two Genome Patches are waiting.")
    assert "genome" not in text.lower()
    assert "proposed change to your model" in text
    assert "proposed changes to your model" in text


def test_raw_fields_are_said_in_words():
    text = in_customer_words(
        "Payroll has `supported: false` and `supported_by` is empty. Every `capability` field is null. "
        "The project is `in_progress`."
    )
    assert "supported:" not in text and "supported_by" not in text and "in_progress" not in text
    assert "null" not in text and "`" not in text
    assert "Payroll is not supported" in text and "nothing is recorded as supporting it" in text
    assert "in progress" in text and "no capability is recorded" in text


def test_arb_and_repository_are_explained():
    text = in_customer_words("Send it to the ARB. It comes from the repository.")
    assert "Architecture Review Board" in text and "ARB" not in text
    assert "the code repository" in text
    assert in_customer_words("The Architecture Review Board (ARB) meets.") == "The Architecture Review Board (ARB) meets."


def test_a_failed_lookup_does_not_show_the_tool_name():
    text = in_customer_words("I could not find it: `explain_element` returned nothing.")
    assert "explain_element" not in text and "explain element" not in text


def test_database_text_in_an_answer_is_replaced_by_a_plain_sentence():
    text = in_customer_words("I tried to link them. " + SQL + ". Anything else?", write_failed=True)
    for fragment in ("null value", "organization_id", "risk_application_links", "constraint"):
        assert fragment not in text
    assert text == "I tried to link them. " + WRITE_FAILED + " Anything else?"


def test_database_text_with_no_failed_write_is_removed_without_claiming_a_failure():
    text = in_customer_words("Done. " + SQL + ". Anything else?")
    assert "null value" not in text and "could not be saved" not in text


def test_plain_answers_are_left_alone():
    sentence = "Finance Ledger is owned by Ada and runs on the Core Platform."
    assert in_customer_words(sentence) == sentence


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def test_nothing_shown_to_the_model_says_genome_patch():
    from app.modules.ai_chat.services import agent_runner

    shown = [agent_runner._AGENT_PREFIX] + [s for schema in TOOL_SCHEMAS for s in _strings(schema)
                                           if s != schema.get("name")]
    offenders = [s[:80] for s in shown if "genome patch" in s.lower()]
    assert not offenders, offenders


def test_a_failed_write_gives_a_plain_sentence_with_no_database_text(app, monkeypatch):
    def raises(self, args):
        raise IntegrityError("INSERT INTO risk_application_links", {}, Exception(SQL))

    monkeypatch.setattr(ToolExecutor, "_tool_find_applications", raises)
    with app.app_context():
        result = ToolExecutor(user_id=1).execute(ToolCall(id="t1", name="find_applications", arguments={}))
    assert result["success"] is False
    assert result["error"] == WRITE_FAILED
    for fragment in ("INSERT", "organization_id", "risk_application_links", "null value", "constraint"):
        assert fragment not in result["error"]


def test_a_handler_that_returns_database_text_is_also_plain(app, monkeypatch):
    monkeypatch.setattr(ToolExecutor, "_tool_find_applications", lambda self, a: {"success": False, "error": SQL})
    with app.app_context():
        result = ToolExecutor(user_id=1).execute(ToolCall(id="t2", name="find_applications", arguments={}))
    assert result["error"] == WRITE_FAILED


UNCHANGED = [
    "The short answer is yes.",
    "Here is the answer: Payroll runs on the Core Platform.",
    "Answer Desk Rota is owned by Ada.",
    "Select an application from the list to see its owner.",
    "I can delete from your model any application you retire.",
    "Update Payroll set to retire by the end of the year.",
    "Insert into the roadmap a review of the Finance Ledger.",
    "You can update the owner, select a status, then delete from the list what you no longer need.",
    "The architecture repository holds the standards and reference models.",
    "Repositories of reusable components sit in the architecture repository.",
    "Select the capability from the map; the value is None until someone records it.",
    "```\nowner = None\nstatus = 'in_progress'\n```",
    "Set `owner = None` before you save.",
    "The `risk_application_links` table is internal.",
    "arb is not a word here; ARBITRARY is also left alone.",
]

CHANGED = [
    ("I called explain_element and it found nothing.", "I called that lookup and it found nothing."),
    ("`explain_element` returned nothing.", "That lookup returned nothing."),
    ("The step find_applications failed.", "That lookup failed."),
    ("Payroll has `supported: false`.", "Payroll is not supported."),
    ("The project is in_progress.", "The project is in progress."),
    ("We queued a genome patch.", "We queued a proposed change to your model."),
    ("Send it to the ARB.", "Send it to the Architecture Review Board."),
    ("It comes from the repository.", "It comes from the code repository."),
    ("I used answer() to reply.", "I used that lookup to reply."),
]


@pytest.mark.parametrize("sentence", UNCHANGED)
def test_ordinary_english_and_code_come_back_unchanged(sentence):
    assert in_customer_words(sentence) == sentence
    assert in_customer_words(sentence, write_failed=True) == sentence


@pytest.mark.parametrize("raw,said", CHANGED)
def test_internal_words_are_replaced_and_the_sentence_keeps_its_shape(raw, said):
    assert in_customer_words(raw) == said


def test_a_replacement_after_a_full_stop_starts_with_a_capital():
    said = in_customer_words("It failed. explain_element returned nothing. Try another.")
    assert said == "It failed. That lookup returned nothing. Try another."


def test_a_streamed_answer_never_shows_a_raw_word_in_any_chunk():
    from app.modules.ai_chat.services.customer_words import StreamingWords

    raw = ("I will queue a genome patch. Payroll has `supported: false` and the project is in_progress. "
           "I called explain_element, the short answer is no. Send it to the ARB.")
    for size in (1, 2, 3, 5, 9, 17):
        words = StreamingWords()
        shown = ""
        for i in range(0, len(raw), size):
            shown += words.feed(raw[i:i + size])
            for bad in ("genome", "supported:", "in_progress", "explain_element"):
                assert bad not in shown.lower(), (size, shown)
            assert "genome" not in shown.lower()
        shown += words.finish()
        assert shown == in_customer_words(raw), (size, shown)
        assert "the short answer is no" in shown


def test_streamed_code_is_not_cut_open():
    from app.modules.ai_chat.services.customer_words import StreamingWords

    raw = "Use this:\n```\nowner = None\n```\nThen save."
    words = StreamingWords()
    shown = "".join(words.feed(raw[i:i + 3]) for i in range(0, len(raw), 3)) + words.finish()
    assert shown == raw


def test_known_pages_lists_only_what_the_role_may_open_under_its_sidebar_name(app):
    from app.modules.ai_chat.services.customer_words import known_pages
    from app.utils.role_access import get_sidebar_zones

    class Person:
        enterprise_role = "enterprise_architect"
        is_authenticated = True
        is_platform_admin = False

        def is_admin(self):
            return False

    with app.test_request_context("/"):
        pages = known_pages(app, Person())
        shown = {l["label"] for z in get_sidebar_zones(Person()) for l in z["links"]}
    assert pages and set(pages.values()) <= shown
    assert not [p for p in pages if p.startswith("/api")]
