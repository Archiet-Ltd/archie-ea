"""A provider read that was given up on does not write through the request's database session.

The read runs on a helper thread with the request's context copied in. A database session is not safe to
share between threads, and the request may have ended by the time an abandoned read finishes.
"""

from __future__ import annotations

import threading
import time

from app import db
from app.modules.ai_chat.services.agent_runner import read_before


def test_an_abandoned_read_finishing_late_uses_its_own_session_not_the_requests(app, db_session):
    request_session = db.session()
    finish = threading.Event()
    seen = {}

    def work(read):
        read.first.set()
        finish.wait(10)  # the provider answers only after the turn gave up on it
        seen["session"] = db.session()
        seen["thread"] = threading.current_thread().name
        return "late"

    started = time.monotonic()
    try:
        read_before(work, started + 0.2)
        raise AssertionError("the read should have been abandoned")
    except TimeoutError:
        pass
    helper = next(t for t in threading.enumerate() if t.name == "provider-read")
    finish.set()
    helper.join(10)

    assert seen["thread"] == "provider-read"
    assert seen["session"] is not request_session, "the abandoned read wrote through the request's session"


def test_what_an_abandoned_read_logs_is_still_in_the_interaction_log(app, db_session):
    from app.models import LLMInteraction
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    marker = "abandoned-read-%d" % time.monotonic_ns()
    finish = threading.Event()

    def work(read):
        read.first.set()
        finish.wait(10)
        LLMService._persist_interaction_record(LLMInteraction(
            prompt="p", response=marker, model_name="m", provider="openrouter",
            token_count_input=0, token_count_output=0, cost=0))
        return "late"

    try:
        read_before(work, time.monotonic() + 0.2)
    except TimeoutError:
        pass
    helper = next(t for t in threading.enumerate() if t.name == "provider-read")
    finish.set()
    helper.join(10)

    assert LLMInteraction.query.filter_by(response=marker).count() == 1
