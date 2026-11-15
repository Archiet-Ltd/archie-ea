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
    assert "code repository" in text
    assert in_customer_words("The Architecture Review Board (ARB) meets.") == "The Architecture Review Board (ARB) meets."


def test_a_failed_lookup_does_not_show_the_tool_name():
    text = in_customer_words("I could not find it: `explain_element` returned nothing.")
    assert "explain_element" not in text and "explain element" not in text


def test_database_text_in_an_answer_is_replaced_by_a_plain_sentence():
    text = in_customer_words("I tried to link them. " + SQL + ". Anything else?")
    for fragment in ("null value", "organization_id", "risk_application_links", "constraint"):
        assert fragment not in text
    assert WRITE_FAILED in text


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
