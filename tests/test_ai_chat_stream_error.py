"""Task 3 (p0-wave): a failing LLM must not leave the chat stream silent.

Repro from the live product review: POST /ai-chat/message/stream returned 200
and persisted an assistant message ("The AI request couldn't be completed.
See the error detail below or check Admin -> API Settings.") but the browser
showed nothing for 15s+ - no typing indicator, no error bubble.

AgentRunner._fallback() (app/modules/ai_chat/services/agent_runner.py) already
does the right thing on the server: when the LLM call raises, it returns a
dict carrying BOTH a friendly `response` message AND the raw `error` reason.
chat_core.py's streaming route spreads that whole dict onto the SSE `done`
event unchanged. The bug this wave fixes was entirely in the browser
(app/static/js/ai_chat/transport.js unconditionally threw away the `response`
text whenever `error` was present, instead of rendering it) - but that
client behaviour can't be exercised from a server-side test.

What this file pins instead is the server-side half of the contract the
client fix depends on: given an LLM client that raises, the SSE stream for
/ai-chat/message/stream must actually carry a `done` event with a non-empty,
human-readable `response` (the message the UI is now supposed to render) and
the `error` field naming what went wrong. If a future change makes
AgentRunner swallow the LLM error into an empty response, or chat_core stop
forwarding `error`, this goes red before the browser regresses silently
again.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    # Bypass the LLM-configuration gate at the route level (require_ai_for_route
    # reads this feature flag, backed by app.services.llm_service.LLMService -
    # a DIFFERENT class than the one AgentRunner uses internally, which this
    # test mocks separately). Real LLM configuration is irrelevant to what is
    # being tested: the fallback/response-shape contract when a call fails.
    app.config["AI_CHAT_ENABLED"] = True

    with app.app_context():
        db.create_all()

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(db):
    from app.models.organization import Organization
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Test Org {suffix}", slug=f"test-org-{suffix}")
    db.session.add(org)
    db.session.flush()

    user = User(
        email=f"chat-{suffix}@example.com",
        first_name="Chat",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="procurement",
    )
    db.session.add(user)
    db.session.commit()
    return user.id


def _login(client, user_id):
    """Standard Flask-Login test-client pattern (see tests/test_ba_tenant_and_authz.py)."""
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id, app=client.application)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _parse_sse_events(body: bytes) -> list[dict]:
    """Pull every `data: {...}` frame out of a raw SSE response body."""
    events = []
    for raw_line in body.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except ValueError:
            continue
    return events


class TestStreamCarriesTheFailureMessage:
    """Mock the LLM client to raise; the SSE `done` event must carry both the
    friendly response text AND the error, not one or the other."""

    def test_llm_failure_streams_a_visible_response_and_an_error(self, app, client):
        from app import db

        with app.app_context():
            user_id = _make_user(db)
        _login(client, user_id)

        from app.modules.ai_chat.services.agent_runner import AgentRunner
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        def _raise_call_llm(self, *args, **kwargs):
            raise RuntimeError("simulated LLM provider outage")

        with app.app_context():
            _orig_provider = LLMService._get_configured_provider
            _orig_keys = LLMService._get_all_api_keys
            _orig_call = AgentRunner._call_llm
            try:
                # Provider selection succeeds (so the failure under test is the
                # LLM CALL itself, not "no provider configured" - a different,
                # already-covered fallback branch).
                LLMService._get_configured_provider = staticmethod(
                    lambda: ("anthropic", "claude-opus-5")
                )
                LLMService._get_all_api_keys = staticmethod(lambda provider: ["fake-test-key"])
                AgentRunner._call_llm = _raise_call_llm

                resp = client.post(
                    "/ai-chat/message/stream",
                    data=json.dumps({"message": "Why is my portfolio out of date?"}),
                    content_type="application/json",
                )
                # Must be consumed (blocks until the background agent thread
                # finishes and emits `done`) BEFORE the monkeypatches are
                # undone below - the thread is started synchronously inside
                # the view above, but runs on its own OS thread and races the
                # `finally` restore otherwise, intermittently seeing the real
                # (unmocked) LLMService/AgentRunner methods instead.
                body = resp.get_data()
            finally:
                LLMService._get_configured_provider = _orig_provider
                LLMService._get_all_api_keys = _orig_keys
                AgentRunner._call_llm = _orig_call

        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"

        events = _parse_sse_events(body)
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1, f"expected exactly one done event, got {events!r}"
        done = done_events[0]

        # A failure reason must be present...
        assert done.get("error"), "done event lost the LLM failure reason"
        # ...but NOT the raw one. Since 31 Aug 2026 the reason is sanitised
        # before it leaves the process (agent_runner.sanitize_agent_error):
        # the raw string is the provider's error body, and OpenRouter's 402
        # carries the provider account's user_id, which was reaching every
        # chat user's browser. The full reason stays in the server log.
        assert "simulated LLM provider outage" not in done["error"]

        # ...ALONGSIDE a real, human-readable message - not empty, not the
        # exception text itself, and not silently dropped. This is exactly
        # the field transport.js used to discard by throwing whenever `error`
        # was set, regardless of whether `response` also carried content.
        response_text = done.get("response") or ""
        assert response_text.strip(), (
            "done event carries no response text for the UI to render - this "
            "is the exact server-side shape that produced 15s+ of dead air"
        )
        assert "Admin" in response_text and "AI settings" in response_text, (
            "the fallback message should point the user at Admin -> AI "
            "settings, not a bare failure"
        )

    def test_no_provider_configured_also_streams_a_visible_response(self, app, client):
        """A second, distinct _fallback() branch (no API key at all) - same
        contract must hold: a message the UI can render, even with no error
        reason attached (this branch's `error` mirrors the human message, it
        never comes back empty)."""
        from app import db

        with app.app_context():
            user_id = _make_user(db)
        _login(client, user_id)

        from app.modules.ai_chat.services.llm_service_impl import LLMService

        with app.app_context():
            _orig_provider = LLMService._get_configured_provider
            _orig_keys = LLMService._get_all_api_keys
            try:
                LLMService._get_configured_provider = staticmethod(
                    lambda: ("anthropic", "claude-opus-5")
                )
                LLMService._get_all_api_keys = staticmethod(lambda provider: [])

                resp = client.post(
                    "/ai-chat/message/stream",
                    data=json.dumps({"message": "Hello"}),
                    content_type="application/json",
                )
                body = resp.get_data()  # see comment in the test above
            finally:
                LLMService._get_configured_provider = _orig_provider
                LLMService._get_all_api_keys = _orig_keys

        assert resp.status_code == 200
        events = _parse_sse_events(body)
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1
        done = done_events[0]
        assert (done.get("response") or "").strip(), "no response text to render"
        assert done.get("error"), "no error reason recorded for the missing API key case"


class TestKeepaliveKeepsTheStreamGenuinelyBusy:
    """Task 3 review fix: the client's 30s idle-read timeout only avoids
    false-positiving on a legitimately slow turn if the server keeps sending
    SOMETHING at a shorter interval. Before this fix, chat_core.py's SSE
    generator only backstopped an empty queue at 95s
    (event_queue.get(timeout=95)) - well OUTSIDE the 30s window it was
    supposed to be safely inside, so any turn slower than 30s to first token
    (not wedged, just busy) got killed by the client as a transport failure.

    This pins the server half of the fix: with the queue genuinely silent
    (the mocked LLM call sleeps well past one keepalive interval before
    ever raising), the SSE stream must carry at least one `{"type":
    "keepalive"}` event before the eventual `done` - proving the interval
    is actually enacted, not just documented in a comment.
    """

    def test_keepalive_event_emitted_during_queue_silence(self, app, client):
        from app import db

        with app.app_context():
            user_id = _make_user(db)
        _login(client, user_id)

        from app.modules.ai_chat.routes import chat_core
        from app.modules.ai_chat.services.agent_runner import AgentRunner
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        # Shrunk from the 15s production interval so this test does not
        # itself take 30+ seconds to prove the same thing.
        _KEEPALIVE_TEST_INTERVAL_S = 0.3

        def _slow_then_raise_call_llm(self, *args, **kwargs):
            # Long enough to guarantee at least two keepalive intervals pass
            # with the event_queue genuinely empty, short enough to keep the
            # test fast.
            time.sleep(_KEEPALIVE_TEST_INTERVAL_S * 3)
            raise RuntimeError("simulated slow, then failing, LLM call")

        with app.app_context():
            _orig_interval = chat_core._STREAM_KEEPALIVE_INTERVAL_S
            _orig_provider = LLMService._get_configured_provider
            _orig_keys = LLMService._get_all_api_keys
            _orig_call = AgentRunner._call_llm
            try:
                chat_core._STREAM_KEEPALIVE_INTERVAL_S = _KEEPALIVE_TEST_INTERVAL_S
                LLMService._get_configured_provider = staticmethod(
                    lambda: ("anthropic", "claude-opus-5")
                )
                LLMService._get_all_api_keys = staticmethod(lambda provider: ["fake-test-key"])
                AgentRunner._call_llm = _slow_then_raise_call_llm

                resp = client.post(
                    "/ai-chat/message/stream",
                    data=json.dumps({"message": "Take your time"}),
                    content_type="application/json",
                )
                body = resp.get_data()  # blocks until the background thread's done event
            finally:
                chat_core._STREAM_KEEPALIVE_INTERVAL_S = _orig_interval
                LLMService._get_configured_provider = _orig_provider
                LLMService._get_all_api_keys = _orig_keys
                AgentRunner._call_llm = _orig_call

        assert resp.status_code == 200
        events = _parse_sse_events(body)

        keepalives = [e for e in events if e.get("type") == "keepalive"]
        assert keepalives, (
            "no keepalive event was emitted while the queue sat empty for "
            f"{_KEEPALIVE_TEST_INTERVAL_S * 3}s against a "
            f"{_KEEPALIVE_TEST_INTERVAL_S}s interval - a client relying on "
            "these to distinguish 'slow' from 'wedged' would misfire"
        )

        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1
        # The keepalive(s) must precede the done event, not follow it - a
        # keepalive after the terminal event would be a generator that keeps
        # looping instead of returning.
        assert events.index(keepalives[0]) < events.index(done_events[0])
