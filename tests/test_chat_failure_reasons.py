"""Every reason the chat can give for an unanswered question has a sentence a person can read."""

import re
from pathlib import Path

from app.modules.ai_chat.services import failure_messages as fm

ROOT = Path(__file__).resolve().parent.parent
SERVER_FILES = [
    ROOT / "app/modules/ai_chat/routes/chat_core.py",
    ROOT / "app/modules/ai_chat/services/agent_runner.py",
]
PAGE_SCRIPT = ROOT / "app/static/js/ai_chat/app.js"


def _codes_written_in(path, pattern):
    return set(re.findall(pattern, path.read_text()))


def test_every_reason_code_the_server_sends_has_a_sentence():
    sent = set()
    for path in SERVER_FILES:
        sent |= _codes_written_in(path, r'"reason_code":\s*"([a-z_]+)"')
        sent |= _codes_written_in(path, r'reason_code\s*=\s*"([a-z_]+)"')
    sent |= {fm.reason_code_for_status(s) for s in (401, 403, 408, 413, 502, 503, 504)}
    sent |= {fm.reason_code_for_text(t) for t in ("timed out", "too large", "403", "connection refused")}
    sent.discard(None)
    assert {"timeout", "provider_unreachable"} <= sent, sent
    missing = {c for c in sent if not fm.FAILURE_REASONS.get(c)}
    assert not missing, "no sentence for %s" % sorted(missing)


def test_every_reason_the_page_can_pick_has_a_sentence():
    picked = _codes_written_in(PAGE_SCRIPT, r"return '([a-z_]+)';")
    picked |= _codes_written_in(PAGE_SCRIPT, r"appendFailure\('([a-z_]+)'")
    picked = {c for c in picked if "_" in c or c in ("timeout", "unknown")}
    assert {"signed_out", "role_not_allowed", "too_large", "timeout", "provider_unreachable"} <= picked
    assert not {c for c in picked if c not in fm.FAILURE_REASONS}


def test_the_five_reasons_are_named_in_plain_words_and_say_what_to_do():
    said = {code: fm.failure_sentence(code) for code in fm.FAILURE_REASONS}
    assert "did not answer in time" in said["timeout"]
    assert "refused" in said["provider_unreachable"] and "reached" in said["provider_unreachable"]
    assert "signed out" in said["signed_out"]
    assert "role does not allow" in said["role_not_allowed"]
    assert "too large" in said["too_large"]
    for code, sentence in said.items():
        assert sentence.endswith("."), (code, sentence)
        assert "_" not in sentence, (code, sentence)


def test_a_provider_timeout_in_a_stopped_turn_is_answered_with_the_timeout_sentence():
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    result = AgentRunner._fallback(object.__new__(AgentRunner), "provider timed out after 60s")
    assert result["response"] == fm.FAILURE_REASONS["timeout"]
    assert result["reason_code"] == "timeout"


def test_an_unrecognised_failure_keeps_the_general_sentence_and_sends_no_code():
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    result = AgentRunner._fallback(object.__new__(AgentRunner), "something odd")
    assert result["response"] == fm.ASSISTANT_UNAVAILABLE
    assert "reason_code" not in result


def test_the_page_is_given_the_table_and_the_saved_failure_sentences():
    table = fm.failure_table_for_page()
    assert table["reasons"] == fm.FAILURE_REASONS
    assert fm.FAILURE_REASONS["timeout"] in table["saved"]
    assert "The assistant is busy right now. Wait a minute, then press Retry." in table["saved"]
